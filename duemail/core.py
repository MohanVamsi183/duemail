"""Config, state and the mail -> calendar pipeline."""
from __future__ import annotations

import os
import re
import sqlite3
from datetime import datetime, time, timedelta
from pathlib import Path

import yaml

from .extract import Deadline, find_deadlines

DEFAULT_CONFIG = """\
# duemail settings. Edit freely; run `duemail run --dry-run` to preview.

# IANA name like America/New_York. Leave empty to use this computer's timezone.
timezone: ""

# Read dates like 03/04/2026 as 3 April (true) or March 4 (false).
day_first: false

# ---- 1) Automatic scan: every mail matching this Gmail search is checked ----
scan:
  enabled: true
  query: "newer_than:3d -in:spam -in:trash -in:sent -in:drafts"
  max_messages: 200
  min_confidence: 0.6          # raise to 0.85 if you get events you don't want
  ignore_labels: []            # mails with these labels are never auto-scanned

# ---- 2) Force label: put a mail under this label and it ALWAYS gets an event,
# even if the scan missed it. If no date is found in the mail, the event is
# placed `default_days` after the mail arrived and marked "check date".
force:
  label: "Deadline"
  default_days: 7
  lookback_days: 30

# ---- 3) How events look. `label_rules` override these per Gmail label; the
# first rule whose label is on the mail wins. A rule for "Jobs" also matches
# nested labels such as "Jobs/Assessments".
defaults:
  calendar: primary
  color: ""                    # lavender sage grape flamingo banana tangerine
                               # peacock graphite blueberry basil tomato
  prefix: "DUE: "
  remind_days_before: [1]      # popup reminders, N days before the deadline
  remind_at_hour: 9            # ...at this hour (used for all-day deadlines)
  work_block:
    enabled: true
    days_before: 2             # block is placed this many days before the deadline
    start: "18:00"
    hours: 2
    prefix: "Work on: "

label_rules:
  - label: "Jobs"
    color: tomato
    prefix: "JOB DUE: "
    remind_days_before: [1, 3]
    work_block: {days_before: 3, hours: 3}
  - label: "Bills"
    color: banana
    prefix: "PAY: "
    work_block: {enabled: false}

# ---- Deadline detection ------------------------------------------------------
# rules = built-in patterns, fully offline (default)
# llm   = ask an AI model; falls back to rules if the model can't be reached.
#         Works with any OpenAI-compatible endpoint. Ollama keeps it local.
extractor: rules
llm:
  base_url: "http://localhost:11434/v1"
  model: "llama3.1"
  api_key: ""
"""


def home_dir() -> Path:
    p = Path(os.environ.get("DUEMAIL_HOME", Path.home() / ".duemail"))
    p.mkdir(parents=True, exist_ok=True)
    return p


def _merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in (over or {}).items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def load_config(path: Path | None = None) -> dict:
    base = yaml.safe_load(DEFAULT_CONFIG)
    base["label_rules"] = []                         # samples are not defaults
    path = path or home_dir() / "config.yaml"
    user = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {}
    return _merge(base, user or {})


def get_tz(cfg: dict):
    from zoneinfo import ZoneInfo
    if cfg.get("timezone"):
        return ZoneInfo(cfg["timezone"])
    try:
        from tzlocal import get_localzone
        return get_localzone()
    except Exception:
        return datetime.now().astimezone().tzinfo


def style_for(cfg: dict, labels: list[str]) -> dict:
    """Event style for a mail: defaults, overridden by the first matching label rule."""
    have = [l.lower() for l in labels]
    for rule in cfg.get("label_rules") or []:
        want = str(rule.get("label", "")).lower()
        if want and any(l == want or l.startswith(want + "/") for l in have):
            return _merge(cfg["defaults"], {k: v for k, v in rule.items() if k != "label"})
    return cfg["defaults"]


# -------------------------------------------------------------------- state --
class Store:
    """Remembers which mails were handled so events are never duplicated."""

    def __init__(self, path: Path):
        self.db = sqlite3.connect(path)
        self.db.execute("CREATE TABLE IF NOT EXISTS seen (msg_id TEXT PRIMARY KEY, "
                        "due TEXT, event_ids TEXT, at TEXT)")

    def status(self, msg_id: str) -> str | None:
        """None = never seen, 'none' = seen without deadline, 'event' = event created."""
        row = self.db.execute("SELECT due FROM seen WHERE msg_id=?", (msg_id,)).fetchone()
        return None if row is None else ("event" if row[0] else "none")

    def mark(self, msg_id: str, due: str | None = None, event_ids: str = ""):
        self.db.execute("INSERT OR REPLACE INTO seen VALUES (?,?,?,?)",
                        (msg_id, due, event_ids, datetime.now().isoformat(timespec="seconds")))
        self.db.commit()


# ------------------------------------------------------------------- events --
def _clean_subject(s: str) -> str:
    return re.sub(r"^\s*((re|fwd?|fw)\s*:\s*)+", "", s or "", flags=re.I).strip() or "(no subject)"


def build_events(mail, dl: Deadline, style: dict, now: datetime, uncertain: bool = False) -> list[dict]:
    """Calendar API bodies: the deadline event, plus a work block when it fits."""
    title = _clean_subject(mail.subject)
    desc = "\n".join(filter(None, [
        "Date not found in the mail - please check it." if uncertain else "",
        f"From: {mail.sender}", f"Mail: {mail.link}",
        f'Found in: "{dl.evidence.strip()[:300]}"' if dl.evidence else "",
        "", "Added by duemail"]))
    base = {"description": desc, "source": {"title": "Open mail", "url": mail.link},
            "extendedProperties": {"private": {"duemail": mail.id}}}
    color = style.get("color")
    if color:
        from .google_api import COLORS
        base["colorId"] = COLORS.get(str(color).lower(), str(color))

    days = [int(d) for d in style.get("remind_days_before") or []][:5]
    if dl.all_day:
        d = dl.due_date
        when = {"start": {"date": d.isoformat()}, "end": {"date": (d + timedelta(days=1)).isoformat()}}
        hour = int(style.get("remind_at_hour", 9))
        mins = [n * 1440 - hour * 60 for n in days if n >= 1]     # counted from 00:00 of due day
    else:
        when = {"start": {"dateTime": (dl.due - timedelta(minutes=30)).isoformat()},
                "end": {"dateTime": dl.due.isoformat()}}
        mins = [n * 1440 for n in days] + [120]
    mins = sorted({m for m in mins if 0 <= m <= 40320})[:5]
    due_event = {**base, **when,
                 "summary": f"{style.get('prefix', '')}{title}" + (" (check date)" if uncertain else ""),
                 "reminders": {"useDefault": False,
                               "overrides": [{"method": "popup", "minutes": m} for m in mins]}}
    events = [due_event]

    wb = style.get("work_block") or {}
    if wb.get("enabled", True):
        hh, mm = (int(x) for x in str(wb.get("start", "18:00")).split(":"))
        day = dl.due_date - timedelta(days=int(wb.get("days_before", 2)))
        start = datetime.combine(day, time(hh, mm), now.tzinfo)
        if start <= now:                                   # too late for that day: try tomorrow
            start = datetime.combine(now.date() + timedelta(days=1), time(hh, mm), now.tzinfo)
        end = start + timedelta(hours=float(wb.get("hours", 2)))
        if end <= dl.due:
            events.append({**base, "summary": f"{wb.get('prefix', 'Work on: ')}{title}",
                           "start": {"dateTime": start.isoformat()},
                           "end": {"dateTime": end.isoformat()},
                           "reminders": {"useDefault": False,
                                         "overrides": [{"method": "popup", "minutes": 30}]}})
    return events


# ----------------------------------------------------------------- pipeline --
def extract(mail, cfg: dict) -> list[Deadline]:
    if cfg.get("extractor") == "llm":
        try:
            from .llm import find_deadlines_llm
            return find_deadlines_llm(mail.subject, mail.body, mail.received, cfg["llm"])
        except Exception as e:                              # model down -> offline rules
            print(f"  (AI extractor failed: {e}; using rules)")
    return find_deadlines(mail.subject, mail.body, mail.received, cfg.get("day_first", False))


def run(cfg: dict, gmail, calendar, store: Store, dry_run: bool = False,
        now: datetime | None = None, log=print) -> int:
    """Process new mail once. Returns the number of mails turned into events."""
    tz = get_tz(cfg)
    now = now or datetime.now(tz)
    scan, force = cfg["scan"], cfg["force"]

    forced: list[str] = []
    if force.get("label"):
        lid = gmail.label_id(force["label"])
        if lid:
            forced = gmail.search(f"newer_than:{int(force.get('lookback_days', 30))}d", lid)
        else:
            log(f'Note: Gmail label "{force["label"]}" does not exist yet. Create it in Gmail '
                "to force an event for a mail.")
    auto = gmail.search(scan["query"], limit=int(scan["max_messages"])) if scan.get("enabled", True) else []
    ignore = {l.lower() for l in scan.get("ignore_labels") or []}
    forced_set, created = set(forced), 0

    for msg_id in dict.fromkeys(forced + auto):
        is_forced = msg_id in forced_set
        seen = store.status(msg_id)
        if seen == "event" or (seen == "none" and not is_forced):
            continue
        mail = gmail.get(msg_id, tz)
        if not is_forced and ignore & {l.lower() for l in mail.labels}:
            continue

        need = 0.0 if is_forced else float(scan.get("min_confidence", 0.6))
        picks = [d for d in extract(mail, cfg) if d.confidence >= need and d.due > now]
        dl, uncertain = (picks[0], False) if picks else (None, False)
        if dl is None and is_forced:
            n = int(force.get("default_days", 7))
            day = mail.received.date() + timedelta(days=n)
            if day <= now.date():
                day = now.date() + timedelta(days=n)
            dl = Deadline(datetime.combine(day, time(23, 59), tz), True, 0.0, "")
            uncertain = True
        if dl is None:
            if not dry_run:
                store.mark(msg_id)
            continue

        style = style_for(cfg, mail.labels)
        events = build_events(mail, dl, style, now, uncertain)
        when = dl.due.strftime("%a %d %b %Y") + ("" if dl.all_day else dl.due.strftime(" %H:%M"))
        log(f"{'[dry-run] ' if dry_run else ''}{when}  {events[0]['summary']}"
            + (f"  (+ work block {events[1]['start']['dateTime'][:16].replace('T', ' ')})"
               if len(events) > 1 else ""))
        if not dry_run:
            ids = [calendar.insert(style.get("calendar") or "primary", e) for e in events]
            store.mark(msg_id, dl.due.isoformat(), ",".join(ids))
        created += 1
    return created
