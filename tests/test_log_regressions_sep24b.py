"""Every bug from the Sep 24 evening log, replayed with Ionut's exact words."""

import asyncio
import json
import time

import pytest

import core.orchestrator_hybrid as orch_mod
from core.memory.cortex import Cortex
from core.orchestrator_hybrid import HybridOrchestrator, ToolBelt, args_for, fast_path
from core.tools_native import resolve_site, tool_open_app
from skills.registry import SkillRegistry
from tests.helpers import make_skill
from tests.test_orchestrator import fake_ollama, run_turn


@pytest.fixture
def belt():
    return ToolBelt(SkillRegistry("skills").discover())


def orch(tmp_path, registry=None):
    return HybridOrchestrator(session_id="t", cortex=Cortex(str(tmp_path / "c.db")),
                              registry=registry or SkillRegistry("skills").discover())


SAY_TOOLS = '''
import json, time
TOOLS = [{"type": "function", "function": {"name": "act", "description": "Do an action.",
          "parameters": {"type": "object", "properties": {"x": {"type": "string"}}}}},
         {"type": "function", "function": {"name": "look", "description": "Look at something.",
          "parameters": {"type": "object", "properties": {}}}},
         {"type": "function", "function": {"name": "slow", "description": "Slow info tool.",
          "parameters": {"type": "object", "properties": {}}}}]
def act(x="", **_):
    return {"result": json.dumps({"done": x}), "say": "Paused, sir."}
def look(**_):
    return {"result": json.dumps({"saw": "a code editor"}), "say": "Here's what I see, sir: a code editor"}
def slow(**_):
    time.sleep(0.4)
    return {"result": json.dumps({"ok": 1})}
FUNCTIONS = {"act": act, "look": look, "slow": slow}
ACKS = {"slow": "One moment, sir."}
ACTIONS = ["act"]
'''


# ---------------------------------------------------------------- "open YouTube" opened open_website.com
def test_open_youtube_goes_to_youtube(belt):
    assert fast_path("can you open YouTube", belt) == ("open_website", {"site": "youtube"})
    assert fast_path("open vs code", belt) == ("open_app", {"app": "vs code"})
    assert fast_path("open spotify", belt) == ("open_app", {"app": "spotify"})


def test_tool_name_as_argument_is_discarded(belt):
    assert args_for(belt.by_name["open_website"], {"tool": "open_website", "site": "open_website"},
                    belt.names()) == {}


@pytest.mark.parametrize("site,ok", [("open_website", False), ("", False), ("https://bad_host.com", False),
                                     ("youtube", True), ("github.com", True)])
def test_resolve_site_never_guesses_garbage(site, ok):
    url, _ = resolve_site(site)
    assert (url is not None) == ok
    assert "open_website" not in (url or "")


@pytest.mark.parametrize("app", ["calc & del C:\\\\", "open_app", "rm -rf /", "notepad; shutdown"])
def test_open_app_refuses_shell_injection_and_unknowns(app):
    assert "error" in json.loads(tool_open_app(app)["result"])


def test_cut_off_sentence_cannot_trigger_an_action(tmp_path, monkeypatch):
    client, seen = fake_ollama(decisions=[{"tool": "open_website", "site": "youtube"}], answer="Of course, sir.")
    monkeypatch.setattr(orch_mod.httpx, "AsyncClient", client)
    o = orch(tmp_path)
    run_turn(o, "I also need you to make me")          # the guard blocks: nothing asks to open anything
    assert "open_website" not in [e["tool"] for e in o.bus.recent() if e["channel"] == "tool.executed"]


# ---------------------------------------------------------------- "Volume set to 0, sir." after a pause
def test_actions_speak_their_own_confirmation_without_the_llm(tmp_path, monkeypatch):
    make_skill(tmp_path / "s", "act", "Do actions", tools_py=SAY_TOOLS)
    client, seen = fake_ollama(decisions=[{"tool": "act", "x": "pause"}], answer="Volume set to 0, sir.")
    monkeypatch.setattr(orch_mod.httpx, "AsyncClient", client)
    events = run_turn(orch(tmp_path, SkillRegistry(tmp_path / "s").discover()), "do the thing please")
    assert events[-1]["text"] == "Paused, sir." and seen["answers"] == 0


# ---------------------------------------------------------------- "I cannot directly see your screen"
def test_denial_after_a_successful_look_is_replaced(tmp_path, monkeypatch):
    make_skill(tmp_path / "s", "look", "Look at things", tools_py=SAY_TOOLS)
    client, _ = fake_ollama(decisions=[{"tool": "look"}], answer="I cannot directly see your screen, sir.")
    monkeypatch.setattr(orch_mod.httpx, "AsyncClient", client)
    events = run_turn(orch(tmp_path, SkillRegistry(tmp_path / "s").discover()), "have you seen it")
    assert events[-1]["text"] == "Here's what I see, sir: a code editor"


# ---------------------------------------------------------------- web search for "how do you like it"
@pytest.mark.parametrize("text", ["how do you like it so far", "do you enjoy functioning like this",
                                  "what would you like to have a skill for", "how are you feeling",
                                  "thanks", "that's better"])
def test_small_talk_never_calls_tools(tmp_path, monkeypatch, text):
    client, seen = fake_ollama(decisions=[{"tool": "web_search", "query": "x"}], answer="Very well, sir.")
    monkeypatch.setattr(orch_mod.httpx, "AsyncClient", client)
    run_turn(orch(tmp_path), text)
    assert seen["decisions"] == 0


# ---------------------------------------------------------------- add_instruction wrote a paragraph into EVA.md
def test_add_instruction_needs_instruction_words(tmp_path, monkeypatch):
    client, _ = fake_ollama(decisions=[{"tool": "add_instruction", "text": "I would like a skill"}], answer="Sure.")
    monkeypatch.setattr(orch_mod.httpx, "AsyncClient", client)
    o = orch(tmp_path)
    run_turn(o, "tell me which new skill would help you assist me")
    assert "add_instruction" not in [e["tool"] for e in o.bus.recent() if e["channel"] == "tool.executed"]


# ---------------------------------------------------------------- notes: invented content
def test_write_a_note_that_goes_to_quick_note_verbatim(belt):
    assert fast_path("I need you to write a note that I need to send Tom a message", belt) == \
        ("obsidian_quick_note", {"text": "I need to send Tom a message"})


def test_project_note_is_created_empty_not_invented(belt):
    assert fast_path("make a project note about ZippZapp branding", belt) == \
        ("obsidian_write", {"title": "ZippZapp branding", "content": "", "folder": "Projects"})


# ---------------------------------------------------------------- "what did I tell you about Tom" -> nothing
def test_recall_searches_past_conversations(tmp_path, monkeypatch, belt):
    assert fast_path("I was wondering what did I tell you about Tom", belt) == ("recall_memory", {"query": "Tom"})
    c = Cortex(str(tmp_path / "c.db"))
    c.log_turn("s", "user", "I need to send Tom a message")
    c.log_turn("s", "user", "what did I tell you about Tom")          # the question itself is not an answer
    monkeypatch.setattr("core.memory.cortex._cortex", c)
    monkeypatch.setenv("EVA_VAULT", str(tmp_path / "vault"))
    mem = SkillRegistry("skills").discover().functions()["recall_memory"]
    res = json.loads(mem(query="Tom")["result"])
    assert [x["you_said"] for x in res["from_past_conversations"]] == ["I need to send Tom a message"]


# ---------------------------------------------------------------- ack arrived only after the tool finished
def test_ack_is_sent_before_the_tool_runs(tmp_path, monkeypatch):
    make_skill(tmp_path / "s", "slow", "Slow info", tools_py=SAY_TOOLS)
    client, _ = fake_ollama(decisions=[{"tool": "slow"}], answer="Done, sir.")
    monkeypatch.setattr(orch_mod.httpx, "AsyncClient", client)
    o = orch(tmp_path, SkillRegistry(tmp_path / "s").discover())

    async def go():
        t0, stamps = time.perf_counter(), []
        async for ev in o.process_stream("run the slow one"):
            stamps.append((ev["type"], time.perf_counter() - t0))
        return stamps
    stamps = asyncio.run(go())
    ack_t = next(t for k, t in stamps if k == "ack")
    final_t = next(t for k, t in stamps if k == "final")
    assert ack_t < 0.2 and final_t >= 0.4


# ---------------------------------------------------------------- confirmation gate
def test_bulk_forget_asks_first_then_yes_runs_it(tmp_path, monkeypatch):
    client, _ = fake_ollama(decisions=[], answer="x")
    monkeypatch.setattr(orch_mod.httpx, "AsyncClient", client)
    c = Cortex(str(tmp_path / "c.db"))
    c.remember("Likes jazz")
    monkeypatch.setattr("core.memory.cortex._cortex", c)
    o = HybridOrchestrator(session_id="g", cortex=c, registry=SkillRegistry("skills").discover())
    ask = run_turn(o, "delete what you just remembered")
    assert "Shall I go ahead" in ask[-1]["text"] and any(e["type"] == "widget" for e in ask)
    assert c.stats()["facts"] == 1                                      # nothing happened yet
    done = run_turn(o, "yes")
    assert c.stats()["facts"] == 0 and "forgotten" in done[-1]["text"]


def test_no_cancels_and_other_replies_drop_the_pending_action(tmp_path, monkeypatch):
    client, _ = fake_ollama(decisions=[], answer="It's jazz, sir.")
    monkeypatch.setattr(orch_mod.httpx, "AsyncClient", client)
    c = Cortex(str(tmp_path / "c.db"))
    c.remember("Likes jazz")
    monkeypatch.setattr("core.memory.cortex._cortex", c)
    o = HybridOrchestrator(session_id="g2", cortex=c, registry=SkillRegistry("skills").discover())
    run_turn(o, "forget everything you just learned")
    assert run_turn(o, "no")[-1]["text"] == "Cancelled, sir." and c.stats()["facts"] == 1
    run_turn(o, "forget everything you just learned")
    run_turn(o, "what music do I like?")                                # not a yes: pending is dropped
    assert o.pending is None and c.stats()["facts"] == 1


@pytest.mark.parametrize("text,small", [("great", True), ("thanks a lot", True), ("that's better", True),
                                        ("great, now open youtube and play music", False),
                                        ("ok so what's the weather tomorrow in Tilburg", False),
                                        ("how do you like it so far", True)])
def test_interjections_only_count_when_they_are_the_whole_message(text, small):
    from core.orchestrator_hybrid import is_small_talk
    assert is_small_talk(text) is small
