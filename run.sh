#!/bin/zsh
# Start the Extropians Archive RAG server (UI at http://127.0.0.1:8123)
cd "$(dirname "$0")"

if [ ! -x rag/.venv/bin/uvicorn ] || [ ! -f data/extropians.db ]; then
  echo "Not set up yet — run ./setup.sh first." >&2
  exit 1
fi

exec rag/.venv/bin/uvicorn server:app --app-dir rag --host 127.0.0.1 --port "${PORT:-8123}"
