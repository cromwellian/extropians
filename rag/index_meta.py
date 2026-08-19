"""Staleness check for the semantic index.

The embedding index is keyed by message id. `ingest.py --fresh` renumbers
ids, so an index built against an older database would map vectors to the
wrong messages — silently returning plausible but incorrect results.

To detect that, we sample a spread of embedded message ids at build time and
hash each one together with its `dedup_key` (a stable content identity). A
rebuild that renumbers changes which message lives at a sampled id, so the
hash changes; merely appending new messages leaves the sample untouched.
"""
import hashlib
import json

SAMPLE_SIZE = 256


def sample_ids(all_ids):
    """Pick a deterministic, evenly spread subset of message ids."""
    uniq = sorted(set(int(i) for i in all_ids))
    if len(uniq) <= SAMPLE_SIZE:
        return uniq
    step = len(uniq) / SAMPLE_SIZE
    return [uniq[int(i * step)] for i in range(SAMPLE_SIZE)]


def signature(con, ids):
    """Hash (id, dedup_key) pairs for the given ids in the given database."""
    if not ids:
        return "empty"
    ph = ",".join("?" * len(ids))
    rows = con.execute(
        f"SELECT id, dedup_key FROM messages WHERE id IN ({ph})",
        list(ids)).fetchall()
    parts = sorted(f"{r[0]}:{r[1]}" for r in rows)
    h = hashlib.sha1("\n".join(parts).encode("utf-8"))
    return f"{len(parts)}-{h.hexdigest()}"


def write(path, con, all_ids, n_chunks):
    ids = sample_ids(all_ids)
    with open(path, "w") as f:
        json.dump({"sample_ids": ids, "signature": signature(con, ids),
                   "n_chunks": int(n_chunks)}, f)


def is_current(path, con):
    """True if the index at `path` matches the database `con`."""
    try:
        with open(path) as f:
            meta = json.load(f)
        return signature(con, meta["sample_ids"]) == meta["signature"]
    except (OSError, ValueError, KeyError):
        return False
