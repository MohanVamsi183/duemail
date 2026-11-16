from datetime import date, datetime
from zoneinfo import ZoneInfo

from mail2cal.extract import find_deadlines, html_to_text

NY = ZoneInfo("America/New_York")
RECEIVED = datetime(2026, 11, 16, 17, 14, tzinfo=NY)   # a Monday


def best(subject, body):
    found = find_deadlines(subject, body, RECEIVED)
    return found[0] if found else None


def test_within_seven_days():
    d = best("Online assessment for Software Engineer",
             "Thanks for applying. Please complete the assessment within 7 days.")
    assert d.due_date == date(2026, 11, 23) and d.all_day


def test_days_to_complete_words():
    d = best("Next steps", "You have five business days to complete the coding challenge.")
    assert d.due_date == date(2026, 11, 23)


def test_expires_in_hours():
    d = best("Your link", "This invitation expires in 48 hours.")
    assert not d.all_day and d.due == datetime(2026, 11, 18, 17, 14, tzinfo=NY)


def test_absolute_with_time_and_zone():
    d = best("HackerRank test", "The test must be submitted by November 20, 2026 at 11:59 PM PST.")
    assert d.due == datetime(2026, 11, 21, 2, 59, tzinfo=NY) and not d.all_day


def test_absolute_no_year_rolls_over():
    d = best("Renewal", "Your membership is due on Jan 5.")
    assert d.due_date == date(2027, 1, 5)


def test_weekday():
    d = best("Form", "Please return the signed form by Friday 5pm.")
    assert d.due == datetime(2026, 11, 20, 17, 0, tzinfo=NY)


def test_numeric_and_iso():
    assert best("Bill", "Payment due 12/01/2026.").due_date == date(2026, 12, 1)
    assert best("Reg", "Registration closes on 2026-12-15.").due_date == date(2026, 12, 15)


def test_deadline_in_subject():
    d = best("Reminder: application deadline Dec 1", "See details on the portal.")
    assert d.due_date == date(2026, 12, 1)


def test_non_deadlines_ignored():
    assert best("Order shipped", "Your order was placed on Nov 20 and is on its way.") is None
    assert best("Hello", "We received your application on November 10, 2026.") is None
    assert best("Newsletter", "Our team grew a lot in 7 days of hacking.") is None
    assert best("Lunch", "It was great seeing you Friday!") is None


def test_past_dates_dropped():
    assert best("Old", "This was due by November 1, 2026.") is None


def test_strong_beats_weak():
    d = best("Offer", "Your start date would be January 11, 2027 if you accept. "
                      "Please respond by November 25, 2026.")
    assert d.due_date == date(2026, 11, 25)


def test_html():
    txt = html_to_text("<html><style>p{}</style><p>Complete it <b>within 3 days</b>.</p></html>")
    assert best("x", txt).due_date == date(2026, 11, 19)
