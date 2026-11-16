"""Optional AI extractor.

Talks to any OpenAI-compatible /chat/completions endpoint. With Ollama
(http://localhost:11434/v1) everything stays on your machine. If you point it
at a hosted service, the text of your mails is sent to that service.
"""
from __future__ import annotations

import json
import re
import urllib.request
from datetime import datetime, time

from dateutil import parser as du_parser

from .extract import Deadline

_PROMPT = """You extract deadlines from emails. A deadline is a date by which the \
recipient must do something (complete an assessment, pay, reply, register, renew...). \
Ignore dates that are not deadlines (when something was sent, event history, marketing).
The email was received at {received} ({weekday}). Resolve relative dates ("within 7 days") \
from that moment.
Answer with JSON only: {{"deadlines": [{{"due": "YYYY-MM-DDTHH:MM" or "YYYY-MM-DD", \
"evidence": "short quote"}}]}}. Use the date-only form when no time is given. \
Return an empty list if there is no deadline."""


def find_deadlines_llm(subject: str, body: str, received: datetime, cfg: dict) -> list[Deadline]:
    payload = {
        "model": cfg["model"], "temperature": 0,
        "messages": [
            {"role": "system", "content": _PROMPT.format(
                received=received.strftime("%Y-%m-%d %H:%M"), weekday=received.strftime("%A"))},
            {"role": "user", "content": f"Subject: {subject}\n\n{body[:6000]}"}]}
    headers = {"Content-Type": "application/json"}
    if cfg.get("api_key"):
        headers["Authorization"] = f"Bearer {cfg['api_key']}"
    req = urllib.request.Request(cfg["base_url"].rstrip("/") + "/chat/completions",
                                 json.dumps(payload).encode(), headers)
    with urllib.request.urlopen(req, timeout=cfg.get("timeout", 120)) as r:
        text = json.load(r)["choices"][0]["message"]["content"]
    m = re.search(r"\{.*\}", text, re.S)
    out = []
    for item in (json.loads(m.group(0)).get("deadlines", []) if m else []):
        raw = str(item.get("due", "")).strip()
        try:
            dt = du_parser.parse(raw)
        except (ValueError, OverflowError):
            continue
        all_day = not re.search(r"\d{1,2}:\d{2}", raw)
        if all_day:
            dt = datetime.combine(dt.date(), time(23, 59))
        dt = dt.replace(tzinfo=received.tzinfo) if dt.tzinfo is None else dt.astimezone(received.tzinfo)
        if dt > received:
            out.append(Deadline(dt, all_day, 0.8, str(item.get("evidence", ""))[:300]))
    return sorted(out, key=lambda d: d.due)
