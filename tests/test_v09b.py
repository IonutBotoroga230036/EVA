"""v0.9b: stopping her work. 27 Nov 10:59: "Can you drop the energy saver settings thing?" and "can you stop this?"
got "Energy saver settings disabled." from the local model while the computer task clicked on, and the window had
no way to see or stop tasks and projects. Fakes only."""

import json
import types

import pytest
from PIL import Image

import core.orchestrator_hybrid as orch_mod
from core import computer, projects
from core.agent.tasks import get_store

from tests.test_agent import T, mgr  # noqa: F401  (fixture)
from tests.test_v07 import screen  # noqa: F401  (fixture)


def running(title="energy saver settings", status="running"):
    t = get_store().new("Turn on Energy Saver on the PC", 0.5, title)
    t.status = status
    get_store().save(t)
    return t


ALL = types.SimpleNamespace(has=lambda name: True)


def test_drop_the_thing_cancels_the_task():
    running()
    assert orch_mod.fast_path("Can you drop the energy saver settings thing?", ALL) == \
        ("task_cancel", {"which": "energy saver settings"})
    assert orch_mod.fast_path("can you stop this?", ALL) == ("task_cancel", {"which": ""})
    assert orch_mod.fast_path("Cancel the energy saver task", ALL)[0] == "task_cancel"


def test_stop_stays_the_musics_when_nothing_works():
    assert orch_mod.stop_task("can you stop this?") is None                 # no task at all
    running(status="waiting")
    assert orch_mod.stop_task("can you stop this?") is None                 # a waiting one isn't "this"
    assert orch_mod.stop_task("stop the music") is None
    assert orch_mod.stop_task("stop") is None
    assert orch_mod.stop_task("drop the guitar stand") is None              # no such task


def test_a_made_up_stop_is_caught():
    assert orch_mod.claims("Sure, sir. Energy saver settings disabled.")
    assert orch_mod.claims("Energy saver settings stopped, sir.")
    assert orch_mod.claims("I've turned off the energy saver.")
    assert not orch_mod.claims("Nothing was stopped, sir.")
    assert not orch_mod.claims("The 10:22 train is cancelled, sir, take the 10:33.")


def test_a_cancelled_task_never_clicks_again(mgr, screen):  # noqa: F811
    """She was mid-turn (a call to Claude) when he said stop: the clicks that came back must not run."""
    m = mgr([[T("computer:screenshot")], [T("computer:left_click", coordinate=[100, 50]),
                                          T("computer:left_click", coordinate=[200, 50])],
             [T("finish", summary="done")]])
    real = m._ask_claude
    n = []

    def ask(claude, t, msgs, c):
        out = real(claude, t, msgs, c)
        n.append(1)
        if len(n) == 2:                                   # he stops it while the second answer is on its way
            fresh = get_store().get(t.id)
            m.cancel(fresh)
        return out
    m._ask_claude = ask
    t = m.start("turn on Energy Saver", toolsets=["computer:act"])
    t = get_store().get(t.id)
    assert t.status == "cancelled"
    assert not any(c[0] in ("click", "moveTo") for c in screen.calls)
    last = get_store().messages(t.id)[-1]["content"]
    assert [r["content"] for r in last] == ["Not done: he cancelled the task."] * 2       # every call answered


def test_screenshots_go_as_jpegs():
    img = Image.effect_noise((1366, 800), 40).convert("RGB")
    block = computer.jpeg_block(img)
    import base64
    import io
    png = io.BytesIO()
    img.save(png, "PNG")
    assert block["source"]["media_type"] == "image/jpeg"
    assert len(base64.b64decode(block["source"]["data"])) < len(png.getvalue()) / 2


def test_tasks_and_projects_from_the_window(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from interfaces.web import server_stream as srv
    import core.agent.runner as runner
    t = running()
    projects.reset_store(tmp_path / "projects")
    projects.tool_project_start("Desk Screen", "a transparent screen")
    pid = projects.get_store().active().id
    m = runner.Manager(threaded=False, claude_factory=lambda: None)
    monkeypatch.setattr(runner, "get_manager", lambda: m)
    with TestClient(srv.app) as tc:
        tasks = tc.get("/api/tasks").json()["tasks"]
        assert tasks[0]["id"] == t.id and tasks[0]["status"] in ("running", "queued")   # start-up requeues it
        assert tc.post(f"/api/tasks/{t.id}/cancel").status_code == 403
        assert tc.post(f"/api/tasks/{t.id}/explode", headers={"X-EVA": "1"}).status_code == 404
        r = tc.post(f"/api/tasks/{t.id}/cancel", headers={"X-EVA": "1"}).json()
        assert r["status"] == "cancelled" and r["say"].startswith("Cancelled the energy saver settings")
        pr = tc.get("/api/projects").json()["projects"]
        assert pr[0]["name"] == "Desk Screen" and pr[0]["active"]
        assert tc.post(f"/api/projects/{pid}/delete").status_code == 403
        assert tc.post(f"/api/projects/{pid}/delete", headers={"X-EVA": "1"}).json() == {"deleted": "Desk Screen"}
        assert tc.get("/api/projects").json()["projects"] == []
    assert get_store().get(t.id).status == "cancelled"


def test_the_page_can_stop_work():
    from pathlib import Path
    page = Path("interfaces/web/eva.html").read_text(encoding="utf-8")
    for bit in ("taskAct(t.task, 'cancel')", "['tasks', 'Tasks']", "['projects', 'Projects']", "/api/projects",
                "'Delete it?'", "eva:bench-show"):
        assert bit in page, bit
