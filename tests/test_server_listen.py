"""Server side of v0.2.5 listening: stt event, binary audio frames, listen/speaking controls,
voice commands becoming turns, wake, barge-in, fallback. The listener is a fake; no model loads."""

import asyncio
import json

from fastapi.testclient import TestClient

import core.orchestrator_hybrid as orch_mod
import interfaces.web.server_stream as srv
from tests.test_orchestrator import fake_ollama
from tests.test_server import FakeTTS, collect_until

SERVER = {"engine": "server", "rate": 16000, "detail": "Silero VAD, Smart Turn v3, Whisper small (cpu int8)"}


class FakeListener:
    made = []

    def __init__(self, on_event, on_command, on_wake, on_barge_in):
        self.on_event, self.on_command, self.on_wake, self.on_barge_in = on_event, on_command, on_wake, on_barge_in
        self.fed, self.said_texts, self.speaking, self.wake, self.armed_secs = [], [], [], None, []
        self.closed = False
        FakeListener.made.append(self)

    async def feed(self, data):
        self.fed.append(data)
        if data == b"CMD!":                                   # the fake "hears" a command
            await self.on_command("roundabout university")
        elif data == b"WAKE":
            await self.on_wake()
        elif data == b"BARG":
            await self.on_event({"type": "barge_in", "stage": "stop"})
            await self.on_barge_in()

    def said(self, text):
        self.said_texts.append(text)

    async def set_speaking(self, on):
        self.speaking.append(on)

    def set_wake(self, on):
        self.wake = on

    def arm(self, secs=None):
        self.armed_secs.append(secs)

    def disarm(self):
        self.armed_secs.append(0)

    async def close(self):
        self.closed = True


def fake_factory(**cb):
    return FakeListener(**cb)


def setup(monkeypatch, answer="Radboud University is in Nijmegen, sir.", status=SERVER, tts=None):
    client, _ = fake_ollama(decisions=[], answer=answer)
    monkeypatch.setattr(orch_mod.httpx, "AsyncClient", client)
    monkeypatch.setattr(srv, "get_tts", lambda: tts)
    monkeypatch.setattr(srv, "pipeline_status", lambda: dict(status))
    monkeypatch.setattr(srv, "build_listener", fake_factory)
    monkeypatch.setattr(srv, "vocabulary_text", lambda: "- Radboud University: roundabout university\n")
    FakeListener.made.clear()


def handshake(ws):
    hello = json.loads(ws.receive_text())
    stt = json.loads(ws.receive_text())
    return hello, stt


def test_stt_event_announces_server_listening(monkeypatch):
    setup(monkeypatch)
    with TestClient(srv.app) as tc, tc.websocket_connect("/ws") as ws:
        hello, stt = handshake(ws)
    assert hello == {"type": "hello", "tts": "browser"}
    assert stt == {"type": "stt", **SERVER}
    assert FakeListener.made == []                            # nothing loads until audio or a listen message


def test_fallback_announced_when_pipeline_is_off(monkeypatch):
    setup(monkeypatch, status={"engine": "browser", "reason": "server listening is off in settings"})
    with TestClient(srv.app) as tc, tc.websocket_connect("/ws") as ws:
        _, stt = handshake(ws)
        ws.send_bytes(b"\x00" * 1024)                          # a stray frame is ignored, not an error
        ws.send_text(json.dumps({"type": "message", "text": "hello"}))
        events = collect_until(ws, lambda e: e["type"] == "final")
    assert stt["engine"] == "browser" and "off in settings" in stt["reason"]
    assert FakeListener.made == [] and events[-1]["type"] == "final"


def test_audio_frames_reach_the_listener_and_a_heard_command_becomes_a_turn(monkeypatch):
    setup(monkeypatch)
    with TestClient(srv.app) as tc, tc.websocket_connect("/ws") as ws:
        handshake(ws)
        ws.send_bytes(b"\x01\x02" * 512)
        ws.send_bytes(b"CMD!")
        events = collect_until(ws, lambda e: e["type"] == "final")
    lst = FakeListener.made[0]
    assert lst.fed[0] == b"\x01\x02" * 512
    heard = [e for e in events if e["type"] == "heard"]
    assert heard == [{"type": "heard", "text": "Radboud University", "stt": True}]   # vocabulary fixed it
    assert {"type": "turn_start", "turn": 1} in events
    assert lst.said_texts and "Nijmegen" in lst.said_texts[-1]   # the echo guard knows what she said
    assert lst.closed


def test_listen_and_speaking_controls(monkeypatch):
    setup(monkeypatch)
    with TestClient(srv.app) as tc, tc.websocket_connect("/ws") as ws:
        handshake(ws)
        ws.send_text(json.dumps({"type": "listen", "wake": True}))
        ws.send_text(json.dumps({"type": "listen", "arm": 7000}))
        ws.send_text(json.dumps({"type": "speaking", "on": True}))
        ws.send_text(json.dumps({"type": "speaking", "on": False}))
        ws.send_text(json.dumps({"type": "listen", "arm": 0}))
        ws.send_bytes(b"WAKE")
        wake = collect_until(ws, lambda e: e["type"] == "wake")[-1]
    lst = FakeListener.made[0]
    assert len(FakeListener.made) == 1                        # one listener per connection
    assert lst.wake is True and lst.armed_secs == [7.0, 0] and lst.speaking == [True, False]
    assert wake == {"type": "wake", "text": "Yes, sir?"}


def test_barge_in_cancels_the_reply_in_progress(monkeypatch):
    long_answer = " ".join(f"Sentence number {i} is here." for i in range(10))
    setup(monkeypatch, answer=long_answer, tts=FakeTTS(delay=0.15))
    with TestClient(srv.app) as tc, tc.websocket_connect("/ws") as ws:
        ws.receive_text(); ws.receive_text(); ws.receive_text()          # hello, phrase, stt
        ws.send_text(json.dumps({"type": "message", "text": "tell me a long story"}))
        collect_until(ws, lambda e: e["type"] == "audio")
        ws.send_bytes(b"BARG")
        collect_until(ws, lambda e: e["type"] == "barge_in")
        ws.send_text(json.dumps({"type": "message", "text": "what time is it"}))
        events = collect_until(ws, lambda e: e["type"] == "audio_end" and e["turn"] == 2, limit=500)
    assert [e["turn"] for e in events if e["type"] == "audio_end"] == [2]   # turn 1 never finished


def test_listener_that_fails_to_build_falls_back_to_the_browser(monkeypatch):
    setup(monkeypatch)

    def broken(**_):
        raise RuntimeError("onnxruntime DLL missing")
    monkeypatch.setattr(srv, "build_listener", broken)
    with TestClient(srv.app) as tc, tc.websocket_connect("/ws") as ws:
        handshake(ws)
        ws.send_bytes(b"\x00" * 1024)
        ev = collect_until(ws, lambda e: e["type"] == "stt")[-1]
        ws.send_text(json.dumps({"type": "message", "text": "still here?"}))
        final = collect_until(ws, lambda e: e["type"] == "final")[-1]
    assert ev["engine"] == "browser" and "onnxruntime DLL missing" in ev["reason"]
    assert final["type"] == "final"                           # the connection survived


def test_oversized_and_malformed_input_is_dropped(monkeypatch):
    setup(monkeypatch)
    with TestClient(srv.app) as tc, tc.websocket_connect("/ws") as ws:
        handshake(ws)
        ws.send_bytes(b"\x00" * (srv.MAX_AUDIO_FRAME + 1))
        ws.send_text("not json")
        ws.send_text("[1, 2]")
        ws.send_text(json.dumps({"type": "audio_format", "rate": 48000}))
        ws.send_bytes(b"\x00\x00" * 4800)                     # 100 ms at 48 kHz -> ~1600 samples at 16 kHz
        ws.send_text(json.dumps({"type": "message", "text": "ok"}))
        collect_until(ws, lambda e: e["type"] == "final")
    lst = FakeListener.made[0]
    assert len(lst.fed) == 1 and 2000 < len(lst.fed[0]) <= 3200    # only the resampled frame arrived, at 16 kHz


def test_status_reports_listening(monkeypatch):
    setup(monkeypatch)
    with TestClient(srv.app) as tc:
        body = tc.get("/api/status").json()
    assert body["stt"]["engine"] == "server"


def test_real_pipeline_status_respects_the_settings_switch(monkeypatch, tmp_path):
    import core.settings as st
    import voice.listen as listen
    local = tmp_path / "settings.local.yaml"
    local.write_text("voice:\n  pipeline:\n    enabled: false\n", encoding="utf-8")
    monkeypatch.setattr(st, "LOCAL_PATH", local)
    st.get_settings.cache_clear()
    status = listen.pipeline_status()
    assert status["engine"] == "browser" and "voice.pipeline.enabled" in status["reason"]
    asyncio.run(asyncio.sleep(0))
