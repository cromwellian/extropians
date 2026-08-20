#!/usr/bin/env python3
"""Fetch the prebuilt index into data/ at build time.

Runs as part of the Vercel build. Set EXTRO_DATA_URL to the archive produced
by scripts/package_data.py. Uses only the standard library, because this runs
before any dependencies are installed.

Skips silently when data/ is already populated (local builds) and fails loudly
when the URL is missing on Vercel, so a deploy cannot quietly ship an empty
index that only surfaces as 500s at runtime.
"""
import os
import sys
import tarfile
import tempfile
import urllib.request

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(REPO, "data")
DB = os.path.join(DATA, "extropians.db")
URL = os.environ.get("EXTRO_DATA_URL", "").strip()
ON_VERCEL = bool(os.environ.get("VERCEL"))


def main():
    if os.path.exists(DB) and os.path.getsize(DB) > 0:
        print(f"data/ already present ({os.path.getsize(DB)/2**20:.0f} MB) "
              "- skipping fetch")
        return
    if not URL:
        msg = ("EXTRO_DATA_URL is not set. Build it locally with ./setup.sh, "
               "package it with scripts/package_data.py, upload it, and set "
               "EXTRO_DATA_URL to its URL.")
        if ON_VERCEL:
            sys.exit(f"ERROR: {msg}")
        print(f"note: {msg}")
        return

    os.makedirs(DATA, exist_ok=True)
    print(f"downloading index from {URL.split('?')[0]} ...")
    with tempfile.NamedTemporaryFile(suffix=".tar.gz", delete=False) as tmp:
        path = tmp.name
    try:
        urllib.request.urlretrieve(URL, path)
        print(f"  {os.path.getsize(path)/2**20:.0f} MB downloaded, extracting")
        with tarfile.open(path) as tar:
            # Archive members are written as data/... relative to the repo.
            for member in tar.getmembers():
                if member.name.startswith(("/", "..")) or ".." in member.name:
                    sys.exit(f"refusing unsafe archive member: {member.name}")
            # filter="data" is the safe extraction policy and becomes the
            # default in 3.14; setting it explicitly silences the warning.
            tar.extractall(REPO, filter="data")
    finally:
        os.unlink(path)

    if not os.path.exists(DB):
        sys.exit("ERROR: archive did not contain data/extropians.db")
    print(f"index ready ({os.path.getsize(DB)/2**20:.0f} MB)")


if __name__ == "__main__":
    main()
