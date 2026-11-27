"""v0.9: the fixes from his 27 Nov morning log (stories vs the claim guard, "continue", "stop the music" during a
question, ranged calendar shifts, the budget estimate, grouped heads-ups, the fact extractor), the glance (idle
screen), what she knows (memory panel), her nightly notes, and the morning heads-up. Everything is a fake."""

import asyncio
import json
import time
import types
from datetime import datetime, timedelta

import pytest

import core.orchestrator_hybrid as orch_mod
from core import glance, review, tempo
from core.memory import extractor
from core.memory.cortex import Cortex

from tests.test_agent import T  # noqa: F401
from tests.test_v05_mind import eva, say, think  # noqa: F401  (fixtures)


# ---------------------------------------------------------------- stories and the claim guard
STORY = ("Here is a story for you, sir. A young man arrived with the look of someone who had been sent to fix "
         "something he did not understand. He had parts ordered on a grant. The lighthouse stayed lit.")


def test_a_story_is_told_not_guarded(eva, think):
    """27 Nov 02:08: the story tripped the claim guard and became 'No, sir, I haven't done that.'"""
    think([[STORY]])
    reply, events = say(eva, "Can you tell me a story for 10 minutes?")
    assert reply == STORY
    spoken = "".join(e["text"] for e in events if e["type"] == "token")
    assert "had been sent to fix" in spoken


def test_a_long_story_gets_room(eva, think):
    s = think([["Once upon a time, sir."]])
    say(eva, "Tell me a story that's 20 minutes long")
    assert s.calls[0]["max_tokens"] >= 20 * 140


def test_nothing_was_changed_is_not_a_claim():
    """27 Nov 10:01: 'Nothing has been moved on your calendar' was caught as a claim."""
    assert not orch_mod.claims("Understood, sir. Nothing has been moved on your calendar.")
    assert not orch_mod.claims("I haven't added anything, sir.")
    assert orch_mod.claims("Done, sir. Your reminder has been set.")
    assert orch_mod.claims("I've ordered the film for you.")


def test_continue_goes_back_to_the_story(eva, think, monkeypatch):
    """27 Nov 02:11: 'Continue.' played Spotify ('No active Spotify device')."""
    think([["Here is the opening, sir. ... Say \"continue\", sir, and I'll pick up with what the girl's grandfather "
            "left in that box."], ["The box held a letter, sir."]])
    say(eva, "Can you tell me a story that's 20 minutes long?")
    ran = []
    monkeypatch.setattr(eva, "_run", lambda *a, **k: ran.append(a) or (_ for _ in ()).throw(AssertionError("tool")))
    reply, _ = say(eva, "Continue.")
    assert reply == "The box held a letter, sir." and not ran


def test_no_project_nagging_rule_is_in_her_instructions():
    from core import mind
    assert "don't tack them" in mind.system_prompt()


# ---------------------------------------------------------------- a new command during her question
def test_stop_the_music_while_she_asks_stops_the_music(eva, monkeypatch):
    """27 Nov 09:51: 'stop the music' during 'Shall I go ahead?' was taken as 'no' and the music played on."""
    eva.pending = {"tool": "calendar_shift", "args": {"minutes": 30}, "ts": time.time(), "text": "move..."}
    ran = []

    async def fake_run(tool, args, *a, **k):
        ran.append(tool)
        yield {"type": "final", "text": "Paused, sir."}
    monkeypatch.setattr(eva, "_run", fake_run)
    reply, _ = say(eva, "stop the music")
    assert reply != "Cancelled, sir." and eva.pending is None
    assert orch_mod._NEW_STOP.match("Hey Eva, stop the music") and not orch_mod._NEW_STOP.match("stop")
    assert not orch_mod._NEW_STOP.match("stop it")


# ---------------------------------------------------------------- the calendar: a range
class Cal:
    def __init__(self, events):
        self.events, self.moved = events, []

    def list_events(self, start, end, **k):
        return [e for e in self.events if start <= e["start"] < end]

    def move_event(self, eid, start, end):
        self.moved.append((eid, start.strftime("%H:%M")))


def ev(eid, h, m, title):
    d = datetime(2026, 11, 27, h, m)
    return {"id": eid, "start": d, "end": d + timedelta(minutes=15), "title": title, "all_day": False}


def test_move_the_items_from_9_until_now():
    cal = Cal([ev("a", 9, 15, "Take Creatine"), ev("b", 9, 30, "Shower"), ev("c", 9, 45, "Dry Up"),
               ev("d", 10, 0, "Dress Up"), ev("e", 8, 50, "Wake Up")])
    out = tempo.shift_events(cal, 30, "today", now=datetime(2026, 11, 27, 9, 52), from_time="9", until_time="now")
    assert [m[0] for m in cal.moved] == ["a", "b", "c"]
    assert out["say"].startswith("Moved 3 events 30 minutes later, sir (Take Creatine, Shower, Dry Up).")


def test_the_question_says_what_moves():
    from skills.calendar.tools import CONFIRM
    words = CONFIRM["calendar_shift"]({"minutes": 30, "day": "9", "from_time": "9", "until_time": "now"})
    assert words == "move the events today from 9:00 until now 30 minutes later"
    assert CONFIRM["calendar_shift"]({"minutes": 60}) == "move everything still ahead today 1 hour later"


def test_a_shift_without_an_amount_goes_to_the_mind():
    assert orch_mod._needs_mind("calendar_shift", {"day": "today"})
    assert not orch_mod._needs_mind("calendar_shift", {"minutes": 30})


def test_today_with_the_past():
    cal = Cal([ev("a", 9, 15, "Lecture"), ev("b", 11, 0, "Lab")])
    out = tempo.agenda(cal, "today", now=datetime(2026, 11, 27, 10, 0), include_past=True)
    assert out["say"].startswith("You have 2 things today")


# ---------------------------------------------------------------- the budget and the line
def test_a_screenshot_is_an_image_not_200k_tokens():
    """27 Nov 10:03: the computer task 'could cost more' than the 91 cents left."""
    from core.claude import _tokens_in
    shot = {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "A" * 600_000}}
    msgs = [{"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t", "content": [shot]}]}]
    assert sum(_tokens_in(m["content"]) for m in msgs) < 2000


def test_heads_ups_at_the_same_time_are_one_sentence():
    from core.oracle import heads_up
    now = datetime(2026, 11, 27, 9, 5)
    group = [{"title": t, "start": datetime(2026, 11, 27, 9, 15)} for t in ("Take Creatine", "Brush Teeth",
                                                                            "Take Vitamines")]
    assert heads_up(group, now) == "Sir, in 10 minutes: Take Creatine, Brush Teeth and Take Vitamines."
    assert heads_up(group[:1], now) == "Sir, Take Creatine starts in 10 minutes."


def test_a_request_is_not_a_preference():
    assert not extractor.worth_extracting("I said, play Kanye West")
    assert not extractor.worth_extracting("Eva, open my calendar for me")
    assert extractor.worth_extracting("My sister lives in Bucharest")
    assert "while working" not in extractor.PROMPT


def test_kokoro_says_why_it_stays_on_the_cpu(monkeypatch):
    from voice import speech
    import sys
    said = []
    monkeypatch.setattr(speech.logger, "info", lambda m: said.append(m))
    monkeypatch.setitem(sys.modules, "torch", types.SimpleNamespace(
        __version__="2.7.0+cpu", cuda=types.SimpleNamespace(is_available=lambda: False)))
    assert speech._device("auto") == "cpu" and "2.7.0+cpu sees no GPU" in said[-1]


# ---------------------------------------------------------------- the glance
def test_the_glance_shows_the_day(monkeypatch, tmp_path):
    from core import projects
    now = datetime(2026, 11, 27, 10, 0)
    monkeypatch.setattr(glance, "_events", lambda n: [ev("a", 10, 30, "Linear Algebra"), ev("b", 12, 0, "Lunch")])
    monkeypatch.setattr(glance, "weather", lambda: {"place": "Breda", "temp_c": 7.4, "conditions": "cloudy",
                                                    "high_c": 9})
    monkeypatch.setattr(glance, "train", lambda n: "Leave at 10:05 for the 10:22 from Breda, sir.")
    projects.reset_store(tmp_path / "projects")
    projects.tool_project_start("Desk Screen", "a transparent screen for the desk")
    snap = glance.snapshot(now)
    assert snap["next"] == {"time": "10:30", "title": "Linear Algebra", "in_min": 30, "now": False}
    assert snap["left_today"] == 2 and snap["project"]["name"] == "Desk Screen"
    assert snap["weather"]["temp_c"] == 7.4 and snap["train"].startswith("Leave at 10:05")
    hello = glance.greeting(now)
    assert hello.startswith("Good morning, sir. It's 7 degrees and cloudy, up to 9. First up: Linear Algebra at "
                            "10:30, and 1 more after it. Leave at 10:05")


def test_the_morning_heads_up_comes_once(monkeypatch, tmp_path):
    monkeypatch.setattr(glance, "GREETED", tmp_path / "g.txt")
    monkeypatch.setattr(glance, "greeting", lambda now=None: "Good morning, sir.")
    nine = datetime(2026, 11, 27, 9, 0)
    assert glance.greeting_once(nine) == "Good morning, sir."
    assert glance.greeting_once(nine + timedelta(minutes=5)) == ""
    assert glance.greeting_once(datetime(2026, 11, 28, 15, 0)) == ""                # afternoon: none
    assert glance.greeting_once(datetime(2026, 11, 28, 8, 0)) == "Good morning, sir."


def test_the_glance_api():
    from fastapi.testclient import TestClient
    from interfaces.web import server_stream as srv
    with TestClient(srv.app) as tc:
        d = tc.get("/api/glance").json()
    assert set(d) >= {"now", "weather", "next", "train", "project", "tasks", "spend", "review"}


# ---------------------------------------------------------------- what she knows
def test_the_memory_panel_edits_and_forgets(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from interfaces.web import server_stream as srv
    cx = Cortex(str(tmp_path / "c.db"))
    a = cx.remember("Prefers Kanye West while working", category="preference", source="auto")["id"]
    cx.remember("Lives in Breda", category="identity")
    monkeypatch.setattr(srv, "get_cortex", lambda: cx)
    with TestClient(srv.app) as tc:
        facts = tc.get("/api/memory").json()["facts"]
        assert {f["text"] for f in facts} == {"Prefers Kanye West while working", "Lives in Breda"}
        assert tc.get("/api/memory?q=breda").json()["facts"][0]["text"] == "Lives in Breda"
        assert tc.post(f"/api/memory/{a}/forget").status_code == 403
        r = tc.post(f"/api/memory/{a}/edit", json={"text": "Likes Kanye West"}, headers={"X-EVA": "1"})
        assert r.json()["text"] == "Likes Kanye West"
        assert tc.post(f"/api/memory/{a}/edit", json={"text": "my key is sk-ant-abc123456789"},
                       headers={"X-EVA": "1"}).status_code == 400
        assert tc.post(f"/api/memory/{a}/forget", headers={"X-EVA": "1"}).json() == {"forgotten": "Likes Kanye West"}
    assert [f["text"] for f in cx.all_facts()] == ["Lives in Breda"]


# ---------------------------------------------------------------- her notes
def test_her_day_is_kept_in_memory_and_reviewed(monkeypatch):
    from loguru import logger
    review._lines.clear()
    review.start()
    logger.info("USER (voice): Continue.")
    logger.info("EVA: No active Spotify device, sir.")
    logger.info("COMMUTE: 5 trips BD -> NM")                            # not worth reading
    logger.info("USER (voice): pair with token=abcdef123")              # secrets never kept
    lines = review.today_lines()
    assert any("Continue." in l for l in lines) and not any("COMMUTE" in l for l in lines)
    assert any("[hidden]" in l for l in lines) and not any("abcdef123" in l for l in lines)
    seen = []

    class Haiku:
        def message(self, **kw):
            seen.append(kw)
            return {"text": json.dumps({"headline": "A good night, one slip with 'continue'.", "mistakes": [
                {"time": "02:11", "what": "'Continue.' played Spotify", "why": "the music reflex", "fix": "story"}],
                "good": ["the story"]}), "cost_eur": 0.003}
    import core.claude as claude_mod
    monkeypatch.setattr(claude_mod, "get_claude", lambda: Haiku())
    r = review.run(force=True)
    assert r["headline"].startswith("A good night") and r["mistakes"][0]["time"] == "02:11"
    assert "Continue." in seen[0]["messages"][0]["content"] and seen[0]["model"] == "claude-haiku-5-5"
    assert review.latest()["date"] == r["date"] and not review.latest()["read"]
    assert "1. [02:11] 'Continue.' played Spotify" in review.as_text(r)
    assert not list(review.DIR.glob("*.log"))                           # only the review is saved
    review.mark_read(r["date"])
    assert review.latest()["read"]


def test_tonights_review_waits_for_quiet(monkeypatch):
    review._lines.clear()
    day = datetime.now().date().isoformat()
    for i in range(6):
        review._lines.append((day, f"10:0{i} INFO USER (voice): hello {i}"))
    late = datetime.now().replace(hour=23, minute=30)
    assert review.due(late, last_turn_ts=time.time() - 3600)
    assert not review.due(late, last_turn_ts=time.time() - 60)            # he's still talking
    assert not review.due(late.replace(hour=20), last_turn_ts=time.time() - 3600)


def test_the_page_has_the_glance_memory_and_notes():
    from pathlib import Path
    page = Path("interfaces/web/eva.html").read_text(encoding="utf-8")
    for bit in ('id="evaGlance"', 'id="evaGlanceFull"', "/api/glance", "What she knows", "Her notes",
                "/api/memory", "Copy for Claude", "stage-idle"):
        assert bit in page, bit


def test_she_logs_how_fast_the_first_sound_comes():
    from voice.speech import TurnSpeaker, logger as slog
    said = []

    async def go():
        async def send(m):
            pass
        sp = TurnSpeaker(types.SimpleNamespace(synth=lambda text: b"RIFF"), send, 1)
        sp.started = time.time() - 1.2
        await sp.say("Hello, sir.")
        await sp.finish()
    sid = slog.add(lambda m: said.append(m.record["message"]), level="INFO")
    try:
        asyncio.run(go())
    finally:
        slog.remove(sid)
    assert any(m.startswith("ECHO: first sound 1.2 s after you finished") for m in said)
