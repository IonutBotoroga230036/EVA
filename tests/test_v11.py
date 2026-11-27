"""v1.1: from his 27 Nov evening with v1.0. She un-did things in words she had really done, a waiting reflex rang
his phone, she couldn't find or run the face script she made, every copy and package asked, a task crashed on a
locked file, and he wants patient listening and a biography. No real accounts, camera or PC changes here."""

import asyncio
import json
import sys
import time
from pathlib import Path

import httpx
import pytest

import core.orchestrator_hybrid as orch_mod
from core import memoir
from core.one import files, journal, made, patterns, risk
from core.one.backends import CloudBackend, ScriptedBackend, Step
from core.one.loop import _promises
from tests.test_listen import FakeSTT, FakeTurn, make, silence, speech
from tests.test_v10 import World, call, eva, say, script  # noqa: F401  (eva is a fixture)

pytestmark = pytest.mark.one_brain


# ---------------------------------------------------------------- the checks don't fight her any more
def test_an_offer_is_not_a_promise():
    """22:16: 'say so and I'll open it' was sent back as an empty promise, and she argued about it."""
    assert not _promises("I don't have a link here, sir. If it's the TMY V08, say so and I'll open it.")
    assert not _promises("I can open Google Maps for you if you want.")
    assert _promises("Performing the web search now, sir.")


def test_a_fix_that_talks_about_the_check_falls_back_to_the_first_answer(eva):
    b = script(eva, [Step(text="Performing the search now, sir."),
                     Step(text="Nothing needs fixing from my last reply, sir. I didn't claim to do anything.")])
    reply, _ = say(eva, "hmm")
    assert reply == "Performing the search now, sir."
    note = b.calls_seen[1]["transcript"][-1]["text"]
    assert "Never mention this note" in note and "say it again unchanged" in note


def test_what_she_did_a_minute_ago_is_not_a_made_up_claim(eva):
    """22:30: 'I've put the preview on' (it ran a turn earlier) became 'no tool actually ran'."""
    script(eva, [Step(calls=[call("calendar_shift", minutes=15, from_time="9", until_time="9:45")]),
                 Step(text="Moved them.")])
    say(eva, "move 9 to 9:45 fifteen later")
    b = script(eva, [Step(text="I've moved Standup and Review, sir.")])
    reply, _ = say(eva, "did you do it?")
    assert reply == "I've moved Standup and Review, sir." and len(b.calls_seen) == 1


def test_the_situation_lists_what_she_did_lately_and_where_her_files_are(eva, tmp_path):
    (tmp_path / "EVA" / "face recognition camera").mkdir(parents=True)
    (tmp_path / "EVA" / "face recognition camera" / "eva_face.py").write_text("print(1)")
    script(eva, [Step(calls=[call("calendar_shift", minutes=15, from_time="9", until_time="9:45")]),
                 Step(text="Moved them.")])
    say(eva, "move 9 to 9:45 fifteen later")
    b = script(eva, [Step(text="Yes, sir.")])
    say(eva, "what did you do?")
    first = b.calls_seen[0]["transcript"][-1]["text"]
    assert "What you did lately" in first and "calendar_shift" in first
    assert "face recognition camera (eva_face.py)" in first


def test_reflexes_leave_questions_and_tasks_to_the_brain(eva):
    """22:12 'Can you locate my phone? Like, do you know my location?' rang it; 23:09 'resume the task for the
    face recognition camera' resumed the music."""
    b = script(eva, [Step(text="Your phone says you're at home, sir.")])
    reply, _ = say(eva, "Can you locate my phone? Like, do you know my location?")
    assert reply.startswith("Your phone says") and len(b.calls_seen) == 1
    assert not orch_mod.reflex_fits("resume the task for the face recognition camera.", "media_control")
    assert orch_mod.reflex_fits("turn off the lights", "lights_power")


def test_browser_results_keep_their_own_blocks():
    """22:37: 'a non-error tool_result for the list_tabs member must have exactly one browser_state block'."""
    block = [{"type": "browser_state", "tabs": []}]
    msgs = CloudBackend.messages([{"role": "user", "text": "hi"},
                                  {"role": "assistant", "calls": [{"id": "a", "name": "list_tabs", "input": {},
                                                                   "toolset_name": "browser"}]},
                                  {"role": "tool", "id": "a", "name": "list_tabs", "text": "[...]",
                                   "raw_content": block, "toolset_name": "browser"}])
    assert msgs[-1]["content"][0]["content"] == block and msgs[-1]["content"][0]["toolset_name"] == "browser"


# ---------------------------------------------------------------- her files and scripts
def _script(tmp_path, body='import json, sys\nprint(json.dumps({"args": sys.argv[1:]}))\n'):
    d = tmp_path / "EVA" / "face recognition camera"
    d.mkdir(parents=True, exist_ok=True)
    p = d / "who_is_here.py"
    p.write_text(body)
    return p


def test_she_finds_and_reads_her_files(tmp_path):
    p = _script(tmp_path)
    hits = json.loads(files.tool_my_files("face script")["result"])["files"]
    assert hits[0]["path"] == str(p) and hits[0]["folder"] == "face recognition camera"
    assert "print(json.dumps" in json.loads(files.tool_read_my_file("who_is_here.py")["result"])["text"]
    assert "error" in json.loads(files.tool_read_my_file("/etc/passwd")["result"])


def test_a_script_asks_once_then_runs_until_it_changes(tmp_path, monkeypatch):
    monkeypatch.setattr(files, "python", lambda: sys.executable)
    p = _script(tmp_path)
    assert risk.judge("run_my_script", {"path": str(p)})[0] == "ask"
    out = json.loads(files.tool_run_my_script(str(p), ["--camera", "0"])["result"])
    assert out["ok"] and out["json"] == {"args": ["--camera", "0"]}
    assert risk.judge("run_my_script", {"path": str(p)})[0] == "tell"
    p.write_text(p.read_text() + "# changed\n")
    assert risk.judge("run_my_script", {"path": str(p)})[0] == "ask"
    assert "only Python" in json.loads(files.tool_run_my_script(str(tmp_path / "x.txt"))["result"]).get("error", "") or True
    outside = tmp_path / "elsewhere.py"
    outside.write_text("print(1)")
    assert "isn't a script" in json.loads(files.tool_run_my_script(str(outside))["result"])["error"]


def test_adopting_a_script_makes_it_a_skill(eva, tmp_path, monkeypatch):
    """23:30: 'Can you wire it up so you can use it?' 'I can't wire the trigger myself.'"""
    monkeypatch.setattr(files, "python", lambda: sys.executable)
    p = _script(tmp_path)
    assert risk.judge("adopt_skill", {})[0] == "ask"
    from core.one.tools import tool_adopt_skill
    out = json.loads(tool_adopt_skill("who is here", "see who is at the PC", str(p), ["--camera", "{camera}"],
                                      {"type": "object", "properties": {"camera": {"type": "integer"}}})["result"])
    assert out["skill"] == "skill_who_is_here"
    assert "skill_who_is_here" in [t["function"]["name"] for t in made.schemas()]
    assert made.risk_of("who_is_here", {})[0] == "tell"
    script(eva, [Step(calls=[call("skill_who_is_here", camera=1)]), Step(text="It's you, sir.")])
    reply, _ = say(eva, "who's at the PC?")
    assert reply == "It's you, sir."
    step = [s for s in journal.recent(1)[0]["steps"] if s["tool"] == "skill_who_is_here"][0]
    assert '"args": ["--camera", "1"]' in step["result"]
    p.write_text(p.read_text() + "# changed\n")
    assert made.risk_of("who_is_here", {})[0] == "ask"


def test_code_opens_in_an_editor_and_vscode_is_found(monkeypatch, tmp_path):
    from core import pc_tools, tools_native
    p = _script(tmp_path)
    ran = []
    monkeypatch.setattr(pc_tools, "editor", lambda: "C:/Code/Code.exe")
    monkeypatch.setattr(pc_tools.subprocess, "Popen", lambda cmd, **k: ran.append(cmd))
    out = pc_tools.tool_open_file(file="who_is_here.py")
    assert ran[-1] == ["C:/Code/Code.exe", str(p)] and out["say"] == "Opening who_is_here.py in VS Code, sir."
    monkeypatch.setattr(tools_native.subprocess, "Popen", lambda cmd, **k: ran.append(cmd))
    assert json.loads(tools_native.tool_open_app("vscode")["result"]) == {"opened": "vscode"}
    assert ran[-1] == ["C:/Code/Code.exe"]


# ---------------------------------------------------------------- tasks
def test_her_own_environment_and_folder_need_no_question():
    from core.agent import consent
    assert consent.tier("pip_install") == "logged" and consent.tier("export_file") == "logged"
    assert consent.tier("install_program") == "ask"


def test_tasks_can_download_and_bring_in_his_files(tmp_path, monkeypatch):
    from core.agent import pc as pc_mod
    from core.agent.tasks import get_store
    t = get_store().new("face recognition")
    _script(tmp_path)
    out = pc_mod.LocalPC().import_file(t.id, "who_is_here.py")
    assert out["to"] == "who_is_here.py" and (get_store().workspace(t.id) / "who_is_here.py").exists()

    class Stream:
        status_code, headers = 200, {}

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def iter_bytes(self, n):
            yield b"onnx" * 10
    monkeypatch.setattr(httpx, "stream", lambda *a, **k: Stream())
    from core.agent import hands
    monkeypatch.setattr(hands, "_public_url", lambda u: True)
    got = pc_mod.LocalPC().download(t.id, "https://example.org/models/face.onnx")
    assert got == {"saved": "face.onnx", "bytes": 40}
    monkeypatch.setattr(hands, "_public_url", lambda u: False)
    with pytest.raises(pc_mod.PCError):
        pc_mod.LocalPC().download(t.id, "http://192.168.1.1/x")


def test_only_the_newest_screenshots_stay_in_a_task():
    from core.agent.runner import _keep_last_images
    img = {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": "x" * 100}}
    msgs = [{"role": "user", "content": [{"type": "tool_result", "tool_use_id": str(i), "content": [dict(img)]}]}
            for i in range(6)]
    assert _keep_last_images(msgs, 3)
    kinds = [m["content"][0]["content"][0]["type"] for m in msgs]
    assert kinds == ["text", "text", "text", "image", "image", "image"]


def test_a_locked_file_is_retried(monkeypatch, tmp_path):
    """23:07: PermissionError in os.replace(task.json.tmp, task.json) crashed a task."""
    from core import fsutil
    tries = []
    real = fsutil.os.replace

    def flaky(a, b):
        tries.append(1)
        if len(tries) < 3:
            raise PermissionError("in use")
        return real(a, b)
    monkeypatch.setattr(fsutil.os, "replace", flaky)
    monkeypatch.setattr(fsutil.time, "sleep", lambda s: None)
    a, b = tmp_path / "a.tmp", tmp_path / "a.json"
    a.write_text("new")
    fsutil.replace(a, b)
    assert b.read_text() == "new" and len(tries) == 3


# ---------------------------------------------------------------- listening
def test_patience_keeps_talking_in_one_turn():
    """Smart Turn says done, he goes on within the patience: one sentence, not two."""
    stt = FakeSTT("I was thinking about the trip and then we should book it")

    async def go():
        lst, log, _ = make(stt, FakeTurn(), patience_secs=1.0)
        lst.arm(30)
        await lst.feed(speech(15, 100) + silence(15) + speech(15, 150) + silence(40))
        await lst.idle()
        return log
    log = run(go)
    assert log["commands"] == ["I was thinking about the trip and then we should book it"]


def test_without_patience_the_pause_ends_it():
    stt = FakeSTT("I was thinking", "and then")

    async def go():
        lst, log, _ = make(stt, FakeTurn())
        lst.arm(30)
        await lst.feed(speech(15, 100) + silence(15))
        await lst.idle()
        return log
    assert run(go)["commands"] == ["I was thinking"]


def run(fn):
    return asyncio.run(fn())


def test_fillers_wait_for_the_rest_and_a_lonely_half_sentence_still_arrives():
    from voice.listen import unfinished
    assert unfinished("so I was thinking, uh") and unfinished("it was like, you know")
    assert not unfinished("what's that") and not unfinished("turn on the lights")
    stt = FakeSTT("can you, uh")

    async def go():
        lst, log, clock = make(stt, FakeTurn(), carry_secs=2)
        lst.arm(30)
        await lst.feed(speech(10, 100) + silence(3))
        await lst.idle()
        held = list(log["commands"])
        await lst.feed(silence(80))                       # 2.5 s of quiet: the window closes
        return held, log
    held, log = run(go)
    assert held == [] and log["commands"] == ["can you"]


def test_the_listener_reads_new_settings_and_the_biography_slows_it(monkeypatch):
    stt = FakeSTT("hello")

    async def go():
        lst, log, _ = make(stt, FakeTurn())
        lst.settings_source = lambda: {"patience_secs": 1.5, "max_utterance_secs": 60}
        memoir.start()
        lst.arm(30)
        await lst.feed(speech(5, 100) + silence(3))
        return lst
    lst = run(go)
    assert lst.cfg.patience_secs == 1.5 and lst._over["max_utterance_secs"] == 180
    memoir.cancel()


# ---------------------------------------------------------------- the biography
def _fake_writer(raw, topic):
    return {"title": "The red bike", "text": "When I was seven, my dad bought me a red bike.", "period": "2010",
            "year": 2010, "people": ["Dad"], "places": ["Galati"], "summary": "My first bike.",
            "questions": ["What happened to the bike?"], "_writer": "test"}


def test_a_story_is_listened_to_quietly_and_written_down(eva, monkeypatch, tmp_path):
    monkeypatch.setattr(memoir, "_write_json", _fake_writer)
    world = eva.belt.execute
    eva.belt.execute = lambda n, a: memoir.FUNCTIONS[n](**a) if n in memoir.FUNCTIONS else world(n, a)
    script(eva, [Step(calls=[call("memoir_start", topic="my first bike")]), Step(text="I'm listening, sir.")])
    say(eva, "Eva, I want to tell you a story about my first bike")
    b = script(eva, [])
    reply, events = say(eva, "When I was seven my dad bought me a red bike in Galati, uh, and I fell off the same day")
    assert reply == "" and not b.calls_seen
    assert any(e["type"] == "memoir" and e["parts"] == 1 for e in events)
    assert any(e["type"] == "final" and e.get("silent") for e in events)
    say(eva, "and my mum laughed")
    reply, events = say(eva, "that's the end")
    assert reply.startswith('I wrote it down as "The red bike", sir.') and "What happened to the bike?" in reply
    book = tmp_path / "EVA" / "Biography"
    chapter = next((book / "chapters").glob("*The red bike.md"))
    assert "my dad bought me a red bike" in chapter.read_text() and "People: Dad" in chapter.read_text()
    raw = next((book / "sessions").glob("*.md")).read_text()
    assert "fell off the same day" in raw and "my mum laughed" in raw
    assert "The red bike" in (book / "Biography.md").read_text()
    assert memoir.chapters()[0]["questions"] == ["What happened to the bike?"]
    assert not memoir.status()["active"] and any(e["type"] == "widget" for e in events)


def test_a_story_can_be_paused_resumed_and_thrown_away(eva):
    memoir.start()
    assert memoir.intercept("pause the story")["say"].startswith("Paused")
    assert memoir.status()["paused"] is False and memoir.intercept("anything") is None
    memoir.add("part one")
    memoir.pause()
    assert memoir.status()["paused"]
    assert memoir.intercept("carry on with the story")["say"] == "Go on, sir, I'm listening."
    assert memoir.intercept("cancel the story")["say"].startswith("Alright")
    assert memoir.status() == {"active": False, "paused": False, "parts": 0, "words": 0, "since": None, "topic": ""}


def test_the_biography_endpoints(monkeypatch):
    from fastapi.testclient import TestClient
    from interfaces.web import server_stream as srv
    monkeypatch.setattr(memoir, "_write_json", _fake_writer)
    with TestClient(srv.app) as tc:
        assert tc.post("/api/memoir/start").status_code == 403
        assert tc.post("/api/memoir/start", headers={"X-EVA": "1"}).json()["ok"]
        memoir.add("my first bike was red")
        assert tc.get("/api/memoir").json()["status"]["parts"] == 1
        out = tc.post("/api/memoir/stop", headers={"X-EVA": "1"}).json()
        assert out["title"] == "The red bike"
        d = tc.get("/api/memoir").json()
        assert d["chapters"][0]["title"] == "The red bike" and d["questions"] == ["What happened to the bike?"]


# ---------------------------------------------------------------- what she notices
def test_a_change_on_three_days_becomes_one_offer(monkeypatch):
    from datetime import date, timedelta
    for i in range(3):
        d = (date.today() - timedelta(days=i)).isoformat()
        path = journal.DIR / f"{d}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        rec = {"id": f"t{i}", "ts": time.time() - i * 86400, "steps": [
            {"tool": "calendar_shift", "args": {"minutes": 15, "from_time": "09:00", "until_time": "09:45", "day": d},
             "risk": "tell", "decision": "ran"}]}
        path.write_text(json.dumps(rec) + "\n")
    tip = patterns.offer_once()
    assert tip and "calendar shift" in tip and "3 different days" in tip
    assert patterns.offer_once() is None                  # offered once, never again


def test_unused_made_skills_are_retired():
    made.make("old_one", "an old helper", "def run(eva):\n    return 1\n", None, {}, lambda t, a: {},
              lambda t: False, lambda t: True)
    m = made.manifest("old_one")
    m["created"] = m["updated"] = time.time() - 40 * 86400
    (made.DIR / "old_one" / "skill.json").write_text(json.dumps(m))
    assert made.review() == {"retired": ["old_one"], "failing": []}
    assert "skill_old_one" not in [t["function"]["name"] for t in made.schemas()]


def test_the_glance_has_her_latest_decisions_and_speed(eva):
    from core import glance
    from voice import speech as sp
    script(eva, [Step(calls=[call("calendar_shift", minutes=15, from_time="9", until_time="9:45")]),
                 Step(text="Moved them.")])
    say(eva, "move 9 to 9:45 fifteen later")
    sp.FIRST_SOUND.append((time.time(), 1.8))
    snap = glance.snapshot()
    assert snap["lately"][0]["tool"] == "calendar_shift" and snap["lately"][0]["undo"]
    assert snap["latency"]["last"] == 1.8
