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

Alongside the chat there is direct search over 150,935 de-duplicated
messages (37.7k threads, 3.3k posters) in three modes: keyword (BM25),
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
   This is the backend that works on Vercel with no secrets of your own:
   deployments get a `VERCEL_OIDC_TOKEN` automatically once AI Gateway is
   enabled for the project.

   It walks a chain of models rather than pinning one, because the free tier
   both restricts which models it serves and rate-limits each separately:

   | Model | Free tier | Context | Input |
   |---|---|---|---|
   | `alibaba/qwen3.7-flash` (default) | served | 991k | $0.03/M |
   | `poolside/laguna-s-2.1-free` | served, tagged free | 256k | $0 |
   | `zai/glm-5.2` | **restricted** — needs paid credits | 1.04M | $0.80/M |

   Override with `EXTRO_GATEWAY_MODEL` (one) or `EXTRO_GATEWAY_MODELS` (a
   comma-separated chain), using any slug from
   `GET https://ai-gateway.vercel.sh/v1/models`.
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
./setup.sh                        # build the index locally (~15 min)
python3 scripts/package_data.py   # -> extropians-data.tar.gz (~408 MB)

# host it anywhere the build can reach. A GitHub Release is free and has no
# bandwidth charge for a public repo, unlike Blob storage:
gh release create data-v1 extropians-data.tar.gz --title "Prebuilt search index v1"
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

`vercel.json` pins the framework preset, install command, build command and
output directory, so the deployment does not depend on the dashboard's
Build & Development Settings being right. That matters: `setup.sh` set as
the Install Command will re-ingest the archive and then try to embed 265k
chunks on a CPU-only builder on every single deploy, which cannot finish
inside the build timeout. `setup.sh` is a local tool, never a build step.

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
one. Memory is not configurable under Active CPU billing — Fluid Compute
ignores a `memory` setting and gives Hobby a 2 GB / 1 vCPU instance.

If cold starts matter more than deployment simplicity, the biggest win by far
is replacing PyTorch with ONNX Runtime and an int8 MiniLM (~500 MB → ~100 MB),
since torch exists purely to embed the one query string per request.

### Running a free public instance

A free instance is workable, with caveats worth knowing up front:

* **A payment method is required even for the free credits.** Until a card is
  on file the gateway answers `403 customer_verification_required` on every
  request. Adding one does not start charging; it unlocks the free tier.
* **$5 of credit per team per month**, refreshing every 30 days. Buying
  credits permanently ends that monthly allowance, so a deliberately free
  instance should never top up.
* **Most models are restricted.** `zai/glm-5.2`, `openai/gpt-5-nano` and
  `anthropic/claude-haiku-4.5` all refuse on the free tier. The chain above
  defaults to models that are actually served.
* **Rate limits are per model and tight.** Roughly six requests in a minute
  exhausted every model tried; they recovered about 90 seconds later. It is a
  short rolling window rather than a daily cap, so a low-traffic site is fine
  and a burst degrades to "retry in a moment".

Retrieval costs nothing: search, threads and the message viewer run entirely
inside the function, so those can stay unlimited and only answer generation
needs rationing. Three levers, in order of effect:

* **Shrink the prompt.** Input tokens dominate, and the prompt is mostly
  archive excerpts. `EXTRO_SOURCES` and `EXTRO_SOURCE_CHARS` (default 14 and
  3000) are the dial. Halving both roughly halves the bill.
* **Pick a cheap model.** A question sends roughly 11k input tokens. At
  `alibaba/qwen3.7-flash` rates that is about $0.0003, so the $5 monthly
  credit is on the order of 15,000 answers; at `zai/glm-5.2` rates the same
  question costs about a cent, or a few hundred answers. `poolside/
  laguna-s-2.1-free` consumes no credit at all.
* **Rate-limit per user.** Without it one visitor can drain the month.

`402` (credits exhausted), `403` (no payment method, or a model the free tier
will not serve) and `429` (rate limited) are surfaced to the reader as a plain
sentence noting that search still works, rather than a raw error, so the site
stays useful when generation is unavailable.

### Notes

`data/` (the ~650 MB database and ~200 MB embedding index), `rag/.venv/`,
`web/node_modules/`, and `web/dist/` are generated by `setup.sh` and are not
tracked in git.

See [RAG.md](RAG.md) for how ingestion, de-duplication, digest splitting, and
retrieval actually work, and for how to rebuild individual stages.
