"""Nov 23: record a meeting (phone or PC), or share a recording, and get a transcript, summary and to-dos."""

import json
import time
import wave
from types import SimpleNamespace

import pytest

from core import meetings

NOTES = {"title": "Sprint planning", "summary": "We planned the sprint.", "key_points": ["GPS app first"],
         "decisions": ["Ship on Friday"], "action_items": [{"task": "Send the build to Wim", "owner": "Ionut",
                                                            "due": "Thursday"}], "open_questions": []}


class FakeWhisper:
    def __init__(self):
        self.paths = []

    def transcribe(self, path, **kw):
        self.paths.append(path)
        return iter([SimpleNamespace(start=0.0, end=2.0, text=" Let's ship on Friday."),
                     SimpleNamespace(start=65.0, end=67.0, text=" I'll send the build to Wim.")]), \
            SimpleNamespace(language="en")


def test_record_append_stop_and_notes():
    rec = meetings.get_recorder()
    meta = rec.start("planning", "phone")
    assert rec.append(meta["id"], b"\x01\x00" * 16000) and rec.append(meta["id"], b"\x02\x00" * 8000 + b"\x03")
    assert not rec.append("20000101-000000-abcd", b"\0\0")
    stopped = rec.stop()
    assert stopped["status"] == "queued" and abs(stopped["seconds"] - 1.5) < 0.01
    assert rec.append(meta["id"], b"\x04\x00" * 1600)                 # the phone's last chunk, just after stop
    fw = FakeWhisper()
    done = meetings.process(meta["id"], model_factory=lambda: fw, notes_fn=lambda t, title: NOTES)
    assert done["status"] == "done" and done["todos"] == 1 and done["title"] == "Sprint planning"
    with wave.open(str(meetings.folder(meta["id"]) / "audio.wav"), "rb") as w:
        assert w.getframerate() == 16000 and w.getnframes() == 16000 + 8000 + 1600
    md = (meetings.folder(meta["id"]) / "notes.md").read_text(encoding="utf-8")
    assert "- [ ] Send the build to Wim (Ionut), due Thursday" in md and "[01:05] I'll send the build to Wim." in md


def test_a_shared_transcript_is_summarized_without_whisper():
    meta = meetings.add_file("Recorder transcript.txt", "Speaker 1: we ship Friday".encode("utf-8"))
    done = meetings.process(meta["id"], model_factory=lambda: pytest.fail("no Whisper for text"),
                            notes_fn=lambda t, title: {**NOTES, "action_items": []})
    assert done["status"] == "done" and done["todos"] == 0


def test_a_shared_audio_file_keeps_its_format():
    meta = meetings.add_file("Recording 12.m4a", b"fake")
    assert (meetings.folder(meta["id"]) / "audio.m4a").exists()


def test_silence_says_so():
    meta = meetings.add_file("x.wav", b"RIFF")
    empty = SimpleNamespace(transcribe=lambda p, **k: (iter([]), SimpleNamespace(language="en")))
    assert meetings.process(meta["id"], model_factory=lambda: empty, notes_fn=lambda t, h: NOTES)["status"] == "empty"


def test_voice_flow_on_the_pc(monkeypatch):
    rec = meetings.get_recorder()
    monkeypatch.setattr(rec, "start_pc_mic", lambda mid: None)
    started = []
    monkeypatch.setattr(meetings, "_announce_when_done", lambda mid, wait=0.0: started.append((mid, wait)))
    out = meetings.tool_meeting_record(on=True, title="standup")
    assert out["say"].startswith("Recording, sir.") and rec.active["source"] == "pc"
    assert meetings.tool_meeting_record(on=True)["say"].startswith("I'm already recording")
    out = meetings.tool_meeting_record(on=False)
    assert out["say"].startswith("Stopped, sir: 1 minute recorded.") and started and started[0][1] == 0.0
    assert meetings.tool_meeting_record(on=False)["say"] == "I'm not recording anything, sir."


def test_no_pc_microphone_says_why(monkeypatch):
    rec = meetings.get_recorder()
    monkeypatch.setattr(rec, "start_pc_mic", lambda mid: "recording on the PC needs sounddevice")
    assert "needs sounddevice" in meetings.tool_meeting_record(on=True)["say"] and rec.active is None


def test_said_to_the_phone_the_phone_records(monkeypatch):
    from core import device
    phone = SimpleNamespace(device_caps=("alarm", "record"))
    asked = []
    monkeypatch.setattr(device, "_ask", lambda cap, action, args: asked.append((action, args)) or {"ok": True})
    monkeypatch.setattr(meetings, "_announce_when_done", lambda mid, wait=0.0: asked.append(("done", wait)))
    token = device.CURRENT.set(phone)
    try:
        meetings.tool_meeting_record(on=True)
        mid = meetings.get_recorder().active["id"]
        meetings.tool_meeting_record(on=False)
    finally:
        device.CURRENT.reset(token)
    assert asked == [("record_start", {"id": mid}), ("record_stop", {"id": mid}), ("done", meetings.LATE_S)]


def test_what_were_the_todos():
    assert meetings.tool_meeting_notes()["say"] == "I don't have any meeting notes yet, sir."
    meta = meetings.add_file("t.txt", b"x")
    meetings.process(meta["id"], notes_fn=lambda t, h: NOTES)
    assert meetings.tool_meeting_notes("todos")["say"] == "From Sprint planning, sir: Send the build to Wim (Ionut)."
    assert meetings.tool_meeting_notes("decisions")["say"] == "Decided, sir: Ship on Friday."
    assert meetings.tool_meeting_notes()["say"].startswith("Sprint planning, sir: We planned")


def test_upload_and_phone_chunks_over_http(monkeypatch):
    from fastapi.testclient import TestClient
    import interfaces.web.server_stream as srv
    queued = []
    monkeypatch.setattr(srv, "get_tts", lambda: None)                 # no voice model in this test
    monkeypatch.setattr(meetings, "process_in_background", lambda mid, tell, wait=0.0: queued.append(mid))
    with TestClient(srv.app) as tc:
        r = tc.post("/api/meetings/upload", content=b"Speaker 1: hi", headers={"X-Filename": "notes.txt"}).json()
        assert queued == [r["id"]] and (meetings.folder(r["id"]) / "transcript.txt").exists()
        meta = meetings.get_recorder().start("x", "phone")
        assert tc.post(f"/api/meetings/{meta['id']}/audio", content=b"\0\0" * 100).json() == {"ok": True}
        assert tc.post("/api/meetings/../../x/audio", content=b"\0\0").status_code in (400, 404)
        meetings.get_recorder().stop()
