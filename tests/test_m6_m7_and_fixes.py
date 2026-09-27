"""v0.2.5 milestone 6 (FORGE background jobs), milestone 7 (local-first switches), and the Sep 27 log fixes."""

import asyncio
import json
import threading
import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import core.brain as brain_mod
import core.forge_jobs as fj
from core.brain import Brain
from core.orchestrator_hybrid import _CLAIM, ToolBelt, fast_path


def proposal(status="ready", name="moon_phase"):
    return SimpleNamespace(name=name, status=status, reason="needs a paid key" if status != "ready" else "",
                           summary="It tells you the moon phase.", network_hosts=[], tools=["moon_phase"],
                           tests_passed=4, cost_eur=0.0, provider="qwen2.5-coder:14b")


class FakeForge:
    def __init__(self, result=None, delay=0.0, boom=None, during=None):
        self.result, self.delay, self.boom, self.during, self.discarded = result or proposal(), delay, boom, during, []

    def build(self, request):
        if self.during:
            self.during()
        time.sleep(self.delay)
        if self.boom:
            raise self.boom
        return self.result

    def discard(self, name):
        self.discarded.append(name)


# ------------------------------------------------------------ milestone 6: jobs
def test_a_build_runs_and_every_channel_hears_the_result():
    done = []
    m = fj.JobManager(forge_factory=lambda: FakeForge(), on_done=lambda j, msg: done.append((j, msg)), threaded=False)
    job = m.submit("tell me the moon phase")
    assert job.status == "done" and job.result_name == "moon_phase"
    (_, msg), = done
    assert msg["say"].startswith("Your skill is ready, sir: moon phase.") and "Shall I install it?" in msg["say"]
    assert msg["confirm_next"]["tool"] == "forge_install" and "Built locally with qwen2.5-coder:14b" in msg["say"]


def test_the_build_really_runs_in_the_background():
    finished = threading.Event()
    m = fj.JobManager(forge_factory=lambda: FakeForge(delay=0.5), on_done=lambda j, msg: finished.set())
    t0 = time.monotonic()
    job = m.submit("slow skill")
    assert time.monotonic() - t0 < 0.2                      # the conversation isn't blocked
    assert finished.wait(5) and job.status == "done"


def test_cancel_a_queued_job():
    m = fj.JobManager(forge_factory=lambda: FakeForge(), threaded=False)
    job = fj.Job(id="q1", request="later")
    m.jobs.append(job)
    assert m.cancel().status == "cancelled"


def test_cancel_while_running_throws_the_result_away():
    forge, done = None, []
    m = fj.JobManager(on_done=lambda j, msg: done.append(msg), threaded=False)
    forge = FakeForge(during=lambda: m.cancel())
    m._forge_factory = lambda: forge
    job = m.submit("never mind")
    assert job.status == "cancelled" and forge.discarded == ["moon_phase"] and done == []


def test_a_failed_build_says_so():
    done = []
    m = fj.JobManager(forge_factory=lambda: FakeForge(boom=RuntimeError("ollama pull qwen2.5-coder:14b")),
                      on_done=lambda j, msg: done.append(msg), threaded=False)
    job = m.submit("x")
    assert job.status == "failed" and "qwen2.5-coder:14b" in done[0]["say"]


def test_a_build_interrupted_by_a_restart_is_marked(tmp_path):
    fj.JOBS_PATH.write_text(json.dumps([{"id": "r1", "request": "moon", "status": "running", "created": 1.0}]),
                            encoding="utf-8")
    job = fj.JobManager(threaded=False).latest()
    assert job.status == "interrupted" and "restarted" in job.error


def test_forge_tools_queue_report_and_cancel(monkeypatch):
    from skills.forge import tools as ft
    m = fj.get_jobs()
    m._forge_factory = lambda: FakeForge()
    out = ft.forge_build(request="tell me the moon phase")
    assert "building it in the background locally" in out["say"] and "keep talking" in out["say"]
    assert "waiting for your approval" in ft.forge_status()["say"]
    running = fj.Job(id="r", request="a weather radar", status="running", started=time.time() - 130)
    m.jobs.append(running)
    assert ft.forge_status()["say"].startswith('Still building "a weather radar", sir, 2 minutes so far.')
    assert "stop as soon as" in ft.forge_cancel()["say"] and running.cancel_requested


def test_default_delivery_pushes_telegram_buttons(monkeypatch):
    from core import telegram_bridge
    sent = []
    monkeypatch.setattr(telegram_bridge, "send_from_thread", lambda text, **k: sent.append((text, k)) or True)
    fj.default_on_done(fj.Job(id="x", request="moon"), fj.result_message(proposal()))
    text, kw = sent[0]
    assert "Shall I install it?" in text
    assert kw["buttons"] == [[{"text": "Install", "callback_data": "forge:install:moon_phase"},
                              {"text": "Discard", "callback_data": "forge:discard:moon_phase"}]]


def test_telegram_install_button_works_for_you_only(monkeypatch):
    from core.telegram_bridge import TelegramBridge
    from skills.forge import tools as ft
    installed, sent = [], []
    monkeypatch.setattr(ft, "forge_install", lambda name="": installed.append(name) or {"say": "Installed, sir."})
    b = TelegramBridge("t", allowed_ids={1})

    async def call(method, **kw):
        return {}

    async def send(chat_id, text, buttons=False):
        sent.append(text)
    b.call, b.send = call, send
    cq = lambda uid: {"callback_query": {"id": "c", "from": {"id": uid}, "data": "forge:install:moon_phase",
                                         "message": {"chat": {"id": uid}}}}
    asyncio.run(b.handle_update(cq(2)))                      # a stranger's tap does nothing
    asyncio.run(b.handle_update(cq(1)))
    assert installed == ["moon_phase"] and sent == ["Installed, sir."]


def test_a_finished_build_reaches_an_open_window_from_a_worker_thread(monkeypatch):
    import interfaces.web.server_stream as srv
    from core.events.bus import get_bus
    monkeypatch.setattr(srv, "get_tts", lambda: None)
    with TestClient(srv.app) as tc, tc.websocket_connect("/ws") as ws:
        ws.receive_text(); ws.receive_text()                  # hello, stt
        msg = fj.result_message(proposal())
        t = threading.Thread(target=lambda: get_bus().publish("forge.done", {"message": msg}))
        t.start(); t.join()
        seen = []
        while True:
            ev = json.loads(ws.receive_text())
            seen.append(ev)
            if ev["type"] == "final":
                break
        assert seen[-1]["text"] == msg["say"]
        conn = next(iter(srv.CONNECTIONS))
        assert conn.orch.pending["tool"] == "forge_install" and conn.orch.pending["args"] == {"name": "moon_phase"}


# ------------------------------------------------------------ milestone 7: local-first switches
class FakeClaude:
    def __init__(self, ready=True):
        self.ready = ready
        self.budget = SimpleNamespace(can_spend=lambda eur: True)
        self.calls = []

    def available(self):
        return self.ready

    def message(self, **kw):
        self.calls.append(kw)
        return {"tool_input": {"steps": [{"command": "turn the lights red", "uses_previous": False},
                                         {"command": "play The Weeknd", "uses_previous": False}]}, "cost_eur": 0.001}


def test_local_is_the_default_everywhere():
    b = Brain(claude=FakeClaude())
    assert b.mode() == "local" and all(b.pick(f) == "local" for f in brain_mod.FEATURES)


def test_one_feature_can_use_claude_while_the_rest_stays_local():
    b = Brain(claude=FakeClaude())
    b.set_feature("forge", "cloud")
    assert b.pick("forge") == "cloud" and b.pick("thinking") == "local" and b.pick() == "local"
    ov = b.overview()
    assert ov["features"]["forge"] == {"setting": "cloud", "effective": "cloud"}
    assert ov["features"]["planning"] == {"setting": "default", "effective": "local"}
    b.set_feature("forge", "default")
    assert b.pick("forge") == "local"


def test_auto_without_claude_runs_locally():
    b = Brain(claude=FakeClaude(ready=False))
    b.set_feature("thinking", "auto")
    assert b.overview()["features"]["thinking"]["effective"] == "local"


@pytest.mark.parametrize("text,args", [
    ("use Claude for FORGE", {"mode": "cloud", "feature": "forge"}),
    ("keep thinking local", {"mode": "local", "feature": "thinking"}),
    ("switch planning to auto", {"mode": "auto", "feature": "planning"}),
    ("switch to local mode", {"mode": "local"}),
])
def test_voice_switches(text, args):
    assert fast_path(text, ToolBelt(None, None)) == ("set_brain_mode", args)


def test_voice_switch_per_feature_says_what_changed():
    from core.tools_native import tool_set_brain_mode
    out = tool_set_brain_mode(mode="cloud", feature="forge")
    assert out["say"].startswith("Done, sir. FORGE now uses Claude.")
    assert brain_mod.get_brain().feature_mode("forge") == "cloud"


def test_planning_on_claude_when_chosen(monkeypatch):
    from core import multistep
    fake = FakeClaude()
    b = Brain(claude=fake)
    b.set_feature("planning", "cloud")
    monkeypatch.setattr(brain_mod, "_brain", b)
    steps = asyncio.run(multistep.plan(None, {}, "lights red and play The Weeknd"))   # no local client needed
    assert [s["command"] for s in steps] == ["turn the lights red", "play The Weeknd"] and fake.calls


def test_status_panel_api(monkeypatch):
    import interfaces.web.server_stream as srv
    monkeypatch.setattr(srv, "get_tts", lambda: None)
    with TestClient(srv.app) as tc:
        ov = tc.post("/api/brain", json={"feature": "forge", "mode": "cloud"}).json()
        assert ov["features"]["forge"]["setting"] == "cloud"
        assert tc.post("/api/brain", json={"feature": "coffee", "mode": "cloud"}).status_code == 400
        assert tc.post("/api/brain", json={"mode": "auto"}).json()["mode"] == "auto"
        status = tc.get("/api/status").json()
        assert status["brain"]["features"]["forge"]["setting"] == "cloud"
        assert status["forge_jobs"] == {"running": "", "minutes": 0, "waiting": 0}


# ------------------------------------------------------------ Sep 27 log
@pytest.mark.parametrize("text,unfinished", [
    ("and...", True), ("Can you also...", True), ("Would you...", True), ("turn the lights red and", True),
    ("turn the lights red", False), ("what time is it?", False), ("Thank you.", False),
])
def test_unfinished_sentences(text, unfinished):
    from voice.listen import unfinished as check
    assert check(text) is unfinished


def test_an_unfinished_sentence_waits_for_the_rest():
    from tests.test_listen import FakeSTT, make, run, silence, speech
    stt = FakeSTT("and...", "put the volume to 90%")

    async def go():
        lst, log, _ = make(stt)
        lst.arm()
        await lst.feed(speech(10) + silence(3))
        await lst.idle()
        assert log["commands"] == [] and lst.armed()          # held, and still listening
        await lst.feed(speech(10, 140) + silence(3))
        await lst.idle()
        return log
    assert run(go)["commands"] == ["and put the volume to 90%"]


def test_volume_self_correction_uses_the_last_number():
    belt = ToolBelt(None, None)
    said = "and can you also put the volume up to 75% maybe or no actually can you put them to 90%"
    assert fast_path(said, belt) == ("set_volume", {"level": 90})
    belt.by_name.setdefault("spotify_volume", {})
    assert fast_path("spotify volume up to 60 percent", belt) == ("spotify_volume", {"level": 60})
    assert fast_path("volume up", belt) == ("media_control", {"action": "volup"})


def test_deleting_drafts_never_reaches_memory():
    import re
    from skills.memory import tools as mem
    belt = ToolBelt(None, None)
    belt.by_name.setdefault("forget_memory", {})
    belt.by_name.setdefault("email_delete_drafts", {})
    for said in ("can you delete all the drafts?", "Now I want you to delete the email drafts that you just made."):
        assert fast_path(said, belt) == ("email_delete_drafts", {})
        assert not re.search(mem.GUARDS["forget_memory"], said, re.I)
    assert re.search(mem.GUARDS["forget_memory"], "forget that I like jazz", re.I)


def test_only_drafts_eva_made_are_deleted(monkeypatch):
    import core.scribe as scribe
    from skills.email import tools as et
    deleted = []

    class G:
        def delete_draft(self, draft_id):
            if draft_id == "gone":
                raise Exception("<HttpError 404 ... Requested entity was not found.>")
            deleted.append(draft_id)
    assert "no drafts of mine" in et.email_delete_drafts()["say"]
    scribe.record_draft("d1", "muaad@example.com", "Hi")
    scribe.record_draft("gone", "tom@example.com", "Re")
    assert et._describe_delete({}) == "delete the 2 email drafts I made (to muaad@example.com, tom@example.com)"
    monkeypatch.setattr(et, "_google", lambda: G())
    out = et.email_delete_drafts()
    assert deleted == ["d1"] and out["say"] == ("Deleted the 1 draft I made, sir. 1 was already gone. "
                                                "Your own drafts are untouched.")
    assert scribe.made_drafts() == [] and et.CONFIRM["email_delete_drafts"] is et._describe_delete


def test_claim_guard_catches_the_invented_lights_line():
    assert _CLAIM.search("Lights set to red, the Weeknd's \"Blinding Lights\" playing, sir.")


def test_no_follow_up_window_after_music_starts(monkeypatch):
    import interfaces.web.server_stream as srv
    from tests.test_server import collect_until
    monkeypatch.setattr(srv, "get_tts", lambda: None)
    monkeypatch.setattr(srv, "MUSIC_TOOLS", {"get_datetime"})     # stands in for spotify_play: local and harmless
    with TestClient(srv.app) as tc, tc.websocket_connect("/ws") as ws:
        ws.receive_text(); ws.receive_text()
        ws.send_text(json.dumps({"type": "message", "text": "what time is it"}))
        meta = collect_until(ws, lambda e: e["type"] == "turn_meta")[-1]
    assert meta["follow_up"] is False


def test_bridge_handles_the_new_events():
    from pathlib import Path
    html = (Path(__file__).resolve().parents[1] / "interfaces" / "web" / "eva.html").read_text(encoding="utf-8")
    for needle in ("case 'turn_meta'", "skipFollow", "emit('brain-feature'", "fetch('/api/brain'", "seg seg--sm",
                   "s.forgeJob && s.forgeJob.running"):
        assert needle in html, needle
    assert "send(`switch to ${e.detail.mode} mode`" not in html          # a switch is not a chat message anymore
