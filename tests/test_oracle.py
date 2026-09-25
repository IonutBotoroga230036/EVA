import asyncio
import json
from datetime import datetime, timedelta

import pytest

from core.oracle import Oracle, ReminderStore, briefing, when_words
from tests.fakes_google import FakeGoogle, ev, msg


def make(tmp_path, google=None, **kw):
    said = []

    async def deliver(text, widget):
        said.append(text)
        return True
    o = Oracle(store=ReminderStore(tmp_path / "r.json"), deliver=deliver, google_getter=lambda: google,
               quiet_hours=(23, 8), **kw)
    return o, said


NOON = datetime.now().replace(hour=12, minute=0, second=0, microsecond=0)


def test_due_reminder_is_spoken_once(tmp_path):
    o, said = make(tmp_path)
    o.store.add("call mom", NOON - timedelta(minutes=1))
    asyncio.run(o.tick(NOON))
    asyncio.run(o.tick(NOON))
    assert said == ["Sir, a reminder: call mom."] and o.last_reminder["text"] == "call mom"


def test_do_not_disturb_holds_reminders_then_delivers(tmp_path):
    o, said = make(tmp_path)
    now = datetime.now()                      # DND is wall-clock based, so the test uses the real clock
    o.set_dnd(30)
    o.store.add("stretch", now - timedelta(minutes=1))
    asyncio.run(o.tick(now))
    assert said == [] and len(o.held) == 1
    o.set_dnd(None)
    asyncio.run(o.tick(now))
    assert said == ["Welcome back, sir. While you were busy: stretch."]


def test_quiet_hours_silence_meetings_but_not_reminders(tmp_path):
    late = NOON.replace(hour=23, minute=30)
    g = FakeGoogle(events=[ev("m", "Late call", late + timedelta(minutes=5))])
    o, said = make(tmp_path, google=g)
    o.store.add("take meds", late - timedelta(minutes=1))
    asyncio.run(o.tick(late))
    assert said == ["Sir, a reminder: take meds."]


def test_meeting_heads_up_comes_once(tmp_path):
    g = FakeGoogle(events=[ev("m1", "Deloitte call", NOON + timedelta(minutes=8))])
    o, said = make(tmp_path, google=g, poll_seconds=0)
    asyncio.run(o.tick(NOON))
    asyncio.run(o.tick(NOON + timedelta(minutes=1)))
    assert said == ["Sir, Deloitte call starts in 8 minutes."]


def test_new_email_after_baseline_only(tmp_path):
    g = FakeGoogle(messages=[msg("old", "Newsletter <n@x.com>", "Weekly digest")], contacts={"tom@deloitte.nl"})
    o, said = make(tmp_path, google=g, poll_seconds=0)
    asyncio.run(o.tick(NOON))                          # baseline: the backlog is not read out
    g.messages.insert(0, msg("promo", "Instagram <no-reply@mail.instagram.com>", "See who's new in your feed",
                             labels=["CATEGORY_SOCIAL"], bulk=True))
    g.messages.insert(0, msg("new", "Tom Reinhoudt <tom@deloitte.nl>", "Design team", labels=["IMPORTANT"]))
    asyncio.run(o.tick(NOON + timedelta(seconds=1)))
    assert said == ["Sir, an important email from Tom Reinhoudt about Design team."]     # Instagram stays quiet


def test_undelivered_reminders_wait_for_a_window(tmp_path):
    said = []

    async def nobody(text, widget):
        return False
    o = Oracle(store=ReminderStore(tmp_path / "r.json"), deliver=nobody, google_getter=lambda: None)
    o.store.add("water the plants", NOON - timedelta(minutes=1))
    asyncio.run(o.tick(NOON))

    async def someone(text, widget):
        said.append(text)
        return True
    o.deliver = someone
    asyncio.run(o.on_client_connected())
    assert said == ["Sir, a reminder: water the plants."]


def test_when_words():
    now = NOON
    assert when_words(now + timedelta(minutes=20), now) == "in 20 minutes"
    assert when_words(now.replace(hour=18), now) == "at 18:00"
    assert when_words(now + timedelta(days=1), now) == "tomorrow at 12:00"


def test_briefing_is_complete_and_exact(tmp_path, monkeypatch):
    import core.tools_native as tn
    monkeypatch.setattr(tn, "get_weather_report", lambda *a, **k: {
        "city": "Breda", "when": "Now", "temp_c": 15, "feels_like_c": 15, "conditions": "clear", "high_c": 20,
        "low_c": 10, "rain_chance_pct": 0})
    import core.google_api as ga
    g = FakeGoogle(events=[ev("1", "Design sync", NOON.replace(hour=14))],
                   messages=[msg("a", "Tom <t@x.nl>", "Contract draft", labels=["IMPORTANT"])], contacts={"t@x.nl"})
    monkeypatch.setattr(ga, "connected", lambda: True)
    monkeypatch.setattr(ga, "get_google", lambda: g)
    o, _ = make(tmp_path)
    o.store.add("call mom", NOON.replace(hour=17))
    text, widget = briefing(NOON.replace(hour=9), oracle=o)
    assert text.startswith("Good morning, sir. It's")
    for part in ("Right now in Breda it's 15 degrees", "You have 1 thing today: 14:00 Design sync",
                 "1 email looks important: Tom about Contract draft", "the next at 17:00: call mom"):
        assert part in text, part


# ---------------------------------------------------------------- the reminders skill
@pytest.fixture
def skill(tmp_path, monkeypatch):
    import core.oracle as co
    o, said = make(tmp_path)
    monkeypatch.setattr(co, "_oracle", o)
    from skills.registry import SkillRegistry
    return SkillRegistry("skills").discover().functions(), o


def test_set_and_list_reminders(skill):
    fns, o = skill
    out = fns["set_reminder"](text="call mom", when="in 20 minutes")
    assert out["say"] == "I'll remind you in 20 minutes to call mom, sir."
    assert "When should I remind you" in fns["set_reminder"](text="call mom")["say"]
    assert "call mom" in fns["list_reminders"]()["say"]


def test_snooze_after_a_reminder(skill):
    fns, o = skill
    o.last_reminder = {"text": "stretch"}
    assert fns["snooze_reminder"](minutes=5)["say"] == "I'll remind you again in 5 minutes, sir."
    assert o.store.open()[0]["text"] == "stretch"


@pytest.mark.parametrize("text,expected", [
    ("remind me to call mom at 18:00", ("set_reminder", {"text": "call mom", "when": "at 18:00"})),
    ("remind me in 20 minutes to stretch", ("set_reminder", {"text": "stretch", "when": "in 20 minutes"})),
    ("do not disturb for an hour", ("do_not_disturb", {"minutes": 60})),
    ("don't disturb me for 30 minutes", ("do_not_disturb", {"minutes": 30})),
    ("I'm back", ("do_not_disturb", {"minutes": 0})),
    ("snooze it", ("snooze_reminder", {"minutes": 10})),
    ("good morning", ("morning_briefing", {})),
    ("what's on my calendar tomorrow", ("calendar_agenda", {"day": "tomorrow"})),
    ("what am I doing this week", ("calendar_agenda", {"day": "this week"})),
    ("any opening tomorrow afternoon", ("calendar_free", {"day": "tomorrow afternoon"})),
    ("check my email", ("email_unread", {})),
    ("do I have any new emails", ("email_unread", {})),
])
def test_fast_paths(text, expected):
    from core.orchestrator_hybrid import ToolBelt, fast_path
    from skills.registry import SkillRegistry
    assert fast_path(text, ToolBelt(SkillRegistry("skills").discover())) == expected


# ---------------------------------------------------------------- routines
from core.oracle import RoutineStore, describe_routine, parse_routine


@pytest.mark.parametrize("text,days,hm,action", [
    ("every weekday at 8 brief me", [0, 1, 2, 3, 4], (8, 0), "briefing"),
    ("every sunday at 19:00 remind me to plan the week", [6], (19, 0), "remind"),
    ("every monday and thursday at 6pm remind me to go to the gym", [0, 3], (18, 0), "remind"),
    ("every day at 7:30 check my email", list(range(7)), (7, 30), "email"),
    ("every weekend at 10 tell me my agenda", [5, 6], (10, 0), "agenda"),
])
def test_parse_routine(text, days, hm, action):
    r = parse_routine(text)
    assert r["days"] == days and (r["hour"], r["minute"]) == hm and r["action"] == action


def test_parse_routine_text_and_description():
    r = parse_routine("every sunday at 19:00 remind me to plan the week")
    assert r["text"] == "plan the week"
    assert describe_routine(r) == "a reminder to plan the week every Sunday at 19:00"
    assert describe_routine(parse_routine("every weekday at 8 brief me")) == "your briefing on weekdays at 08:00"
    assert parse_routine("remind me to call mom") is None


def test_routine_fires_once_per_day_within_its_window(tmp_path):
    rs = RoutineStore(tmp_path / "r.json")
    rs.add({"days": list(range(7)), "hour": 8, "minute": 0, "action": "remind", "text": "drink water"})
    morning = NOON.replace(hour=8, minute=5)
    assert [r["text"] for r in rs.due(morning)] == ["drink water"]
    assert rs.due(morning.replace(minute=10)) == []                        # already ran today
    assert rs.due(morning.replace(hour=11) + timedelta(days=1)) == []      # missed its window tomorrow: skip


def test_oracle_delivers_a_routine(tmp_path):
    o, said = make(tmp_path)
    o.routines.add({"days": list(range(7)), "hour": 8, "minute": 0, "action": "remind", "text": "stretch"})
    asyncio.run(o.tick(NOON.replace(hour=8, minute=1)))
    assert said == ["Sir, your routine reminder: stretch."]


def test_routine_skill_and_fast_path(skill):
    fns, o = skill
    assert fns["schedule_routine"](request="every weekday at 8 brief me")["say"] == \
        "Done, sir. I'll give you your briefing on weekdays at 08:00."
    assert "briefing" in fns["list_routines"]()["say"]
    from core.orchestrator_hybrid import ToolBelt, fast_path
    from skills.registry import SkillRegistry
    assert fast_path("every weekday at 8 brief me", ToolBelt(SkillRegistry("skills").discover())) == \
        ("schedule_routine", {"request": "every weekday at 8 brief me"})



@pytest.mark.parametrize("text,hour", [("tomorrow at 4", 16), ("friday at 10", 10), ("remind me at 8 am", 8),
                                       ("every weekday at 7 brief me", 7)])
def test_clock_after_other_words_regression(text, hour):
    from core.timeparse import parse_clock
    assert parse_clock(text)[0] == hour
