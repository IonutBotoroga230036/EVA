"""Nov 23 phone log: every alarm said "your phone app isn't open" while it was (the server module existed twice),
"turn off the lights and stop the music in 10 minutes" did both at once, "turn off the music later" planned a
toggle, "Hey Eva, can you..." was cut after "can you", "Two Faced radio" played a stranger's playlist, and
private things always needed an unlock ("the point of using her is to not touch the phone")."""

import asyncio
import importlib.util
import json
import time
from pathlib import Path

import httpx
import pytest

import core.device as device
import core.later as later
import core.orchestrator_hybrid as orch_mod
from core import phone_prefs
from core.memory.cortex import Cortex
from core.tools_native import _fill_media
from skills.registry import SkillRegistry


# ---------------------------------------------------------------- "your phone app isn't open" (it was)
def test_one_set_of_windows_even_when_the_server_runs_as_main():
    import interfaces.web.server_stream as srv
    assert srv.CONNECTIONS is device.CONNECTIONS
    # `python -m interfaces.web.server_stream`: a second copy of the module must still share the same windows
    path = Path("interfaces/web/server_stream.py")
    spec = importlib.util.spec_from_file_location("eva_server_second_copy", path)
    copy = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(copy)
    assert copy.CONNECTIONS is device.CONNECTIONS
    src = path.read_text(encoding="utf-8")
    assert 'sys.modules.setdefault("interfaces.web.server_stream", sys.modules[__name__])' in src


class Phone:
    def __init__(self, caps, at):
        self.device_caps, self.connected_at, self.asked = caps, at, []

    async def request_device(self, action, args, timeout=12.0):
        self.asked.append((action, args))
        return {"ok": True}


def test_the_newest_phone_window_gets_the_alarm():
    old, new, pc = Phone(("alarm", "timer"), 1.0), Phone(("alarm", "timer"), 2.0), Phone((), 3.0)
    device.CONNECTIONS.update({new, pc, old})
    assert device.phones("alarm") == [old, new]

    async def go():
        device.LOOP = asyncio.get_running_loop()
        return await asyncio.to_thread(device.tool_phone_alarm, time="7:00 am")
    try:
        out = asyncio.run(go())
    finally:
        device.LOOP = None
    assert out["say"] == "Alarm set on your phone for 07:00, sir." and new.asked and not old.asked


def test_phone_volume():
    asked = []
    orig = device._ask
    try:
        device._ask = lambda cap, action, args: asked.append((cap, action, args)) or {"ok": True}
        assert device.tool_phone_volume(level=50)["say"] == "Phone volume at 50 percent, sir."
        assert asked == [("volume", "set_volume", {"level": 50})]
        device._ask = lambda *a: {"ok": False, "error": "no phone"}
        assert device.tool_phone_volume(level=50)["say"] == device.NO_PHONE
    finally:
        device._ask = orig
    kt = Path("apps/eva_android/android_overlay/app/src/main/kotlin/nl/ionut/eva_android/DeviceActions.kt")
    assert '"set_volume" ->' in kt.read_text(encoding="utf-8")
    assert "'volume'] });" in Path("interfaces/web/eva.html").read_text(encoding="utf-8")


# ---------------------------------------------------------------- the phone locked
def test_locked_phone_asks_to_unlock_by_default_but_can_just_work():
    assert phone_prefs.get()["ask_unlock"] is True
    assert not orch_mod._fine_locked("calendar_list", {})
    phone_prefs.set(ask_unlock=False)
    assert orch_mod._fine_locked("calendar_list", {}) and orch_mod._fine_locked("email_check", {})
    phone_prefs.set(ask_unlock=True)
    # turning the asking OFF needs one unlock; turning it back ON works locked
    assert not orch_mod._fine_locked("phone_ask_unlock", {"on": False})
    assert orch_mod._fine_locked("phone_ask_unlock", {"on": True})
    for t in ("set_brain_mode", "spotify_volume", "phone_volume"):
        assert t in orch_mod.LOCKED_OK


@pytest.fixture
def eva(tmp_path, monkeypatch):
    ran = []

    async def aexecute(self, name, args):
        ran.append((name, dict(args)))
        if name == "phone_ask_unlock":
            return device.tool_phone_ask_unlock(**args)
        return {"result": json.dumps({"ok": True}), "say": "Done, sir.", "exact": True}

    monkeypatch.setattr(orch_mod.ToolBelt, "aexecute", aexecute)
    o = orch_mod.HybridOrchestrator(session_id="l", cortex=Cortex(str(tmp_path / "c.db")),
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


@pytest.mark.parametrize("said,on", [
    ("Eva, work while locked", False), ("stop asking me to unlock", False), ("you can work while my phone is locked", False),
    ("I don't want to unlock my phone anymore", False), ("ask me to unlock again", True), ("unlock mode on", True),
])
def test_unlock_switch_by_voice(eva, said, on):
    o, _ = eva
    assert orch_mod.fast_path(said, o.belt) == ("phone_ask_unlock", {"on": on})


def test_switching_it_off_needs_one_unlock(eva):
    o, ran = eva
    o.locked = True
    text, evs = turn(o, "Eva, work while locked")
    assert text == orch_mod.LOCKED_SAY and ran == [] and phone_prefs.get()["ask_unlock"] is True
    o.locked = False
    text, _ = turn(o, "Eva, work while locked")
    assert text.startswith("Done, sir. Everything works with your phone locked now") and not phone_prefs.get()["ask_unlock"]
    o.locked = True
    text, _ = turn(o, "ask me to unlock again")                       # works locked
    assert text.startswith("Alright, sir.") and phone_prefs.get()["ask_unlock"] is True


def test_status_panel_has_the_switch():
    page = Path("interfaces/web/eva.html").read_text(encoding="utf-8")
    assert "['ask', 'Ask to unlock'], ['work', 'Just work']" in page and "emit('phone-setting', { ask_unlock:" in page
    assert phone_prefs.set(ask_unlock=False)["ask_unlock"] is False


# ---------------------------------------------------------------- "X and Y in 10 minutes"
def planner(steps, decide):
    def handler(request):
        body = json.loads(request.content)
        props = (body.get("format") or {}).get("properties", {})
        if "steps" in props:
            return httpx.Response(200, json={"message": {"content": json.dumps({"steps": steps})}})
        if "facts" in props:
            return httpx.Response(200, json={"message": {"content": json.dumps({"facts": []})}})
        if "tool" in props:
            last = body["messages"][-1]["content"].lower()
            d = next((v for k, v in decide.items() if k in last), {"tool": "none"})
            return httpx.Response(200, json={"message": {"content": json.dumps(d)}})
        return httpx.Response(200, json={"message": {"content": "{}"}, "done": True})
    real = httpx.AsyncClient
    return lambda *a, **k: real(transport=httpx.MockTransport(handler))


def test_one_time_for_every_step(eva, monkeypatch):
    o, ran = eva
    monkeypatch.setattr(orch_mod.httpx, "AsyncClient", planner(
        [{"command": "turn off the lights", "uses_previous": False}, {"command": "stop the music", "uses_previous": False}],
        {"lights": {"tool": "lights_power", "on": False}, "music": {"tool": "media_control", "action": "playpause"}}))
    text, _ = turn(o, "can you turn off the lights and stop the music in 10 minutes")
    assert ran == []                                                  # nothing now
    items = later.get_later().pending()
    assert sorted(i["tool"] for i in items) == ["lights_power", "media_control"]
    assert all(590 < i["due"] - time.time() <= 600 for i in items)
    assert next(i for i in items if i["tool"] == "media_control")["args"] == {"action": "pause"}
    assert "turn off the lights in 10 minutes" in text and "stop the music in 10 minutes" in text


def test_a_step_with_its_own_time_keeps_it(eva, monkeypatch):
    o, ran = eva
    monkeypatch.setattr(orch_mod.httpx, "AsyncClient", planner(
        [{"command": "turn off the lights", "uses_previous": False},
         {"command": "stop the music in 10 minutes", "uses_previous": False}],
        {"lights": {"tool": "lights_power", "on": False}, "music": {"tool": "media_control", "action": "playpause"}}))
    turn(o, "turn off the lights and stop the music in 10 minutes")
    assert ran == [("lights_power", {"on": False})]
    assert [i["tool"] for i in later.get_later().pending()] == ["media_control"]


# ---------------------------------------------------------------- stop means pause, never a toggle
@pytest.mark.parametrize("said,action", [
    ("also turn off the music", "pause"), ("stop the music", "pause"), ("pause", "pause"),
    ("turn the music back on", "play"), ("resume", "play"), ("play it again", "play"), ("next song please", "playpause"),
])
def test_media_words(said, action):
    assert _fill_media({"action": "playpause"}, said)["action"] == action
    assert _fill_media({"action": "next"}, said)["action"] == "next"


def test_undo_of_pause_is_play():
    assert orch_mod.HybridOrchestrator._UNDO["media_control"]({"action": "pause"}) == {"action": "play"}
    assert orch_mod.HybridOrchestrator._UNDO["media_control"]({"action": "play"}) == {"action": "pause"}


# ---------------------------------------------------------------- "Hey Eva, can you..." (pause)
@pytest.mark.parametrize("said", ["can you", "Can you?", "Eva, could you please", "um", "I want you to", "and"])
def test_half_a_sentence_gets_yes_sir(eva, said):
    o, ran = eva
    text, evs = turn(o, said)
    assert text == "Yes, sir?" and ran == [] and len(evs) == 1


def test_a_whole_sentence_is_not_half(eva):
    assert not orch_mod._DANGLING.fullmatch("can you stop the music")
    assert not orch_mod._DANGLING.fullmatch("so what's the weather")
    ws = Path("apps/eva_android/android_overlay/app/src/main/kotlin/nl/ionut/eva_android/WakeService.kt")
    assert "now - lastLoud > 1300" in ws.read_text(encoding="utf-8")


# ---------------------------------------------------------------- "play Two Faced radio"
class FakeSpotify:
    def __init__(self, search, mine=None):
        self.search, self.mine = search, mine

    def req(self, method, path, **kw):
        assert path == "/search"
        return self.search

    def match_mine(self, name, threshold=0.72):
        return self.mine


def resolve(fake, what):
    from core.spotify import Spotify
    return Spotify.resolve(fake, what)


def test_song_radio_plays_the_song_then_autoplay():
    from core.spotify import Spotify
    fake = FakeSpotify({"artists": {"items": [{"name": "Linkin Park", "uri": "spotify:artist:lp"}]},
                        "tracks": {"items": [{"name": "Two Faced", "uri": "spotify:track:tf",
                                              "artists": [{"name": "Linkin Park"}]}]}})
    fake._radio = lambda seed: Spotify._radio(fake, seed)
    out = resolve(fake, "two faced radio")
    assert out == {"uris": ["spotify:track:tf"], "label": "a radio from Two Faced by Linkin Park"}
    out = resolve(fake, "Linkin Park radio")
    assert out == {"context_uri": "spotify:artist:lp", "label": "Linkin Park radio"}


def test_your_own_radio_playlist_still_wins():
    fake = FakeSpotify({}, mine={"uri": "spotify:playlist:mine", "name": "Chill Radio"})
    fake._radio = lambda seed: pytest.fail("your playlist first")
    assert resolve(fake, "chill radio")["context_uri"] == "spotify:playlist:mine"
