#!/usr/bin/env bash
# One-shot setup: builds data/extropians.db, the semantic index, and the web UI
# from the raw archives/ and digests/ in this repo. Idempotent; re-run to rebuild.
#
# This is a local build tool. It is deliberately NOT the Vercel install or
# build command: embedding takes ~13 minutes on a GPU and far longer on
# Vercel's CPU-only builders, well past the build timeout. See README.
set -e
cd "$(dirname "$0")"

echo "==> Python environment (rag/.venv)"
if [ ! -x rag/.venv/bin/python ]; then
  if command -v uv >/dev/null 2>&1; then
    uv venv rag/.venv --python 3.12
  else
    python3 -m venv rag/.venv
  fi
fi
if command -v uv >/dev/null 2>&1; then
  uv pip install --python rag/.venv/bin/python -r rag/requirements.txt
else
  rag/.venv/bin/pip install -r rag/requirements.txt
fi

echo "==> Ingesting archives + digests (unzips Disk*.zip automatically)"
rag/.venv/bin/python rag/ingest.py --fresh

echo "==> Ingesting WinWord .doc essays (macOS textutil; skipped elsewhere)"
rag/.venv/bin/python rag/ingest_docs.py

echo "==> Building semantic index (downloads MiniLM model on first run)"
rag/.venv/bin/python rag/embed.py

echo "==> Building web UI"
(cd web && npm install && npm run build)

echo
echo "Done. Start the server with ./run.sh and open http://127.0.0.1:8123"
