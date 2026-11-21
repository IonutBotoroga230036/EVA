"""Nov 20 log: "Can you stop the music in about 20 minutes?" stopped it right away ('about' wasn't expected), and
the correction "No, not now, I said in 20 minutes" was planned as the command "No, not now, I said"."""

import asyncio
import json
import time

import httpx
import pytest

import core.later as later
import core.orchestrator_hybrid as orch_mod
from core.memory.cortex import Cortex
from skills.registry import SkillRegistry


@pytest.mark.parametrize("text,clean,secs", [
    ("Can you stop the music in about 20 minutes?", "Can you stop the music", 1200),
    ("stop the music in around 10 minutes", "stop the music", 600),
    ("stop the music in like 2 minutes", "stop the music", 120),
    ("turn the lights off in roughly half an hour", "turn the lights off", 1800),
    ("stop the music in twenty-five minutes", "stop the music", 1500),
    ("stop the music in an hour and a half", "stop the music", 5400),
    ("stop the music in 1 hour and 30 minutes", "stop the music", 5400),
    ("lights off 10 minutes from now", "lights off", 600),
    ("lights off in 10 minutes' time", "lights off", 600),
    ("turn it off in a couple of minutes", "turn it off", 120),
    ("In 20 minutes stop the music", "stop the music", 1200),
    ("play 20 minutes of jazz", "play 20 minutes of jazz", None),              # a length, not a delay
    ("remind me in about 20 minutes to call mom", "remind me in about 20 minutes to call mom", None),
])
def test_how_people_actually_say_later(text, clean, secs):
    assert later.split_delay(text) == (clean, secs)


@pytest.mark.parametrize("text,yes", [
    ("No, not now, I said", True), ("not now", True), ("no, I meant", True), ("wait, do it", True),
    ("stop the music", False), ("turn the lights off", False), ("", False),
])
def test_what_counts_as_a_correction(text, yes):
    assert later.is_correction(text) is yes


def test_tidy_turns_a_request_into_a_plan():
    assert later.tidy("Can you stop the music") == "stop the music"
    assert later.tidy("Eva, could you please turn the lights off please") == "turn the lights off"


def fake_ollama(decide):
    def handler(request):
        body = json.loads(request.content)
        props = (body.get("format") or {}).get("properties", {})
        if "steps" in props:
            return httpx.Response(200, json={"message": {"content": json.dumps({"steps": []})}})
        if "facts" in props:
            return httpx.Response(200, json={"message": {"content": json.dumps({"facts": []})}})
        if "tool" in props:
            last = body["messages"][-1]["content"].lower()
            d = next((v for k, v in decide.items() if k in last), {"tool": "none"})
            return httpx.Response(200, json={"message": {"content": json.dumps(d)}})
        if body.get("format"):
            return httpx.Response(200, json={"message": {"content": "{}"}})
        return httpx.Response(200, text=json.dumps({"message": {"content": "ok"}, "done": True}))
    real = httpx.AsyncClient
    return lambda *a, **k: real(transport=httpx.MockTransport(handler))


@pytest.fixture
def eva(tmp_path, monkeypatch):
    ran = []

    async def aexecute(self, name, args):
        ran.append((name, dict(args)))
        return {"result": json.dumps({"ok": True}), "say": "Done, sir."}

    monkeypatch.setattr(orch_mod.ToolBelt, "aexecute", aexecute)
    monkeypatch.setattr(orch_mod, "fast_path", lambda *a, **k: None)
    monkeypatch.setattr(orch_mod.httpx, "AsyncClient", fake_ollama(
        {"stop the music": {"tool": "media_control", "action": "playpause"},
         "lights off": {"tool": "lights_power", "on": False}}))
    o = orch_mod.HybridOrchestrator(session_id="n", cortex=Cortex(str(tmp_path / "c.db")),
                                    registry=SkillRegistry("skills").discover())
    return o, ran


def turn(o, text):
    async def go():
        evs = [e async for e in o.process_stream(text)]
        if o._bg:
            await asyncio.gather(*o._bg, return_exceptions=True)
        return [e for e in evs if e["type"] == "final"][-1]["text"]
    return asyncio.run(go())


def test_about_20_minutes_is_planned_not_done(eva):
    o, ran = eva
    text = turn(o, "Can you stop the music in about 20 minutes?")
    assert text.startswith("Okay, sir: stop the music in 20 minutes, at ") and ran == []
    (item,) = later.get_later().pending()
    assert item["tool"] == "media_control" and 1190 < item["due"] - time.time() <= 1200


def test_no_not_now_undoes_and_plans_the_previous_command(eva):
    o, ran = eva
    turn(o, "stop the music")
    assert ran == [("media_control", {"action": "playpause"})]
    text = turn(o, "No, not now, I said in 20 minutes.")
    assert ran[-1] == ("media_control", {"action": "playpause"}) and len(ran) == 2       # music back on
    assert "put it back" in text and "stop the music in 20 minutes" in text
    (item,) = later.get_later().pending()
    assert item["text"] == "stop the music" and item["tool"] == "media_control"


def test_correcting_a_plan_moves_it_instead_of_adding_a_second(eva):
    o, ran = eva
    turn(o, "turn the lights off in 5 minutes")
    text = turn(o, "no, I said in 20 minutes")
    (item,) = later.get_later().pending()
    assert ran == [] and "moved" in text and 1190 < item["due"] - time.time() <= 1200


def test_a_correction_with_nothing_before_it_asks(eva):
    o, ran = eva
    assert turn(o, "no, not now, in 20 minutes") == "What should I do in 20 minutes, sir?"
    assert ran == [] and later.get_later().pending() == []
