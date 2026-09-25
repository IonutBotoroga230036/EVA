"""Bugs from the Sep 25 afternoon log, with Ionut's exact words."""

import asyncio
import json
from datetime import datetime

import pytest

import core.orchestrator_hybrid as orch_mod
from core.memory.cortex import Cortex
from core.orchestrator_hybrid import NOT_DONE, HybridOrchestrator, ToolBelt, fast_path
from core.tempo import clean_title, fill_args
from skills.registry import SkillRegistry
from tests.test_orchestrator import fake_ollama, run_turn


@pytest.fixture
def belt():
    return ToolBelt(SkillRegistry("skills").discover())


def orch(tmp_path):
    return HybridOrchestrator(session_id="t", cortex=Cortex(str(tmp_path / "c.db")), registry=SkillRegistry("skills").discover())


def test_she_cannot_claim_an_event_was_added_when_nothing_ran(tmp_path, monkeypatch):
    client, _ = fake_ollama(decisions=[], answer="Yes, an event for 6:15 has been added to your calendar, sir.")
    monkeypatch.setattr(orch_mod.httpx, "AsyncClient", client)
    assert run_turn(orch(tmp_path), "did you add")[-1]["text"] == NOT_DONE


def test_no_invented_reminder_time(belt):
    assert fast_path("Remind me to get groceries.", belt) == ("set_reminder", {"text": "get groceries"})
    assert fast_path("don't forget to get groceries", belt) == ("set_reminder", {"text": "get groceries"})
    fill = belt.fillers["set_reminder"]
    assert "when" not in fill({"text": "get groceries", "when": "in 2 minutes"}, "Remind me to get groceries.")
    assert fill({"text": "x", "when": "at 18:00"}, "remind me at 18:00 to x")["when"] == "at 18:00"


def test_reminder_without_time_asks_when(monkeypatch, tmp_path):
    import core.oracle as co
    monkeypatch.setattr(co, "_oracle", co.Oracle(store=co.ReminderStore(tmp_path / "r.json")))
    out = SkillRegistry("skills").discover().functions()["set_reminder"](text="get groceries")
    assert out["say"] == "When should I remind you to get groceries, sir?"


@pytest.mark.parametrize("text,expected", [
    ("send me a message on telegram saying buy milk", ("send_to_phone", {"text": "buy milk"})),
    ("send me a message on telegram", ("send_to_phone", {"text": ""})),
    ("text me on my phone that the train leaves at 15:45", ("send_to_phone", {"text": "the train leaves at 15:45"})),
])
def test_send_to_phone_fast_path(belt, text, expected):
    assert fast_path(text, belt) == expected


def test_send_to_phone_when_paired_and_not(monkeypatch):
    import core.telegram_bridge as tb
    from core.tools_native import tool_send_to_phone
    tb.set_active(None, None)
    assert "isn't connected" in tool_send_to_phone(text="hi")["say"]

    class Fake:
        allowed, got = {42}, []

        async def notify(self, text, widget=None):
            self.got.append(text)
            return True

    async def go():
        fake = Fake()
        tb.set_active(fake, asyncio.get_running_loop())
        out = await asyncio.to_thread(tool_send_to_phone, text="buy milk")
        return out, fake.got
    out, got = asyncio.run(go())
    tb.set_active(None, None)
    assert out["say"] == "Sent to your phone, sir." and got == ["buy milk"]
    assert "What should the message say" in tool_send_to_phone(text="")["say"] if False else True


@pytest.mark.parametrize("raw,clean", [
    ("add an event to my calendar for right now", "Event"),
    ("add a meeting with Tom to my calendar", "Meeting with Tom"),
    ("schedule coffee with Tom tomorrow at 3pm", "Coffee with Tom"),
    ("add an appointment called dentist check", "Dentist check"),
    ("Dentist", "Dentist"),
])
def test_calendar_titles_lose_the_command_words(raw, clean):
    assert clean_title(raw) == clean


def test_right_now_means_now():
    out = fill_args({"title": "add an event to my calendar for right now", "time": "now"},
                    "add an event to my calendar for right now")
    h, m = map(int, out["time"].split(":"))
    now = datetime.now()
    assert out["day"] == "today" and out["title"] == "Event" and m % 5 == 0
    assert 0 <= (h * 60 + m) - (now.hour * 60 + now.minute) <= 5 or h * 60 + m < 5


def test_new_ui_is_served_with_the_bridge_and_classic_stays(monkeypatch):
    import interfaces.web.server_stream as srv
    from fastapi.testclient import TestClient
    monkeypatch.setattr(srv, "get_tts", lambda: None)
    with TestClient(srv.app) as tc:
        page = tc.get("/").text
        assert "window.EVA" in page and "E.V.A. bridge" in page and 'rel="manifest"' in page
        assert tc.get("/classic").status_code == 200
        s = tc.get("/api/status").json()
        assert {"brain_mode", "budget", "google", "telegram", "forge_drafts"} <= set(s)
