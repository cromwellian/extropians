#!/usr/bin/env python3
"""Ingest the Extropians mailing list archives into SQLite.

Sources:
  - archives/list-archive.*        mbox files (1996-2003)
  - digests/** and unzipped zips   RFC1153 digest files (1992-1994)

Dedup happens at two levels: whole files (byte-identical copies inside
zips vs. loose files) and individual messages (Message-ID when present,
otherwise a content fingerprint).
"""
import argparse
import hashlib
import os
import re
import sqlite3
import sys
from email import policy
from email.parser import BytesParser
from email.utils import parsedate_to_datetime, parseaddr

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.environ.get("EXTRO_DB", os.path.join(REPO, "data", "extropians.db"))

DIGEST_SEP = re.compile(r"^-{25,40}\s*$")
HEADER_LINE = re.compile(r"^([A-Za-z][A-Za-z0-9-]*):\s?(.*)$")
DIGEST_TITLE = re.compile(
    r"^(.*Digest)\s+(?:[A-Z][a-z]{2},?\s+\d{1,2}\s+[A-Z][a-z]{2}\s+\d{2,4})?\s*"
    r"Volume\s+(\d+)\s*:\s*Issue\s+(\d+)", re.M)
KEEP_HEADERS = ["From", "Date", "Subject", "To", "Cc", "Message-ID",
                "In-Reply-To", "References", "Reply-To"]

RE_PREFIX = re.compile(r"^\s*((re|fwd?|aw)(\[\d+\])?\s*:\s*)+", re.I)


def norm_subject(subj):
    s = RE_PREFIX.sub("", (subj or "").strip())
    s = re.sub(r"\s+", " ", s).strip().lower()
    return s or "(no subject)"


def clean_text(s):
    if not s:
        return ""
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    s = re.sub(r"[ \t]+$", "", s, flags=re.M)
    return s.strip("\n")


def fingerprint(from_h, date_h, subject, body):
    norm_body = re.sub(r"\s+", " ", body or "").strip()[:2000]
    _, addr = parseaddr(from_h or "")
    key = "|".join([(addr or from_h or "").lower(), (date_h or "").strip(),
                    norm_subject(subject), norm_body])
    return hashlib.sha1(key.encode("utf-8", "replace")).hexdigest()


def parse_date(date_h):
    """Return (iso_string, epoch) or (None, None)."""
    if not date_h:
        return None, None
    try:
        dt = parsedate_to_datetime(date_h.strip())
        if dt is None:
            return None, None
        if dt.tzinfo is None:
            from datetime import timezone
            dt = dt.replace(tzinfo=timezone.utc)
        # guard against garbage years
        if not (1990 <= dt.year <= 2005):
            return None, None
        return dt.isoformat(), int(dt.timestamp())
    except Exception:
        return None, None


def extract_body(msg):
    """Best-effort plain text body from an email.message.EmailMessage."""
    try:
        if msg.is_multipart():
            part = msg.get_body(preferencelist=("plain",))
            if part is None:
                for p in msg.walk():
                    if p.get_content_type() == "text/plain":
                        part = p
                        break
            if part is None:
                return ""
            return part.get_content()
        return msg.get_content()
    except Exception:
        # fall back to raw payload
        try:
            payload = msg.get_payload(decode=True)
            if payload:
                return payload.decode("latin-1", "replace")
            return str(msg.get_payload())
        except Exception:
            return ""


def headers_text(msg_or_dict):
    lines = []
    for h in KEEP_HEADERS:
        v = None
        if hasattr(msg_or_dict, "get"):
            v = msg_or_dict.get(h) or msg_or_dict.get(h.lower())
        if v:
            v = re.sub(r"\s+", " ", str(v)).strip()
            lines.append(f"{h}: {v}")
    return "\n".join(lines)


# ---------------------------------------------------------------- mbox

def split_mbox(raw: bytes):
    """Split raw mbox bytes into per-message byte chunks.

    A new message starts at a "From " line at column 0 that is either the
    first line of the file or preceded by a blank line (standard mbox).
    """
    lines = raw.split(b"\n")
    starts = []
    for i, ln in enumerate(lines):
        if ln.startswith(b"From ") and (i == 0 or lines[i - 1].strip() == b""):
            starts.append(i)
    if not starts:
        return []
    chunks = []
    for j, s in enumerate(starts):
        e = starts[j + 1] if j + 1 < len(starts) else len(lines)
        chunks.append(b"\n".join(lines[s:e]))
    return chunks


def parse_rfc822(chunk: bytes):
    # drop the leading mbox "From " line
    if chunk.startswith(b"From "):
        nl = chunk.find(b"\n")
        chunk = chunk[nl + 1:]
    return BytesParser(policy=policy.default).parsebytes(chunk)


# ---------------------------------------------------------------- RFC1153

def split_digest(body_text, digest_label):
    """Split an RFC1153 digest body into message dicts."""
    lines = body_text.split("\n")
    sections = []
    cur = []
    for ln in lines:
        if DIGEST_SEP.match(ln):
            sections.append(cur)
            cur = []
        else:
            cur.append(ln)
    sections.append(cur)

    out = []
    for sec in sections:
        # trim leading blanks
        while sec and not sec[0].strip():
            sec.pop(0)
        if not sec:
            continue
        first = "\n".join(sec[:30])
        if re.match(r"^End of .{0,60}Digest", sec[0], re.I):
            continue
        # parse leading header block
        hdrs = {}
        i = 0
        last_key = None
        while i < len(sec):
            ln = sec[i]
            if not ln.strip():
                break
            m = HEADER_LINE.match(ln)
            if m and m.group(1).lower() in (
                    "date", "from", "subject", "to", "cc", "message-id",
                    "in-reply-to", "references", "reply-to", "sender",
                    "x-original-message-id", "mime-version", "content-type",
                    "content-transfer-encoding"):
                last_key = m.group(1).title() if "-" not in m.group(1) else m.group(1)
                hdrs[last_key.lower()] = m.group(2).strip()
                i += 1
            elif (ln.startswith((" ", "\t"))) and last_key:
                hdrs[last_key.lower()] += " " + ln.strip()
                i += 1
            else:
                break
        if "from" not in hdrs or ("subject" not in hdrs and "date" not in hdrs):
            continue  # preamble / administrivia
        body = clean_text("\n".join(sec[i:]))
        if not body:
            continue
        out.append({
            "from": hdrs.get("from", ""),
            "date": hdrs.get("date", ""),
            "subject": hdrs.get("subject", "(no subject)"),
            "message_id": hdrs.get("message-id"),
            "hdrs": hdrs,
            "body": body,
        })
    return out


# ---------------------------------------------------------------- DB

SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
  id INTEGER PRIMARY KEY,
  dedup_key TEXT UNIQUE,
  message_id TEXT,
  from_raw TEXT, from_name TEXT, from_email TEXT,
  date_iso TEXT, date_epoch INTEGER, date_raw TEXT,
  subject TEXT, norm_subject TEXT,
  headers TEXT, body TEXT,
  source_file TEXT, source_kind TEXT, digest_label TEXT,
  in_reply_to TEXT, refs TEXT
);
CREATE INDEX IF NOT EXISTS idx_norm_subject ON messages(norm_subject);
CREATE INDEX IF NOT EXISTS idx_date ON messages(date_epoch);
CREATE INDEX IF NOT EXISTS idx_from_email ON messages(from_email);
CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
  subject, body, author, content='', tokenize='porter unicode61'
);
"""


def open_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.executescript(SCHEMA)
    con.execute("PRAGMA journal_mode=WAL")
    return con


class Ingestor:
    def __init__(self, con):
        self.con = con
        self.seen_files = set()
        self.n_msgs = 0
        self.n_dupe_msgs = 0
        self.n_dupe_files = 0

    def add_message(self, *, from_raw, date_raw, subject, body, headers,
                    message_id, source_file, source_kind, digest_label=None,
                    in_reply_to=None, refs=None):
        body = clean_text(body)
        if not body and not subject:
            return
        if message_id:
            dedup = "mid:" + message_id.strip().lower()
        else:
            dedup = "fp:" + fingerprint(from_raw, date_raw, subject, body)
        name, addr = parseaddr(from_raw or "")
        if not name and addr:
            name = addr.split("@")[0]
        date_iso, epoch = parse_date(date_raw)
        subject = re.sub(r"\s+", " ", subject or "(no subject)").strip()
        try:
            cur = self.con.execute(
                """INSERT INTO messages (dedup_key, message_id, from_raw,
                   from_name, from_email, date_iso, date_epoch, date_raw,
                   subject, norm_subject, headers, body, source_file,
                   source_kind, digest_label, in_reply_to, refs)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (dedup, message_id, from_raw, name, (addr or "").lower(),
                 date_iso, epoch, date_raw, subject, norm_subject(subject),
                 headers, body, source_file, source_kind, digest_label,
                 in_reply_to, refs))
            self.con.execute(
                "INSERT INTO messages_fts(rowid, subject, body, author) VALUES (?,?,?,?)",
                (cur.lastrowid, subject, body, f"{name} {addr}"))
            self.n_msgs += 1
        except sqlite3.IntegrityError:
            self.n_dupe_msgs += 1

    def file_seen(self, path):
        with open(path, "rb") as f:
            h = hashlib.sha256(f.read()).hexdigest()
        if h in self.seen_files:
            self.n_dupe_files += 1
            return True
        self.seen_files.add(h)
        return False

    # -- one file that may be an mbox of regular msgs and/or digests
    def ingest_file(self, path, rel):
        if self.file_seen(path):
            return
        with open(path, "rb") as f:
            raw = f.read()
        chunks = split_mbox(raw)
        if not chunks:
            chunks = [raw]
        for chunk in chunks:
            try:
                msg = parse_rfc822(chunk)
            except Exception:
                continue
            subject = str(msg.get("Subject", "") or "")
            body = extract_body(msg)
            m = DIGEST_TITLE.search(body[:4000] if body else "")
            digest_msgs = []
            if m and "digest" in subject.lower():
                digest_msgs = split_digest(body, f"V{m.group(2)} #{m.group(3)}")
            if digest_msgs:
                label = f"V{m.group(2)} #{m.group(3)}"
                date_raw = str(msg.get("Date", "") or "")
                for dm in digest_msgs:
                    self.add_message(
                        from_raw=dm["from"], date_raw=dm["date"] or date_raw,
                        subject=dm["subject"], body=dm["body"],
                        headers=headers_text(dm["hdrs"]),
                        message_id=dm["message_id"],
                        source_file=rel, source_kind="digest",
                        digest_label=label)
            else:
                self.add_message(
                    from_raw=str(msg.get("From", "") or ""),
                    date_raw=str(msg.get("Date", "") or ""),
                    subject=subject, body=body,
                    headers=headers_text(msg),
                    message_id=(msg.get("Message-ID") and str(msg.get("Message-ID")).strip()) or None,
                    source_file=rel, source_kind="mbox",
                    in_reply_to=(msg.get("In-Reply-To") and str(msg.get("In-Reply-To"))[:500]) or None,
                    refs=(msg.get("References") and str(msg.get("References"))[:1000]) or None)


def extract_zips():
    """Extract digests/*.zip into a temp dir; return its path (or None)."""
    import tempfile
    import zipfile
    zips = []
    for dirpath, _, names in os.walk(os.path.join(REPO, "digests")):
        zips += [os.path.join(dirpath, n) for n in names
                 if n.lower().endswith(".zip")]
    if not zips:
        return None
    out = tempfile.mkdtemp(prefix="extro_zips_")
    for z in sorted(zips):
        dest = os.path.join(out, os.path.splitext(os.path.basename(z))[0])
        try:
            with zipfile.ZipFile(z) as zf:
                zf.extractall(dest)
        except zipfile.BadZipFile:
            print(f"SKIP bad zip: {z}", file=sys.stderr)
    print(f"extracted {len(zips)} zips -> {out}")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--unzipped",
                    help="dir with pre-extracted zip contents "
                         "(default: extract digests/*.zip automatically)")
    ap.add_argument("--fresh", action="store_true")
    args = ap.parse_args()
    if not args.unzipped:
        args.unzipped = extract_zips()

    if args.fresh and os.path.exists(DB_PATH):
        os.remove(DB_PATH)
        for ext in ("-wal", "-shm"):
            p = DB_PATH + ext
            if os.path.exists(p):
                os.remove(p)

    con = open_db()
    ing = Ingestor(con)

    roots = []
    arch = os.path.join(REPO, "archives")
    dig = os.path.join(REPO, "digests")
    for root in [arch, dig] + ([args.unzipped] if args.unzipped else []):
        if root and os.path.isdir(root):
            roots.append(root)

    files = []
    for root in roots:
        for dirpath, _, names in os.walk(root):
            for n in sorted(names):
                if n.lower().endswith((".zip", ".doc")):
                    continue
                files.append(os.path.join(dirpath, n))

    for i, path in enumerate(files):
        rel = os.path.relpath(path, REPO) if path.startswith(REPO) else \
            "zips/" + os.path.relpath(path, args.unzipped)
        try:
            ing.ingest_file(path, rel)
        except Exception as e:
            print(f"ERROR {rel}: {e}", file=sys.stderr)
        if (i + 1) % 100 == 0:
            con.commit()
            print(f"  {i+1}/{len(files)} files, {ing.n_msgs} msgs "
                  f"({ing.n_dupe_msgs} dup msgs, {ing.n_dupe_files} dup files)")
    con.commit()

    # thread assignment: thread root = earliest message per norm_subject
    con.execute("""
        CREATE TABLE IF NOT EXISTS threads AS
        SELECT norm_subject,
               MIN(COALESCE(date_epoch, 9999999999)) AS first_epoch,
               COUNT(*) AS n_msgs
        FROM messages GROUP BY norm_subject
    """)
    con.execute("CREATE INDEX IF NOT EXISTS idx_threads_subj ON threads(norm_subject)")
    con.commit()

    total = con.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
    print(f"\nDone: {total} messages "
          f"({ing.n_dupe_msgs} duplicate messages skipped, "
          f"{ing.n_dupe_files} duplicate files skipped)")
    con.close()


if __name__ == "__main__":
    main()
