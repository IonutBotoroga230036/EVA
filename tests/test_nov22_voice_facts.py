"""Nov 22 phone log: "also stop the music at that time" ran at once, "No, not right now, but 211" was answered with
an invented "Music stopped at that time", "Tomorrow looks clear" came with no calendar check. Plus the phone's
wake-word barge-in and "let's talk"."""

import asyncio
import json
from datetime import datetime

import httpx
import numpy as np
import pytest

import core.later as later
import core.orchestrator_hybrid as orch_mod
from core.memory.cortex import Cortex
from skills.registry import SkillRegistry


# ---------------------------------------------------------------- clock times and "at that time"
NOW = datetime(2026, 11, 22, 2, 1, 40)


@pytest.mark.parametrize("text,clean,mins", [
    ("Can you stop the music at 2:15", "Can you stop the music", 13.33),
    ("stop music at 211", "stop music", 9.33),
    ("lights off at 7 in the evening", "lights off in the evening", 17 * 60 - 1.67 + 0.0),
    ("turn the lights off at 11 pm", "turn the lights off", 20 * 60 + 58.33),
])
def test_clock_times(text, clean, mins):
    c, secs = later.split_clock(text, NOW)
    assert c == clean and abs(secs / 60 - mins) < 0.1


def test_bare_times_only_in_corrections_and_never_for_reminders():
    assert later.split_clock("No, not right now, but 211", NOW)[1] is None
    c, secs = later.split_clock("No, not right now, but 211", NOW, bare=True)
    assert later.is_correction(c) and abs(secs / 60 - 9.33) < 0.1
    assert later.split_clock("remind me at 5 to call mom", NOW) == ("remind me at 5 to call mom", None)


def test_same_time_words():
    assert later.split_same_time("Can you also stop the music at that time?") == ("Can you also stop the music", True)
    assert later.split_same_time("what time is it")[1] is False


def fake_ollama(decide, answer="ok"):
    def handler(request):
        body = json.loads(request.content)
        props = (body.get("format") or {}).get("properties", {})
        if "steps" in props:
            return httpx.Response(200, json={"message": {"content": json.dumps({"steps": []})}})
        if "facts" in props:
            return httpx.Response(200, json={"message": {"content": json.dumps({"facts": []})}})
        if "tool" in props:
            last = body["messages"][-1]["content"].lower()
            d = next((v for k, v in decide.items() if k in last), {"tool": "none"})
            return httpx.Response(200, json={"message": {"content": json.dumps(d)}})
        if body.get("format"):
            return httpx.Response(200, json={"message": {"content": "{}"}})
        if body.get("stream"):
            lines = [json.dumps({"message": {"content": w + " "}, "done": False}) for w in answer.split(" ")]
            return httpx.Response(200, text="\n".join(lines + [json.dumps({"message": {"content": ""}, "done": True})]))
        return httpx.Response(200, json={"message": {"content": answer}, "done": True})
    real = httpx.AsyncClient
    return lambda *a, **k: real(transport=httpx.MockTransport(handler))


@pytest.fixture
def eva(tmp_path, monkeypatch):
    ran = []

    async def aexecute(self, name, args):
        ran.append((name, dict(args)))
        say = {"lights_power": "Lights off, sir.", "media_control": "Done, sir."}.get(name, "Done, sir.")
        return {"result": json.dumps({"ok": True}), "say": say, "exact": True}

    monkeypatch.setattr(orch_mod.ToolBelt, "aexecute", aexecute)
    o = orch_mod.HybridOrchestrator(session_id="n", cortex=Cortex(str(tmp_path / "c.db")),
                                    registry=SkillRegistry("skills").discover())
    return o, ran


def turn(o, text):
    async def go():
        evs = [e async for e in o.process_stream(text)]
        if o._bg:
            await asyncio.gather(*o._bg, return_exceptions=True)
        return evs
    evs = asyncio.run(go())
    return [e for e in evs if e["type"] == "final"][-1]["text"], evs


def test_also_stop_the_music_at_that_time_plans_it_for_the_same_moment(eva, monkeypatch):
    o, ran = eva
    monkeypatch.setattr(orch_mod.httpx, "AsyncClient", fake_ollama(
        {"turn off the lights": {"tool": "lights_power", "on": False},
         "stop the music": {"tool": "media_control", "action": "playpause"}}))
    turn(o, "Turn off the lights in 10 minutes")
    text, _ = turn(o, "Can you also stop the music at that time?")
    items = later.get_later().pending()
    assert ran == [] and len(items) == 2 and abs(items[0]["due"] - items[1]["due"]) < 2
    assert "stop the music" in text.lower()


def test_no_not_right_now_but_211_moves_what_she_just_did(eva, monkeypatch):
    o, ran = eva
    monkeypatch.setattr(orch_mod.httpx, "AsyncClient", fake_ollama(
        {"stop the music": {"tool": "media_control", "action": "playpause"}}))
    from datetime import timedelta
    at = datetime.now() + timedelta(minutes=10)
    turn(o, "stop the music")
    text, _ = turn(o, f"No, not right now, but {at.hour % 12 or 12}{at.minute:02d}")     # how Whisper writes 2:11
    (item,) = later.get_later().pending()
    assert len(ran) == 2 and item["tool"] == "media_control" and f"{at:%H:%M}" in text


@pytest.mark.parametrize("answer,kind", [
    ("Tomorrow looks clear for a 1-hour slot in the afternoon, sir.", "calendar"),
    ("Lights off at 02:11, sir. Music stopped at that time.", "music"),
    ("You have 3 new emails, sir.", "email"),
    ("It's 7 degrees and cloudy, sir.", "weather"),
])
def test_made_up_facts_are_caught(answer, kind):
    assert orch_mod.unchecked_fact(answer, []) == kind


@pytest.mark.parametrize("answer,tools", [
    ("You're free to ask me anything, sir.", []),
    ("Water boils at 100 degrees.", []),
    ("You're free from 12:15 to 13:30, sir.", ["calendar_free"]),
    ("I'm doing well, thank you, sir.", []),
])
def test_honest_answers_pass(answer, tools):
    assert orch_mod.unchecked_fact(answer, tools) is None


def test_an_invented_calendar_answer_becomes_an_offer_and_yes_checks(eva, monkeypatch):
    o, ran = eva
    monkeypatch.setattr(orch_mod.httpx, "AsyncClient", fake_ollama({}, answer="Tomorrow looks clear, sir. Enjoy it."))
    text, evs = turn(o, "what's it for tomorrow")
    spoken = "".join(e["text"] for e in evs if e["type"] == "token")
    assert text == "I haven't checked your calendar for that yet, sir. Should I?" and "clear" not in spoken
    assert ran == []
    turn(o, "yes")
    assert ran == [("calendar_free", {"day": "tomorrow"})]          # "looks clear" was about free time


def test_the_last_unfinished_sentence_is_still_spoken(eva, monkeypatch):
    o, _ = eva
    monkeypatch.setattr(orch_mod.httpx, "AsyncClient", fake_ollama({}, answer="Good evening, sir. How was your day"))
    text, evs = turn(o, "hey how are you")
    assert "".join(e["text"] for e in evs if e["type"] == "token").strip().endswith("How was your day")


# ---------------------------------------------------------------- let's talk
def test_lets_talk_keeps_listening_until_thats_all(eva):
    import core.tools_native as tn
    o, _ = eva
    belt = o.belt
    assert orch_mod.fast_path("Eva, let's talk", belt) == ("keep_listening", {"on": True})
    assert orch_mod.fast_path("that's all", belt) is None or not tn.talk_mode()      # only while talking
    tn.tool_keep_listening(True)
    assert tn.talk_mode() and orch_mod.fast_path("Okay, that's all for now, thanks", belt) == ("keep_listening", {"on": False})
    assert orch_mod.fast_path("that's it for tomorrow", belt) != ("keep_listening", {"on": False})
    tn.tool_keep_listening(False)
    assert not tn.talk_mode()


# ---------------------------------------------------------------- "Eva" over her voice (phone, media mode)
class FakeVAD:
    def __init__(self):
        self.on = False

    async def analyze(self, chunk):
        return self.on


class FakeTurn:
    def append(self, chunk, speaking):
        return False

    async def analyze(self):
        return True

    def clear(self):
        pass


class FakeSTT:
    def __init__(self, text):
        self.text = text

    def transcribe(self, audio):
        return self.text


def make_listener(stt_text):
    from voice.listen import Listener, ListenConfig
    events, commands, barges, wakes = [], [], [], []

    async def ev(e):
        events.append(e)

    async def cmd(t):
        commands.append(t)

    async def wake():
        wakes.append(1)

    async def barge():
        barges.append(1)
    vad = FakeVAD()
    li = Listener(vad, FakeTurn(), FakeSTT(stt_text), ListenConfig(), on_event=ev, on_command=cmd, on_wake=wake,
                  on_barge_in=barge)
    return li, vad, events, commands, barges, wakes


async def speak_over_her(li, vad, secs=1.5):
    from voice.listen import FRAME_SAMPLES
    frame = (np.zeros(FRAME_SAMPLES, dtype="<i2") + 300).tobytes()
    await li.set_speaking(True)
    vad.on = True
    for _ in range(int(secs * 1000 / (FRAME_SAMPLES / 16))):
        await li._frame(frame)
    vad.on = False
    await li._frame(frame)
    await asyncio.sleep(0.05)
    await li.idle()


def run_listener(text, mode="wake"):
    async def go():
        li, vad, events, commands, barges, wakes = make_listener(text)
        li.barge_mode = mode
        task = asyncio.create_task(li._transcribe_loop())
        await speak_over_her(li, vad)
        task.cancel()
        return events, commands, barges, wakes
    return asyncio.run(go())


def test_eva_over_her_voice_stops_her_and_runs_the_command():
    events, commands, barges, _ = run_listener("the forecast says rain later Eva what time is it")
    assert barges and commands == ["what time is it"]
    assert not any(e.get("stage") == "duck" for e in events)          # never ducked for her own echo


def test_her_own_voice_without_eva_is_ignored():
    events, commands, barges, wakes = run_listener("the forecast says rain later today sir")
    assert barges == [] and commands == [] and wakes == []


def test_eva_alone_over_her_voice_opens_a_window():
    _, commands, barges, wakes = run_listener("and that is all for today Eva")
    assert barges and wakes == [1] and commands == []


def test_pc_mode_still_interrupts_on_any_speech():
    events, _, barges, _ = run_listener("hold on", mode="voice")
    assert any(e.get("stage") == "duck" for e in events) and barges


def test_durations_read_naturally():
    assert later.human(5400) == "1 and a half hours" and later.human(42900) == "11 hours and 55 minutes"
    assert later.human(7200) == "2 hours" and later.human(600) == "10 minutes"


# ---------------------------------------------------------------- 9b: the phone's wake service and overlay
def test_phone_downloads_the_models_it_runs(tmp_path, monkeypatch):
    import interfaces.web.server_stream as srv
    from core.wakeword import features as wf
    from fastapi.testclient import TestClient
    model, info = tmp_path / "eva.onnx", tmp_path / "eva.json"
    monkeypatch.setattr(srv, "get_tts", lambda: None)
    with TestClient(srv.app) as tc:
        monkeypatch.setattr(wf, "MODEL", model)
        monkeypatch.setattr(wf, "INFO", info)
        assert tc.get("/api/wakeword/info").status_code == 409                 # not trained yet: says how
        model.write_bytes(b"onnx-bytes")
        info.write_text(json.dumps({"threshold": 0.7, "phone_threshold": 0.45}), encoding="utf-8")
        i = tc.get("/api/wakeword/info").json()
        assert i["phone_threshold"] == 0.45 and i["files"] == ["embedding_model.onnx", "eva.onnx", "melspectrogram.onnx"]
        assert tc.get("/api/wakeword/files/eva.onnx").content == b"onnx-bytes"
        mel = tc.get("/api/wakeword/files/melspectrogram.onnx").content
        assert mel == (wf.MODELS / "melspectrogram.onnx").read_bytes()
        assert tc.get("/api/wakeword/files/..%2Fsecrets.env").status_code == 404
        assert tc.get("/api/wakeword/files/cortex.db").status_code == 404


def test_overlay_page_is_the_core_visual_driven_by_the_real_bridge():
    import interfaces.web.server_stream as srv
    page = srv.build_overlay()
    assert "window.EVA_EMBED = true" in page and "let vname='core'" in page
    assert "drawCore" in page                                   # the Core personality from your design
    assert "E.V.A. bridge" in page and "case 'unlock'" in page and "pcm_command" in page
    assert page.index("const W = window.EVA") < page.index("E.V.A. bridge")     # shim before bridge
    assert page.count("<html") == 1 and page.rstrip().endswith("</html>")


def test_a_locked_phone_keeps_private_things_private(eva, monkeypatch):
    o, ran = eva
    monkeypatch.setattr(orch_mod.httpx, "AsyncClient", fake_ollama({}))
    o.locked = True
    text, evs = turn(o, "any new emails")
    assert ran == [] and text == orch_mod.LOCKED_SAY and any(e["type"] == "unlock" for e in evs)
    assert o.after_unlock[0] == "any new emails"
    text, _ = turn(o, "turn off the lights")
    assert ran == [("lights_power", {"on": False})]             # harmless things still work
    o.locked = False
    turn(o, "any new emails")
    assert ran[-1][0] == "email_unread"


def test_android_pieces_are_wired():
    from pathlib import Path
    root = Path("apps/eva_android")
    for base in ("android_overlay/app/src/main", "android/app/src/main"):
        man = (root / base / "AndroidManifest.xml").read_text(encoding="utf-8")
        assert 'android:foregroundServiceType="microphone"' in man and "FOREGROUND_SERVICE_MICROPHONE" in man
        assert ".OverlayActivity" in man and 'android:showWhenLocked="true"' in man and "SYSTEM_ALERT_WINDOW" in man
        kt = root / base / "kotlin/nl/ionut/eva_android"
        for f in ("WakeService.kt", "WakeEngine.kt", "OverlayActivity.kt", "MainActivity.kt"):
            assert (kt / f).exists(), f
    engine = (root / "android_overlay/app/src/main/kotlin/nl/ionut/eva_android/WakeEngine.kt").read_text(encoding="utf-8")
    assert '"input"' in engine and '"input_1"' in engine and "/ 10f + 2f" in engine and "76" in engine
    gradle = (root / "android/app/build.gradle.kts").read_text(encoding="utf-8")
    assert "com.microsoft.onnxruntime:onnxruntime-android:" in gradle
    dart = (root / "lib/main.dart").read_text(encoding="utf-8")
    assert "'startHandsFree'" in dart and "'stopHandsFree'" in dart


@pytest.mark.parametrize("heard,cmd", [
    ("Eva, what time is it?", "what time is it"), ("Hey Eva.", ""), ("so, Eva turn off the lights", "turn off the lights"),
    ("Ava, play some music", "play some music"), ("She started to turn worse.", None),
    ("I'm talking to the computer because I'm doing voice to text", None),
])
def test_the_pc_double_checks_a_phone_wake(heard, cmd):
    import interfaces.web.server_stream as srv
    assert srv.heard_wake(heard) == cmd


def test_verify_endpoint(monkeypatch):
    import interfaces.web.server_stream as srv
    from fastapi.testclient import TestClient

    class STT:
        def __init__(self, text):
            self.text = text

        def transcribe(self, audio):
            return self.text
    monkeypatch.setattr(srv, "get_tts", lambda: None)
    pcm = (np.zeros(16000, dtype="<i2")).tobytes()
    with TestClient(srv.app) as tc:
        monkeypatch.setattr(srv, "_stt_engine", lambda: STT("Eva, what time is it"))
        assert tc.post("/api/wakeword/verify", content=pcm).json() == {"wake": True, "command": "what time is it",
                                                                     "heard": "Eva, what time is it",
                                                                     "stay_dark": False, "ask_unlock": True}
        monkeypatch.setattr(srv, "_stt_engine", lambda: STT("so I was saying to the computer"))
        assert tc.post("/api/wakeword/verify", content=pcm).json()["wake"] is False
        assert tc.post("/api/wakeword/verify", content=b"").status_code == 400
        assert tc.post("/api/wakeword/verify", content=b"\0" * (13 * 32000)).status_code == 400


# ---------------------------------------------------------------- Nov 22 evening: fix round
def test_stay_dark_by_voice_and_by_button(eva):
    from fastapi.testclient import TestClient
    import interfaces.web.server_stream as srv
    from core import phone_prefs
    o, _ = eva
    for said, on in [("Eva, stay dark", True), ("stay dark off", False), ("keep the screen off", True),
                     ("light the screen when I call you", False)]:
        assert orch_mod.fast_path(said, o.belt) == ("phone_stay_dark", {"on": on}), said
    import core.device as dev
    dev.tool_phone_stay_dark(True)
    assert phone_prefs.get()["stay_dark"] is True
    with TestClient(srv.app) as tc:
        assert tc.post("/api/phone", json={"stay_dark": False}).json() == {"stay_dark": False, "ask_unlock": True}
        assert tc.get("/api/status").json()["phone"] == {"stay_dark": False, "ask_unlock": True}
    assert "phone_stay_dark" in orch_mod.LOCKED_OK


def test_screen_off_conversation(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    import interfaces.web.server_stream as srv

    class TTS:
        def warm(self):
            return True

        def phrase(self, text):
            return None

        def synth(self, text):
            return b"RIFF-" + text.encode()

    class Orch:
        locked = None

        def __init__(self, *a, **k):
            pass

        async def process_stream(self, text):
            import core.device as dev
            Orch.locked = self.locked
            if "alarm" in text:
                out = await asyncio.to_thread(dev.tool_phone_alarm, time="7:00")
                yield {"type": "final", "text": out["say"]}
            else:
                yield {"type": "final", "text": "It's 23:28, sir."}
    monkeypatch.setattr(srv, "get_tts", lambda: TTS())
    monkeypatch.setattr(srv, "HybridOrchestrator", Orch)
    srv._dark_session.clear()
    with TestClient(srv.app) as tc:
        import core.device as dev
        dev.LOOP = None
        r = tc.post("/api/phone/turn", json={"text": "what time is it", "locked": True}).json()
        assert r["text"] == "It's 23:28, sir." and r["listen"] and r["audio"] and Orch.locked is True
        assert tc.post("/api/phone/turn", json={"text": ""}).json()["text"] == "Yes, sir?"        # just "Eva"
        assert tc.post("/api/phone/turn", json={"text": "that's all"}).json()["listen"] is False
    srv._dark_session.clear()


def test_daily_routine_is_summed_up_unless_asked():
    from core import tempo
    from datetime import datetime as dt

    class G:
        def list_events(self, a, b, max_results=100):
            d = a.replace(hour=0)
            mk = lambda t, h, m, r: {"title": t, "start": d.replace(hour=h, minute=m), "end": d.replace(hour=h, minute=m + 10),
                                     "all_day": False, "routine": r}
            return [mk("Wake Up", 7, 0, True), mk("Take Vitamins", 7, 0, True), mk("Brush Teeth", 7, 15, True),
                    mk("From Data to Model", 10, 30, False), mk("Linear Algebra", 13, 30, False)]
    now = dt(2026, 11, 22, 23, 0)
    out = tempo.agenda(G(), "tomorrow", now)
    assert out["say"].startswith("You have 2 things tomorrow, sir: 10:30 From Data to Model")
    assert out["say"].endswith("13:30 Linear Algebra. Plus your daily routine (3 items).")
    assert "Wake Up" in tempo.agenda(G(), "tomorrow", now, everything=True)["say"]


def test_daily_series_are_recognised():
    from core.google_api import GoogleClient

    class Svc:
        calls = 0

        def events(self):
            svc = self

            class E:
                def get(self, calendarId, eventId):
                    svc.calls += 1
                    rule = {"wake": ["RRULE:FREQ=DAILY"], "week": ["RRULE:FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR"],
                            "class": ["RRULE:FREQ=WEEKLY;BYDAY=MO"], "alt": ["RRULE:FREQ=DAILY;INTERVAL=2"]}[eventId]
                    return type("X", (), {"execute": lambda _s: {"recurrence": rule}})()
            return E()
    g = GoogleClient.__new__(GoogleClient)
    g.cal, g._daily = Svc(), {}
    assert g._is_daily("primary", "wake") and g._is_daily("primary", "week")
    assert not g._is_daily("primary", "class") and not g._is_daily("primary", "alt") and not g._is_daily("primary", None)
    g._is_daily("primary", "wake")
    assert g.cal.calls == 4                                              # each series looked up once


def test_overlay_can_set_alarms():
    from pathlib import Path
    kt = Path("apps/eva_android/android_overlay/app/src/main/kotlin/nl/ionut/eva_android/OverlayActivity.kt").read_text(encoding="utf-8")
    assert 'addJavascriptInterface(Native(), "EvaNative")' in kt and "eva:device-result" in kt
    ws = Path("apps/eva_android/android_overlay/app/src/main/kotlin/nl/ionut/eva_android/WakeService.kt").read_text(encoding="utf-8")
    assert "/api/phone/turn" in ws and "stay_dark" in ws and "DeviceActions.run" in ws
    page = Path("interfaces/web/eva.html").read_text(encoding="utf-8")
    assert "['understanding', 'Understanding you']" in page and "emit('phone-setting'" in page


def test_twelve_something_just_after_midnight_is_tonight():
    c, secs = later.split_clock("stop the music at 12:16", datetime(2026, 11, 23, 0, 6))
    assert c == "stop the music" and abs(secs / 60 - 10) < 0.1
    assert abs(later.split_clock("stop the music at 12:16", datetime(2026, 11, 23, 11, 0))[1] / 60 - 76) < 0.1
