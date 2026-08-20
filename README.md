extropians
==========

Archive of the Extropians mailing list — the 1990s transhumanist discussion
list — plus a local RAG app for searching and chatting with it.

* `archives/` — mbox archives, 1996.07–2003.09
* `digests/` — RFC1153 digests, 1992–1994 (loose files and `Disk*.zip`),
  and the ExI essay collection
* `rag/`, `web/` — the search + chat application described below

Ask the archive
---------------

A local web app that answers questions about the list using only the archive,
citing the original emails. Click any citation to read the message it came
from, in a syntax-colored email view, and jump to its full thread.

Alongside the chat there is direct search over ~148,000 de-duplicated
messages (37k threads, 3.2k posters) in three modes: keyword (BM25),
semantic (embeddings), or hybrid.

### Setup

Requirements: Python 3.10+, Node 18+, and about 1 GB of free disk. macOS is
assumed for the `.doc` essay conversion (`textutil`); everything else is
cross-platform and that step is skipped automatically elsewhere.

```bash
./setup.sh
```

That one command builds everything from the raw `archives/` and `digests/`
already in this repo: it creates the Python venv, installs dependencies,
unzips the digest disks, parses and de-duplicates every message into
`data/extropians.db`, builds the semantic index, and compiles the web UI.
Expect roughly 15 minutes on a laptop, most of it computing embeddings.
It is idempotent — re-run it any time to rebuild from scratch.

### Run

```bash
./run.sh          # then open http://127.0.0.1:8123
```

### LLM backend

Four backends are supported. The server picks one automatically, in this
order, and shows which is active in the UI header:

1. **Anthropic API** — when `ANTHROPIC_API_KEY` (or `ANTHROPIC_AUTH_TOKEN`) is
   set. Uses `claude-opus-5`; override with `EXTRO_MODEL`.
2. **Vercel AI Gateway** — when `AI_GATEWAY_API_KEY` or `VERCEL_OIDC_TOKEN` is
   present. Hundreds of hosted models behind one OpenAI-compatible endpoint.
   Defaults to `zai/glm-5.2` (1.04M context, $0.80/$2.52 per M tokens);
   override with `EXTRO_GATEWAY_MODEL` using any slug from
   `GET https://ai-gateway.vercel.sh/v1/models`, e.g. `anthropic/claude-opus-5`.
   This is the backend that works on Vercel with no secrets of your own:
   deployments get a `VERCEL_OIDC_TOKEN` automatically once AI Gateway is
   enabled for the project.
3. **`claude` CLI** — headless mode using your existing Claude Code login, so
   no API key is needed. Override with `EXTRO_CLI_MODEL` (default `sonnet`).
4. **A local model** — any OpenAI-compatible server: LM Studio, Ollama,
   `llama.cpp`'s server, vLLM.

Pin a specific backend with `EXTRO_LLM=anthropic|gateway|cli|local`.

### Using a local model

Start your server, then run with `EXTRO_LLM=local`:

```bash
# LM Studio: load a model and start its server (defaults to port 1234), or
ollama serve && ollama pull qwen2.5:7b-instruct   # OpenAI shim on port 11434

EXTRO_LLM=local ./run.sh
```

Ports 1234 and 11434 are probed automatically and the server's first model is
used. To be explicit:

```bash
EXTRO_LLM=local \
EXTRO_LOCAL_URL=http://localhost:11434/v1 \
EXTRO_LOCAL_MODEL=qwen2.5:7b-instruct ./run.sh
```

Local models are only auto-selected when neither Anthropic option is
available, so a server you happen to have running for something else never
silently downgrades your answers — asking for it is explicit.

Because local models usually have much smaller context windows, the chat
automatically packs fewer and shorter excerpts for them (6 × 1,200 chars
instead of 14 × 3,000). Tune with `EXTRO_LOCAL_SOURCES` and
`EXTRO_LOCAL_SOURCE_CHARS`. A model with a large context window can take the
hosted numbers; a 4k-context one may need less. Expect weaker citation
discipline from small models — the answer quality depends heavily on the
model, but retrieval is identical either way.

Search and the message viewer work with no LLM configured at all; only the
chat pane needs one.

Deploying to Vercel
-------------------

The whole app runs as a single Python function. It needs **large functions**
(5 GB bundles) because the index is ~850 MB and PyTorch is another ~500 MB —
well past the 500 MB standard Python limit, but comfortably inside 5 GB.

The index is too large to commit, so it is built locally, uploaded once, and
pulled back down at build time.

```bash
./setup.sh                              # build the index locally (~15 min)
python3 scripts/package_data.py         # -> extropians-data.tar.gz
vercel blob put extropians-data.tar.gz  # or any URL the build can reach
```

Then set these in the Vercel project (Settings → Environment Variables):

| Variable | Value |
|---|---|
| `EXTRO_DATA_URL` | the URL of the uploaded archive |
| `VERCEL_SUPPORT_LARGE_FUNCTIONS` | `1` (only needed for projects created before 2026-06-30) |

For the chat backend, enable **AI Gateway** on the project and nothing else is
needed — deployments receive a `VERCEL_OIDC_TOKEN` automatically and the
gateway backend picks it up. Set `ANTHROPIC_API_KEY` instead if you would
rather bill Anthropic directly; the `claude` CLI backend does not exist on
Vercel.

Confirm Fluid Compute with Active CPU is on (the default for new projects),
then `vercel deploy`. `scripts/fetch_data.py` fails the build loudly if
`EXTRO_DATA_URL` is missing, rather than shipping an empty index that would
only show up as runtime errors.

### What deployment changes

* **The database is opened read-only.** Vercel's filesystem is read-only, and
  SQLite would otherwise try to create `-wal`/`-shm` sidecars and fail to open
  at all. `EXTRO_READONLY=1` (set automatically when `VERCEL` is present)
  switches to `mode=ro&immutable=1`. `package_data.py` also vacuums the
  database into a clean non-WAL file first, since `immutable=1` ignores a
  `-wal` sidecar and would silently miss any un-checkpointed pages.
* **Static assets go to the CDN.** The `/assets` `StaticFiles` mount is
  promoted at build time, so only HTML and API calls hit the function.
* **Source archives are excluded** from the bundle via `excludeFiles` — the
  420 MB of `archives/` and `digests/` is only needed to *build* the index.

### Cost and cold starts

The first semantic query on a cold instance pays for importing PyTorch and
loading the embedding matrix — expect several seconds. Keyword search and the
message viewer are unaffected, because the semantic index loads lazily. Fluid
Compute reuses warm instances, so this is a cold-start cost, not a per-request
one. `memory` is set to 2048 MB in `vercel.json`, which fits Hobby's 2 GB cap;
Pro and Enterprise can raise it to 4096 for more headroom.

If cold starts matter more than deployment simplicity, the biggest win by far
is replacing PyTorch with ONNX Runtime and an int8 MiniLM (~500 MB → ~100 MB),
since torch exists purely to embed the one query string per request.

### Running a free public instance

There is no free chat model on AI Gateway — the free-tier catalogue is audio
models, not conversational ones. What is free is **$5 of AI Gateway credit per
team per month**, refreshing every 30 days. Note that buying credits ends the
monthly free allowance, so a deliberately free instance should stay on it.

Retrieval costs nothing: search, threads and the message viewer run entirely
inside the function, so those can stay unlimited and only answer generation
needs rationing. Three levers, in order of effect:

* **Shrink the prompt.** Input tokens dominate, and the prompt is mostly
  archive excerpts. `EXTRO_SOURCES` and `EXTRO_SOURCE_CHARS` (default 14 and
  3000) are the dial. Halving both roughly halves the bill.
* **Pick a cheap model.** At `zai/glm-5.2` rates a question costs on the order
  of a cent, so $5 is a few hundred answers a month. Small Llama and Qwen
  slugs run 20–40× cheaper per input token and stretch that into the
  thousands — check current rates in the gateway model list.
* **Rate-limit per user.** Without it one visitor can drain the month.

`402` (credits exhausted) and `429` (provider rate limit) are surfaced to the
reader as a plain sentence noting that search still works, rather than a raw
error, so the site stays useful when generation is unavailable.

### Notes

`data/` (the ~650 MB database and ~200 MB embedding index), `rag/.venv/`,
`web/node_modules/`, and `web/dist/` are generated by `setup.sh` and are not
tracked in git.

See [RAG.md](RAG.md) for how ingestion, de-duplication, digest splitting, and
retrieval actually work, and for how to rebuild individual stages.
