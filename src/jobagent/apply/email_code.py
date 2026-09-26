"""Fetch Greenhouse's emailed security code over IMAP (free; Gmail needs an app password)."""

from __future__ import annotations

import email
import imaplib
import os
import re
import time
from email.utils import parsedate_to_datetime

from ..config import EmailConfig

CODE = re.compile(r"(?:security code|verification code|code)[^A-Za-z0-9]{0,40}\b([A-Za-z0-9]{8})\b", re.I)


def extract_code(body: str) -> str | None:
    m = CODE.search(body)
    return m.group(1) if m else None


def _body(msg) -> str:
    parts = msg.walk() if msg.is_multipart() else [msg]
    out = []
    for p in parts:
        if p.get_content_type() in ("text/plain", "text/html"):
            payload = p.get_payload(decode=True) or b""
            out.append(re.sub(r"<[^>]+>", " ", payload.decode(p.get_content_charset() or "utf-8", "ignore")))
    return "\n".join(out)


def make_fetcher(cfg: EmailConfig, wait_seconds: int = 90):
    password = os.environ.get("IMAP_PASSWORD")
    if not (cfg.imap_host and cfg.username and password):
        return None

    def fetch(since_ts: float) -> str | None:
        deadline = time.time() + wait_seconds
        while time.time() < deadline:
            with imaplib.IMAP4_SSL(cfg.imap_host) as m:
                m.login(cfg.username, password)
                m.select("INBOX")
                _, ids = m.search(None, '(FROM "greenhouse")', "UNSEEN")
                for mid in reversed(ids[0].split()[-5:]):
                    _, data = m.fetch(mid, "(RFC822)")
                    msg = email.message_from_bytes(data[0][1])
                    try:
                        sent = parsedate_to_datetime(msg["Date"]).timestamp()
                    except Exception:
                        sent = time.time()
                    if sent + 60 < since_ts:
                        continue
                    if code := extract_code(_body(msg)):
                        return code
            time.sleep(8)
        return None

    return fetch
