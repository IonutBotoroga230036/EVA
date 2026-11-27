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
        if isinstance(blocks, Exception):
            raise blocks
        stop = None
        if isinstance(blocks, tuple) and blocks[0] == "cut":     # a reply cut off at max_tokens
            stop, blocks = "max_tokens", blocks[1]
        content, uses = [], []
        for b in blocks:
            if isinstance(b, str):
                content.append({"type": "text", "text": b})
            else:
                self.n += 1
                u = {"type": "tool_use", "id": f"toolu_{self.n:03d}", "name": b[0], "input": b[1]}
                if ":" in b[0]:                            # "browser:navigate": a toolset member (v0.7)
                    u["toolset_name"], u["name"] = b[0].split(":", 1)
                content.append(u)
                uses.append({k: u[k] for k in ("id", "name", "input", "toolset_name") if k in u})
        text = "".join(b for b in blocks if isinstance(b, str))
        if kw.get("on_text"):                              # v0.8: words arrive a few at a time, like the stream
            import re as _re
            for piece in _re.findall(r"\S+\s*", text):
                kw["on_text"](piece)
        return {"content": content, "tool_uses": uses, "text": text, "cost_eur": self.cost,
                "stop_reason": stop or ("tool_use" if uses else "end_turn")}


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
    from core.agent import consent as _c
    _c.QUIET = set()                     # v1.1 doesn't ask for these: this test is about the asking
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
    assert m.status_text(t).startswith("The guitar stand: done. The stand is modelled")
    assert all(s["done"] for s in t.plan)                     # finishing ticks off the plan


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
    out, trimmed = _compact(msgs, 60000)
    assert trimmed and _compact(out[:3], 60000)[1] is False
    check_shape(out)
    assert len(json.dumps(out)) < 50000 and out[-1]["content"][0]["content"] == "y" * 3000
    assert out[1]["content"][0]["input"]["content"].endswith("(trimmed: the file is in the task folder)")


# ---------------------------------------------------------------- consent tiers
def test_the_tiers():
    from core.agent import consent as _c
    _c.QUIET = set()                     # v1.1 doesn't ask for these: this test is about the asking
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


# ---------------------------------------------------------------- Nov 25 log: the first real guitar stand
def test_the_instructions_are_written_once_and_resent_unchanged(mgr, monkeypatch):
    """The system prompt held the time of day, so it changed every minute: every earlier thinking block became
    invalid (400 'bound to a different conversation') and the prompt cache never hit."""
    from core.agent import runner
    m = mgr([[T("ask_user", question="Which guitar?")], [T("update_plan", steps=["a"])], [T("finish", summary="ok")]])
    t = m.start("design me a guitar stand")
    monkeypatch.setattr(runner, "_context", lambda: "something new in EVA.md")     # you edit EVA.md meanwhile
    m.answer("electric")
    systems = {c["system"] for c in m.script.calls}
    assert len(systems) == 1 and "started on" in systems.pop()
    assert get_store().get(t.id).system
    c = m.script.calls[0]
    assert c["thinking"] == {"type": "adaptive", "block_binding": {"prefix_mismatch_behavior": "error"}}
    assert "thinking-binding-controls-2026-08-01" in c["betas"] and c["effort"] == "medium"
    assert c["max_tokens"] == 16000


def test_stale_thinking_is_dropped_and_the_task_goes_on(mgr):
    from core.claude import ThinkingBound
    m = mgr([[T("ask_user", question="Which guitar?")],
             ThinkingBound("messages.5.content.0: ... The block is bound to a different conversation."),
             [T("finish", summary="Done.")]])
    t = m.start("design me a guitar stand")
    m.answer("electric")
    t = get_store().get(t.id)
    assert t.status == "done" and t.drop_thinking
    assert m.script.calls[-1]["thinking"]["block_binding"]["prefix_mismatch_behavior"] == "drop_block"


def test_a_second_binding_error_fails_the_task_and_resume_tries_again(mgr):
    from core.claude import ThinkingBound
    from core.agent.runner import _spoken_error
    m = mgr([ThinkingBound("bound to a different conversation"), ThinkingBound("bound to a different conversation"),
             [T("finish", summary="Done.")]])
    t = get_store().new("design me a guitar stand")
    with pytest.raises(ThinkingBound):
        m.run(t.id)
    t = get_store().get(t.id)
    t.status = "failed"
    get_store().save(t)
    assert m.resume(t) == "Trying the guitar stand again, sir."
    assert get_store().get(t.id).status == "done"
    e = RuntimeError('Claude API error 400: {"type":"error","error":{"type":"invalid_request_error","message":'
                     '"messages.5.content.0: Invalid signature"}}')
    assert _spoken_error(e) == "messages.5.content.0: Invalid signature"


def test_a_cut_off_reply_carries_on_instead_of_asking(mgr):
    """19:12:33: 4000 tokens out, no tool call, so she asked 'How should I go on with the guitar stand, sir?'"""
    m = mgr([("cut", ["Here is the OpenSCAD model: module stand() {", T("write_file", path="stand.scad", content="mod")]),
             [T("finish", summary="Done.")]])
    t = m.start("design me a guitar stand")
    assert get_store().get(t.id).status == "done" and not [x for x in m.told if x[0] == "question"]
    last = m.script.calls[-1]["messages"]
    assert last[-1]["content"].startswith("(Your last reply hit the length limit")
    assert [b["type"] for b in last[-2]["content"]] == ["text"]          # the unfinished tool call is gone


def test_any_other_questions_while_she_works(mgr):
    m = mgr([])
    t = get_store().new("design me a guitar stand")
    t.status = "running"
    get_store().save(t)
    task, say = m.answer("do you have any other questions?")
    assert task is None and say.startswith("Not right now, sir. I'm working on the guitar stand")


def test_her_question_doesnt_say_sir_twice(mgr):
    m = mgr([[T("ask_user", question="Sir, two quick choices: bed size and guitar?")]])
    m.start("design me a guitar stand")
    assert m.told[-1][1] == "Sir, about the guitar stand: Two quick choices: bed size and guitar?"


class _Resp:
    def __init__(self, code, body):
        self.status_code, self._body = code, body
        self.text = json.dumps(body)

    def json(self):
        return self._body


class _HTTP:
    def __init__(self, replies):
        self.replies, self.sent = list(replies), []

    def post(self, url, json=None, headers=None):
        import copy
        self.sent.append((copy.deepcopy(json), dict(headers or {})))
        return self.replies.pop(0)


def _client(replies, tmp_path):
    from core.budget import BudgetTracker
    from core.claude import ClaudeClient
    return ClaudeClient(api_key="test", budget=BudgetTracker(track_file=str(tmp_path / "b.json")), http=_HTTP(replies))


OK = {"content": [{"type": "thinking", "thinking": "", "signature": "s"}, {"type": "text", "text": "hi"}],
      "usage": {"input_tokens": 5, "output_tokens": 5}, "stop_reason": "end_turn",
      "input_transformations": [{"type": "thinking_dropped", "path": "messages.1.content.0"}]}
BOUND = {"type": "error", "error": {"type": "invalid_request_error", "message": "messages.1.content.0: Invalid "
         "`signature` in `thinking` block. The block is bound to a different conversation."}}


def test_the_client_sends_the_binding_setting_and_names_the_error(tmp_path):
    from core.claude import BINDING_BETA, ThinkingBound
    th = {"type": "adaptive", "block_binding": {"prefix_mismatch_behavior": "error"}}
    c = _client([_Resp(200, OK)], tmp_path)
    out = c.message(system="s", messages=[{"role": "user", "content": "x"}], thinking=th, betas=[BINDING_BETA])
    body, headers = c.http.sent[0]
    assert body["thinking"] == th and headers["anthropic-beta"] == BINDING_BETA
    assert out["text"] == "hi" and out["input_transformations"][0]["type"] == "thinking_dropped"
    c = _client([_Resp(400, BOUND)], tmp_path)
    with pytest.raises(ThinkingBound):
        c.message(system="s", messages=[{"role": "user", "content": "x"}], thinking=th, betas=[BINDING_BETA])
    assert len(c.http.sent) == 1                          # never silently retried without thinking


def test_without_the_beta_it_goes_on_without_the_setting(tmp_path):
    from core.claude import BINDING_BETA
    th = {"type": "adaptive", "block_binding": {"prefix_mismatch_behavior": "error"}}
    no_beta = {"type": "error", "error": {"type": "invalid_request_error",
                                          "message": "thinking.block_binding: Extra inputs are not permitted"}}
    c = _client([_Resp(400, no_beta), _Resp(200, OK)], tmp_path)
    assert c.message(system="s", messages=[{"role": "user", "content": "x"}], thinking=th,
                     betas=[BINDING_BETA])["text"] == "hi"
    body, headers = c.http.sent[1]
    assert body["thinking"] == {"type": "adaptive"} and "anthropic-beta" not in headers


# ---------------------------------------------------------------- one window speaks, the others show
def test_a_task_update_is_spoken_in_one_window_only():
    import asyncio
    from interfaces.web import server_stream as ss

    class Conn:
        def __init__(self, talk):
            self.last_talk, self.connected_at, self.heard = talk, 0.0, []
            self.orch = type("O", (), {"pending": None, "awaiting": None})()

        async def proactive(self, text, widget=None, speak=True):
            self.heard.append((text, speak))
    phone, pc = Conn(100.0), Conn(200.0)
    ss.CONNECTIONS.update({phone, pc})
    try:
        asyncio.run(ss.announce_task({"task": "abc", "title": "guitar stand", "kind": "question", "text": "Which?"}))
    finally:
        for c in (phone, pc):
            ss.CONNECTIONS.remove(c)
    assert pc.heard == [("Which?", True)] and phone.heard == [("Which?", False)]
    assert phone.orch.awaiting["tool"] == "task_answer" and pc.orch.awaiting["tool"] == "task_answer"


def test_windows_connection_resets_are_not_logged():
    from interfaces.web import server_stream as ss
    seen = []

    class Loop:
        def default_exception_handler(self, ctx):
            seen.append(ctx)
    ss._quiet_resets(Loop(), {"message": "Exception in callback _ProactorBasePipeTransport._call_connection_lost(None)",
                              "exception": ConnectionResetError(10054, "forcibly closed")})
    assert seen == []
    ss._quiet_resets(Loop(), {"message": "Task exception was never retrieved", "exception": ValueError("x")})
    assert len(seen) == 1


def test_the_manifest_is_fetched_with_the_pairing_cookie():
    for name in ("eva.html", "eva_classic.html"):
        html = (Path("interfaces/web") / name).read_text(encoding="utf-8")
        assert 'rel="manifest" href="/manifest.webmanifest" crossorigin="use-credentials"' in html


# ---------------------------------------------------------------- Nov 25, second run: seeing the model
def _box_stl(path, w=100.0, d=50.0, h=20.0, binary=True):
    import struct
    v = [(0, 0, 0), (w, 0, 0), (w, d, 0), (0, d, 0), (0, 0, h), (w, 0, h), (w, d, h), (0, d, h)]
    f = [(0, 2, 1), (0, 3, 2), (4, 5, 6), (4, 6, 7), (0, 1, 5), (0, 5, 4), (1, 2, 6), (1, 6, 5), (2, 3, 7),
         (2, 7, 6), (3, 0, 4), (3, 4, 7)]
    if binary:
        data = b"\0" * 80 + struct.pack("<I", len(f))
        for a, b, c in f:
            data += struct.pack("<12fH", 0, 0, 0, *v[a], *v[b], *v[c], 0)
        Path(path).write_bytes(data)
    else:
        rows = ["solid box"] + [f"facet normal 0 0 0\n outer loop\n" + "".join(
            f"  vertex {x} {y} {z}\n" for x, y, z in (v[a], v[b], v[c])) + " endloop\nendfacet" for a, b, c in f]
        Path(path).write_text("\n".join(rows + ["endsolid box"]))


@pytest.mark.parametrize("binary", [True, False])
def test_a_model_becomes_a_picture(tmp_path, binary):
    from PIL import Image
    from core.agent.preview import render
    _box_stl(tmp_path / "box.stl", binary=binary)
    out = render(tmp_path / "box.stl")
    assert out["size_mm"] == [100.0, 50.0, 20.0] and out["triangles"] == 12
    img = Image.open(out["png"])
    assert img.size == (1260, 460)
    colours = {tuple(px) for px in __import__("numpy").asarray(img.crop((0, 0, 420, 420))).reshape(-1, 3)}
    assert len(colours) > 3                                    # shaded faces and lines, not a blank box


def test_not_an_stl_is_refused(tmp_path):
    from core.agent.preview import render
    (tmp_path / "x.stl").write_text("hello")
    with pytest.raises(ValueError):
        render(tmp_path / "x.stl")


def test_she_looks_at_her_own_model(mgr):
    m = mgr([[T("write_file", path="a.txt", content="x")], [T("preview_model", path="box.stl")],
             [T("finish", summary="Done.")]])
    t = get_store().new("design me a box")
    _box_stl(get_store().workspace(t.id) / "box.stl")
    m.run(t.id)
    res = m.script.calls[2]["messages"][-1]["content"][0]
    assert res["type"] == "tool_result" and isinstance(res["content"], list)
    assert res["content"][1]["type"] == "image" and res["content"][1]["source"]["media_type"] == "image/png"
    assert "100 x 50 x 20" in res["content"][0]["text"]
    assert (get_store().workspace(t.id) / "_eva_preview" / "box.png").exists()
    assert "preview_model" not in [f["path"] for f in pc_mod.LocalPC().list_files(t.id)["files"]]


def test_pictures_dont_count_as_text_and_old_ones_go(mgr):
    from core.agent.runner import _compact, _size
    img = {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "A" * 200000}}
    msgs = [{"role": "user", "content": "goal"}]
    for i in range(12):
        msgs.append({"role": "assistant", "content": [{"type": "tool_use", "id": f"t{i}", "name": "preview_model",
                                                       "input": {"path": "a.stl"}}]})
        msgs.append({"role": "user", "content": [{"type": "tool_result", "tool_use_id": f"t{i}",
                                                  "content": [{"type": "text", "text": "ok"}, dict(img)]}]})
    assert _size(msgs[:5]) < 10000                             # two pictures are not 400 KB of text
    out, trimmed = _compact(msgs, 30000)
    check_shape(out)
    assert trimmed and out[2]["content"][0]["content"][1]["type"] == "text"
    assert out[-1]["content"][0]["content"][1]["type"] == "image"           # the newest ones stay


def test_all_files_are_delivered_with_one_question(mgr):
    from core.agent import consent as _c
    _c.QUIET = set()                     # v1.1 doesn't ask for these: this test is about the asking
    m = mgr([[T("write_file", path="a.stl", content="solid a"), T("write_file", path="b.md", content="# b")],
             [T("deliver_file", paths=["a.stl", "b.md"])], [T("finish", summary="Done.")]])
    t = m.start("design me a hook")
    t = get_store().get(t.id)
    assert t.waiting["text"] == "copy a.stl and b.md to your E.V.A. folder (" + \
        f"{pc_mod._cfg()['output_dir']}/hook)"
    m.answer("yes")
    t = get_store().get(t.id)
    assert t.status == "done" and sorted(Path(f).name for f in t.files) == ["a.stl", "b.md"]
    assert len([x for x in m.told if x[0] == "consent"]) == 1


def test_she_says_two_sentences_and_shows_the_rest(mgr):
    details = "**stand_base.stl** is 210 x 190 mm.\n- PLA, 0.2 mm layers\n- no supports"
    m = mgr([[T("write_file", path="s.stl", content="solid s")], [T("deliver_file", paths=["s.stl"])],
             [T("finish", summary="The stand is ready in two parts. Print the base first. Then the post.",
                details=details)]])
    t = m.start("design me a guitar stand")
    m.answer("yes")
    t = get_store().get(t.id)
    assert t.details == details
    kind, said = m.told[-1]
    assert kind == "done" and said == ("Sir, the guitar stand is done. The stand is ready in two parts. Print the "
                                       "base first. The files are in your E.V.A. folder, and the model is in the "
                                       "window: say 'show me' to see it.")
    assert "**" not in m.status_text(t) and "0 of" not in m.status_text(t)


def test_an_old_write_up_in_the_summary_is_spoken_short():
    from core.agent.runner import spoken
    long = ("Sir, the guitar stand is ready as two STL files in Documents/E.V.A./guitar stand. I haven't "
            "test-printed it.\n\n- **stand_base.stl** is 210 x 190 x 43 mm.\n\n**Print settings:**\n- PLA")
    assert spoken(long) == ("The guitar stand is ready as two STL files in Documents/E.V.A./guitar stand. I haven't "
                            "test-printed it.")


def test_show_me_the_stand(monkeypatch):
    from core.agent import tools as atools
    t = get_store().new("design me a guitar stand")
    t.status, t.summary, t.details = "done", "Ready.", "Base 210 x 190 mm."
    get_store().save(t)
    ws = get_store().workspace(t.id)
    _box_stl(ws / "stand_base.stl")
    _box_stl(ws / "assembly.stl", 120, 60, 300)
    opened = []
    monkeypatch.setattr(atools.os if hasattr(atools, "os") else __import__("os"), "startfile", opened.append,
                        raising=False)
    out = atools.tool_task_show("guitar stand", on_pc=True)
    w = out["widget"]
    assert w["kind"] == "model" and w["task"] == t.id and w["text"] == "Base 210 x 190 mm."
    assert [x["name"] for x in w["models"]] == ["assembly.stl", "stand_base.stl"]       # the whole thing first
    assert w["models"][0]["size_mm"] == [120.0, 60.0, 300.0] and w["models"][0]["png"].startswith("_eva_preview/")
    assert out["say"].startswith("Here's the guitar stand, sir: 2 models in the window.")
    assert opened == [str(ws)]


def test_show_me_without_models_shows_the_write_up():
    from core.agent import tools as atools
    t = get_store().new("plan my week")
    t.status, t.details = "done", "Monday: gym."
    get_store().save(t)
    out = atools.tool_task_show()
    assert out["widget"] == {"kind": "note", "title": "week", "text": "Monday: gym."} or \
        out["widget"]["text"] == "Monday: gym."
    assert out["say"] == "Here's what the week found, sir, in the window."      # v0.8: not "no 3D models"


def test_the_window_gets_the_model_files_and_nothing_else(monkeypatch):
    from fastapi.testclient import TestClient
    from interfaces.web import server_stream
    t = get_store().new("design me a guitar stand")
    ws = get_store().workspace(t.id)
    _box_stl(ws / "stand.stl")
    (ws / "secret.py").write_text("x")
    c = TestClient(server_stream.app)
    r = c.get(f"/api/tasks/{t.id}/file", params={"path": "stand.stl"})
    assert r.status_code == 200 and r.headers["content-type"] == "model/stl" and len(r.content) == 84 + 12 * 50
    assert c.get(f"/api/tasks/{t.id}/file", params={"path": "secret.py"}).status_code == 404
    assert c.get(f"/api/tasks/{t.id}/file", params={"path": "../task.json"}).status_code == 404
    assert c.get("/api/tasks/deadbeef/file", params={"path": "stand.stl"}).status_code == 404
    assert c.get("/api/tasks/..%2F..%2Fx/file", params={"path": "a.stl"}).status_code == 404


def test_a_finished_task_shows_its_model_in_the_window():
    import asyncio
    from interfaces.web import server_stream as ss
    t = get_store().new("design me a guitar stand")
    t.status = "done"
    get_store().save(t)
    _box_stl(get_store().workspace(t.id) / "stand.stl")

    class Conn:
        last_talk, connected_at = 1.0, 0.0

        def __init__(self):
            self.orch = type("O", (), {"pending": None, "awaiting": None})()
            self.got = []

        async def proactive(self, text, widget=None, speak=True):
            self.got.append(widget)
    c = Conn()
    ss.CONNECTIONS.add(c)
    try:
        asyncio.run(ss.announce_task({"task": t.id, "title": "guitar stand", "kind": "done", "text": "Done."}))
    finally:
        ss.CONNECTIONS.remove(c)
    assert c.got[0]["kind"] == "model" and c.got[0]["models"][0]["name"] == "stand.stl"


def test_the_window_can_turn_the_model():
    html = Path("interfaces/web/eva.html").read_text(encoding="utf-8")
    assert "function modelViewer(src)" in html and "function parseSTL(buf)" in html
    assert "model(d) {" in html and "'vision', 'model', 'gallery', 'media', 'project'].includes(d.kind)" in html
    assert "d.kind==='model'" in Path("interfaces/web/eva_classic.html").read_text(encoding="utf-8")


def test_a_port_in_use_is_found():
    import socket
    from interfaces.web import server_stream as ss
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        s.listen()
        port = s.getsockname()[1]
        assert ss.busy_ports([("127.0.0.1", port)]) == [port]
    assert ss.busy_ports([("127.0.0.1", port)]) == []
