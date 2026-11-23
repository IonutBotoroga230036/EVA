"""Nov 23: a meeting's to-dos go into your reminders or calendar after your yes; speaker labels in the notes when
diarization is available. Nothing real is touched: fake Google, fake Whisper, fake diarizer."""

import json
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from core import meetings
from core.oracle import get_oracle
from core.orchestrator_hybrid import ToolBelt, fast_path

NOTES = {"title": "Sprint planning", "summary": "We planned the sprint.", "key_points": [], "decisions": [],
         "action_items": [{"task": "Send the build to Wim", "owner": "Ionut", "due": "Thursday"},
                          {"task": "Book the lab", "owner": "", "due": ""},
                          {"task": "Review the GPS export", "owner": "Wim", "due": "Friday"}],
         "open_questions": []}


def done_meeting(notes=NOTES):
    meta = meetings.add_file("t.txt", b"hello")
    meetings.process(meta["id"], notes_fn=lambda t, h: notes)
    return meta["id"]


class FakeGoogle:
    def __init__(self):
        self.added = []

    def insert_event(self, title, start, end, description=""):
        self.added.append((title, start, end))
        return {"id": "x"}


def test_my_todos_become_reminders():
    done_meeting()
    out = meetings.tool_meeting_todos_add(where="reminders")
    open_ = get_oracle().store.open()
    texts = sorted(r["text"] for r in open_)
    assert texts == ["Book the lab", "Send the build to Wim"]
    thursday = [r for r in open_ if r["text"] == "Send the build to Wim"][0]
    due = datetime.fromtimestamp(thursday["due"])
    assert due == meetings.todo_time({"due": "Thursday"})[0] and due.hour == 9
    lab = datetime.fromtimestamp([r for r in open_ if r["text"] == "Book the lab"][0]["due"])
    assert lab.date() == (datetime.now() + timedelta(days=1)).date() and lab.hour == 9
    assert out["say"].startswith("Added 2 to-dos from Sprint planning to your reminders, sir")
    assert "I left out 1 that's Wim's." in out["say"]


def test_everyone_s_todos_when_asked():
    done_meeting()
    meetings.tool_meeting_todos_add(where="reminders", which="all")
    assert len(get_oracle().store.open()) == 3


def test_todos_into_the_calendar(monkeypatch):
    import core.google_api as gapi
    g = FakeGoogle()
    monkeypatch.setattr(gapi, "connected", lambda: True)
    monkeypatch.setattr(gapi, "get_google", lambda: g)
    done_meeting()
    out = meetings.tool_meeting_todos_add(where="calendar")
    assert sorted(t for t, _, _ in g.added) == ["To-do: Book the lab", "To-do: Send the build to Wim"]
    assert all((e - s) == timedelta(minutes=30) for _, s, e in g.added)
    assert "to your calendar" in out["say"]


def test_twice_doesnt_add_them_twice():
    done_meeting()
    meetings.tool_meeting_todos_add(where="reminders")
    out = meetings.tool_meeting_todos_add(where="reminders")
    assert len(get_oracle().store.open()) == 2 and "already" in out["say"]


def test_it_asks_first_and_lists_them():
    done_meeting()
    belt = ToolBelt()
    assert belt.needs_confirm("meeting_todos_add")
    desc = belt.describe("meeting_todos_add", {"where": "reminders"})
    thu = meetings.when_label(meetings.todo_time({"due": "Thursday"})[0])
    assert desc == (f"put 2 to-dos from Sprint planning in your reminders: Send the build to Wim ({thu}); "
                    "Book the lab (tomorrow 09:00)")


def test_no_meeting_and_no_own_todos_are_said_plainly():
    assert "don't have any meeting notes" in meetings.tool_meeting_todos_add()["say"]
    done_meeting({**NOTES, "action_items": [{"task": "Review the export", "owner": "Wim"}]})
    out = meetings.tool_meeting_todos_add()
    assert out["say"] == ("None of the to-dos from Sprint planning are yours, sir. Say 'add all the to-dos' to add "
                          "Wim's too.")


@pytest.mark.parametrize("text,where", [("put the meeting to-dos in my reminders", "reminders"),
                                        ("add the to-dos to my calendar", "calendar"),
                                        ("add those action items to my reminders", "reminders"),
                                        ("schedule the todos", "calendar")])
def test_by_voice(text, where):
    assert fast_path(text, ToolBelt()) == ("meeting_todos_add", {"where": where})


def test_the_ready_message_offers_it(monkeypatch):
    said = []
    meta = meetings.add_file("t.txt", b"hello")
    monkeypatch.setattr(meetings, "process", lambda mid: {"status": "done", "title": "Sprint planning", "todos": 2})
    import threading
    real = threading.Thread

    class Now(real):
        def start(self):
            self.run()
    monkeypatch.setattr(threading, "Thread", Now)
    meetings.process_in_background(meta["id"], said.append)
    assert said and "put the to-dos in my reminders" in said[0]


# ---------------------------------------------------------------- speaker labels
class FakeWhisper:
    def transcribe(self, path, **kw):
        return iter([SimpleNamespace(start=0.0, end=2.0, text=" Let's ship Friday."),
                     SimpleNamespace(start=3.0, end=5.0, text=" I'll send the build."),
                     SimpleNamespace(start=6.0, end=7.5, text=" Great.")]), SimpleNamespace(language="en")


def test_speakers_are_labelled_when_diarization_runs():
    meta = meetings.add_file("x.wav", b"RIFF")
    turns = [(0.0, 2.5, "SPEAKER_01"), (2.6, 5.2, "SPEAKER_00"), (5.9, 8.0, "SPEAKER_01")]
    seen = {}

    def notes_fn(t, h):
        seen["t"] = t
        return {**NOTES, "action_items": []}
    meetings.process(meta["id"], model_factory=lambda: FakeWhisper(), notes_fn=notes_fn, diarizer=lambda p: turns)
    assert seen["t"].splitlines() == ["[00:00] Speaker 1: Let's ship Friday.", "[00:03] Speaker 2: I'll send the build.",
                                      "[00:06] Speaker 1: Great."]


def test_without_diarization_the_transcript_is_as_before():
    meta = meetings.add_file("x.wav", b"RIFF")
    seen = {}
    meetings.process(meta["id"], model_factory=lambda: FakeWhisper(),
                     notes_fn=lambda t, h: seen.setdefault("t", t) and {**NOTES, "action_items": []},
                     diarizer=lambda p: None)
    assert seen["t"].splitlines()[0] == "[00:00] Let's ship Friday."


def test_diarization_is_off_without_a_token(monkeypatch):
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.setattr(meetings, "_hf_token", lambda: None)
    assert meetings.diarize("x.wav") is None


def test_all_the_todos_by_voice():
    assert fast_path("add all the to-dos to my reminders", ToolBelt()) == \
        ("meeting_todos_add", {"where": "reminders", "which": "all"})


def test_nothing_to_add_means_no_yes_question():
    assert meetings.precheck_todos({"where": "reminders"}) == "I don't have any meeting notes yet, sir."
    done_meeting()
    assert meetings.precheck_todos({"where": "reminders"}) is None
    meetings.tool_meeting_todos_add(where="reminders")
    assert "already" in meetings.precheck_todos({"where": "reminders"})
    assert ToolBelt().prechecks["meeting_todos_add"] is meetings.precheck_todos
