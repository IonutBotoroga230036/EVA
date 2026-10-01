"""v0.3 round 2: actions later (10b), phone alarms and timers (9c), wake-word recorder (9b prep), and the two
phone fixes (wake button state, media audio)."""

import asyncio
import json
import threading
import time
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

import core.later as later
import core.orchestrator_hybrid as orch_mod
from core import device
from core.memory.cortex import Cortex
from core.orchestrator_hybrid import ToolBelt, fast_path
from skills.registry import SkillRegistry

ROOT = Path(__file__).resolve().parents[1]


# ------------------------------------------------------------ actions later
@pytest.mark.parametrize("text,clean,secs", [
    ("turn the lights off in 5 minutes", "turn the lights off", 300),
    ("stop the music in 10 minutes", "stop the music", 600),
    ("in half an hour, turn the lights red", "turn the lights red", 1800),
    ("pause the music in an hour", "pause the music", 3600),
    ("turn the volume down in 30 seconds", "turn the volume down", 30),
    ("remind me to call mom in 5 minutes", "remind me to call mom in 5 minutes", None),   # reminders keep their time
    ("set a timer for 10 minutes", "set a timer for 10 minutes", None),
    ("what's the weather", "what's the weather", None),
])
def test_split_delay(text, clean, secs):
    assert later.split_delay(text) == (clean, secs)


def test_the_scheduler_runs_the_tool_then_reports_its_own_result():
    done, told = [], []

    async def execute(tool, args):
        done.append((tool, args))
        return {"result": json.dumps({"ok": True}), "say": "Lights off, sir."}

    async def announce(text, session=""):
        told.append(text)

    async def go():
        lt = later.Later()
        lt.start(asyncio.get_running_loop(), execute, announce)
        lt.add("lights_power", {"on": False}, 0.05, "turn the lights off")
        await asyncio.sleep(0.3)
        return lt
    lt = asyncio.run(go())
    assert done == [("lights_power", {"on": False})] and told == ["As planned: Lights off, sir."]
    assert lt.pending() == [] and json.loads(later.STORE.read_text(encoding="utf-8")) == []


def test_a_restart_keeps_plans_but_skips_ones_that_are_far_too_late():
    later.STORE.write_text(json.dumps([
        {"id": "a", "tool": "media_control", "args": {}, "due": time.time() + 600, "text": "stop the music",
         "status": "pending"},
        {"id": "b", "tool": "lights_power", "args": {}, "due": time.time() - 7200, "text": "lights off",
         "status": "pending"}]), encoding="utf-8")

    async def go():
        lt = later.Later()
        lt.start(asyncio.get_running_loop(), None, None)
        return [i["text"] for i in lt.pending()]
    assert asyncio.run(go()) == ["stop the music"]


def test_cancel_and_list_by_voice():
    from core.tools_native import tool_later_cancel, tool_later_list
    lt = later.get_later()
    lt.add("media_control", {"action": "playpause"}, 600, "stop the music")
    assert "stop the music" in tool_later_list()["say"]
    assert tool_later_cancel(what="the music")["say"] == "Cancelled: stop the music, sir."
    assert tool_later_list()["say"] == "Nothing is planned for later, sir."


def fake_ollama(plan_steps=None, decide=None):
    def handler(request):
        body = json.loads(request.content)
        props = (body.get("format") or {}).get("properties", {})
        if "steps" in props:
            return httpx.Response(200, json={"message": {"content": json.dumps({"steps": plan_steps or []})}})
        if "facts" in props:
            return httpx.Response(200, json={"message": {"content": json.dumps({"facts": []})}})
        if "tool" in props:
            last = body["messages"][-1]["content"].lower()
            d = next((v for k, v in (decide or {}).items() if k in last), {"tool": "none"})
            return httpx.Response(200, json={"message": {"content": json.dumps(d)}})
        if body.get("format"):
            return httpx.Response(200, json={"message": {"content": "{}"}})
        return httpx.Response(200, text=json.dumps({"message": {"content": "ok"}, "done": True}))
    real = httpx.AsyncClient
    return lambda *a, **k: real(transport=httpx.MockTransport(handler))


def turn(o, text):
    async def go():
        evs = [e async for e in o.process_stream(text)]
        if o._bg:
            await asyncio.gather(*o._bg, return_exceptions=True)
        return [e for e in evs if e["type"] == "final"][-1]["text"]
    return asyncio.run(go())


@pytest.fixture
def orch(tmp_path, monkeypatch):
    def make(**kw):
        monkeypatch.setattr(orch_mod.httpx, "AsyncClient", fake_ollama(**kw))
        return orch_mod.HybridOrchestrator(session_id="l", cortex=Cortex(str(tmp_path / "c.db")),
                                           registry=SkillRegistry("skills").discover())
    return make


def test_lights_off_in_5_minutes_is_planned_not_done(orch, monkeypatch):
    monkeypatch.setattr(orch_mod, "fast_path", lambda *a, **k: None)
    o = orch(decide={"lights off": {"tool": "lights_power", "on": False}})
    text = turn(o, "turn the lights off in 5 minutes")
    assert text.startswith("Okay, sir: turn the lights off in 5 minutes, at ")
    (item,) = later.get_later().pending()
    assert item["tool"] == "lights_power" and 290 < item["due"] - time.time() <= 300


def test_two_delays_in_one_sentence(orch, monkeypatch):
    monkeypatch.setattr(orch_mod, "fast_path", lambda *a, **k: None)
    steps = [{"command": "close the lights in 5 minutes", "uses_previous": False},
             {"command": "stop the music in 10 minutes", "uses_previous": False}]
    o = orch(plan_steps=steps, decide={"close the lights": {"tool": "lights_power", "on": False},
                                       "stop the music": {"tool": "media_control", "action": "playpause"}})
    text = turn(o, "close the lights in 5 minutes and stop the music in 10 minutes")
    assert "close the lights in 5 minutes" in text and "stop the music in 10 minutes" in text
    assert [i["tool"] for i in later.get_later().pending()] == ["lights_power", "media_control"]


def test_actions_that_need_a_yes_are_never_scheduled(orch):
    o = orch()
    text = turn(o, "delete the drafts you made in 5 minutes")
    assert "needs your yes at the moment it happens" in text and later.get_later().pending() == []


# ------------------------------------------------------------ phone alarms and timers
@pytest.mark.parametrize("text,hm", [("7", (7, 0)), ("7:30 am", (7, 30)), ("7.30 pm", (19, 30)), ("19:05", (19, 5)),
                                     ("12 am", (0, 0)), ("half past 6", (6, 30)), ("noon-ish", None)])
def test_parse_clock(text, hm):
    assert device.parse_clock(text) == hm


@pytest.mark.parametrize("text,secs", [("10 minutes", 600), ("1 hour 30 minutes", 5400), ("90 seconds", 90),
                                       ("half an hour", 1800), ("a while", None)])
def test_parse_duration(text, secs):
    assert device.parse_duration(text) == secs


@pytest.mark.parametrize("text,call", [
    ("set an alarm for 7:30 am", ("phone_alarm", {"time": "7:30 am"})),
    ("wake me up at 7 tomorrow", ("phone_alarm", {"time": "7 tomorrow"})),
    ("set a timer for 10 minutes", ("phone_timer", {"duration": "10 minutes"})),
])
def test_phone_fast_paths(text, call):
    assert fast_path(text, ToolBelt(None, None)) == call


def test_no_phone_app_open_is_said_plainly(monkeypatch):
    monkeypatch.setattr(device, "phones", lambda cap: [])
    assert device.tool_phone_alarm(time="7:30")["say"] == device.NO_PHONE


def test_the_phone_is_asked_and_its_answer_decides(monkeypatch):
    loop = asyncio.new_event_loop()
    t = threading.Thread(target=loop.run_forever, daemon=True)
    t.start()
    asked = []

    class Phone:
        device_caps = ("alarm", "timer")

        async def request_device(self, action, args, timeout):
            asked.append((action, args))
            return {"ok": action == "set_alarm", "error": "" if action == "set_alarm" else "clock app refused"}
    monkeypatch.setattr(device, "LOOP", loop)
    monkeypatch.setattr(device, "phones", lambda cap: [Phone()])
    try:
        assert device.tool_phone_alarm(time="7:30 am")["say"] == "Alarm set on your phone for 07:30, sir."
        assert device.tool_phone_timer(duration="10 minutes")["say"] == \
            "Your phone couldn't start the timer, sir: clock app refused."
        assert asked[0] == ("set_alarm", {"hour": 7, "minute": 30, "label": "E.V.A."})
        assert asked[1] == ("set_timer", {"seconds": 600, "label": "E.V.A."})
    finally:
        loop.call_soon_threadsafe(loop.stop)
        t.join(2)


def test_server_round_trip_with_the_app(monkeypatch):
    import interfaces.web.server_stream as srv
    monkeypatch.setattr(srv, "get_tts", lambda: None)
    with TestClient(srv.app) as tc, tc.websocket_connect("/ws") as ws:
        ws.receive_text(); ws.receive_text()
        ws.send_text(json.dumps({"type": "device", "kind": "android", "caps": ["alarm", "timer"]}))
        time.sleep(0.2)
        result = {}
        th = threading.Thread(target=lambda: result.update(device.tool_phone_alarm(time="6:45")))
        th.start()
        while True:
            ev = json.loads(ws.receive_text())
            if ev["type"] == "device_action":
                break
        assert ev["action"] == "set_alarm" and ev["args"] == {"hour": 6, "minute": 45, "label": "E.V.A."}
        ws.send_text(json.dumps({"type": "device_result", "id": ev["id"], "ok": True}))
        th.join(5)
    assert result["say"] == "Alarm set on your phone for 06:45, sir."


# ------------------------------------------------------------ wake-word recorder
def test_recorder_saves_only_small_wav_clips(tmp_path, monkeypatch):
    import interfaces.web.server_stream as srv
    monkeypatch.setattr(srv, "WAKE_DIR", tmp_path / "wake")
    monkeypatch.setattr(srv, "get_tts", lambda: None)
    wav = b"RIFF" + b"\x00" * 4 + b"WAVE" + b"\x00" * 2000
    with TestClient(srv.app) as tc:
        assert tc.get("/wakeword").status_code == 200
        r = tc.post("/api/wakeword/sample?label=eva", content=wav)
        assert r.status_code == 200 and r.json()["counts"]["eva"] == 1
        assert tc.post("/api/wakeword/sample?label=../../x", content=wav).status_code == 400
        assert tc.post("/api/wakeword/sample?label=eva", content=b"not a wav" * 200).status_code == 400
        assert tc.get("/api/wakeword/status").json()["counts"] == {"eva": 1, "hey_eva": 0, "other": 0, "speech": 0}
    assert (tmp_path / "wake" / "eva" / "eva_001.wav").exists()


# ------------------------------------------------------------ page and app wiring
HTML = (ROOT / "interfaces/web/eva.html").read_text(encoding="utf-8")
DART = (ROOT / "apps/eva_android/lib/main.dart").read_text(encoding="utf-8")
KT = (ROOT / "apps/eva_android/android_overlay/app/src/main/kotlin/nl/ionut/eva_android/MainActivity.kt").read_text(encoding="utf-8")
MANIFEST = (ROOT / "apps/eva_android/android_overlay/app/src/main/AndroidManifest.xml").read_text(encoding="utf-8")


def test_the_ear_button_and_the_logic_share_one_state():
    assert "localStorage.getItem('eva.wake')" in HTML and "localStorage.setItem('eva.wake'" in HTML
    assert "btn.setAttribute('aria-pressed', String(wakeMode))" in HTML


def test_media_mode_pauses_the_mic_while_she_speaks():
    assert "if (phoneMedia()) { if (on) micPause(); else micResume(); }" in HTML
    assert "function micPause()" in HTML and "async function micResume()" in HTML
    assert "audio=call" in HTML and "' audio=call'" in DART


def test_alarms_travel_page_to_app_to_clock():
    assert "case 'device_action':" in HTML and "window.EvaNative.postMessage" in HTML
    assert "addJavaScriptChannel('EvaNative'" in DART and "'deviceAction'" in DART and '"deviceAction"' in KT
    assert "AlarmClock.ACTION_SET_ALARM" in KT and "AlarmClock.ACTION_SET_TIMER" in KT
    assert "com.android.alarm.permission.SET_ALARM" in MANIFEST


def test_media_mode_asks_for_the_mic_without_echo_cancellation():
    """Sep 29 20:44: music and her voice jumped from Bluetooth headphones to the phone (call mode)."""
    html = (ROOT / "interfaces/web/eva.html").read_text(encoding="utf-8")
    assert "echoCancellation: !phoneMedia()" in html
    assert html.count("getUserMedia({ audio: micConstraints() })") == 2          # start and resume
