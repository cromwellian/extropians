#!/usr/bin/env python3
"""Extropians archive RAG server.

Serves the React UI plus:
  GET  /api/search?q=...&mode=hybrid|keyword|semantic
  GET  /api/message/{id}
  GET  /api/thread/{id}        (thread containing message id)
  GET  /api/stats
  POST /api/chat               (SSE: sources event, then text deltas)
"""
import json
import os
import re
import sqlite3
import threading

import numpy as np
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import index_meta
import llm

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.environ.get("EXTRO_DB", os.path.join(REPO, "data", "extropians.db"))
EMB_PATH = os.path.join(REPO, "data", "embeddings.npy")
IDS_PATH = os.path.join(REPO, "data", "chunk_ids.npy")
META_PATH = os.path.join(REPO, "data", "index_meta.json")
WEB_DIST = os.path.join(REPO, "web", "dist")

app = FastAPI(title="Extropians Archive RAG")

_local = threading.local()


def db():
    if not hasattr(_local, "con"):
        _local.con = sqlite3.connect(DB_PATH)
        _local.con.row_factory = sqlite3.Row
    return _local.con


# ---------------------------------------------------------------- semantic

class Semantic:
    def __init__(self):
        self.emb = None
        self.ids = None
        self.model = None
        self.status = "not built"
        self.lock = threading.Lock()

    def ensure(self):
        with self.lock:
            if self.model is not None:
                return True
            if self.status == "stale":
                return False
            if not (os.path.exists(EMB_PATH) and os.path.exists(IDS_PATH)):
                self.status = "not built"
                return False
            # An index built against a renumbered database would map
            # vectors to the wrong messages; fall back to keyword only.
            if not index_meta.is_current(META_PATH, db()):
                self.status = "stale"
                print("WARNING: semantic index is stale (database was "
                      "rebuilt after embedding). Falling back to keyword "
                      "search. Re-run rag/embed.py to fix.")
                return False
            from sentence_transformers import SentenceTransformer
            self.emb = np.load(EMB_PATH).astype(np.float32)
            self.ids = np.load(IDS_PATH)
            self.model = SentenceTransformer(
                "sentence-transformers/all-MiniLM-L6-v2")
            self.status = "ready"
            return True

    def search(self, query, k=60):
        """Return [(message_id, score)] best-chunk-per-message."""
        if not self.ensure():
            return []
        q = self.model.encode([query], normalize_embeddings=True)[0]
        scores = self.emb @ q.astype(np.float32)
        top = np.argpartition(-scores, min(k * 4, len(scores) - 1))[:k * 4]
        top = top[np.argsort(-scores[top])]
        best = {}
        for idx in top:
            mid = int(self.ids[idx])
            if mid not in best:
                best[mid] = float(scores[idx])
            if len(best) >= k:
                break
        return sorted(best.items(), key=lambda x: -x[1])


SEM = Semantic()


STOPWORDS = set("""a an and are as at be but by did do does for from had has
have how i in is it its of on one or people short paragraph please summarize
summary tell that the their there these they think thought to was were what
when where which who why with would you your about""".split())


def _fts_run(fq, k):
    try:
        rows = db().execute(
            "SELECT rowid, rank FROM messages_fts WHERE messages_fts MATCH ? "
            "ORDER BY rank LIMIT ?", (fq, k)).fetchall()
    except sqlite3.OperationalError:
        return []
    return [(r["rowid"], -r["rank"]) for r in rows]


def keyword_search(q, k=60):
    terms = re.findall(r"[A-Za-z0-9']+", q)[:24]
    if not terms:
        return []
    content = [t for t in terms if t.lower() not in STOPWORDS] or terms
    quoted = [f'"{t}"' for t in content]
    # strict AND first (precision), then OR over content words (recall)
    results = _fts_run(" ".join(quoted), k)
    if len(results) < max(5, k // 6):
        seen = {mid for mid, _ in results}
        for mid, s in _fts_run(" OR ".join(quoted), k):
            if mid not in seen:
                results.append((mid, s))
        results = results[:k]
    return results


def hybrid_search(q, k=30):
    """Reciprocal-rank fusion of keyword and semantic results."""
    kw = keyword_search(q, k=60)
    sem = SEM.search(q, k=60)
    scores = {}
    for results, weight in ((kw, 1.0), (sem, 1.2)):
        for rank, (mid, _) in enumerate(results):
            scores[mid] = scores.get(mid, 0.0) + weight / (60 + rank)
    ranked = sorted(scores.items(), key=lambda x: -x[1])[:k]
    return ranked


def msg_meta(row):
    return {
        "id": row["id"],
        "from_name": row["from_name"],
        "from_email": row["from_email"],
        "date": row["date_iso"],
        "subject": row["subject"],
        "source_kind": row["source_kind"],
        "digest_label": row["digest_label"],
        "source_file": row["source_file"],
    }


def fetch_msgs(ids):
    if not ids:
        return {}
    ph = ",".join("?" * len(ids))
    rows = db().execute(
        f"SELECT * FROM messages WHERE id IN ({ph})", list(ids)).fetchall()
    return {r["id"]: r for r in rows}


# ---------------------------------------------------------------- routes

@app.get("/api/stats")
def stats():
    con = db()
    total = con.execute("SELECT COUNT(*) c FROM messages").fetchone()["c"]
    span = con.execute(
        "SELECT MIN(date_iso) a, MAX(date_iso) b FROM messages "
        "WHERE date_iso IS NOT NULL").fetchone()
    threads = con.execute(
        "SELECT COUNT(DISTINCT norm_subject) c FROM messages").fetchone()["c"]
    people = con.execute(
        "SELECT COUNT(DISTINCT from_email) c FROM messages").fetchone()["c"]
    if SEM.status == "ready" or SEM.model is not None:
        sem = "ready"
    elif not os.path.exists(EMB_PATH):
        sem = "not built"
    elif not index_meta.is_current(META_PATH, con):
        sem = "stale"
    else:
        sem = "ready"
    return {"messages": total, "threads": threads, "people": people,
            "from": span["a"], "to": span["b"],
            "semantic": sem == "ready", "semantic_status": sem,
            "backend": llm.backend_name()}


@app.get("/api/search")
def search(q: str, mode: str = "hybrid", limit: int = 30):
    if mode == "keyword":
        ranked = keyword_search(q, k=limit)
    elif mode == "semantic":
        ranked = SEM.search(q, k=limit)
    else:
        ranked = hybrid_search(q, k=limit)
    rows = fetch_msgs([mid for mid, _ in ranked])
    out = []
    for mid, score in ranked:
        r = rows.get(mid)
        if not r:
            continue
        m = msg_meta(r)
        m["snippet"] = re.sub(r"\s+", " ", r["body"][:220])
        m["score"] = round(score, 4)
        out.append(m)
    return {"results": out}


@app.get("/api/message/{mid}")
def message(mid: int):
    r = db().execute("SELECT * FROM messages WHERE id=?", (mid,)).fetchone()
    if not r:
        raise HTTPException(404)
    m = msg_meta(r)
    m["headers"] = r["headers"]
    m["body"] = r["body"]
    m["norm_subject"] = r["norm_subject"]
    n = db().execute(
        "SELECT COUNT(*) c FROM messages WHERE norm_subject=?",
        (r["norm_subject"],)).fetchone()["c"]
    m["thread_size"] = n
    return m


@app.get("/api/thread/{mid}")
def thread(mid: int):
    r = db().execute(
        "SELECT norm_subject FROM messages WHERE id=?", (mid,)).fetchone()
    if not r:
        raise HTTPException(404)
    rows = db().execute(
        "SELECT * FROM messages WHERE norm_subject=? "
        "ORDER BY COALESCE(date_epoch, 9999999999), id LIMIT 500",
        (r["norm_subject"],)).fetchall()
    return {"subject": r["norm_subject"],
            "messages": [dict(msg_meta(x), snippet=re.sub(r"\s+", " ", x["body"][:180]))
                         for x in rows]}


# ---------------------------------------------------------------- chat

class ChatTurn(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    messages: list[ChatTurn]


SYSTEM_PROMPT = """You are a research assistant for the Extropians mailing \
list archive (1992-2003), a transhumanist discussion list. You answer \
questions using ONLY the archive excerpts provided in each request.

Rules:
- Cite sources inline using bracketed numbers like [3] that refer to the \
numbered SOURCES. Cite every claim you draw from a source. Use multiple \
citations where relevant.
- If the sources don't answer the question, say so plainly; you may note \
what related material the sources do contain.
- When asked about people, describe what they actually posted or discussed.
- Quote short memorable passages when they help, with citation.
- Answer in Markdown. Be concrete and specific: names, dates, thread titles."""


def build_context(question):
    max_sources, source_chars = llm.context_budget()
    ranked = hybrid_search(question, k=max_sources)
    rows = fetch_msgs([mid for mid, _ in ranked])
    sources = []
    blocks = []
    for i, (mid, _) in enumerate(ranked, 1):
        r = rows.get(mid)
        if not r:
            continue
        body = r["body"]
        if len(body) > source_chars:
            body = body[:source_chars] + "\n[...truncated...]"
        sources.append({"n": i, "id": mid, "subject": r["subject"],
                        "from_name": r["from_name"], "date": r["date_iso"]})
        blocks.append(
            f"[{i}] From: {r['from_name']} <{r['from_email']}>\n"
            f"    Date: {r['date_iso'] or r['date_raw']}\n"
            f"    Subject: {r['subject']}\n{body}")
    return sources, "\n\n---\n\n".join(blocks)


@app.post("/api/chat")
def chat(req: ChatRequest):
    question = next((t.content for t in reversed(req.messages)
                     if t.role == "user"), "")
    if not question.strip():
        raise HTTPException(400, "empty question")

    # augment retrieval query with a little prior context
    prior = [t.content for t in req.messages[:-1] if t.role == "user"][-1:]
    retrieval_q = (" ".join(prior) + " " + question).strip() if prior else question

    sources, context = build_context(retrieval_q)

    history = ""
    if len(req.messages) > 1:
        turns = []
        for t in req.messages[:-1][-6:]:
            who = "User" if t.role == "user" else "Assistant"
            turns.append(f"{who}: {t.content[:1500]}")
        history = "Conversation so far:\n" + "\n\n".join(turns) + "\n\n"

    prompt = (f"{history}SOURCES (archive excerpts):\n\n{context}\n\n"
              f"===\nQuestion: {question}\n\n"
              f"Answer using the sources above, citing like [1].")

    def gen():
        yield "event: sources\ndata: " + json.dumps(sources) + "\n\n"
        try:
            for text in llm.stream_completion(SYSTEM_PROMPT, prompt):
                yield "event: delta\ndata: " + json.dumps(text) + "\n\n"
        except Exception as e:
            yield "event: error\ndata: " + json.dumps(str(e)) + "\n\n"
        yield "event: done\ndata: {}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


# ---------------------------------------------------------------- static UI

if os.path.isdir(WEB_DIST):
    app.mount("/assets", StaticFiles(directory=os.path.join(WEB_DIST, "assets")),
              name="assets")

    @app.get("/{path:path}")
    def spa(path: str):
        full = os.path.join(WEB_DIST, path)
        if path and os.path.isfile(full):
            return FileResponse(full)
        return FileResponse(os.path.join(WEB_DIST, "index.html"))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8123)
