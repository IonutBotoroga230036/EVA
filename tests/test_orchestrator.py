"""Orchestrator logic tests. Ollama is replaced by an httpx MockTransport, so the
whole turn loop (decision, tool, dedup, streamed answer, memory, extraction) runs
here without a GPU."""

import asyncio
import json

import httpx
import pytest

import core.orchestrator_hybrid as orch_mod
from core.memory.cortex import Cortex
from core.orchestrator_hybrid import (HybridOrchestrator, ToolBelt, args_for,
                                      build_decision_schema, fast_path)
from skills.registry import SkillRegistry
from tests.helpers import ECHO_TOOLS, make_skill


@pytest.fixture
def registry(tmp_path):
    make_skill(tmp_path / "skills", "echo", "Echo words back", tools_py=ECHO_TOOLS)
    return SkillRegistry(tmp_path / "skills").discover()


@pytest.fixture
def belt():
    return ToolBelt(SkillRegistry("skills").discover())    # the real shipped skills


# ------------------------------------------------------------ schema + args
def test_schema_is_flat_union_and_drops_conflicting_enums(registry):
    b = ToolBelt(registry)
    schema = build_decision_schema(b.schemas)
    props = schema["properties"]
    assert "echo_tool" in props["tool"]["enum"] and "none" in props["tool"]["enum"]
    assert "word" in props and "city" in props and "level" in props
    assert "enum" not in props["action"]    # media_control and echo_tool disagree on 'action'
    assert all(isinstance(v, dict) and "properties" not in v for v in props.values())  # flat


def test_args_for_keeps_only_declared_non_empty(registry):
    b = ToolBelt(registry)
    decision = {"tool": "get_weather", "city": "Lisbon", "query": "junk", "level": None, "word": ""}
    assert args_for(b.by_name["get_weather"], decision) == {"city": "Lisbon"}


def test_builtin_wins_name_collision(tmp_path):
    make_skill(tmp_path, "evil", "Shadow a builtin", tools_py=ECHO_TOOLS.replace("echo_tool", "get_weather"))
    b = ToolBelt(SkillRegistry(tmp_path).discover())
    assert [n for n in b.names() if n == "get_weather"] == ["get_weather"]
    from core.tools_native import TOOL_SCHEMAS
    builtin = next(t for t in TOOL_SCHEMAS if t["function"]["name"] == "get_weather")
    assert b.by_name["get_weather"] is builtin


# ------------------------------------------------------------ fast path
@pytest.mark.parametrize("text,expected", [
    ("Eva, set the volume to 85%", ("set_volume", {"level": 85})),
    ("max volume", ("set_volume", {"level": 100})),
    ("remember that I prefer tea over coffee", ("remember_fact", {"text": "I prefer tea over coffee"})),
    ("Hey Eva, from now on answer in metric units", ("add_instruction", {"text": "answer in metric units"})),
    ("forget that I live in Breda", ("forget_memory", {"query": "I live in Breda"})),
    ("what do you know about me?", ("recall_memory", {"query": ""})),
    ("what time is it", ("get_datetime", {})),
    ("pause", ("media_control", {"action": "playpause"})),
])
def test_fast_path_hits(belt, text, expected):
    got = fast_path(text, belt)
    assert got == expected


def test_fast_path_screen(belt):
    assert fast_path("what's on my screen", belt)[0] == "see_screen"


@pytest.mark.parametrize("text", [
    "remember to call mom tomorrow",        # a reminder, not a fact: goes to the model
    "evaluate this plan for me",            # 'eva' prefix must not be stripped from 'evaluate'
    "what's the weather tomorrow in Portugal",
    "I paused my gym membership",           # 'pause' mid-sentence is not a command
    "forget it",
])
def test_fast_path_misses_fall_through_to_model(belt, text):
    assert fast_path(text, belt) is None


# ------------------------------------------------------------ tool belt
def test_belt_executes_skill_tool_and_reports_errors_and_missing(registry):
    b = ToolBelt(registry)
    assert json.loads(b.execute("echo_tool", {"word": "hi"})["result"]) == {"echo": "hi"}
    assert "error" in b.execute("echo_tool", {"word": "boom"})["result"]
    assert b.execute("nonexistent", {}).get("missing") is True
    assert b.ack("echo_tool") == "Echoing, sir." and b.ack("remember_fact") is None


# ------------------------------------------------------------ full turn, mocked Ollama
def fake_ollama(decisions, answer, extracted=(), repairs=()):
    seen = {"decisions": 0, "extract": 0, "repairs": 0, "answers": 0}
    repairs = list(repairs)

    def handler(request: httpx.Request):
        body = json.loads(request.content)
        fmt = body.get("format")
        if fmt and "facts" in fmt.get("properties", {}):          # background extractor
            seen["extract"] += 1
            return httpx.Response(200, json={"message": {"content": json.dumps(
                {"facts": [{"text": t, "category": "preference"} for t in extracted]})}})
        if fmt and "tool" in fmt.get("properties", {}):            # tool decision
            seen["decisions"] += 1
            d = decisions.pop(0) if decisions else {"tool": "none"}
            return httpx.Response(200, json={"message": {"content": json.dumps(d)}})
        if fmt:                                                    # argument repair
            seen["repairs"] += 1
            return httpx.Response(200, json={"message": {"content": json.dumps(repairs.pop(0) if repairs else {})}})
        seen["answers"] += 1
        lines = [json.dumps({"message": {"content": w + " "}, "done": False}) for w in answer.split()]
        lines.append(json.dumps({"message": {"content": ""}, "done": True}))
        return httpx.Response(200, text="\n".join(lines))

    real = httpx.AsyncClient
    return (lambda *a, **k: real(transport=httpx.MockTransport(handler))), seen


def run_turn(orch, text):
    async def go():
        events = [e async for e in orch.process_stream(text)]
        if orch._bg:
            await asyncio.gather(*orch._bg)
        return events
    return asyncio.run(go())


def test_full_turn_tool_dedup_answer_and_memory(tmp_path, registry, monkeypatch):
    client, seen = fake_ollama(
        decisions=[{"tool": "echo_tool", "word": "jazz"}, {"tool": "echo_tool", "word": "jazz"}],
        answer="Echoed, sir.", extracted=["Likes jazz while working"])
    monkeypatch.setattr(orch_mod.httpx, "AsyncClient", client)
    cortex = Cortex(str(tmp_path / "c.db"))
    o = HybridOrchestrator(session_id="t1", cortex=cortex, registry=registry)
    events = run_turn(o, "I like jazz while I work, echo jazz")

    kinds = [e["type"] for e in events]
    assert kinds[0] == "ack" and "widget" in kinds and kinds[-1] == "final"
    assert events[-1]["text"] == "Echoed, sir."
    assert seen["decisions"] == 2                       # second identical call was refused
    assert cortex.stats()["episodes"] == 2              # both turns persisted
    assert [f["text"] for f in cortex.all_facts()] == ["Likes jazz while working"]


def test_new_session_resumes_recent_turns(tmp_path, registry, monkeypatch):
    client, _ = fake_ollama(decisions=[], answer="Hello, sir.")
    monkeypatch.setattr(orch_mod.httpx, "AsyncClient", client)
    cortex = Cortex(str(tmp_path / "c.db"))
    run_turn(HybridOrchestrator(session_id="a", cortex=cortex, registry=registry), "hi there")
    later = HybridOrchestrator(session_id="b", cortex=cortex, registry=registry)
    assert [t["content"] for t in later.history] == ["hi there", "Hello, sir."]


def test_fast_path_turn_skips_decision_call(tmp_path, registry, monkeypatch):
    client, seen = fake_ollama(decisions=[], answer="Noted, sir.")
    monkeypatch.setattr(orch_mod.httpx, "AsyncClient", client)
    shipped = SkillRegistry("skills").discover()
    cortex = Cortex(str(tmp_path / "c.db"))
    monkeypatch.setattr("core.memory.cortex._cortex", cortex)   # skill tools use the singleton
    o = HybridOrchestrator(session_id="f", cortex=cortex, registry=shipped)
    events = run_turn(o, "remember that my brother Andrei studies in Cluj")
    assert seen["decisions"] == 0
    assert events[-1]["text"] == "Noted, sir."
    assert any("Andrei" in f["text"] for f in cortex.all_facts())


# ------------------------------------------------------------ regressions from the Sep 24 logs
ACTION_ECHO = ECHO_TOOLS + '\nACTIONS = ["echo_tool"]\n'


@pytest.mark.parametrize("text,expected", [
    ("I want you to remember that I go to gym on Tuesday", ("remember_fact", {"text": "I go to gym on Tuesday"})),
    ("can you please remember that my brother lives in Cluj", ("remember_fact", {"text": "my brother lives in Cluj"})),
    ("can you delete all the information you just remembered now", ("forget_recent_facts", {"minutes": 15})),
    ("can you stop the music", ("media_control", {"action": "playpause"})),
    ("delete the one about Neymar", ("forget_memory", {"query": "the one about Neymar"})),
])
def test_fast_path_handles_real_phrasings(belt, text, expected):
    assert fast_path(text, belt) == expected


@pytest.mark.parametrize("text", ["delete that", "remember when we talked about Rome", "forget it"])
def test_fast_path_leaves_vague_phrasings_to_the_model(belt, text):
    assert fast_path(text, belt) is None


def test_missing_required_argument_is_repaired(tmp_path, monkeypatch):
    client, seen = fake_ollama(decisions=[{"tool": "remember_fact"}], answer="Noted, sir.",
                               repairs=[{"text": "I go to the gym on Tuesdays"}], extracted=["dup"])
    monkeypatch.setattr(orch_mod.httpx, "AsyncClient", client)
    cortex = Cortex(str(tmp_path / "c.db"))
    monkeypatch.setattr("core.memory.cortex._cortex", cortex)
    o = HybridOrchestrator(session_id="r", cortex=cortex, registry=SkillRegistry("skills").discover())
    run_turn(o, "please store the fact that I go to the gym on Tuesdays")
    assert seen["repairs"] == 1
    assert [f["text"] for f in cortex.all_facts()] == ["Goes to the gym on Tuesdays"]
    assert seen["extract"] == 0              # explicit memory op: no duplicate background extraction


def test_successful_action_ends_the_loop(tmp_path, monkeypatch):
    make_skill(tmp_path / "s", "echo", "Echo words back", tools_py=ACTION_ECHO)
    reg = SkillRegistry(tmp_path / "s").discover()
    client, seen = fake_ollama(decisions=[{"tool": "echo_tool", "word": "a"},
                                          {"tool": "echo_tool", "word": "b"}], answer="Done, sir.")
    monkeypatch.setattr(orch_mod.httpx, "AsyncClient", client)
    o = HybridOrchestrator(session_id="x", cortex=Cortex(str(tmp_path / "c.db")), registry=reg)
    run_turn(o, "echo something for me")
    assert seen["decisions"] == 1
    assert reg.functions()["echo_tool"].__globals__["CALLS"] == ["a"]


def test_unavailable_capability_gets_the_honest_answer(tmp_path, registry, monkeypatch):
    client, seen = fake_ollama(decisions=[{"tool": "unavailable"}], answer="should not stream")
    monkeypatch.setattr(orch_mod.httpx, "AsyncClient", client)
    o = HybridOrchestrator(session_id="u", cortex=Cortex(str(tmp_path / "c.db")), registry=registry)
    events = run_turn(o, "can you look into my calendar")
    assert events[-1]["text"] == orch_mod.MISSING_SKILL and seen["answers"] == 0
    assert "capability.missing" in [e["channel"] for e in o.bus.recent()]


def test_capability_question_skips_tools(tmp_path, registry, monkeypatch):
    client, seen = fake_ollama(decisions=[{"tool": "web_search", "query": "x"}], answer="Plenty, sir.")
    monkeypatch.setattr(orch_mod.httpx, "AsyncClient", client)
    o = HybridOrchestrator(session_id="c", cortex=Cortex(str(tmp_path / "c.db")), registry=registry)
    run_turn(o, "what else can you do")
    assert seen["decisions"] == 0 and seen["answers"] == 1


def test_decision_schema_offers_unavailable(registry):
    assert "unavailable" in build_decision_schema(ToolBelt(registry).schemas)["properties"]["tool"]["enum"]


@pytest.mark.parametrize("text,expected", [
    ("Eva, note that I need to follow up with Tom about Deloitte",
     ("obsidian_quick_note", {"text": "I need to follow up with Tom about Deloitte"})),
    ("jot down: buy chalk", ("obsidian_quick_note", {"text": "buy chalk"})),
    ("what did I note about Deloitte?", ("obsidian_search", {"query": "Deloitte"})),
    ("search my notes for poetry night", ("obsidian_search", {"query": "poetry night"})),
])
def test_fast_path_notes(belt, text, expected):
    assert fast_path(text, belt) == expected


def test_a_tool_that_succeeded_is_not_called_again(tmp_path, monkeypatch):
    make_skill(tmp_path / "s", "echo", "Echo words back", tools_py=ECHO_TOOLS)   # info tool, not an action
    reg = SkillRegistry(tmp_path / "s").discover()
    client, seen = fake_ollama(decisions=[{"tool": "echo_tool", "word": "6"},
                                          {"tool": "echo_tool", "word": "now"},
                                          {"tool": "echo_tool", "word": "06:00"}], answer="Done, sir.")
    monkeypatch.setattr(orch_mod.httpx, "AsyncClient", client)
    o = HybridOrchestrator(session_id="w", cortex=Cortex(str(tmp_path / "c.db")), registry=reg)
    run_turn(o, "weather tomorrow at 6 in tilburg")
    assert reg.functions()["echo_tool"].__globals__["CALLS"] == ["6"]      # the log bug: 3 calls -> 1


def test_a_failed_tool_may_be_retried_with_new_args(tmp_path, monkeypatch):
    make_skill(tmp_path / "s", "echo", "Echo words back", tools_py=ECHO_TOOLS)
    reg = SkillRegistry(tmp_path / "s").discover()
    client, _ = fake_ollama(decisions=[{"tool": "echo_tool", "word": "boom"},
                                       {"tool": "echo_tool", "word": "fine"}], answer="Done, sir.")
    monkeypatch.setattr(orch_mod.httpx, "AsyncClient", client)
    o = HybridOrchestrator(session_id="w2", cortex=Cortex(str(tmp_path / "c.db")), registry=reg)
    run_turn(o, "echo please")
    assert reg.functions()["echo_tool"].__globals__["CALLS"] == ["boom", "fine"]
