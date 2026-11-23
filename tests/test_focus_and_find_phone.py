"""Nov 23: focus mode (during calendar events she holds announcements except Mom or urgent, then says what came in)
and find my phone (ring it at full volume from the PC, even when only the hands-free service runs)."""

import asyncio
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from core import device, focus, messages
from core.orchestrator_hybrid import ToolBelt, fast_path

NOW = datetime(2026, 11, 24, 11, 0)


def event(title="Neural Networks", start=datetime(2026, 11, 24, 10, 45), minutes=105, **kw):
    return {"id": title, "title": title, "start": start, "end": start + timedelta(minutes=minutes),
            "all_day": False, "routine": False, "location": "", **kw}


def msg(sender="Andrei", text="are you coming tonight?", group=False):
    return messages.get_inbox().add("com.whatsapp", sender, text, group=group)


# ---------------------------------------------------------------- focus mode
def test_in_a_long_event_you_are_in_focus():
    assert focus.current([event()], NOW)["title"] == "Neural Networks"
    assert focus.current([event(minutes=5, title="Creatine", start=NOW)], NOW) is None       # a 5-minute reminder
    assert focus.current([event(all_day=True)], NOW) is None
    assert focus.current([event(routine=True)], NOW) is None
    assert focus.current([event(start=NOW + timedelta(minutes=5))], NOW) is None             # not started yet


def test_focus_holds_ordinary_messages_but_not_mom_or_urgent():
    events = [event()]
    assert focus.holds(msg(), events, NOW) is True
    assert focus.holds(msg("Mom", "Unde esti?"), events, NOW) is False
    assert focus.holds(msg("Dan", "call me, it's urgent"), events, NOW) is False
    assert focus.holds(msg(), [], NOW) is False                                               # no event: no focus


def test_focus_can_be_switched_off_and_on_by_voice():
    assert "off" in focus.tool_focus_mode(on=False)["say"]
    assert focus.holds(msg(), [event()], NOW) is False
    say = focus.tool_focus_mode(on=True)["say"]
    assert "30 minutes or longer" in say and "Mom" in say


@pytest.mark.parametrize("text,on", [("focus mode off", False), ("turn off focus mode", False),
                                     ("focus mode on", True), ("Eva, turn on focus mode", True)])
def test_focus_mode_by_voice(text, on):
    assert fast_path(text, ToolBelt()) == ("focus_mode", {"on": on})


def test_the_focus_status_says_where_you_are(monkeypatch):
    from core import oracle
    monkeypatch.setattr(oracle.get_oracle(), "_events", [event(start=datetime.now() - timedelta(minutes=10))])
    say = focus.tool_focus_mode()["say"]
    assert "Right now you're in Neural Networks until" in say


def test_after_the_event_she_says_what_came_in():
    from core import oracle as oracle_mod
    said = []

    async def deliver(text, widget=None):
        said.append(text)
        return True
    o = oracle_mod.Oracle(store=oracle_mod.get_oracle().store, deliver=deliver, google_getter=lambda: None,
                          announce_email=False)
    o._events = [event()]
    asyncio.run(o.tick(NOW))                                            # in the lecture
    focus.note_held("message", "Andrei")
    focus.note_held("message", "Andrei")
    focus.note_held("email", "Tom Reinhoudt")
    asyncio.run(o.tick(NOW + timedelta(minutes=30)))
    assert said == []
    asyncio.run(o.tick(datetime(2026, 11, 24, 12, 31)))                 # it ended at 12:30
    assert said == ["Sir, while you were in Neural Networks: 2 messages from Andrei and an email from Tom Reinhoudt. "
                    "Say 'any messages' to hear them."]
    asyncio.run(o.tick(datetime(2026, 11, 24, 12, 32)))
    assert len(said) == 1


def test_nothing_held_means_nothing_said():
    from core import oracle as oracle_mod
    said = []

    async def deliver(text, widget=None):
        said.append(text)
        return True
    o = oracle_mod.Oracle(store=oracle_mod.get_oracle().store, deliver=deliver, google_getter=lambda: None,
                          announce_email=False)
    o._events = [event()]
    asyncio.run(o.tick(NOW))
    asyncio.run(o.tick(datetime(2026, 11, 24, 12, 31)))
    assert said == []


def test_the_phone_endpoint_keeps_quiet_in_focus(monkeypatch):
    from core import oracle
    from interfaces.web import server_stream
    monkeypatch.setattr(oracle.get_oracle(), "_events", [event(start=datetime.now() - timedelta(minutes=10))])
    monkeypatch.setattr(oracle.Oracle, "in_quiet_hours", lambda self, now=None: False)
    spoken = []

    async def fake_broadcast(line, *a, **k):
        spoken.append(line)
    monkeypatch.setattr(server_stream, "broadcast", fake_broadcast)
    monkeypatch.setattr(server_stream, "get_tts", lambda: None)
    c = TestClient(server_stream.app)
    r = c.post("/api/phone/message", json={"app": "com.whatsapp", "sender": "Andrei", "text": "you there?"}).json()
    assert r["important"] and r["announce"] == "" and spoken == []
    r = c.post("/api/phone/message", json={"app": "com.whatsapp", "sender": "Mom", "text": "Suna-ma"}).json()
    assert r["announce"].startswith("Message from Mom") and len(spoken) == 1
    assert focus.held_summary() == "a message from Andrei"


# ---------------------------------------------------------------- find my phone
class FakePhone:
    def __init__(self, caps=("ring",), ok=True):
        self.device_caps, self.connected_at, self.ok, self.asked = caps, 1.0, ok, []

    async def request_device(self, action, args, timeout=12.0):
        self.asked.append((action, args))
        return {"ok": self.ok, "error": "" if self.ok else "no sound"}


@pytest.fixture
def loop_thread():
    import threading
    loop = asyncio.new_event_loop()
    t = threading.Thread(target=loop.run_forever, daemon=True)
    t.start()
    device.LOOP = loop
    yield loop
    loop.call_soon_threadsafe(loop.stop)
    device.LOOP = None


def test_find_my_phone_rings_an_open_app(loop_thread):
    phone = FakePhone()
    device.CONNECTIONS.add(phone)
    out = device.tool_phone_ring()
    assert phone.asked == [("ring_phone", {"seconds": 45})]
    assert out["say"] == "Ringing your phone at full volume, sir. Say 'found it' to stop it."
    device.tool_phone_ring(on=False)
    assert phone.asked[-1] == ("stop_ring", {})


def test_without_the_app_the_hands_free_service_gets_it(monkeypatch):
    device.LAST_POLL["ts"] = __import__("time").time() - 5
    out = device.tool_phone_ring()
    assert "within about 20 seconds" in out["say"]
    assert device.take_pending()[0]["action"] == "ring_phone" and device.take_pending() == []


def test_no_app_and_no_service_is_said_honestly():
    out = device.tool_phone_ring()
    assert "can't reach your phone" in out["say"] and "google.com/android/find" in out["say"]
    assert device.PENDING                                             # still waiting, in case it checks in soon


def test_stale_ring_requests_never_go_off_later(monkeypatch):
    import time
    device.queue_for_phone("ring_phone", {"seconds": 45})
    device.PENDING[0]["ts"] -= 300
    assert device.take_pending() == []


def test_the_poll_endpoint_hands_over_the_queue():
    from interfaces.web import server_stream
    device.queue_for_phone("ring_phone", {"seconds": 45})
    c = TestClient(server_stream.app)
    r = c.get("/api/phone/poll").json()
    assert r["actions"] == [{"action": "ring_phone", "args": {"seconds": 45}}] and r["every_s"] == 20
    assert c.get("/api/phone/poll").json()["actions"] == [] and device.LAST_POLL["ts"] > 0


@pytest.mark.parametrize("text,on", [("find my phone", True), ("where's my phone?", True), ("ring my phone", True),
                                     ("I can't find my phone", True), ("found it", False), ("stop ringing", False)])
def test_find_my_phone_by_voice(text, on):
    device.RINGING["ts"] = __import__("time").time()                  # "found it" counts while it rings
    assert fast_path(text, ToolBelt()) == ("phone_ring", {"on": on})


def test_phone_found_only_when_it_was_ringing_by_voice():
    device.RINGING["ts"] = 0
    assert fast_path("found it", ToolBelt()) is None                  # "found it" means nothing otherwise
    device.RINGING["ts"] = __import__("time").time()
    assert fast_path("found it", ToolBelt()) == ("phone_ring", {"on": False})


def test_android_side_knows_ring_and_poll():
    from pathlib import Path
    base = Path("apps/eva_android")
    for root in ("android_overlay", "android"):
        k = base / root / "app/src/main/kotlin/nl/ionut/eva_android"
        actions = (k / "DeviceActions.kt").read_text(encoding="utf-8")
        assert '"ring_phone"' in actions and '"stop_ring"' in actions
        assert "/api/phone/poll" in (k / "WakeService.kt").read_text(encoding="utf-8")
        assert (k / "PhoneRinger.kt").exists()
    assert "'ring'" in Path("interfaces/web/eva.html").read_text(encoding="utf-8")
