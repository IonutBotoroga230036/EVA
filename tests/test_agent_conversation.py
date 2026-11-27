"""v0.4: tasks in a real E.V.A. turn (fake local model, fake Claude for the task, nothing real)."""

import asyncio
import json

import httpx
import pytest

import core.orchestrator_hybrid as orch_mod
from core.agent import consent, runner
from core.agent.tasks import get_store
from core.memory.cortex import Cortex
from interfaces.web.server_stream import prime_task
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
        return httpx.Response(200, text=json.dumps({"message": {"content": "ok"}, "done": True}))
    real = httpx.AsyncClient
    monkeypatch.setattr(orch_mod.httpx, "AsyncClient", lambda *a, **k: real(transport=httpx.MockTransport(handler)))
    o = orch_mod.HybridOrchestrator(registry=SkillRegistry("skills"))
    o.cortex = Cortex(str(tmp_path / "c.db"))
    o.history = []
    return o


@pytest.fixture
def manager(monkeypatch):
    def make(turns):
        script = Script(turns)
        m = runner.Manager(claude_factory=lambda: script, notify=lambda *a: None, threaded=False)
        monkeypatch.setattr(runner, "_manager", m)
        return m
    return make


def say(o, text):
    async def go():
        out = ""
        async for ev in o.process_stream(text):
            if ev["type"] == "final":
                out = ev["text"]
        return out
    return asyncio.run(go())


def test_yes_always_in_conversation_reaches_the_task(eva, manager):
    from core.agent import consent as _c
    _c.QUIET = set()                     # v1.1 doesn't ask for these: this test is about the asking
    m = manager([[T("install_python_packages", packages=["trimesh"], why="to check the model")],
                 [T("finish", summary="Done.")]])
    t = m.start("design a guitar stand")
    prime_task(eva, {"task": t.id, "kind": "consent", "text": "install trimesh"})
    reply = say(eva, "yes, always")
    assert "I won't ask about that again" in reply
    assert "pip_install" in consent.standing() and get_store().get(t.id).status == "done"


def test_no_in_conversation_reaches_the_task(eva, manager):
    m = manager([[T("install_program", winget_id="Blender.Blender", name="Blender", why="x")],
                 ["Fine, I'll use OpenSCAD instead. OK?"]])
    t = m.start("design a guitar stand")
    prime_task(eva, {"task": t.id, "kind": "consent", "text": "install Blender"})
    reply = say(eva, "no, too big")
    assert reply.startswith("Alright, sir, I won't.")
    assert get_store().get(t.id).waiting["text"] == "Fine, I'll use OpenSCAD instead. OK?"


def test_an_open_question_takes_your_words(eva, manager):
    m = manager([[T("ask_user", question="Which guitar?")], [T("finish", summary="Done for the Stratocaster.")]])
    t = m.start("design a guitar stand")
    prime_task(eva, {"task": t.id, "kind": "question", "text": "Which guitar?"})
    assert say(eva, "a Fender Stratocaster") == "Got it, sir. Back to the guitar stand."
    assert get_store().get(t.id).summary == "Done for the Stratocaster."


def test_starting_a_task_by_voice_asks_first(eva, manager, monkeypatch):
    manager([[T("finish", summary="ok")]])
    import core.understand.brains as U

    async def fake_understand(messages, schemas, legacy, fill, mode="auto", claude=None, **_):
        assert any(s["function"]["name"] == "task_start" for s in schemas)
        return {"tool": "task_start", "goal": "design me a guitar stand I can 3D print", "_trusted": True,
                "_by": "claude"}
    monkeypatch.setattr(U, "understand", fake_understand)
    real = U.cfg
    monkeypatch.setattr(U, "cfg", lambda: {**real(), "mode": "auto"})
    reply = say(eva, "Eva, design me a guitar stand I can 3D print")
    assert "start a task: design me a guitar stand I can 3D print" in reply and "up to 1.00 euros" in reply
    assert say(eva, "yes").startswith("On it, sir: the guitar stand.")
    assert get_store().all()[-1].status == "done"


def test_no_is_a_fine_answer_to_a_task_question(eva, manager):
    m = manager([[T("ask_user", question="Do you want supports?")], [T("finish", summary="No supports.")]])
    t = m.start("design a guitar stand")
    prime_task(eva, {"task": t.id, "kind": "question", "text": "Do you want supports?"})
    assert say(eva, "no") == "Got it, sir. Back to the guitar stand."
    assert get_store().get(t.id).summary == "No supports."
