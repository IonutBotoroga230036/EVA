"""Multi-step commands (v0.2.5 milestone 3) and the truthfulness fixes from the Sep 26 voice log.

A fake Ollama answers the planner, the per-step decisions, and the final answer. A test skill records every
call with timestamps, so the tests can see what ran, in which order, and what ran at the same time.
"""

import asyncio
import json
import time

import httpx
import pytest

import core.orchestrator_hybrid as orch_mod
from core import multistep
from core.memory.cortex import Cortex
from core.orchestrator_hybrid import _CLAIM, HybridOrchestrator
from core.orchestrator_hybrid import fast_path as _REAL_FAST_PATH
from skills.registry import SkillRegistry
from tests.helpers import make_skill

TOOLS_PY = r'''
import json, threading, time
CALLS = []
_L = threading.Lock()
def _rec(name, **kw):
    with _L:
        CALLS.append({"tool": name, "args": kw, "t0": time.monotonic()})
def _slow(): time.sleep(0.3)
def lights_set(color="", **_):
    _rec("lights_set", color=color); _slow()
    return {"result": json.dumps({"ok": True}), "say": f"Lights {color}."}
def music_play(what="", **_):
    _rec("music_play", what=what); _slow()
    return {"result": json.dumps({"ok": True}), "say": f"Playing {what}."}
def music_volume(level=0, **_):
    _rec("music_volume", level=level)
    return {"result": json.dumps({"ok": True}), "say": f"Spotify at {level}."}
def pc_volume(level=0, **_):
    _rec("pc_volume", level=level); _slow()
    return {"result": json.dumps({"ok": True}), "say": f"Volume {level}."}
def mail_read(who="", **_):
    _rec("mail_read", who=who)
    return {"result": json.dumps({"from": who, "subject": "Friday?", "body": "Are you free on Friday?"})}
def mail_draft(to="", instructions="", **_):
    _rec("mail_draft", to=to, instructions=instructions)
    return {"result": json.dumps({"drafted": True}), "say": f"The draft to {to} is in Gmail, sir."}
def cal_add(title="", **_):
    _rec("cal_add", title=title)
    return {"result": json.dumps({"added": title}), "say": f"Added {title}, sir."}
def remind_set(text="", **_):
    _rec("remind_set", text=text)
    return {"result": json.dumps({"set": text}), "say": f"Reminder set: {text}, sir."}
def forecast(city="", **_):
    _rec("forecast", city=city)
    return {"result": json.dumps({"city": city, "temp_c": 16}), "say": f"{city}: 16 degrees.", "exact": True}
def _t(name, **props):
    return {"type": "function", "function": {"name": name, "description": name.replace("_", " "),
            "parameters": {"type": "object", "properties": {k: {"type": v} for k, v in props.items()},
                           "required": list(props)}}}
TOOLS = [_t("lights_set", color="string"), _t("music_play", what="string"), _t("music_volume", level="integer"),
         _t("pc_volume", level="integer"), _t("mail_read", who="string"), _t("mail_draft", to="string", instructions="string"),
         _t("cal_add", title="string"), _t("remind_set", text="string"), _t("forecast", city="string")]
FUNCTIONS = {t["function"]["name"]: globals()[t["function"]["name"]] for t in TOOLS}
ACTIONS = ["lights_set", "music_play", "music_volume", "pc_volume", "mail_draft", "cal_add", "remind_set"]
CONFIRM = {"mail_draft": "draft a reply to {to}", "cal_add": "add {title} to your calendar"}
GUARDS = {"remind_set": r"\bremind", "forecast": r"\b(weather|forecast)\b"}
'''


@pytest.fixture(autouse=True)
def no_fast_path(monkeypatch):
    """The fast path runs BUILT-IN tools (system volume, Spotify). These tests only ever run the test skill."""
    monkeypatch.setattr(orch_mod, "fast_path", lambda *a, **k: None)


@pytest.fixture
def registry(tmp_path):
    make_skill(tmp_path / "skills", "multi_test", "Test tools for multi-step commands", tools_py=TOOLS_PY)
    return SkillRegistry(tmp_path / "skills").discover()


def calls(registry):
    mod = next(s for s in registry.enabled() if s.name == "multi_test")
    return mod.functions["lights_set"].__globals__["CALLS"]


def fake(plan=None, decisions=None, answer="Tom asks if you're free on Friday, sir."):
    """plan: steps for the planner, or None to fail it. decisions: {substring of the step: decision}."""
    seen = {"plan": 0, "decision_prompts": [], "answers": 0}

    def handler(request: httpx.Request):
        body = json.loads(request.content)
        fmt = body.get("format")
        props = (fmt or {}).get("properties", {})
        if "steps" in props:
            seen["plan"] += 1
            return httpx.Response(200, json={"message": {"content": json.dumps({"steps": plan or []})}})
        if "facts" in props:
            return httpx.Response(200, json={"message": {"content": json.dumps({"facts": []})}})
        if "tool" in props:
            last = body["messages"][-1]["content"].lower()
            seen["decision_prompts"].append(body["messages"][0]["content"] + "\n>>> " + last)
            d = next((v for k, v in (decisions or {}).items() if k in last), {"tool": "none"})
            return httpx.Response(200, json={"message": {"content": json.dumps(d)}})
        if fmt:
            return httpx.Response(200, json={"message": {"content": "{}"}})
        seen["answers"] += 1
        lines = [json.dumps({"message": {"content": w + " "}, "done": False}) for w in answer.split()]
        lines.append(json.dumps({"message": {"content": ""}, "done": True}))
        return httpx.Response(200, text="\n".join(lines))

    real = httpx.AsyncClient
    return (lambda *a, **k: real(transport=httpx.MockTransport(handler))), seen


def turn(o, text):
    async def go():
        evs = [e async for e in o.process_stream(text)]
        if o._bg:
            await asyncio.gather(*o._bg, return_exceptions=True)
        return evs
    return asyncio.run(go())


def final(evs):
    return [e for e in evs if e["type"] == "final"][-1]["text"]


def orch(tmp_path, registry):
    return HybridOrchestrator(session_id="m", cortex=Cortex(str(tmp_path / "c.db")), registry=registry)


# ------------------------------------------------------------ planning, no model
@pytest.mark.parametrize("text,multi", [
    ("lights red, play The Weeknd, Spotify at 50 and the PC at 100", True),
    ("read Tom's last emails and draft him a warm reply saying Friday works", True),
    ("turn the lights purple and then play some jazz", True),
    ("what's the weather in Breda and Tilburg", False),              # one command, two places
    ("Can you recommend me something to train my guitar skills? Maybe blues or maybe rock", False),
    ("what time is it", False),
    ("thanks, that's great", False),
])
def test_looks_multi(text, multi):
    assert multistep.looks_multi(text) is multi


def test_plan_validation_drops_invented_steps_and_duplicates():
    said = "lights red and play The Weeknd"
    raw = {"steps": [{"command": "turn the lights red", "uses_previous": True},
                     {"command": "play The Weeknd", "uses_previous": False},
                     {"command": "play The Weeknd", "uses_previous": False},
                     {"command": "delete all my emails", "uses_previous": False},
                     {"command": "set the volume to 80", "uses_previous": False}]}
    steps = multistep.parse_plan(raw, said)
    assert [s["command"] for s in steps] == ["turn the lights red", "play The Weeknd"]
    assert steps[0]["uses_previous"] is False                       # the first step has nothing before it


def test_a_one_step_plan_means_a_normal_turn():
    assert multistep.parse_plan({"steps": [{"command": "turn the lights red", "uses_previous": False}]},
                                "lights red and bright") is None
    assert multistep.parse_plan({}, "x and y") is None


def test_grounding_keeps_filled_in_fragments():
    assert multistep.grounded("set the Spotify volume to 50", "Spotify at 50 and the PC at 100")
    assert not multistep.grounded("set the Spotify volume to 70", "Spotify at 50 and the PC at 100")


# ------------------------------------------------------------ running several commands
PLAN_4 = [{"command": "turn the lights red", "uses_previous": False},
          {"command": "play The Weeknd", "uses_previous": False},
          {"command": "set the Spotify volume to 50", "uses_previous": False},
          {"command": "set the PC volume to 100", "uses_previous": False}]
DECIDE_4 = {"lights red": {"tool": "lights_set", "color": "red"},
            "play the weeknd": {"tool": "music_play", "what": "The Weeknd"},
            "spotify volume": {"tool": "music_volume", "level": 50},
            "pc volume": {"tool": "pc_volume", "level": 100}}


def test_four_commands_in_one_sentence(tmp_path, registry, monkeypatch):
    client, seen = fake(PLAN_4, DECIDE_4)
    monkeypatch.setattr(orch_mod.httpx, "AsyncClient", client)
    calls(registry).clear()
    t0 = time.monotonic()
    evs = turn(orch(tmp_path, registry), "lights red, play The Weeknd, Spotify at 50 and the PC at 100")
    elapsed = time.monotonic() - t0
    assert final(evs) == "Lights red. Playing The Weeknd. Spotify at 50. Volume 100."
    assert seen["answers"] == 0                                      # all exact lines, no model rephrasing
    names = [c["tool"] for c in calls(registry)]
    assert sorted(names) == ["lights_set", "music_play", "music_volume", "pc_volume"]
    assert names.index("music_play") < names.index("music_volume")   # same family: in order
    assert elapsed < 0.85                                            # three 0.3 s tools ran side by side
    assert [e["type"] for e in evs].count("ack") == 1                # one spoken ack for the batch


def test_dependent_step_sees_the_earlier_result_and_asks_before_drafting(tmp_path, registry, monkeypatch):
    plan = [{"command": "read Tom's last email", "uses_previous": False},
            {"command": "draft him a warm reply saying Friday works", "uses_previous": True}]
    decide = {"read tom": {"tool": "mail_read", "who": "Tom"},
              "draft him": {"tool": "mail_draft", "to": "Tom", "instructions": "warm, Friday works"}}
    client, seen = fake(plan, decide)
    monkeypatch.setattr(orch_mod.httpx, "AsyncClient", client)
    calls(registry).clear()
    o = orch(tmp_path, registry)
    evs = turn(o, "read Tom's last email and draft him a warm reply saying Friday works")
    draft_prompt = next(p for p in seen["decision_prompts"] if ">>> draft him" in p)
    assert "Are you free on Friday?" in draft_prompt                 # the email was in view for the draft
    text = final(evs)
    assert text.startswith("Tom asks if you're free on Friday, sir.")
    assert "Just to confirm, sir: draft a reply to Tom. Shall I go ahead?" in text
    assert [c["tool"] for c in calls(registry)] == ["mail_read"]     # nothing drafted without a yes
    evs = turn(o, "yes")
    assert final(evs) == "The draft to Tom is in Gmail, sir."
    assert calls(registry)[-1]["tool"] == "mail_draft"


def test_two_confirmations_are_asked_one_after_the_other(tmp_path, registry, monkeypatch):
    plan = [{"command": "add dinner with Mom to my calendar", "uses_previous": False},
            {"command": "draft Tom a reply saying yes", "uses_previous": False}]
    decide = {"dinner with mom": {"tool": "cal_add", "title": "Dinner with Mom"},
              "draft tom": {"tool": "mail_draft", "to": "Tom", "instructions": "yes"}}
    client, _ = fake(plan, decide)
    monkeypatch.setattr(orch_mod.httpx, "AsyncClient", client)
    calls(registry).clear()
    o = orch(tmp_path, registry)
    assert final(turn(o, "add dinner with Mom to my calendar and draft Tom a reply saying yes")) == \
        "Just to confirm, sir: add Dinner with Mom to your calendar. Shall I go ahead?"
    assert final(turn(o, "yes")) == "Added Dinner with Mom, sir. Next, sir: draft a reply to Tom. Shall I go ahead?"
    assert final(turn(o, "no")) == "Cancelled, sir."
    assert [c["tool"] for c in calls(registry)] == ["cal_add"]


def test_a_step_she_cannot_do_is_said_plainly(tmp_path, registry, monkeypatch):
    plan = [{"command": "turn the lights red", "uses_previous": False},
            {"command": "book me a pizza", "uses_previous": False}]
    client, _ = fake(plan, {"lights red": {"tool": "lights_set", "color": "red"}})
    monkeypatch.setattr(orch_mod.httpx, "AsyncClient", client)
    text = final(turn(orch(tmp_path, registry), "turn the lights red and book me a pizza"))
    assert text == 'Lights red. I didn\'t do "book me a pizza", sir; I wasn\'t sure how.'


def test_planner_failure_falls_back_to_one_command(tmp_path, registry, monkeypatch):
    client, seen = fake(plan=None, decisions={"lights": {"tool": "lights_set", "color": "red"}})
    monkeypatch.setattr(orch_mod.httpx, "AsyncClient", client)
    text = final(turn(orch(tmp_path, registry), "lights red, play The Weeknd, Spotify at 50"))
    assert seen["plan"] == 1 and text == "Lights red."


# ------------------------------------------------------------ truthfulness (Sep 26 voice log)
def test_a_guard_blocked_action_asks_instead_of_pretending(tmp_path, registry, monkeypatch):
    """Log: 'guard: set_reminder blocked' then 'Reminder set for 15:00 tomorrow'. Nothing had been set."""
    client, seen = fake(decisions={"coffee": {"tool": "remind_set", "text": "coffee with Tom tomorrow at 15:00"}},
                        answer="Reminder set for 15:00 tomorrow: coffee with Tom, sir.")
    monkeypatch.setattr(orch_mod.httpx, "AsyncClient", client)
    calls(registry).clear()
    o = orch(tmp_path, registry)
    text = final(turn(o, "Add coffee with Tom tomorrow at 3 o'clock"))
    assert text == "Just to confirm, sir: run remind_set. Shall I go ahead?"
    assert calls(registry) == [] and seen["answers"] == 0 and o.pending["tool"] == "remind_set"


def test_follow_up_three_turns_later_still_reaches_the_data_tool(tmp_path, registry, monkeypatch):
    """Log: 'No, I want Monday' blocked get_weather and she made up a number."""
    decide = {"weather in nijmegen": {"tool": "forecast", "city": "Nijmegen"},
              "lights": {"tool": "lights_set", "color": "blue"},
              "i want monday": {"tool": "forecast", "city": "Nijmegen"}}
    client, _ = fake(decisions=decide)
    monkeypatch.setattr(orch_mod.httpx, "AsyncClient", client)
    calls(registry).clear()
    o = orch(tmp_path, registry)
    turn(o, "what's the weather in Nijmegen")
    turn(o, "lights blue")
    assert final(turn(o, "No, I want Monday")) == "Nijmegen: 16 degrees."
    assert [c["tool"] for c in calls(registry)] == ["forecast", "lights_set", "forecast"]


@pytest.mark.parametrize("text,claims", [
    ("Reminder set for 15:00 tomorrow: coffee with Tom, sir.", True),
    ("Reminder set for tomorrow at 15:00: Coffee with Tom, no ads, sir.", True),
    ("Added Coffee with Tom to your calendar, sir.", True),
    ("Done, sir.", True),
    ("Tomorrow in Breda it's 16 degrees, sir.", False),
    ("Shall I set a reminder for that, sir?", False),
])
def test_claim_guard_catches_the_phrasings_from_the_log(text, claims):
    assert bool(_CLAIM.search(text)) is claims


def test_spotify_volume_never_changes_the_pc_volume():
    """Only decides; nothing is executed."""
    from core.orchestrator_hybrid import ToolBelt
    belt = ToolBelt(None, None)
    belt.by_name.setdefault("spotify_volume", {})
    assert _REAL_FAST_PATH("set the Spotify volume to 50", belt) == ("spotify_volume", {"level": 50})
    assert _REAL_FAST_PATH("set the volume to 40", belt) == ("set_volume", {"level": 40})
