"""v0.5 workbench: every step of a task reaches the window live (plan, what she's doing, code, output, models)."""

import asyncio
import json
from pathlib import Path

import pytest

from core.agent import live
from core.agent.tasks import get_store
from core.events.bus import get_bus

from tests.test_agent import T, _box_stl, mgr  # noqa: F401  (the scripted task fixture)


@pytest.fixture(autouse=True)
def _fresh():
    live.reset()
    yield
    live.reset()


@pytest.fixture
def heard():
    got = []
    cb = lambda data: got.append(data)          # noqa: E731
    get_bus().subscribe("agent.live", cb)
    yield got
    get_bus().unsubscribe("agent.live", cb)


GEN = '''
import struct
tri = [((0,0,0),(100,0,0),(0,50,0)), ((0,0,0),(0,50,0),(0,0,20))]
data = b"\\0" * 80 + struct.pack("<I", len(tri))
for a, b, c in tri:
    data += struct.pack("<12fH", 0, 0, 0, *a, *b, *c, 0)
open("stand.stl", "wb").write(data)
print("made stand.stl")
'''


def test_a_task_streams_its_work(mgr, heard):  # noqa: F811
    m = mgr([[T("update_plan", steps=["Model it", "Look at it"])],
             ["A Strat is about 3.6 kg, so I'll lean it back.", T("write_file", path="stand.py", content=GEN)],
             [T("run_python", code=GEN)],
             [T("mark_step", index=0), T("preview_model", path="stand.stl")],
             [T("finish", summary="Done.")]])
    t = m.start("design me a guitar stand")
    kinds = [d["event"]["kind"] for d in heard if d["task"] == t.id]
    for k in ("status", "doing", "plan", "say", "code", "output", "model", "did"):
        assert k in kinds, k
    labels = [d["event"].get("label") for d in heard if d["event"]["kind"] == "doing"]
    assert "Writing stand.py" in labels and "Running the code" in labels and "Looking at stand.stl" in labels
    assert "Thinking" in labels
    st = live.state(t.id)
    assert st["status"] == "done" and st["now"] == "Done" and st["plan"][0]["done"]
    assert st["code"]["path"] == "the code she's running" and "made stand.stl" in st["output"]["text"]
    assert st["model"]["path"] == "stand.stl" and st["model"]["png"].startswith("_eva_preview/")
    says = [e for e in st["events"] if e["kind"] == "say"]
    assert says and says[0]["text"].startswith("A Strat is about 3.6 kg")
    every = heard[-1]["event"]["state"]
    assert set(every) == {"title", "status", "plan", "now", "cost_eur", "steps"}


def test_a_failing_run_shows_as_an_error(mgr):  # noqa: F811
    m = mgr([[T("run_python", code="raise SystemExit(3)")], [T("finish", summary="ok")]])
    t = m.start("design me a hook")
    st = live.state(t.id)
    assert st["output"]["ok"] is False
    assert any(e["kind"] == "did" and e.get("ok") is False for e in st["events"])


def test_waiting_shows_what_she_asked(mgr):  # noqa: F811
    m = mgr([[T("ask_user", question="Which guitar?")]])
    t = m.start("design me a guitar stand")
    st = live.state(t.id)
    assert st["status"] == "waiting" and st["now"] == "Waiting for you"
    assert st["events"][-1]["asked"] == "Which guitar?"


def test_after_a_restart_the_state_comes_from_the_task():
    t = get_store().new("design me a guitar stand")
    t.note("wrote stand.py")
    t.status = "done"
    get_store().save(t)
    st = live.state(t.id)
    assert st["title"] == "guitar stand" and st["now"] == "Done"
    assert any(e["label"] == "wrote stand.py" for e in st["events"])
    assert live.state("deadbeef") is None


def test_labels():
    assert live.label("fetch_page", {"url": "https://www.printables.com/x"}) == "Reading www.printables.com"
    assert live.label("deliver_file", {"paths": ["a.stl", "b.md"]}) == "Delivering a.stl, b.md"
    assert live.label("install_program", {"winget_id": "OpenSCAD.OpenSCAD"}) == "Installing OpenSCAD.OpenSCAD"
    assert live.label("something_new", {}) == "Something new"


def test_the_window_gets_it(mgr):  # noqa: F811
    from fastapi.testclient import TestClient
    from interfaces.web import server_stream
    m = mgr([[T("update_plan", steps=["a"])], [T("ask_user", question="Which?")]])
    t = m.start("design me a guitar stand")
    c = TestClient(server_stream.app)
    tasks = c.get("/api/tasks/live").json()["tasks"]
    assert tasks[0]["task"] == t.id and tasks[0]["status"] == "waiting"
    st = c.get(f"/api/tasks/{t.id}/live").json()
    assert st["title"] == "guitar stand" and st["plan"][0]["step"] == "a" and isinstance(st["events"], list)
    assert c.get("/api/tasks/deadbeef/live").status_code == 404
    assert c.get("/api/tasks/..%2Fx/live").status_code == 404
    assert "id=\"evaBench\"" in c.get("/stage").text


def test_every_window_gets_each_step():
    from interfaces.web import server_stream as ss

    class Conn:
        def __init__(self):
            self.sent = []

        async def send(self, msg):
            self.sent.append(msg)
    a, b = Conn(), Conn()
    ss.CONNECTIONS.update({a, b})
    try:
        asyncio.run(ss.send_live({"task": "abcd1234", "event": {"kind": "doing", "label": "Writing stand.py"}}))
    finally:
        ss.CONNECTIONS.difference_update({a, b})
    for c in (a, b):
        assert c.sent == [{"type": "task_live", "task": "abcd1234", "event": {"kind": "doing", "label": "Writing stand.py"}}]


def test_the_page_has_the_workbench_in_the_right_script():
    html = Path("interfaces/web/eva.html").read_text(encoding="utf-8")
    ui, bridge = html.split("})();\n</script>", 1)
    for name in ("function benchEvent", "function modelViewer", "function parseSTL", "model(d) {",
                 "window.EVA.bench = "):
        assert name in ui, name                     # the card, the viewer and h() live together
    assert "E.bench.event(ev.task" in bridge and "function modelViewer" not in bridge
