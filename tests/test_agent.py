"""v0.4 "Agent": the task lane. A scripted Claude plays the agent and checks every request has the shape the real API
demands (each tool_use answered by a tool_result, first, in the next message). Nothing real is installed, nothing
leaves the test's folder, no network."""

import json
import threading
import time
from pathlib import Path

import pytest

from core.agent import consent, hands as hands_mod, pc as pc_mod
from core.agent.runner import Manager, _compact
from core.agent.tasks import get_store, short_title


# ---------------------------------------------------------------- a scripted Claude
class Script:
    def __init__(self, turns, cost=0.01):
        self.turns, self.cost, self.calls, self.n = list(turns), cost, [], 0

    def available(self):
        return True

    def message(self, **kw):
        import copy
        check_shape(kw["messages"])
        self.calls.append(copy.deepcopy(kw))          # the loop keeps appending to the same list
        if not self.turns:
            raise AssertionError("the agent asked Claude more often than scripted")
        blocks = self.turns.pop(0)
        content, uses = [], []
        for b in blocks:
            if isinstance(b, str):
                content.append({"type": "text", "text": b})
            else:
                self.n += 1
                u = {"type": "tool_use", "id": f"toolu_{self.n:03d}", "name": b[0], "input": b[1]}
                content.append(u)
                uses.append({"id": u["id"], "name": u["name"], "input": u["input"]})
        text = "".join(b for b in blocks if isinstance(b, str))
        return {"content": content, "tool_uses": uses, "text": text, "cost_eur": self.cost,
                "stop_reason": "tool_use" if uses else "end_turn"}


def check_shape(msgs):
    assert msgs and msgs[0]["role"] == "user"
    for i, m in enumerate(msgs):
        assert m["role"] == ("user" if i % 2 == 0 else "assistant"), f"roles must alternate at {i}"
        if m["role"] == "assistant" and isinstance(m["content"], list):
            ids = [b["id"] for b in m["content"] if b.get("type") == "tool_use"]
            if ids:
                nxt = msgs[i + 1]["content"] if i + 1 < len(msgs) else None
                assert isinstance(nxt, list), "tool_use must be answered by tool_result blocks"
                got = [b["tool_use_id"] for b in nxt if b.get("type") == "tool_result"]
                assert got == ids, f"tool results {got} don't answer {ids}"
                assert all(b.get("type") == "tool_result" for b in nxt[:len(ids)])


def T(_tool, **inp):
    return (_tool, inp)


class Fixed:
    """Search and web pages, without the internet."""
    def __init__(self):
        self.searches = []

    def search(self, q):
        self.searches.append(q)
        return {"results": [{"title": "3D Print Service Breda - MakerSpace", "href": "https://example.org/maker",
                             "body": "Print your STL in PLA from 5 euros, Ginnekenweg, Breda"}]}


class FakeHTTP:
    def get(self, url, **kw):
        class R:
            status_code = 200
            headers = {"content-type": "application/json"}

            def json(self):
                return [{"name": "MakerSpace Breda", "display_name": "MakerSpace Breda, Ginnekenweg 12, Breda",
                         "type": "workshop"}]
            text = "[]"
        return R()


@pytest.fixture
def mgr(monkeypatch):
    told = []
    web = Fixed()

    def hands_factory(task, store, notify):
        return hands_mod.Hands(task, store, notify, http=FakeHTTP(), search=web.search, home="Breda, Netherlands")

    def make(turns, cost=0.01):
        script = Script(turns, cost)
        m = Manager(claude_factory=lambda: script, notify=lambda t, kind, text: told.append((kind, text)),
                    threaded=False, hands_factory=hands_factory)
        m.script, m.told, m.web = script, told, web
        return m
    return make


STL = '''solid stand
facet normal 0 0 1
 outer loop
  vertex 0 0 0
  vertex 120 0 0
  vertex 0 90 0
 endloop
endfacet
endsolid stand
'''
GEN = f"open('stand.stl', 'w').write({STL!r})\nprint('triangles: 1, size 120 x 90 x 300 mm')"


# ---------------------------------------------------------------- the final boss test, scripted
def test_the_guitar_stand_end_to_end(mgr):
    m = mgr([
        [T("update_plan", steps=["Ask about the guitar", "Check for a CAD tool", "Model the stand",
                                 "Make the STL", "Find a print shop", "Write the how-to", "Deliver"])],
        [T("ask_user", question="What guitar is it for, and how tall should the stand be? Electric and 30 cm is "
                                "my default.")],
        [T("mark_step", index=0, result="electric, 30 cm, minimal"), T("check_program", name="openscad")],
        [T("install_program", winget_id="OpenSCAD.OpenSCAD", name="OpenSCAD", exe="openscad",
           why="It's a free 3D design program; I'll model the stand in it.")],
        [T("write_file", path="stand.scad", content="cube([120, 90, 300]);"),
         T("install_python_packages", packages=["numpy-stl"], why="to check the STL's size")],
        [T("run_python", code=GEN)],
        [T("find_places", what="3D printing service")],
        [T("write_file", path="how_to_print.md", content="# How to print the stand\n1. PLA, 0.2 mm, 20% infill")],
        [T("deliver_file", path="stand.stl")],
        [T("deliver_file", path="how_to_print.md"), T("deliver_file", path="stand.scad")],
        [T("finish", summary="The stand is modelled: 120 x 90 x 300 mm, printable in PLA without supports. "
                             "MakerSpace Breda on the Ginnekenweg prints it from 5 euros.")],
    ])
    t = m.start("design me a guitar stand so I can print it on a 3D printer")
    assert t.title == "guitar stand"
    t = get_store().get(t.id)
    assert t.status == "waiting" and t.waiting["kind"] == "question"
    assert m.told[-1] == ("question", "Sir, about the guitar stand: What guitar is it for, and how tall should the "
                                      "stand be? Electric and 30 cm is my default.")
    assert len(t.plan) == 7

    _, say = m.answer("electric, about 30 cm, keep it minimal")
    assert say == "Got it, sir. Back to the guitar stand."
    t = get_store().get(t.id)
    assert t.status == "waiting" and t.waiting["kind"] == "consent" and t.waiting["consent_kind"] == "install_program"
    assert m.told[-1][1].startswith("Sir, for the guitar stand, may I install OpenSCAD on your PC (winget "
                                    "OpenSCAD.OpenSCAD).")

    m.answer("yes")                                           # tests never really install (conftest)
    t = get_store().get(t.id)
    assert t.waiting["consent_kind"] == "pip_install"         # the .scad was written first, in the same turn
    assert (get_store().workspace(t.id) / "stand.scad").read_text() == "cube([120, 90, 300]);"

    m.answer("yes, always")
    assert "pip_install" in consent.standing()                # never asked again
    t = get_store().get(t.id)
    assert (get_store().workspace(t.id) / "stand.stl").read_text().startswith("solid stand")   # real sandbox run
    assert t.waiting["consent_kind"] == "export_file"

    m.answer("yes, and for the rest of this task")
    t = get_store().get(t.id)
    assert t.status == "done", t.waiting
    out = Path(pc_mod._cfg()["output_dir"]) / "guitar stand"
    assert sorted(p.name for p in out.iterdir()) == ["how_to_print.md", "stand.scad", "stand.stl"]
    assert m.told[-1][0] == "done" and "MakerSpace Breda" in m.told[-1][1]
    decisions = [c["decision"] for c in t.consents if c["decision"] in ("yes", "yes, always", "yes, for this task")]
    assert decisions == ["yes", "yes, always", "yes, for this task"]
    assert m.web.searches == ["3D printing service Breda, Netherlands"]
    assert t.steps == 11 and abs(t.cost_eur - 0.11) < 1e-9
    assert all(c["cache"] is True and c["model"] == "claude-sonnet-5-5" for c in m.script.calls)
    assert "how's the guitar stand" not in m.status_text(t)
    assert m.status_text(t).startswith("The guitar stand: done, 7 of 7 steps done" if all(
        s["done"] for s in t.plan) else "The guitar stand: done")


def test_a_no_is_heard_and_she_works_around_it(mgr):
    m = mgr([
        [T("install_program", winget_id="Blender.Blender", name="Blender", why="to model it")],
        [T("write_file", path="stand.scad", content="cube(1);")],
        ["Done without Blender, sir. Shall I make it taller?"],
    ])
    t = m.start("model a small stand for my phone")
    t2, say = m.answer("no, don't install anything")
    assert say.startswith("Alright, sir, I won't.")
    last = m.script.calls[1]["messages"][-1]["content"][0]           # the call right after your no
    assert last["is_error"] is True and "said no" in last["content"]
    t = get_store().get(t.id)
    assert t.status == "waiting" and t.waiting["text"] == "Done without Blender, sir. Shall I make it taller?"


def test_an_unclear_answer_to_a_request_is_asked_again(mgr):
    m = mgr([[T("install_program", winget_id="OpenSCAD.OpenSCAD", name="OpenSCAD", why="x")]])
    m.start("design a phone holder")
    _, say = m.answer("hmm what is that")
    assert say.startswith("Is that a yes, sir?")


def test_it_survives_a_restart(mgr):
    m = mgr([[T("ask_user", question="Which colour?")], [T("finish", summary="Done in red.")]])
    t = m.start("design a red keychain")
    script = m.script
    restarted = Manager(claude_factory=lambda: script, notify=lambda *a: None, threaded=False,
                        hands_factory=lambda task, store, notify: hands_mod.Hands(task, store, notify))
    _, say = restarted.answer("red")
    assert get_store().get(t.id).status == "done" and get_store().get(t.id).summary == "Done in red."


def test_a_running_task_carries_on_after_a_restart(mgr):
    m = mgr([[T("finish", summary="ok")]])
    t = get_store().new("write a short plan for my week")
    t.status = "running"
    get_store().save(t)
    assert m.resume_after_restart() == 1 and get_store().get(t.id).status == "done"


def test_the_budget_is_a_question(mgr):
    m = mgr([[T("update_plan", steps=["a"])], [T("update_plan", steps=["b"])], [T("finish", summary="ok")]], cost=0.6)
    t = m.start("research the best snowboard bindings for me")
    t = get_store().get(t.id)
    assert t.status == "waiting" and t.waiting["consent_kind"] == "spend_more"
    assert "it has used 1.20 of its 1.00 euros" in t.waiting["text"]
    m.answer("yes")
    t = get_store().get(t.id)
    assert t.status == "done" and t.budget_eur == pytest.approx(2.2)


def test_no_more_budget_pauses_it(mgr):
    m = mgr([[T("update_plan", steps=["a"])], [T("update_plan", steps=["b"])]], cost=0.6)
    t = m.start("research the best snowboard bindings for me")
    m.answer("no")
    assert get_store().get(t.id).status == "paused"


def test_a_spending_yes_can_never_be_always(mgr):
    m = mgr([[T("update_plan", steps=["a"])], [T("update_plan", steps=["b"])], [T("finish", summary="ok")]], cost=0.6)
    m.start("research the best snowboard bindings for me")
    m.answer("yes, always")
    assert "spend_more" not in consent.standing()


def test_pause_resume_cancel(mgr):
    m = mgr([[T("ask_user", question="Which size?")], [T("finish", summary="ok")]])
    t = m.start("design a cable organiser")
    assert "Paused the cable organiser" in m.pause(get_store().get(t.id))
    assert m.resume(get_store().get(t.id)).startswith("Resumed, sir. The cable organiser is still waiting")
    assert "Cancelled the cable organiser" in m.cancel(get_store().get(t.id))
    assert m.answer("large")[0] is None                       # nothing waits any more


def test_text_without_tools_is_a_question(mgr):
    m = mgr([["Do you want it in PLA or PETG, sir?"], [T("finish", summary="PLA it is.")]])
    t = m.start("design a headphone hook")
    assert get_store().get(t.id).waiting["text"] == "Do you want it in PLA or PETG, sir?"
    m.answer("PLA")
    assert get_store().get(t.id).status == "done"
    assert m.script.calls[-1]["messages"][-1] == {"role": "user", "content": "The user answered: PLA"}


def test_a_tool_error_is_a_result_not_a_crash(mgr):
    m = mgr([[T("read_file", path="nothing.txt")], [T("finish", summary="ok")]])
    m.start("summarise my notes file")
    res = m.script.calls[-1]["messages"][-1]["content"][0]
    assert res["is_error"] is True and "doesn't exist" in res["content"]


def test_without_claude_she_says_so(mgr):
    m = mgr([])
    m.script.available = lambda: False
    t = m.start("design a desk organiser")
    assert "ANTHROPIC_API_KEY" in get_store().get(t.id).waiting["text"]


def test_short_titles():
    assert short_title("Hey Eva, design me a guitar stand so I can print it on a 3D printer") == "guitar stand"
    assert short_title("research the best snowboard bindings for me") == "best snowboard bindings for me"
    assert short_title("plan my trip to Brasov") == "trip to Brasov"


def test_long_conversations_are_trimmed_but_keep_their_shape():
    msgs = [{"role": "user", "content": "goal"}]
    for i in range(30):
        msgs.append({"role": "assistant", "content": [{"type": "tool_use", "id": f"t{i}", "name": "write_file",
                                                       "input": {"path": "a", "content": "x" * 3000}}]})
        msgs.append({"role": "user", "content": [{"type": "tool_result", "tool_use_id": f"t{i}", "content": "y" * 3000}]})
    out = _compact(msgs, 60000)
    check_shape(out)
    assert len(json.dumps(out)) < 50000 and out[-1]["content"][0]["content"] == "y" * 3000
    assert out[1]["content"][0]["input"]["content"].endswith("(trimmed: the file is in the task folder)")


# ---------------------------------------------------------------- consent tiers
def test_the_tiers():
    assert consent.tier("look") == "free" and consent.tier("workspace") == "logged"
    assert consent.tier("install_program") == "ask" and consent.tier("pip_install") == "ask"
    assert consent.grant("pip_install") and consent.tier("pip_install") == "logged"
    assert not consent.grant("spend_more") and consent.tier("spend_more") == "ask"
    assert not consent.grant("eva_action")
    assert consent.tier("export_file", {"export_file": 1.0}) == "logged"           # a yes for this task only
    assert consent.revoke() == ["pip_install"] and consent.tier("pip_install") == "ask"


# ---------------------------------------------------------------- the PC and its sandbox
def test_the_sandbox_keeps_code_inside_the_task_folder():
    t = get_store().new("try the sandbox")
    pc = pc_mod.LocalPC()
    out = pc.run_python(t.id, "open('../escape.txt','w').write('x')")
    assert out["exit"] != 0 and "outside the task folder" in out["stderr"]
    out = pc.run_python(t.id, "import socket\nsocket.create_connection(('example.org', 80))")
    assert out["exit"] != 0 and "no network" in out["stderr"]
    out = pc.run_python(t.id, "open('ok.txt','w').write('fine'); print('hi')")
    assert out["exit"] == 0 and out["stdout"].strip() == "hi" and out["new_files"] == ["ok.txt"]
    assert pc.list_files(t.id)["files"] == [{"path": "ok.txt", "bytes": 4}]


def test_files_stay_inside_the_task_folder():
    t = get_store().new("try the files")
    pc = pc_mod.LocalPC()
    with pytest.raises(pc_mod.PCError):
        pc.write_file(t.id, "../../evil.txt", "x")
    with pytest.raises(pc_mod.PCError):
        pc.run_program(t.id, "python", ["C:\\Windows\\system.ini"])
    with pytest.raises(pc_mod.PCError):
        pc.run_program(t.id, "definitely-not-a-program-xyz", [])
    assert pc.write_file(t.id, "sub/a.txt", "hello")["path"] == "sub/a.txt"


def test_a_remote_pc_gets_the_same_calls():
    import asyncio
    loop = asyncio.new_event_loop()
    th = threading.Thread(target=loop.run_forever, daemon=True)
    th.start()

    class Link:
        name = "laptop"

        async def request(self, method, kwargs):
            return pc_mod.serve(method, kwargs)
    pc_mod.LINK.update(conn=Link(), loop=loop)
    try:
        remote = pc_mod.get_pc()
        assert isinstance(remote, pc_mod.RemotePC)
        t = get_store().new("remote files")
        assert remote.write_file(t.id, "a.txt", "hi") == {"path": "a.txt", "bytes": 2}
        assert remote.read_file(t.id, "a.txt")["text"] == "hi"
        with pytest.raises(pc_mod.PCError):
            remote.read_file(t.id, "../../x")
    finally:
        pc_mod.LINK.update(conn=None)
        loop.call_soon_threadsafe(loop.stop)


def test_the_pc_agent_answers_calls():
    import asyncio
    from interfaces import pc_agent
    sent = []

    class WS:
        async def send(self, raw):
            sent.append(json.loads(raw))
    asyncio.run(pc_agent.handle(WS(), json.dumps({"type": "call", "id": "r1", "method": "info", "kwargs": {}})))
    assert sent[0]["id"] == "r1" and sent[0]["ok"] is True and "python" in sent[0]["data"]
    assert pc_agent.ws_url("https://brain.example:8443") == "wss://brain.example:8443/ws/pc"
    assert pc_agent.ws_url("") == "ws://localhost:8001/ws/pc"


def test_the_pc_link_endpoint(monkeypatch):
    from fastapi.testclient import TestClient
    from interfaces.web import server_stream
    c = TestClient(server_stream.app)
    with c.websocket_connect("/ws/pc") as ws:
        ws.send_text(json.dumps({"type": "hello", "name": "test-pc", "caps": ["info"]}))
        deadline = time.time() + 5
        while pc_mod.LINK.get("conn") is None and time.time() < deadline:
            time.sleep(0.02)
        link = pc_mod.LINK["conn"]
        assert link is not None and link.name == "test-pc"
        box = {}
        th = threading.Thread(target=lambda: box.setdefault("out", pc_mod.RemotePC(link).info()))
        th.start()
        call = json.loads(ws.receive_text())
        assert call["type"] == "call" and call["method"] == "info"
        ws.send_text(json.dumps({"type": "result", "id": call["id"], "ok": True, "data": {"os": "fake"}}))
        th.join(10)
        assert box["out"] == {"os": "fake"}
    deadline = time.time() + 5
    while pc_mod.LINK.get("conn") is not None and time.time() < deadline:
        time.sleep(0.02)
    assert pc_mod.LINK.get("conn") is None


# ---------------------------------------------------------------- the hands' own rules
def test_reading_eva_tools_is_free_but_acting_asks():
    t = get_store().new("plan my evening")
    h = hands_mod.Hands(t, get_store(), lambda *a: None)
    assert h.needs_yes("use_eva_tool", {"tool": "get_datetime", "args": {}}) is None
    q = h.needs_yes("use_eva_tool", {"tool": "send_to_phone", "args": {"text": "hi"}})
    assert q and q.startswith("use send_to_phone")
    assert h.call("use_eva_tool", {"tool": "task_start", "args": {}}, approved=True)[2] is True


def test_private_addresses_are_never_fetched():
    t = get_store().new("read a page")
    h = hands_mod.Hands(t, get_store(), lambda *a: None)
    kind, text, err = h.call("fetch_page", {"url": "http://127.0.0.1:8001/api/status"})
    assert err and "public" in text


def test_progress_updates_are_rate_limited():
    t = get_store().new("a long job")
    told = []
    h = hands_mod.Hands(t, get_store(), lambda kind, text: told.append(text))
    h.call("tell_user", {"message": "Modelling now."})
    h.call("tell_user", {"message": "Still modelling."})
    assert told == ["Modelling now."] and "Still modelling" in t.log[-1]["what"]


# ---------------------------------------------------------------- in conversation
def test_starting_a_task_asks_first_and_names_the_budget():
    from core.orchestrator_hybrid import ToolBelt
    belt = ToolBelt()
    assert belt.needs_confirm("task_start")
    assert belt.describe("task_start", {"goal": "design a guitar stand"}) == (
        "start a task: design a guitar stand. I'll work on it with Claude in the background, up to 1.00 euros "
        "before I ask for more")


def test_status_list_and_permissions_by_tool(monkeypatch, mgr):
    from core.agent import runner, tools
    m = mgr([[T("update_plan", steps=["Ask", "Model"])], [T("ask_user", question="Which guitar?")]])
    monkeypatch.setattr(runner, "_manager", m)
    out = tools.tool_task_start(goal="design me a guitar stand I can 3D print")
    assert out["say"].startswith("On it, sir: the guitar stand.")
    st = tools.tool_task_status(which="how's the guitar stand going")["say"]
    assert st == "The guitar stand: waiting for you, 0 of 2 steps done; next: Ask. I asked: Which guitar?. So far it " \
                 "cost 2 cents, sir."
    assert tools.tool_task_list()["say"] == "Working on: the guitar stand (waiting), sir."
    assert "won't ask before installing Python packages" in tools.tool_permission_grant(what="python packages")["say"]
    assert "installing Python packages" in tools.tool_permission_list()["say"]
    assert "ask first again" in tools.tool_permission_revoke(what="all of them")["say"]


def test_a_yes_in_the_window_answers_the_task(monkeypatch, mgr):
    import asyncio
    from core.agent import runner
    from core.orchestrator_hybrid import HybridOrchestrator
    from interfaces.web.server_stream import prime_task
    m = mgr([[T("install_program", winget_id="OpenSCAD.OpenSCAD", name="OpenSCAD", why="x")],
             [T("finish", summary="ok")]])
    monkeypatch.setattr(runner, "_manager", m)
    t = m.start("design a guitar stand")
    orch = HybridOrchestrator.__new__(HybridOrchestrator)
    orch.pending = None
    prime_task(orch, {"task": t.id, "kind": "consent", "text": "may I install OpenSCAD?"})
    assert orch.pending["tool"] == "task_answer" and orch.pending["on_no"]["tool"] == "task_answer"
    prime_task(orch, {"task": t.id, "kind": "question", "text": "Which guitar?"})
    assert orch.awaiting == {"tool": "task_answer", "args": {"task": t.id}, "field": "answer",
                             "ts": orch.awaiting["ts"]}


def test_telegram_buttons_answer_tasks(monkeypatch, mgr):
    import asyncio
    from core import telegram_bridge
    from core.agent import runner
    m = mgr([[T("install_program", winget_id="OpenSCAD.OpenSCAD", name="OpenSCAD", why="x")],
             [T("finish", summary="ok")]])
    monkeypatch.setattr(runner, "_manager", m)
    t = m.start("design a guitar stand")
    sent = []
    bridge = telegram_bridge.TelegramBridge.__new__(telegram_bridge.TelegramBridge)

    async def send(chat_id, text, **kw):
        sent.append(text)
    bridge.send = send
    asyncio.run(bridge.handle_task_button(1, f"task:always:{t.id}"))
    assert "install_program" in consent.standing() and get_store().get(t.id).status == "done"
    assert sent and "I won't ask about that again" in sent[0]


# ---------------------------------------------------------------- the security pass
@pytest.mark.parametrize("code,why", [
    ("import subprocess; subprocess.run(['echo', 'hi'])", "doesn't allow subprocess.Popen"),
    ("import os; os.system('echo hi')", "doesn't allow os.system"),
    ("import os; os.remove('../../somefile')", "outside the task folder"),
    ("import shutil; shutil.rmtree('..')", "outside the task folder"),
    ("import os; fd = os.open('../x.txt', os.O_WRONLY | os.O_CREAT)", "outside the task folder"),
    ("import urllib.request; urllib.request.urlopen('http://example.org')", "doesn't allow"),
])
def test_the_sandbox_blocks_the_ways_out(code, why):
    t = get_store().new("try to escape")
    out = pc_mod.LocalPC().run_python(t.id, code)
    assert out["exit"] != 0 and why in out["stderr"], out


def test_temp_files_stay_in_the_task_folder():
    t = get_store().new("temp files")
    out = pc_mod.LocalPC().run_python(t.id, "import tempfile\nwith tempfile.NamedTemporaryFile('w', delete=False) as f:\n"
                                            "    f.write('x')\nprint(f.name)")
    assert out["exit"] == 0 and str(get_store().workspace(t.id)) in out["stdout"]


@pytest.mark.parametrize("prog", ["powershell", "cmd.exe", "python", "C:/Windows/System32/cmd.exe", "winget"])
def test_shells_and_interpreters_never_run(prog):
    t = get_store().new("no shells")
    with pytest.raises(pc_mod.PCError, match="can run other code"):
        pc_mod.LocalPC().run_program(t.id, prog, ["-c", "anything"])


def test_unknown_programs_ask_and_known_ones_dont():
    t = get_store().new("run programs")
    h = hands_mod.Hands(t, get_store(), lambda *a: None)
    assert h.needs_yes("run_program", {"program": "openscad", "args": ["-o", "a.stl", "a.scad"]}) is None
    assert h.needs_yes("run_program", {"program": "someslicer", "args": ["a.stl"]}).startswith("run someslicer")
    t.grants["run_program"] = 1.0
    assert h.needs_yes("run_program", {"program": "someslicer", "args": ["a.stl"]}) is None


def test_personal_eva_tools_ask_even_for_reading():
    t = get_store().new("read my mail")
    h = hands_mod.Hands(t, get_store(), lambda *a: None)
    assert h.needs_yes("use_eva_tool", {"tool": "get_datetime"}) is None
    q = h.needs_yes("use_eva_tool", {"tool": "email_check", "args": {}})
    assert q and q.startswith("read with email_check")


def test_numpy_works_in_the_sandbox():
    pytest.importorskip("numpy")
    t = get_store().new("numbers")
    out = pc_mod.LocalPC().run_python(t.id, "import numpy as np\nnp.save('a.npy', np.arange(3))\nprint(np.load('a.npy').sum())")
    assert out["exit"] == 0 and out["stdout"].strip() == "3", out
