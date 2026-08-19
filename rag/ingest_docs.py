#!/usr/bin/env python3
"""Ingest the WinWord .doc essay files (converted via macOS textutil)."""
import glob
import os
import subprocess
import sys
import tempfile

from ingest import Ingestor, open_db, REPO


def main():
    import shutil
    if not shutil.which("textutil"):
        print("textutil not found (macOS only) — skipping .doc essays",
              file=sys.stderr)
        return
    con = open_db()
    ing = Ingestor(con)
    docs = sorted(glob.glob(os.path.join(REPO, "digests", "**", "*.doc"),
                            recursive=True))
    tmpdir = tempfile.mkdtemp(prefix="extro_docs_")
    n = 0
    for doc in docs:
        rel = os.path.relpath(doc, REPO)
        out = os.path.join(tmpdir, os.path.basename(doc) + ".txt")
        r = subprocess.run(["textutil", "-convert", "txt", "-output", out, doc],
                           capture_output=True)
        if r.returncode != 0 or not os.path.exists(out):
            print(f"SKIP (convert failed): {rel}", file=sys.stderr)
            continue
        with open(out, "rb") as f:
            raw = f.read()
        if b"\nFrom " in raw or raw.startswith(b"From "):
            ing.ingest_file(out, rel)
        else:
            title = os.path.splitext(os.path.basename(doc))[0]
            ing.add_message(
                from_raw="", date_raw="", subject=title,
                body=raw.decode("utf-8", "replace"),
                headers=f"Subject: {title}", message_id=None,
                source_file=rel, source_kind="essay")
        n += 1
    con.commit()
    print(f"{n} docs processed, +{ing.n_msgs} messages "
          f"({ing.n_dupe_msgs} dups skipped)")
    con.close()


if __name__ == "__main__":
    main()
