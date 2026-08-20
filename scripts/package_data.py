#!/usr/bin/env python3
"""Package the prebuilt index into a single archive for deployment.

The deployed function needs data/ (database + embeddings), which is far too
large to commit. Run this after ./setup.sh, upload the resulting archive
somewhere the build can fetch it, and set EXTRO_DATA_URL in the Vercel
project. scripts/fetch_data.py pulls it back down at build time.

The database is vacuumed into a fresh non-WAL file first: the deployed copy
is opened read-only with immutable=1, which ignores -wal sidecars, so any
un-checkpointed pages would silently go missing.
"""
import os
import shutil
import sqlite3
import sys
import tarfile
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(REPO, "data")
OUT = os.path.join(REPO, "extropians-data.tar.gz")
MEMBERS = ["extropians.db", "embeddings.npy", "chunk_ids.npy",
           "index_meta.json"]


def main():
    missing = [m for m in MEMBERS if not os.path.exists(os.path.join(DATA, m))]
    if missing:
        sys.exit(f"missing {', '.join(missing)} - run ./setup.sh first")

    tmp = tempfile.mkdtemp(prefix="extro_pkg_")
    flat_db = os.path.join(tmp, "extropians.db")
    print("vacuuming database into a clean non-WAL file...")
    src = sqlite3.connect(os.path.join(DATA, "extropians.db"))
    src.execute("VACUUM INTO ?", (flat_db,))
    src.close()
    # VACUUM INTO writes a fresh file in the default (delete) journal mode,
    # so no -wal accompanies it.
    dst = sqlite3.connect(flat_db)
    mode = dst.execute("PRAGMA journal_mode").fetchone()[0]
    n = dst.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
    dst.close()
    print(f"  {n} messages, journal_mode={mode}, "
          f"{os.path.getsize(flat_db)/2**20:.0f} MB")

    print(f"writing {OUT} ...")
    with tarfile.open(OUT, "w:gz") as tar:
        tar.add(flat_db, arcname="data/extropians.db")
        for m in MEMBERS[1:]:
            tar.add(os.path.join(DATA, m), arcname=f"data/{m}")
    shutil.rmtree(tmp, ignore_errors=True)

    print(f"done: {os.path.getsize(OUT)/2**20:.0f} MB\n\n"
          "Next:\n"
          "  1. upload it, e.g.  vercel blob put extropians-data.tar.gz\n"
          "  2. set EXTRO_DATA_URL to the returned URL in your Vercel project\n"
          "  3. deploy")


if __name__ == "__main__":
    main()
