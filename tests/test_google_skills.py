import json
from datetime import date, datetime, timedelta

import pytest

from core import scribe, tempo
from tests.fakes_google import FakeGoogle, ev, msg

TODAY = date.today()


def at(h, m=0, days=0):
    return datetime.combine(TODAY + timedelta(days=days), datetime.min.time()).replace(hour=h, minute=m)


@pytest.fixture
def g():
    return FakeGoogle(events=[ev("1", "Design sync", at(10), at(11)), ev("2", "Lunch with Muaad", at(13), at(14)),
                              ev("3", "Gym", at(18), at(19)), ev("4", "Deloitte call", at(15, days=1), at(16, days=1))],
                      messages=[msg("a", "Tom Reinhoudt <tom@deloitte.nl>", "Design team", "Can we talk Friday?"),
                                msg("b", "Radboud <info@ru.nl>", "Enrolment", "Your enrolment is confirmed")])


def test_agenda_today_is_exact(g):
    out = tempo.agenda(g, "today", now=at(8))
    assert out["say"] == "You have 3 things today, sir: 10:00 Design sync, 13:00 Lunch with Muaad, and 18:00 Gym."
    assert out["widget"]["kind"] == "calendar" and len(out["widget"]["events"]) == 3


def test_agenda_tomorrow_and_empty_day(g):
    assert "15:00 Deloitte call" in tempo.agenda(g, "tomorrow")["say"]
    assert tempo.agenda(FakeGoogle(), "today")["say"] == "Your calendar is clear today, sir."


def test_free_slots_find_the_gaps(g):
    out = tempo.free_slots(g, "today afternoon", 30, now=at(9))
    assert out["gaps"][0] == ("12:00", "13:00") and ("14:00", "18:00") in out["gaps"]
    assert "afternoon" in out["say"]


def test_add_event_uses_your_time_and_day(g):
    out = tempo.add_event(g, "Coffee with Tom", "tomorrow", "3pm", 30)
    title, start, end = g.inserted[0]
    assert title == "Coffee with Tom" and start.hour == 15 and (end - start).seconds == 1800
    assert start.date() == TODAY + timedelta(days=1) and out["say"].startswith("Added Coffee with Tom on")
    assert "What time" in tempo.add_event(g, "Dentist", "tomorrow", "")["say"]


def test_delete_finds_the_event_by_a_rough_title(g):
    out = tempo.delete_event(g, "lunch muaad", "today")
    assert g.deleted == ["2"] and "Lunch with Muaad" in out["say"]
    assert "couldn't find" in tempo.delete_event(g, "zebra meeting")["say"]


def test_unread_is_exact(g):
    out = scribe.unread(g)
    assert out["say"] == ("You have 2 unread emails, sir. The latest: Tom Reinhoudt about Design team, "
                          "and Radboud about Enrolment.")
    assert scribe.unread(FakeGoogle())["say"].startswith("Your inbox is clear")


def test_draft_reply_goes_to_drafts_in_the_right_thread(g, monkeypatch):
    monkeypatch.setattr(scribe, "compose_body", lambda instr, orig, name, url, model: f"Friday works. {name}")
    out = scribe.draft(g, "say Friday works", reply_to="tom", sender_name="Ionuț")
    d = g.drafts[0]
    assert d["to"] == "tom@deloitte.nl" and d["subject"] == "Re: Design team" and d["thread_id"] == "ta"
    assert d["in_reply_to"] == "<a@x>" and d["body"] == "Friday works. Ionuț"
    assert "nothing was sent" in out["say"]
    assert not hasattr(g, "send") and not hasattr(g, "send_message")        # there is no way to send


def test_skills_ask_you_to_sign_in_when_google_isnt_connected(monkeypatch, tmp_path):
    from skills.registry import SkillRegistry
    import core.google_api as ga
    monkeypatch.setattr(ga, "TOKEN", tmp_path / "none.json")
    monkeypatch.setattr(ga, "_client", None)
    monkeypatch.setattr(ga, "credentials_present", lambda: False)
    fns = SkillRegistry("skills").discover().functions()
    for name in ("calendar_agenda", "email_unread"):
        out = fns[name]()
        assert "docs/GOOGLE_SETUP.md" in out["say"] and out["exact"]


def test_calendar_add_asks_first_with_a_readable_description():
    from core.orchestrator_hybrid import ToolBelt
    from skills.registry import SkillRegistry
    belt = ToolBelt(SkillRegistry("skills").discover())
    assert belt.needs_confirm("calendar_add") and belt.needs_confirm("calendar_delete")
    assert belt.describe("calendar_add", {"title": "Dentist", "day": "friday", "time": "14:00"}) == \
        "add Dentist to your calendar friday at 14:00"
    assert not belt.needs_confirm("email_draft")                             # drafts are never sent


def test_today_focuses_on_what_is_still_coming(g):
    out = tempo.agenda(g, "today", now=at(12))
    assert out["say"] == "You have 2 things still to come today, sir: 13:00 Lunch with Muaad, and 18:00 Gym."
    assert tempo.agenda(g, "today", now=at(20))["say"].startswith("That's everything for today")


def test_whats_next(g):
    out = tempo.next_up(g, now=at(12, 40))
    assert out["say"] == "Next is Lunch with Muaad in 20 minutes at 13:00. After that, Gym at 18:00, sir."
    busy = tempo.next_up(g, now=at(13, 30))
    assert busy["say"] == "Right now: Lunch with Muaad, until 14:00. Next is Gym at 18:00, sir."
    late = tempo.next_up(g, now=at(21))
    assert late["say"] == "Nothing else today. Tomorrow starts at 15:00 with Deloitte call, sir."


def test_whats_next_fast_path():
    from core.orchestrator_hybrid import ToolBelt, fast_path
    from skills.registry import SkillRegistry
    belt = ToolBelt(SkillRegistry("skills").discover())
    for text in ("what's next on my schedule", "what's my next meeting", "what's coming up"):
        assert fast_path(text, belt) == ("calendar_next", {}), text
