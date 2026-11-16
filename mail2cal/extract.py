"""Find deadlines in email text.

Rule-based and fully offline. Given a mail's subject, body and the time it was
received, returns candidate deadlines sorted best-first.

Two kinds of deadline are recognised:
  * relative  - "complete within 7 days", "expires in 48 hours"
  * absolute  - "due by Nov 23, 2026 at 11:59 PM", "before Friday", "12/01/2026"

A date only counts when a deadline-ish word ("due", "by", "expires" ...) sits in
the same sentence, so "we received your application on Nov 10" is ignored.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from html.parser import HTMLParser

from dateutil import parser as du_parser


@dataclass
class Deadline:
    due: datetime          # timezone-aware; for all-day deadlines time is 23:59
    all_day: bool
    confidence: float      # 0..1
    evidence: str          # the sentence the deadline was found in

    @property
    def due_date(self) -> date:
        return self.due.date()


# ----------------------------------------------------------------- patterns --
_MONTH = (r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|"
          r"aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)")
_WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
_WEEKDAY = r"(?:" + "|".join(_WEEKDAYS) + r")"
_NUMWORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
             "eight": 8, "nine": 9, "ten": 10, "fourteen": 14, "thirty": 30,
             "a": 1, "an": 1}
_NUM = r"(\d{1,3}|" + "|".join(_NUMWORDS) + r")"

_ABS_DATE = re.compile(
    rf"\b(?:{_MONTH}\.?\s+\d{{1,2}}(?:st|nd|rd|th)?(?:,?\s+\d{{4}})?"       # Nov 23, 2026
    rf"|\d{{1,2}}(?:st|nd|rd|th)?\s+(?:of\s+)?{_MONTH}\.?(?:,?\s+\d{{4}})?"  # 23 November 2026
    r"|\d{4}-\d{2}-\d{2}"                                                    # 2026-11-23
    r"|\d{1,2}/\d{1,2}/\d{2,4})\b",                                          # 11/23/2026
    re.I)
_WORD_DATE = re.compile(
    rf"\b(?:(next|this)\s+)?({_WEEKDAY})\b|\b(today|tonight|tomorrow|eod|"
    r"end of (?:the )?day|end of (?:the |this )?week)\b", re.I)
_TIME = re.compile(
    r"\b(\d{1,2})(?::(\d{2}))?\s*([ap])\.?\s?m\b\.?|\b([01]?\d|2[0-3]):([0-5]\d)\b"
    r"|\b(noon|midnight)\b", re.I)

_RELATIVE = re.compile(
    rf"\b(?:within|in|next|have|has|for|after)\s+(?:the\s+)?(?:next\s+)?{_NUM}\s+"
    r"(business\s+|working\s+|calendar\s+)?(hour|day|week)s?\b", re.I)
# "7 days to complete", "48 hours left"
_RELATIVE_B = re.compile(
    rf"\b{_NUM}\s+(business\s+|working\s+|calendar\s+)?(hour|day|week)s?\s+"
    r"(?:to|left|remaining|from)\b", re.I)

# Words that make a date a deadline when they come right before it.
_STRONG = re.compile(
    r"\b(?:by|before|until|till|due(?: on| date)?|deadline(?: is| of| for)?|no later than|"
    r"not later than|on or before|expir\w*(?: on| at)?|clos\w+(?: on| at)?|ends?(?: on| at)?|"
    r"prior to|last (?:date|day)(?: is| to \w+)?)\W*(?:is\W*|on\W*|the\W*)*$", re.I)
# Words that make it a deadline when they appear anywhere in the sentence.
_WEAK = re.compile(
    r"\b(?:deadline|due|expir\w*|complet\w*|submi\w+|respond|reply|rsvp|clos\w+|valid|"
    r"must|required?|register|registration|renew\w*|pay(?:ment)?|accept|confirm|finish|"
    r"last (?:date|day)|return|sign|upload|schedule|book)\b", re.I)
# Words right before a date that mean it is NOT a deadline.
_ANTI = re.compile(
    r"\b(?:sent|received|applied|posted|dated|since|placed|ordered|created|updated|"
    r"joined|on)\W*(?:on\W*|the\W*)*$", re.I)

_TZ = {"EST": -5 * 3600, "EDT": -4 * 3600, "CST": -6 * 3600, "CDT": -5 * 3600,
       "MST": -7 * 3600, "MDT": -6 * 3600, "PST": -8 * 3600, "PDT": -7 * 3600,
       "ET": -5 * 3600, "CT": -6 * 3600, "MT": -7 * 3600, "PT": -8 * 3600,
       "UTC": 0, "GMT": 0, "IST": 5 * 3600 + 1800, "BST": 3600, "CET": 3600}
_TZ_RE = re.compile(r"^\s*\(?(" + "|".join(_TZ) + r")\b")


# ------------------------------------------------------------------ helpers --
class _Stripper(HTMLParser):
    def __init__(self):
        super().__init__()
        self.out, self._skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "head"):
            self._skip += 1
        elif tag in ("br", "p", "div", "tr", "li", "h1", "h2", "h3", "td"):
            self.out.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "head") and self._skip:
            self._skip -= 1
        elif tag in ("p", "div", "tr", "li"):
            self.out.append("\n")

    def handle_data(self, data):
        if not self._skip:
            self.out.append(data)


def html_to_text(html: str) -> str:
    s = _Stripper()
    s.feed(html)
    return "".join(s.out)


def _sentences(text: str) -> list[str]:
    text = text.replace("\r", "").replace(" ", " ")
    out = []
    for line in text.split("\n"):
        line = re.sub(r"[ \t]+", " ", line).strip()
        if not line or line.startswith(">"):       # skip quoted replies
            continue
        out.extend(p for p in re.split(r"(?<=[.!?])\s+(?=[A-Z])", line) if p)
    return out


def _num(tok: str) -> int:
    return int(tok) if tok.isdigit() else _NUMWORDS[tok.lower()]


def _add_business_days(d: date, n: int) -> date:
    while n > 0:
        d += timedelta(days=1)
        if d.weekday() < 5:
            n -= 1
    return d


def _find_time(sentence: str, start: int, end: int):
    """Look for a clock time just after (or just before) a date. -> (time, tzoffset|None)"""
    after = sentence[end:end + 40]
    m = _TIME.search(after)
    tail = after[m.end():] if m else ""
    if not m:
        before = sentence[max(0, start - 25):start]
        hits = list(_TIME.finditer(before))
        m = hits[-1] if hits else None
        tail = before[m.end():] if m else ""
    if not m:
        return None, None
    if m.group(6):
        t = time(12, 0) if m.group(6).lower() == "noon" else time(23, 59)
    elif m.group(3):
        h = int(m.group(1)) % 12 + (12 if m.group(3).lower() == "p" else 0)
        if int(m.group(1)) > 12:
            return None, None
        t = time(h, int(m.group(2) or 0))
    else:
        t = time(int(m.group(4)), int(m.group(5)))
    tz = _TZ_RE.match(tail)
    return t, (_TZ[tz.group(1).upper()] if tz else None)


def _resolve_word_date(m: re.Match, received: datetime) -> date:
    today = received.date()
    if m.group(2):
        target = _WEEKDAYS.index(m.group(2).lower())
        delta = (target - today.weekday()) % 7
        if delta == 0 or (m.group(1) or "").lower() == "next" and delta < 3:
            delta += 7
        return today + timedelta(days=delta)
    w = m.group(3).lower()
    if w == "tomorrow":
        return today + timedelta(days=1)
    if "week" in w:
        return today + timedelta(days=(4 - today.weekday()) % 7)   # Friday
    return today


def _resolve_abs_date(txt: str, received: datetime, day_first: bool):
    has_year = bool(re.search(r"\d{4}|/\d{2}$", txt))
    try:
        d = du_parser.parse(txt, default=datetime(received.year, 1, 1),
                            dayfirst=day_first).date()
    except (ValueError, OverflowError):
        return None
    if not has_year and d < received.date() - timedelta(days=1):
        d = d.replace(year=d.year + 1)              # "Jan 5" seen in December
    return d


def _make(d: date, t, tzoff, received: datetime):
    from datetime import timezone
    if t is None:
        return datetime.combine(d, time(23, 59), received.tzinfo), True
    tz = timezone(timedelta(seconds=tzoff)) if tzoff is not None else received.tzinfo
    return datetime.combine(d, t, tz).astimezone(received.tzinfo), False


# --------------------------------------------------------------------- main --
def find_deadlines(subject: str, body: str, received: datetime,
                   day_first: bool = False) -> list[Deadline]:
    """Return deadlines found in a mail, best first. `received` must be tz-aware."""
    found: list[Deadline] = []
    sentences = [("subject", subject or "")] + [("body", s) for s in _sentences(body or "")]

    for where, s in sentences:
        weak = bool(_WEAK.search(s))

        # --- relative: "within 7 days"
        for rx in (_RELATIVE, _RELATIVE_B):
            for m in rx.finditer(s):
                if not weak:
                    continue
                n, biz, unit = _num(m.group(1)), bool(m.group(2) and
                                                       "calendar" not in m.group(2).lower()), m.group(3).lower()
                if n == 0 or n > 365:
                    continue
                if unit == "hour":
                    due, all_day = received + timedelta(hours=n), False
                else:
                    days = n * 7 if unit == "week" else n
                    d = (_add_business_days(received.date(), days) if biz
                         else received.date() + timedelta(days=days))
                    due, all_day = _make(d, None, None, received)
                found.append(Deadline(due, all_day, 0.85, s))

        # --- absolute dates and weekday words
        hits = [(m, "abs") for m in _ABS_DATE.finditer(s)] + \
               [(m, "word") for m in _WORD_DATE.finditer(s)]
        for m, kind in hits:
            before = s[:m.start()]
            if _STRONG.search(before):
                conf = 0.9
            elif _ANTI.search(before):
                continue
            elif weak:
                conf = 0.6
            else:
                continue
            d = (_resolve_abs_date(m.group(0), received, day_first) if kind == "abs"
                 else _resolve_word_date(m, received))
            if d is None:
                continue
            t, tzoff = _find_time(s, m.start(), m.end())
            due, all_day = _make(d, t, tzoff, received)
            if kind == "word" and conf < 0.9:
                conf = 0.5                      # "see you Friday" style mentions
            found.append(Deadline(due, all_day, conf, s))

    # keep future ones only, drop duplicates, best first
    seen, out = set(), []
    for dl in found:
        if dl.due <= received or dl.due > received + timedelta(days=730):
            continue
        key = (dl.due_date, dl.all_day)
        if key in seen:
            continue
        seen.add(key)
        out.append(dl)
    out.sort(key=lambda x: (-x.confidence, x.due))
    return out
