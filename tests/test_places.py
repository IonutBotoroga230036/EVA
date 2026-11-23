"""Nov 23: places, arrive/leave reminders and actions, "where did I park", history only on request."""

import asyncio
import json
import time
from datetime import datetime

import pytest

import core.orchestrator_hybrid as orch_mod
from core import places
from core.memory.cortex import Cortex
from skills.registry import SkillRegistry

HOME = (51.5719, 4.7683)          # Breda
WORK = (51.5880, 4.7760)          # ~1.9 km north


def test_distance():
    assert 1700 < places.meters(HOME, WORK) < 2100
    assert places.distance_words(340) == "340 meters" and places.distance_words(1940) == "1.9 km"


@pytest.mark.parametrize("said,clean,place,event,once", [
    ("remind me to buy milk when I leave work", "remind me to buy milk", "work", "leave", True),
    ("when I get home turn on the lights", "turn on the lights", "home", "arrive", True),
    ("turn on the lights when I get home", "turn on the lights", "home", "arrive", True),
    ("every time I arrive at the gym, play my workout playlist", "play my workout playlist", "gym", "arrive", False),
    ("once I'm at the office remind me to call Wim", "remind me to call Wim", "work", "arrive", True),
    ("what's the weather", "what's the weather", None, None, True),
])
def test_trigger_words(said, clean, place, event, once):
    assert places.split_trigger(said) == (clean, place, event, once)


def test_arrive_and_leave_with_a_margin(tmp_path):
    now = [1000.0]
    pl = places.Places(tmp_path / "p.json", tmp_path / "h.jsonl", now=lambda: now[0])
    pl.update(*HOME, acc=20)
    pl.remember("home")
    pl.add_rule("home", "leave", "take your keys")
    pl.add_rule("home", "arrive", "water the plants", once=False)
    assert pl.update(HOME[0] + 0.0012, HOME[1], acc=20) == []          # ~130 m: inside the margin, not "left"
    assert pl.update(HOME[0] + 0.0015, HOME[1], acc=400) == []         # a bad fix never triggers
    fired = pl.update(*WORK, acc=30)
    assert [f["rule"]["text"] for f in fired] == ["take your keys"]
    assert pl.update(*HOME, acc=20)[0]["rule"]["text"] == "water the plants"
    pl.update(*WORK, acc=30)
    assert pl.update(*HOME, acc=20)[0]["rule"]["text"] == "water the plants"   # every time
    assert [r["text"] for r in pl.rules()] == ["water the plants"]
    assert pl.data["visits"][0]["place"] == "home"


def test_no_arrival_on_the_first_fix(tmp_path):
    pl = places.Places(tmp_path / "p.json", tmp_path / "h.jsonl")
    pl.data["places"]["home"] = {"lat": HOME[0], "lon": HOME[1], "radius": 120}
    pl.add_rule("home", "arrive", "x")
    assert pl.update(*HOME) == [] and "home" in pl.data["inside"]


def test_history_only_when_tracking(tmp_path):
    now = [time.time()]
    pl = places.Places(tmp_path / "p.json", tmp_path / "h.jsonl", now=lambda: now[0])
    pl.update(*HOME)
    assert not (tmp_path / "h.jsonl").exists()
    pl.set_tracking(True)
    pl.update(*WORK)
    assert (tmp_path / "h.jsonl").exists() and pl.at_time(datetime.fromtimestamp(now[0]))["lat"] == WORK[0]
    pl.set_tracking(False)
    assert not (tmp_path / "h.jsonl").exists()                       # "stop tracking" deletes the trail


def test_where_did_i_park():
    pl = places.get_places()
    assert places.tool_where_am_i("car")["say"].startswith("I don't know where you parked")
    pl.update(*WORK)
    assert places.tool_place_remember("where I parked")["say"] == "Got it, sir: I'll remember where you parked."
    pl.update(*HOME)
    out = places.tool_where_am_i("my car")
    assert "km from you" in out["say"] and out["widget"]["text"].startswith("https://www.google.com/maps/search/")


def test_where_am_i_needs_a_fix():
    assert places.tool_where_am_i()["say"] == places.NO_FIX
    places.get_places().update(*HOME)
    places.tool_place_remember("home")
    assert places.tool_where_am_i()["say"].startswith("You're at home, since ")


def test_when_from_words():
    now = datetime(2026, 11, 23, 10, 0)
    assert places.when_from_words("yesterday at 3", now) == datetime(2026, 11, 22, 15, 0)
    assert places.when_from_words("at 9 this morning", now) == datetime(2026, 11, 23, 9, 0)
    assert places.when_from_words("2 hours ago", now) == datetime(2026, 11, 23, 8, 0)
    assert places.when_from_words("at 8 pm", now) == datetime(2026, 11, 22, 20, 0)     # tonight hasn't come yet


@pytest.fixture
def eva(tmp_path, monkeypatch):
    ran = []

    async def aexecute(self, name, args):
        ran.append((name, dict(args)))
        return {"result": json.dumps({"ok": True}), "say": "Lights on, sir.", "exact": True}
    monkeypatch.setattr(orch_mod.ToolBelt, "aexecute", aexecute)
    o = orch_mod.HybridOrchestrator(session_id="p", cortex=Cortex(str(tmp_path / "c.db")),
                                    registry=SkillRegistry("skills").discover())
    return o, ran


def turn(o, text):
    async def go():
        return [e async for e in o.process_stream(text)]
    evs = asyncio.run(go())
    return [e for e in evs if e["type"] == "final"][-1]["text"]


def test_an_action_when_you_get_home(eva):
    o, ran = eva
    assert turn(o, "when I get home turn on the lights").startswith("I don't know where home is yet")
    places.get_places().update(*HOME)
    places.tool_place_remember("home")
    places.get_places().update(*WORK)
    text = turn(o, "turn on the lights when I get home")
    assert text == "Okay, sir: when you get home, I'll turn on the lights." and ran == []
    (rule,) = places.get_places().rules()
    assert rule["tool"] == "lights_power" and rule["args"] == {"on": True}
    text = turn(o, "remind me to buy milk when I leave home")
    assert text == "Okay, sir: when you leave home, I'll remind you to buy milk."


def test_the_phone_reports_and_she_reminds(monkeypatch):
    from fastapi.testclient import TestClient
    import interfaces.web.server_stream as srv

    class TTS:
        def warm(self):
            return True

        def phrase(self, text):
            return None

        def synth(self, text):
            return b"RIFF-" + text.encode()
    said = []

    async def broadcast(text, widget=None):
        said.append(text)
        return True
    monkeypatch.setattr(srv, "get_tts", lambda: TTS())
    monkeypatch.setattr(srv, "broadcast", broadcast)
    pl = places.get_places()
    pl.update(*WORK)
    pl.remember("work")
    pl.add_rule("work", "leave", "buy milk")
    with TestClient(srv.app) as tc:
        assert tc.post("/api/phone/location", json={"lat": 999, "lon": 0}).status_code == 400
        assert tc.post("/api/phone/location", json={"lat": WORK[0], "lon": WORK[1]}).json()["say"] == ""
        r = tc.post("/api/phone/location", json={"lat": HOME[0], "lon": HOME[1], "acc": 30}).json()
    assert r["say"] == "Reminder, sir: buy milk." and r["audio"] and said == [r["say"]]


def test_registered_and_allowed_locked():
    belt = orch_mod.ToolBelt(SkillRegistry("skills").discover(), orch_mod.MCPManager())
    for t in ("place_remember", "where_am_i", "place_reminder", "location_tracking", "location_history"):
        assert belt.has(t)
    assert {"place_remember", "where_am_i", "place_reminder"} <= orch_mod.LOCKED_OK
    assert "location_history" not in orch_mod.LOCKED_OK
