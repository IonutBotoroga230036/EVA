"""Sep 27 night voice log: the fixes, one test each."""

import asyncio
import json
from datetime import datetime, timedelta

import pytest

import core.orchestrator_hybrid as orch_mod
from core.orchestrator_hybrid import _CANCEL, _CLAIM, ToolBelt, fast_path, spoken, volume_level


def belt(*extra):
    b = ToolBelt(None, None)
    for name in extra:
        b.by_name.setdefault(name, {})
    return b


@pytest.mark.parametrize("text", ["Calendar updated: Coffee with Tom at 15:00 tomorrow, sir.", "Volume at 50, sir."])
def test_the_two_claims_that_got_through(text):
    assert _CLAIM.search(text)


def test_add_something_tomorrow_at_3_becomes_a_calendar_question():
    assert fast_path("Add coffee with Tom tomorrow at 3", belt("calendar_add")) == \
        ("calendar_add", {"title": "coffee with Tom", "day": "tomorrow", "time": "3"})
    assert fast_path("add milk to my shopping list", belt("calendar_add", "shopping_add"))[0] == "shopping_add"


@pytest.mark.parametrize("text,title,time", [
    ("Can you move coffee with Tom for 4pm?", "coffee with Tom", "4pm"),
    ("I want you to move coffee with Tom at 4 instead of 3.", "coffee with Tom", "4"),
])
def test_move_one_event(text, title, time):
    assert fast_path(text, belt("calendar_move")) == ("calendar_move", {"title": title, "time": time})


def test_shift_is_only_for_everything():
    import re
    from skills.calendar import tools as ct
    assert not re.search(ct.GUARDS["calendar_shift"], "move coffee with Tom to 4pm", re.I)
    assert re.search(ct.GUARDS["calendar_shift"], "push everything an hour later", re.I)
    assert ct.CONFIRM["calendar_move"]({"title": "Coffee with Tom", "time": "4pm"}) == "move Coffee with Tom to 4pm"


def test_moving_keeps_the_day_and_the_length():
    from core import tempo
    start = datetime.now().replace(hour=15, minute=0, second=0, microsecond=0) + timedelta(days=1)
    moved = []

    class G:
        def list_events(self, a, b, max_results=50):
            return [{"id": "e1", "title": "Coffee with Tom", "start": start, "end": start + timedelta(minutes=45),
                     "all_day": False}]

        def move_event(self, eid, s, e):
            moved.append((eid, s, e))
    out = tempo.move_event_to(G(), "coffee with Tom", "4pm")
    eid, s, e = moved[0]
    assert eid == "e1" and s.date() == start.date() and s.hour == 16 and e - s == timedelta(minutes=45)
    assert out["say"].startswith("Moved Coffee with Tom to") and "16:00" in out["say"]


@pytest.mark.parametrize("text", ["Nevermind, it's fine.", "Don't.", "Don't", "never mind", "forget it", "no thanks",
                                  "it's fine", "cancel that"])
def test_cancel_phrases(text):
    assert _CANCEL.match(text)


@pytest.mark.parametrize("text", ["stop the music", "don't play that song", "cancel my 3pm meeting"])
def test_real_requests_are_not_cancels(text):
    assert not _CANCEL.match(text)


def test_every_weekday_is_a_routine_and_brief_me_is_understood(monkeypatch):
    from skills.reminders import tools as rt
    made = []
    monkeypatch.setattr(rt, "schedule_routine", lambda request="": made.append(request) or {"say": "ok"})
    rt.set_reminder(text="Believe me", when="Every weekday at 8")
    assert made == ["Every weekday at 8 Believe me"]
    assert rt._BRIEF.sub("brief me", "every weekday at 8, believe me") == "every weekday at 8, brief me"


@pytest.mark.parametrize("n,level", [(250, 50), (270, 70), (75, 75), (140, 100), (-5, 0), (300, 100)])
def test_volume_levels(n, level):
    assert volume_level(n) == level


def test_misheard_spotify_volume_is_fixed_everywhere():
    assert fast_path("Cedars Spotify Volume 250", belt("spotify_volume")) == ("spotify_volume", {"level": 50})
    from core.tools_native import ARG_FILLERS
    assert ARG_FILLERS["spotify_volume"]({"level": 250}, "") == {"level": 50}
    assert ARG_FILLERS["set_volume"]({"level": 180}, "") == {"level": 100}


def test_confirmations_never_say_run_a_tool_name():
    assert belt().describe("set_volume", {"level": 50}) == "set volume (level 50)"


def test_lei_heard_as_lay():
    from core.tools_native import ARG_FILLERS
    assert ARG_FILLERS["convert_currency"]({"from_currency": "EUR", "to_currency": "LAY", "amount": 100}, "")[
        "to_currency"] == "RON"


def test_playlist_is_said_once():
    from core.spotify import _pl
    assert _pl("your the weeknd sex playlist") == "your the weeknd sex playlist"
    assert _pl("your Chill") == "your Chill playlist"


def test_forge_computes_offline_first_and_never_refuses_for_the_internet_alone():
    from core import forge_engine
    src = open(forge_engine.__file__, encoding="utf-8").read()
    assert "synodic month" in src and "NEVER by itself a reason for feasible=false" in src


def test_build_status_by_voice():
    from core import forge_jobs as fj
    b = belt("forge_status")
    assert fast_path("how is the skill building going?", b) == ("forge_status", {})
    assert fast_path("how is it going?", b) is None or fast_path("how is it going?", b)[0] != "forge_status"
    fj.get_jobs().jobs.append(fj.Job(id="x", request="moon", status="done", finished=__import__("time").time()))
    assert fast_path("how is it going?", b) == ("forge_status", {})             # a build just happened


def test_markdown_is_never_spoken():
    assert spoken("Use [MyIP.com](https://www.myip.com/) or **Whoer**.") == "Use MyIP.com or Whoer."


def test_weather_advice_uses_the_weather_tool():
    assert fast_path("Would you advise me to take a raincoat for tomorrow?", belt())[0] == "get_weather"


def test_an_opinion_question_is_talked_about_not_searched(tmp_path, monkeypatch):
    from core.memory.cortex import Cortex
    from skills.registry import SkillRegistry
    from tests.test_m4_and_sep27b import fake_ollama, turn
    client, seen = fake_ollama(decide={"animals": {"tool": "web_search", "query": "opinion on animals"}},
                               answer="I find them fascinating, sir. Do you have a favourite?")
    monkeypatch.setattr(orch_mod.httpx, "AsyncClient", client)
    o = orch_mod.HybridOrchestrator(session_id="a", cortex=Cortex(str(tmp_path / "c.db")),
                                    registry=SkillRegistry("skills").discover())
    assert turn(o, "What's your opinion on animals") == "I find them fascinating, sir. Do you have a favourite?"
    assert "web_search" not in o.last_tools and o.last_lane == "conversation"


def test_yes_with_a_detail_confirms_and_adds_the_detail(tmp_path, monkeypatch):
    from core.memory.cortex import Cortex
    from skills.registry import SkillRegistry
    from tests.test_m4_and_sep27b import fake_ollama, turn
    import core.tempo as tempo
    added = []
    monkeypatch.setattr(tempo, "add_event", lambda g, title, day, time_text, duration_minutes=60:
                        added.append((title, day, time_text)) or {"added": title, "say": f"Added {title}, sir."})
    client, _ = fake_ollama()
    monkeypatch.setattr(orch_mod.httpx, "AsyncClient", client)
    reg = SkillRegistry("skills").discover()
    mod = next(s for s in reg.enabled() if s.name == "calendar").functions["calendar_add"].__globals__
    monkeypatch.setitem(mod, "get_google", lambda: object())
    o = orch_mod.HybridOrchestrator(session_id="y", cortex=Cortex(str(tmp_path / "c.db")), registry=reg)
    o.pending = {"tool": "calendar_add", "args": {"title": "Coffee with Tom", "day": "tomorrow"},
                 "desc": "add Coffee with Tom", "ts": __import__("time").time()}
    assert turn(o, "At 3 o'clock, yes") == "Added Coffee with Tom, sir."
    assert added == [("Coffee with Tom", "tomorrow", "15:00")]              # "3 o'clock" became 15:00


def test_cancel_clears_a_question_she_keeps_asking(tmp_path, monkeypatch):
    from core.memory.cortex import Cortex
    from skills.registry import SkillRegistry
    from tests.test_m4_and_sep27b import fake_ollama, turn
    client, _ = fake_ollama(decide={"": {"tool": "set_reminder", "text": "breathe me"}})
    monkeypatch.setattr(orch_mod.httpx, "AsyncClient", client)
    o = orch_mod.HybridOrchestrator(session_id="n", cortex=Cortex(str(tmp_path / "c.db")),
                                    registry=SkillRegistry("skills").discover())
    o.last_tools = {"set_reminder"}
    assert turn(o, "Nevermind, it's fine.") == "Okay, sir."
    assert turn(o, "Don't") == "Okay, sir."


def test_while_music_plays_no_follow_up_windows(monkeypatch):
    import interfaces.web.server_stream as srv
    from fastapi.testclient import TestClient
    from tests.test_server import collect_until
    monkeypatch.setattr(srv, "get_tts", lambda: None)
    monkeypatch.setattr(srv, "MUSIC_TOOLS", {"get_datetime"})             # stands in for spotify_play
    with TestClient(srv.app) as tc, tc.websocket_connect("/ws") as ws:
        ws.receive_text(); ws.receive_text()
        ws.send_text(json.dumps({"type": "message", "text": "what time is it"}))
        assert collect_until(ws, lambda e: e["type"] == "turn_meta")[-1]["follow_up"] is False
        ws.send_text(json.dumps({"type": "message", "text": "17% of 240"}))       # music still on
        assert collect_until(ws, lambda e: e["type"] == "turn_meta")[-1]["follow_up"] is False
