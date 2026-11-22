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
