#!/usr/bin/env python3
"""Embed all messages with sentence-transformers for semantic search.

Produces data/embeddings.npy (float16, unit-normalized) and
data/chunk_ids.npy (int64 message ids, one per chunk row).
"""
import os
import sqlite3

import numpy as np

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.environ.get("EXTRO_DB", os.path.join(REPO, "data", "extropians.db"))
EMB_PATH = os.path.join(REPO, "data", "embeddings.npy")
IDS_PATH = os.path.join(REPO, "data", "chunk_ids.npy")
META_PATH = os.path.join(REPO, "data", "index_meta.json")
MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"
CHUNK_CHARS = 1400
MAX_CHUNKS_PER_MSG = 4


def chunks_for(subject, author, body):
    """Yield text chunks for one message; each is prefixed with context."""
    prefix = f"Subject: {subject}\nFrom: {author}\n"
    body = body or ""
    if len(body) <= CHUNK_CHARS:
        yield prefix + body
        return
    step = CHUNK_CHARS - 200  # small overlap
    for i in range(MAX_CHUNKS_PER_MSG):
        piece = body[i * step:i * step + CHUNK_CHARS]
        if not piece.strip():
            break
        yield prefix + piece


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--append", action="store_true",
                    help="only embed messages not already in the index")
    args = ap.parse_args()

    from sentence_transformers import SentenceTransformer
    import torch

    device = "mps" if torch.backends.mps.is_available() else "cpu"
    model = SentenceTransformer(MODEL_NAME, device=device)

    con = sqlite3.connect(DB_PATH)
    rows = con.execute(
        "SELECT id, subject, from_name, body FROM messages ORDER BY id").fetchall()
    con.close()

    old_emb = old_ids = None
    if args.append and os.path.exists(EMB_PATH) and os.path.exists(IDS_PATH):
        old_emb = np.load(EMB_PATH)
        old_ids = np.load(IDS_PATH)
        have = set(old_ids.tolist())
        rows = [r for r in rows if r[0] not in have]
        print(f"append mode: {len(rows)} new messages to embed")
        if not rows:
            return

    texts, ids = [], []
    for mid, subject, author, body in rows:
        for c in chunks_for(subject or "", author or "", body or ""):
            texts.append(c)
            ids.append(mid)
    print(f"{len(rows)} messages -> {len(texts)} chunks")

    emb = model.encode(texts, batch_size=256, show_progress_bar=True,
                       normalize_embeddings=True, convert_to_numpy=True)
    emb = emb.astype(np.float16)
    id_arr = np.asarray(ids, dtype=np.int64)
    if old_emb is not None:
        emb = np.concatenate([old_emb, emb])
        id_arr = np.concatenate([old_ids, id_arr])
    np.save(EMB_PATH, emb)
    np.save(IDS_PATH, id_arr)

    import index_meta
    con = sqlite3.connect(DB_PATH)
    index_meta.write(META_PATH, con, id_arr, len(id_arr))
    con.close()
    print(f"saved {emb.shape} -> {EMB_PATH}")


if __name__ == "__main__":
    main()
