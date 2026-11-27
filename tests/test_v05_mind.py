"""v0.5 one mind: sentences that need thinking get a reasoning loop with every tool. A scripted Claude plays the
mind; the local model is a fake that understands nothing; nothing real is installed or opened."""

import asyncio
import json
import os
from pathlib import Path

import httpx
import pytest

import core.orchestrator_hybrid as orch_mod
from core import mind, pc_tools
from core.agent.tasks import get_store
from core.memory.cortex import Cortex
from skills.registry import SkillRegistry

from tests.test_agent import T, Script


@pytest.fixture
def eva(tmp_path, monkeypatch):
    def handler(req):
        body = json.loads(req.content)
        props = (body.get("format") or {}).get("properties", {})
        if "steps" in props:
            return httpx.Response(200, json={"message": {"content": json.dumps({"steps": []})}})
        if "facts" in props:
            return httpx.Response(200, json={"message": {"content": json.dumps({"facts": []})}})
        if "tool" in props:
            return httpx.Response(200, json={"message": {"content": json.dumps({"tool": "none"})}})
        return httpx.Response(200, text=json.dumps({"message": {"content": "the old way"}, "done": True}))
    real = httpx.AsyncClient
    monkeypatch.setattr(orch_mod.httpx, "AsyncClient", lambda *a, **k: real(transport=httpx.MockTransport(handler)))
    monkeypatch.setattr(orch_mod, "ack_after", lambda: -1)
    o = orch_mod.HybridOrchestrator(registry=SkillRegistry("skills"))
    o.cortex = Cortex(str(tmp_path / "c.db"))
    o.history = []
    return o


@pytest.fixture
def think(monkeypatch):
    """The mind on, with a scripted Claude."""
    def make(turns):
        script = Script(turns)
        monkeypatch.setattr(mind, "allowed", lambda: True)
        import core.claude as claude_mod
        monkeypatch.setattr(claude_mod, "get_claude", lambda: script)
        return script
    return make


def say(o, text):
    async def go():
        out, events = "", []
        async for ev in o.process_stream(text):
            events.append(ev)
            if ev["type"] == "final":
                out = ev["text"]
        return out, events
    return asyncio.run(go())


def test_how_can_i_see_the_files_gets_options(eva, think, monkeypatch):
    """Nov 25 19:31: 'What should I do with the files?' read the whole summary out; the mind looks and offers."""
    monkeypatch.setattr(pc_tools, "default_app", lambda ext: "")
    s = think([[T("check_installed", file_type="stl")],
               ["Nothing on your PC opens STL files yet, sir. I can show the stand here, or install PrusaSlicer so "
                "you can open it on the PC. Which would you like?"]])
    reply, _ = say(eva, "How can I see the files you made?")
    assert reply.startswith("Nothing on your PC opens STL files yet, sir.")
    first = s.calls[0]
    assert first["model"] == "claude-haiku-5-5" and first["purpose"] == "mind" and first["cache"] is True
    assert first["effort"] == "low"                  # v0.8: no tool hint = talk, thinks briefly
    assert "How can I see the files you made?" in first["messages"][-1]["content"]
    assert "<situation>" in first["messages"][-1]["content"]
    res = s.calls[1]["messages"][-1]["content"][0]
    assert res["type"] == "tool_result" and '"opens_with": null' in res["content"]
    assert eva.history[-1] == {"role": "assistant", "content": reply}


def test_installing_asks_first_and_yes_runs_it(eva, think):
    think([["PrusaSlicer opens STL files and is free.",
            T("install_app", winget_id="Prusa3D.PrusaSlicer", name="PrusaSlicer")]])
    reply, events = say(eva, "Install something so I can look at the stand")
    assert reply == ("PrusaSlicer opens STL files and is free. Just to confirm, sir: install PrusaSlicer on your PC "
                     "with winget (Prusa3D.PrusaSlicer). Shall I go ahead?")
    assert any(e["type"] == "widget" and e["data"]["kind"] == "confirm" for e in events)
    assert eva.pending["tool"] == "install_app"
    reply, _ = say(eva, "yes")
    assert reply == "The install of PrusaSlicer didn't work, sir."           # tests never install (conftest)


def test_it_opens_the_stand_on_the_pc(eva, think, monkeypatch):
    t = get_store().new("design me a guitar stand")
    t.status = "done"
    get_store().save(t)
    stl = get_store().workspace(t.id) / "stand_base.stl"
    stl.write_text("solid x")
    opened = []
    monkeypatch.setattr(os, "startfile", opened.append, raising=False)
    monkeypatch.setattr(pc_tools, "default_app", lambda ext: "PrusaSlicer")
    think([[T("open_file", file="stand_base.stl")], ["It's opening in PrusaSlicer, sir."]])
    reply, _ = say(eva, "Can you open the base of the guitar stand for me?")
    assert opened == [str(stl)] and reply == "It's opening in PrusaSlicer, sir."


def test_think_harder_switches_to_sonnet(eva, think):
    s = think([[T("think_harder", why="a design question")], ["Lean it back about 15 degrees, sir."]])
    reply, _ = say(eva, "Why would my guitar stand tip over and how would you redesign it?")
    assert reply == "Lean it back about 15 degrees, sir."
    assert [c["model"] for c in s.calls] == ["claude-haiku-5-5", "claude-sonnet-5-5"]
    assert s.calls[1]["effort"] == "medium"


def test_if_claude_fails_the_old_way_answers(eva, think):
    think([RuntimeError("Claude API error 400: something odd")])     # v0.8: a dropped line says so instead
    reply, _ = say(eva, "What would you suggest for my evening?")
    assert reply == "the old way"


def test_short_small_talk_stays_local(eva, think):
    s = think([])                                   # any call would fail the script
    reply, _ = say(eva, "how are you?")
    assert s.calls == [] and reply


def test_a_reflex_never_reaches_the_mind(eva, think):
    s = think([])
    reply, _ = say(eva, "what time is it")
    assert s.calls == [] and reply


def test_understanding_can_hand_it_over(eva, think, monkeypatch):
    async def decide(client, skills, gathered, messages=None):
        return {"tool": "work_it_out", "_trusted": True}
    monkeypatch.setattr(eva, "_decide", decide)
    s = think([["I'd print it at the library, sir: it's free for students."]])
    reply, _ = say(eva, "Should I buy a 3D printer or use the one at the library?")
    assert reply.startswith("I'd print it at the library") and len(s.calls) == 1


def test_markdown_never_reaches_his_ears(eva, think):
    think([["**Two options:**\n- show it here\n- install a viewer"]])
    reply, _ = say(eva, "What are my options for viewing the model?")
    assert reply == "Two options: show it here install a viewer"


def test_her_tools_and_instructions_are_stable():
    from core.orchestrator_hybrid import ToolBelt
    b = ToolBelt(None, None)
    a, c = mind.tools_for(b.schemas), mind.tools_for(b.schemas)
    names = [t["name"] for t in a]
    assert a == c and "work_it_out" not in names and names[-1] == "think_harder"
    assert {"check_installed", "install_app", "open_file", "task_show", "task_start"} <= set(names)
    assert all(set(t) == {"name", "description", "input_schema"} for t in a)
    assert mind.system_prompt() == mind.system_prompt() and "Now:" not in mind.system_prompt()


def test_the_situation_knows_what_she_just_made():
    t = get_store().new("design me a guitar stand")
    t.status, t.summary, t.files = "done", "Two parts.", ["/x/stand_base.stl", "/x/stand_post.stl"]
    get_store().save(t)
    s = mind.situation()
    assert "Last finished task: 'guitar stand'" in s and "stand_base.stl, stand_post.stl" in s


def test_earlier_turns_alternate_and_end_with_this_sentence():
    hist = [{"role": "assistant", "content": "Hello"}, {"role": "user", "content": "a"},
            {"role": "user", "content": "b"}, {"role": "assistant", "content": "c"},
            {"role": "user", "content": "now"}]
    msgs = mind.build_messages(hist, "now", "Now: x", "", 10)
    assert [m["role"] for m in msgs] == ["user", "assistant", "user"]
    assert msgs[0]["content"] == "a\nb" and msgs[-1]["content"].endswith("now")


def test_local_only_or_no_key_means_no_mind(monkeypatch):
    from core.brain import get_brain
    assert mind.allowed() is False                  # tests have no key
    import core.claude as claude_mod

    class K:
        def available(self):
            return True
    monkeypatch.setattr(claude_mod, "get_claude", lambda: K())
    assert mind.allowed() is True
    get_brain().set_mode("local")                   # "Eva, local only"
    assert mind.allowed() is False


# ---------------------------------------------------------------- her hands on the PC
def test_open_file_only_opens_documents_from_tasks(monkeypatch):
    t = get_store().new("design me a guitar stand")
    ws = get_store().workspace(t.id)
    (ws / "run.exe").write_text("x")
    (ws / "run.py").write_text("x")
    (ws / "stand.stl").write_text("solid x")
    opened, edited = [], []
    monkeypatch.setattr(os, "startfile", opened.append, raising=False)
    monkeypatch.setattr(pc_tools, "default_app", lambda ext: "")
    monkeypatch.setattr(pc_tools, "editor", lambda: "/usr/bin/code")
    monkeypatch.setattr(pc_tools.subprocess, "Popen", lambda cmd, **k: edited.append(cmd))
    assert "aren't opened from here" in pc_tools.tool_open_file(file="run.exe", task="guitar stand")["result"]
    out = pc_tools.tool_open_file(file="run.py", task="guitar stand")   # v1.1: code opens in an editor, never runs
    assert edited == [["/usr/bin/code", str(ws / "run.py")]] and out["say"] == "Opening run.py in VS Code, sir."
    out = pc_tools.tool_open_file(file="stand.stl", task="guitar stand")
    assert opened == [] and out["say"] == "Nothing on your PC opens .stl files yet, sir."
    monkeypatch.setattr(pc_tools, "default_app", lambda ext: "PrusaSlicer")
    pc_tools.tool_open_file(file="stand.stl", task="guitar stand")
    assert opened == [str(ws / "stand.stl")]
    assert "isn't among" in pc_tools.tool_open_file(file="secret.txt", task="guitar stand")["result"]


def test_open_with_a_program_never_a_shell(monkeypatch):
    from core.agent import pc
    t = get_store().new("design me a guitar stand")
    (get_store().workspace(t.id) / "stand.stl").write_text("solid x")
    ran = []
    monkeypatch.setattr(pc_tools.subprocess, "Popen", lambda cmd, **k: ran.append(cmd))
    assert "isn't something" in pc_tools.tool_open_file(file="stand.stl", program="powershell")["result"]
    monkeypatch.setattr(pc.LocalPC, "find_program", lambda self, n: {"found": True, "path": "C:/P/prusa-slicer.exe"})
    out = pc_tools.tool_open_file(file="stand.stl", program="prusa-slicer")
    assert out["say"] == "Opening stand.stl in prusa-slicer, sir." and ran[0][0] == "C:/P/prusa-slicer.exe"


def test_check_installed(monkeypatch):
    from core.agent import pc
    monkeypatch.setattr(pc.LocalPC, "find_program", lambda self, n: {"found": n == "openscad", "path": ""})
    monkeypatch.setattr(pc_tools, "default_app", lambda ext: "")
    out = json.loads(pc_tools.tool_check_installed(program="blender", file_type=".STL")["result"])
    assert out == {"program": {"name": "blender", "installed": False, "path": ""},
                   "file_type": {"type": "stl", "opens_with": None}}


def test_installing_always_asks():
    from core.orchestrator_hybrid import ToolBelt
    b = ToolBelt(None, None)
    assert b.needs_confirm("install_app") and b.is_action("install_app")
    assert b.describe("install_app", {"winget_id": "Prusa3D.PrusaSlicer", "name": "PrusaSlicer"}) == \
        "install PrusaSlicer on your PC with winget (Prusa3D.PrusaSlicer)"


def test_the_mind_cant_make_up_his_calendar(eva, think):
    think([["Tomorrow looks clear, sir."]])
    reply, _ = say(eva, "Do you think I'll have time to print it tomorrow?")
    assert "Tomorrow looks clear" not in reply


def test_the_mind_cant_claim_what_it_didnt_do(eva, think):
    think([["Done, sir. I've installed PrusaSlicer for you."]])
    reply, _ = say(eva, "Could you get me something to look at STL files with?")
    assert "installed" not in reply
