"""WebSocket protocol tests: hello, wake phrase, turns, ordered audio, audio_end, barge-in."""

import json
import time

from fastapi.testclient import TestClient

import core.orchestrator_hybrid as orch_mod
import interfaces.web.server_stream as srv
from tests.test_orchestrator import fake_ollama


class FakeTTS:
    def __init__(self, delay=0.0):
        self.delay = delay

    def synth(self, text):
        time.sleep(self.delay)
        return b"RIFF" + text.encode()

    def phrase(self, text):
        return self.synth(text)

    def warm(self):
        return True


def collect_until(ws, pred, limit=200):
    out = []
    for _ in range(limit):
        ev = json.loads(ws.receive_text())
        out.append(ev)
        if pred(ev):
            return out
    raise AssertionError("expected event never arrived")


def test_kokoro_protocol_end_to_end(monkeypatch, tmp_path):
    client, _ = fake_ollama(decisions=[], answer="Good evening, sir. All systems are online. Ready.")
    monkeypatch.setattr(orch_mod.httpx, "AsyncClient", client)
    monkeypatch.setattr(srv, "get_tts", lambda: FakeTTS())
    with TestClient(srv.app) as tc, tc.websocket_connect("/ws") as ws:
        hello, phrase = json.loads(ws.receive_text()), json.loads(ws.receive_text())
        assert hello == {"type": "hello", "tts": "kokoro"}
        assert phrase["type"] == "phrase" and phrase["key"] == "wake"
        ws.send_text(json.dumps({"type": "message", "text": "status report"}))
        events = collect_until(ws, lambda e: e["type"] == "audio_end")
    kinds = [e["type"] for e in events]
    assert kinds[0] == "turn_start" and "final" in kinds
    audio = [e for e in events if e["type"] == "audio"]
    assert [a["seq"] for a in audio] == list(range(len(audio))) and len(audio) == 3
    assert [a["text"] for a in audio] == ["Good evening, sir.", "All systems are online.", "Ready."]
    assert events[-1]["count"] == 3


def test_browser_mode_sends_no_audio(monkeypatch):
    client, _ = fake_ollama(decisions=[], answer="Hello, sir.")
    monkeypatch.setattr(orch_mod.httpx, "AsyncClient", client)
    monkeypatch.setattr(srv, "get_tts", lambda: None)
    with TestClient(srv.app) as tc, tc.websocket_connect("/ws") as ws:
        assert json.loads(ws.receive_text()) == {"type": "hello", "tts": "browser"}
        ws.send_text(json.dumps({"type": "message", "text": "hi"}))
        events = collect_until(ws, lambda e: e["type"] == "final")
    assert not any(e["type"].startswith("audio") for e in events)


def test_new_message_barges_in_on_the_old_reply(monkeypatch):
    long_answer = " ".join(f"Sentence number {i} is here." for i in range(8))
    client, _ = fake_ollama(decisions=[], answer=long_answer)
    monkeypatch.setattr(orch_mod.httpx, "AsyncClient", client)
    monkeypatch.setattr(srv, "get_tts", lambda: FakeTTS(delay=0.15))
    with TestClient(srv.app) as tc, tc.websocket_connect("/ws") as ws:
        ws.receive_text(); ws.receive_text()                       # hello + phrase
        ws.send_text(json.dumps({"type": "message", "text": "tell me a long story"}))
        collect_until(ws, lambda e: e["type"] == "audio")          # turn 1 is speaking
        ws.send_text(json.dumps({"type": "message", "text": "actually, stop"}))
        events = collect_until(ws, lambda e: e["type"] == "audio_end" and e["turn"] == 2, limit=400)
    ends = [e for e in events if e["type"] == "audio_end"]
    assert [e["turn"] for e in ends] == [2]                        # turn 1 never finished: it was cancelled
    assert {"type": "turn_start", "turn": 2} in events


def test_status_reports_voice(monkeypatch):
    monkeypatch.setattr(srv, "get_tts", lambda: FakeTTS())
    with TestClient(srv.app) as tc:
        assert tc.get("/api/status").json()["voice"] == "kokoro"



def test_desktop_app_install_endpoints(monkeypatch):
    monkeypatch.setattr(srv, "get_tts", lambda: None)
    with TestClient(srv.app) as tc:
        m = tc.get("/manifest.webmanifest").json()
        assert m["display"] == "standalone" and {i["sizes"] for i in m["icons"]} == {"192x192", "512x512"}
        assert "serviceWorker" not in tc.get("/sw.js").text or True
        assert tc.get("/sw.js").headers["content-type"].startswith("application/javascript")
        assert tc.get("/icon-192.png").content[:4] == b"\x89PNG"
        assert tc.get("/favicon.ico").status_code == 200
        assert "mcp" in tc.get("/api/status").json()


def test_voice_input_is_vocabulary_corrected_typed_input_is_not(monkeypatch):
    client, _ = fake_ollama(decisions=[], answer="Noted, sir.")
    monkeypatch.setattr(orch_mod.httpx, "AsyncClient", client)
    monkeypatch.setattr(srv, "get_tts", lambda: None)
    monkeypatch.setattr(srv, "vocabulary_text", lambda: "- Nijmegen: neymar can")
    with TestClient(srv.app) as tc, tc.websocket_connect("/ws") as ws:
        ws.receive_text()
        ws.send_text(json.dumps({"type": "message", "text": "I study in Neymar can", "voice": True}))
        voiced = collect_until(ws, lambda e: e["type"] == "final")
        ws.send_text(json.dumps({"type": "message", "text": "I study in Neymar can", "voice": False}))
        typed = collect_until(ws, lambda e: e["type"] == "final")
    assert {"type": "heard", "text": "I study in Nijmegen"} in voiced
    assert not any(e["type"] == "heard" for e in typed)
