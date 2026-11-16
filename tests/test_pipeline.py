"""End-to-end run with fake Gmail/Calendar objects (no network)."""
from datetime import datetime
from zoneinfo import ZoneInfo

import yaml

from duemail.core import DEFAULT_CONFIG, Store, _merge, load_config, run
from duemail.google_api import Mail

NY = ZoneInfo("America/New_York")
NOW = datetime(2026, 11, 16, 17, 14, tzinfo=NY)


class FakeGmail:
    def __init__(self, mails, forced=()):
        self.mails, self.forced = {m.id: m for m in mails}, list(forced)

    def label_id(self, name):
        return "L1"

    def search(self, query="", label_id=None, limit=100):
        return self.forced if label_id else list(self.mails)

    def get(self, msg_id, tz):
        return self.mails[msg_id]


class FakeCal:
    def __init__(self):
        self.events = []

    def insert(self, cal, body):
        self.events.append((cal, body))
        return f"e{len(self.events)}"


def mail(i, subject, body, labels=()):
    return Mail(i, "t" + i, subject, "hr@example.com", NOW, body, list(labels))


def cfg(tmp_path):
    c = load_config(tmp_path / "missing.yaml")
    c["timezone"] = "America/New_York"
    c["label_rules"] = yaml.safe_load(DEFAULT_CONFIG)["label_rules"]
    return c


def test_job_mail_gets_due_event_and_work_block(tmp_path):
    g = FakeGmail([mail("1", "Re: Online assessment", "Please complete it within 7 days.", ["INBOX", "Jobs/Assessments"]),
                   mail("2", "Weekly digest", "Nothing to do here.", ["INBOX"])])
    cal, store = FakeCal(), Store(tmp_path / "s.db")
    assert run(cfg(tmp_path), g, cal, store, now=NOW) == 1
    due, block = (e for _, e in cal.events)
    assert due["summary"] == "JOB DUE: Online assessment"
    assert due["start"] == {"date": "2026-11-23"} and due["colorId"] == "11"
    assert [o["minutes"] for o in due["reminders"]["overrides"]] == [900, 3780]   # 9am, 1 and 3 days before
    assert block["summary"] == "Work on: Online assessment"
    assert block["start"]["dateTime"].startswith("2026-11-20T18:00")
    assert block["end"]["dateTime"].startswith("2026-11-20T21:00")
    # second run adds nothing
    assert run(cfg(tmp_path), g, cal, store, now=NOW) == 0 and len(cal.events) == 2


def test_dry_run_changes_nothing(tmp_path):
    g = FakeGmail([mail("1", "Bill", "Payment due by Nov 30, 2026.", ["Bills"])])
    cal, store = FakeCal(), Store(tmp_path / "s.db")
    assert run(cfg(tmp_path), g, cal, store, dry_run=True, now=NOW, log=lambda *_: None) == 1
    assert cal.events == [] and store.status("1") is None


def test_bills_rule_disables_work_block(tmp_path):
    g = FakeGmail([mail("1", "Bill", "Payment due by Nov 30, 2026.", ["Bills"])])
    cal = FakeCal()
    run(cfg(tmp_path), g, cal, Store(tmp_path / "s.db"), now=NOW)
    assert len(cal.events) == 1 and cal.events[0][1]["summary"] == "PAY: Bill"


def test_force_label_without_date(tmp_path):
    m = mail("9", "Catch up?", "Let me know what you think.", ["Deadline"])
    g = FakeGmail([m], forced=["9"])
    cal, store = FakeCal(), Store(tmp_path / "s.db")
    store.mark("9")                                    # auto-scan saw it earlier, found nothing
    assert run(cfg(tmp_path), g, cal, store, now=NOW) == 1
    assert cal.events[0][1]["summary"] == "DUE: Catch up? (check date)"
    assert cal.events[0][1]["start"] == {"date": "2026-11-23"}


def test_ignore_labels_and_low_confidence(tmp_path):
    c = _merge(cfg(tmp_path), {"scan": {"ignore_labels": ["Promotions"]}})
    g = FakeGmail([mail("1", "Sale", "Offer valid until Nov 30, 2026.", ["Promotions"]),
                   mail("2", "Hi", "We could finish this Friday maybe.", ["INBOX"])])
    cal = FakeCal()
    assert run(c, g, cal, Store(tmp_path / "s.db"), now=NOW) == 0
