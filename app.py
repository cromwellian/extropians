"""Vercel entrypoint.

Vercel auto-detects a top-level `app` in app.py and runs the whole FastAPI
application as a single function, passing through original request paths.
The application itself lives in rag/server.py; that directory goes on
sys.path so its sibling imports (llm, index_meta) resolve as they do locally.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "rag"))

from server import app  # noqa: E402

__all__ = ["app"]
