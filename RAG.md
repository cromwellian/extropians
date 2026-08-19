# How the archive RAG works

Setup and run instructions live in [README.md](README.md) — this file covers
the internals and how to rebuild individual stages.

## Pipeline

`setup.sh` runs these in order; each is independently re-runnable.

```bash
rag/.venv/bin/python rag/ingest.py --fresh   # archives + digests -> data/extropians.db
rag/.venv/bin/python rag/ingest_docs.py      # WinWord .doc essays (macOS textutil)
rag/.venv/bin/python rag/embed.py            # semantic index -> data/embeddings.npy
(cd web && npm run build)                    # UI -> web/dist, served by the API
```

`ingest.py --fresh` rebuilds the database from zero and renumbers message ids,
so **re-run `embed.py` after it** — the embedding index is keyed by message id.
`embed.py --append` only embeds messages not already in the index, which is the
cheap path after `ingest_docs.py` or any incremental addition.

Forgetting that would otherwise be dangerous: vectors would point at the wrong
messages and semantic search would return confidently wrong results. To make
that impossible, `embed.py` records `data/index_meta.json` — a hash of
`(id, dedup_key)` pairs for a sample of embedded messages — and the server
re-checks it at startup (`rag/index_meta.py`). If the database has been
renumbered underneath the index, semantic search is disabled, keyword search
carries on, and the UI header says `semantic index stale — keyword only`.

By default `ingest.py` extracts `digests/**/*.zip` into a temp directory
itself. Pass `--unzipped DIR` to point at a pre-extracted copy instead. Set
`EXTRO_DB` to build into a database other than `data/extropians.db`.

## Ingestion

Two archive formats, both landing in one `messages` table:

* **mbox** (`archives/list-archive.*`, 1996–2003) — split on `From ` lines at
  column 0 preceded by a blank line, then parsed with Python's `email` package
  so MIME multipart and quoted-printable bodies decode correctly.
* **RFC1153 digests** (`digests/**`, 1992–1994) — a digest arrives as one
  email whose body holds many messages separated by dashed rules. Each section
  is split back out into its own message, keeping the per-message `From`,
  `Date`, and `Subject` headers and tagging it with the digest volume/issue.
  A reply whose *subject* merely mentions "digest" is not a digest; if section
  splitting yields nothing, the message is kept whole rather than dropped.
* **`.doc` essays** — WinWord 2.0 files converted via `textutil`. Most contain
  the original list email verbatim and are re-parsed as mail; the rest are
  stored as standalone essays.

De-duplication happens at two levels, because the `Disk*.zip` files are almost
entirely re-packagings of the loose digest files:

* **File level** — SHA-256 of file contents; ~1,150 duplicate files skipped.
* **Message level** — `Message-ID` when present, otherwise a SHA-1 fingerprint
  over sender address, date, normalized subject, and normalized body prefix;
  ~1,800 duplicate messages skipped.

Threads are grouped by normalized subject (`Re:`/`Fwd:` prefixes stripped,
whitespace and case folded) and ordered by true UTC timestamp, which is what
the thread view shows.

## Retrieval

* **Keyword** — SQLite FTS5 with a porter tokenizer, ranked by BM25. Query
  terms are ANDed first for precision; if that returns too little, it falls
  back to OR over the content words with stopwords removed, so conversational
  questions ("what did people think about…") still retrieve.
* **Semantic** — `all-MiniLM-L6-v2` embeddings over message chunks (~1,400
  chars, small overlap, up to 4 chunks per message, each prefixed with its
  subject and author). Stored as a float16 matrix loaded once at first use;
  a query is a single matrix multiply, then best-chunk-per-message.
* **Hybrid** (default) — reciprocal-rank fusion of the two, weighted slightly
  toward semantic.

## Chat

The top retrieved messages are packed into the prompt as numbered sources, and
the system prompt requires inline `[n]` citations and forbids answering beyond
the sources. Responses stream to the browser over SSE: a `sources` event
first, then text deltas.

How much gets packed depends on the backend (`llm.context_budget()`): 14
sources × 3,000 chars for the hosted models, 6 × 1,200 for a local one. That
is not just a cost tweak — overflowing a small context window gets the prompt
truncated from the front, which is exactly where the citation instructions
live, so an over-stuffed local model tends to answer without citing anything.

`rag/llm.py` resolves the backend (Anthropic SDK, `claude` CLI, or any
OpenAI-compatible local server) and exposes a single
`stream_completion(system, prompt)` generator, so `server.py` never branches
on which one is active.

The UI rewrites `[n]` into clickable chips resolved against that turn's source
list, so a citation opens the exact message in the viewer — quote-depth
coloring, dimmed signatures, linkified URLs, and a link into the full thread.
Messages are deep-linkable as `#msg-<id>`.

## HTTP API

| Endpoint | Purpose |
|---|---|
| `GET /api/stats` | corpus counts, date span, active LLM backend |
| `GET /api/search?q=&mode=hybrid\|keyword\|semantic` | ranked results with snippets |
| `GET /api/message/{id}` | full message, headers, thread size |
| `GET /api/thread/{id}` | every message sharing that normalized subject |
| `POST /api/chat` | SSE: `sources`, then `delta` events |
