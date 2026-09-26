"""Server-side listening (v0.2.5 milestone 1): pre-roll, thinking pauses, gating, wake word, barge-in, echo.

Fakes stand in for the models: a frame whose first byte is non-zero is "speech" to the fake VAD,
the second byte is a marker so tests can see exactly which audio reached Whisper.
"""

import asyncio

import numpy as np
import pytest

from voice.listen import FRAME_BYTES, FRAME_MS, ListenConfig, Listener, strip_wake


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class FakeVAD:
    def __init__(self, clock):
        self.clock = clock

    async def analyze(self, chunk):
        self.clock.t += FRAME_MS / 1000                  # every frame is 32 ms of real time
        return chunk[0] != 0

    async def close(self):
        pass


class FakeTurn:
    """analyze() answers from a script (default: complete). append() ends the turn after N silent frames."""

    def __init__(self, verdicts=(), silence_frames=90):
        self.verdicts = list(verdicts)
        self.silence_frames = silence_frames
        self.quiet, self.heard, self.analyzed = 0, False, 0

    def append(self, chunk, is_speech):
        if is_speech:
            self.heard, self.quiet = True, 0
            return False
        if self.heard:
            self.quiet += 1
            if self.quiet >= self.silence_frames:
                self.clear()
                return True
        return False

    async def analyze(self):
        self.analyzed += 1
        return self.verdicts.pop(0) if self.verdicts else True

    def clear(self):
        self.heard, self.quiet = False, 0

    async def close(self):
        pass


class FakeSTT:
    def __init__(self, *texts, fail=False):
        self.texts = list(texts)
        self.heard = []                                   # marker bytes of the audio Whisper received
        self.fail = fail

    def transcribe(self, audio):
        if self.fail:
            raise RuntimeError("no cuDNN")
        pcm = (np.asarray(audio) * 32768.0).round().astype("<i2").tobytes()
        self.heard.append([pcm[i + 1] for i in range(0, len(pcm), FRAME_BYTES)])
        return self.texts.pop(0) if self.texts else ""


def frame(speech, marker=0):
    b = bytearray(FRAME_BYTES)
    b[0] = 1 if speech else 0
    b[1] = marker % 256
    return bytes(b)


def speech(n, start=100):
    return b"".join(frame(True, start + i) for i in range(n))


def silence(n, start=0):
    return b"".join(frame(False, start + i) for i in range(n))


def make(stt, turn=None, **cfg):
    clock = Clock()
    log = {"events": [], "commands": [], "wakes": 0, "barge": 0}

    async def on_event(e):
        log["events"].append(e)

    async def on_command(t):
        log["commands"].append(t)

    async def on_wake():
        log["wakes"] += 1

    async def on_barge_in():
        log["barge"] += 1

    lst = Listener(FakeVAD(clock), turn or FakeTurn(), stt, ListenConfig(**cfg), on_event=on_event,
                   on_command=on_command, on_wake=on_wake, on_barge_in=on_barge_in, clock=clock)
    return lst, log, clock


def run(coro_fn):
    return asyncio.run(coro_fn())


def stages(log):
    return [(e["type"], e.get("state") or e.get("stage")) for e in log["events"]]


# ------------------------------------------------------------ pre-roll and turns
def test_pre_roll_keeps_the_first_words():
    stt = FakeSTT("what time is it")

    async def go():
        lst, log, _ = make(stt, pre_roll_ms=160)          # 5 frames of pre-roll
        lst.arm()
        await lst.feed(silence(20) + speech(20) + silence(5))
        await lst.idle()
        return log
    log = run(go)
    assert log["commands"] == ["what time is it"]
    heard = stt.heard[0]
    assert heard[:5] == [15, 16, 17, 18, 19]             # the 160 ms before VAD fired are included
    assert heard[5:25] == list(range(100, 120))           # then every speech frame


def test_thinking_pause_keeps_one_utterance():
    stt = FakeSTT("add coffee with Tom tomorrow at three")
    turn = FakeTurn(verdicts=[False, True])                # the first stop is only a pause

    async def go():
        lst, log, _ = make(stt, turn)
        lst.arm()
        await lst.feed(speech(15, 100) + silence(20) + speech(15, 150) + silence(3))
        await lst.idle()
        return log
    log = run(go)
    assert log["commands"] == ["add coffee with Tom tomorrow at three"]
    assert len(stt.heard) == 1                            # one utterance, both halves
    assert 100 in stt.heard[0] and 164 in stt.heard[0]
    assert ("listen", "pause") in stages(log)
    assert turn.analyzed == 2


def test_silence_fallback_ends_a_turn_the_model_called_unfinished():
    stt = FakeSTT("so I was thinking")
    turn = FakeTurn(verdicts=[False], silence_frames=40)

    async def go():
        lst, log, _ = make(stt, turn)
        lst.arm()
        await lst.feed(speech(15) + silence(39))
        await lst.idle()
        assert log["commands"] == []                      # still waiting through the pause
        await lst.feed(silence(2))
        await lst.idle()
        return log
    assert run(go)["commands"] == ["so I was thinking"]


def test_frames_can_arrive_in_any_size():
    stt = FakeSTT("hello there")

    async def go():
        lst, log, _ = make(stt)
        lst.arm()
        data = silence(5) + speech(12) + silence(3)
        for i in range(0, len(data), 333):                # odd sizes, split mid-frame
            await lst.feed(data[i:i + 333])
        await lst.idle()
        return log
    assert run(go)["commands"] == ["hello there"]


def test_max_utterance_cuts_a_monologue():
    stt = FakeSTT("a very long story")

    async def go():
        lst, log, _ = make(stt, max_utterance_secs=1.0)
        lst.arm()
        await lst.feed(speech(40))                        # 1.28 s without a pause
        await lst.idle()
        return log
    assert run(go)["commands"] == ["a very long story"]


def test_short_noise_is_never_transcribed():
    stt = FakeSTT("should not be used")

    async def go():
        lst, log, _ = make(stt, min_speech_ms=250)
        lst.arm()
        await lst.feed(speech(3) + silence(3))             # ~100 ms click
        await lst.idle()
        return log
    log = run(go)
    assert stt.heard == [] and log["commands"] == []
    assert ("listen", "noise") in stages(log)


# ------------------------------------------------------------ gating
def test_unarmed_speech_without_wake_mode_is_ignored_and_never_transcribed():
    stt = FakeSTT("private conversation")

    async def go():
        lst, log, _ = make(stt)
        await lst.feed(speech(20) + silence(3))
        await lst.idle()
        return log
    log = run(go)
    assert stt.heard == [] and log["commands"] == [] and log["events"] == []


def test_one_command_per_armed_window():
    stt = FakeSTT("what time is it", "and something the tv said")

    async def go():
        lst, log, _ = make(stt)
        lst.arm()
        await lst.feed(speech(10) + silence(3))
        await lst.feed(speech(10, 130) + silence(3))       # after the command, the window is closed
        await lst.idle()
        return log
    log = run(go)
    assert log["commands"] == ["what time is it"] and len(stt.heard) == 1


def test_window_that_closes_in_silence_says_timeout():
    async def go():
        lst, log, _ = make(FakeSTT())
        lst.arm(0.5)
        await lst.feed(silence(20))                        # 640 ms of nothing
        return log
    assert ("listen", "timeout") in stages(run(go))


def test_empty_transcript_keeps_the_window_open():
    stt = FakeSTT("", "lights purple")

    async def go():
        lst, log, _ = make(stt)
        lst.arm(5)
        await lst.feed(speech(10) + silence(3))            # a breath Whisper hears as nothing
        await lst.idle()
        await lst.feed(speech(10, 140) + silence(3))
        await lst.idle()
        return log
    assert run(go)["commands"] == ["lights purple"]


@pytest.mark.parametrize("text,cmd", [
    ("Hey Eva, what time is it?", "what time is it?"),
    ("Eva what's the weather in Tilburg", "what's the weather in Tilburg"),
    ("E.V.A., lights red", "lights red"),
    ("Okay Eva. Play The Weeknd.", "Play The Weeknd."),
    ("What's on my calendar, Eva?", "What's on my calendar"),
    ("Eva.", ""),
    ("Hey Eva!", ""),
    ("I told Ava about the party", None),
    ("my sister Eve is coming over", None),
    ("what time is it", None),
    ("", None),
])
def test_strip_wake(text, cmd):
    assert strip_wake(text) == cmd


def test_wake_mode_takes_commands_with_the_wake_word_only():
    stt = FakeSTT("I told him about it", "Eva, what time is it?")

    async def go():
        lst, log, _ = make(stt)
        lst.set_wake(True)
        await lst.feed(speech(10) + silence(3) + speech(10, 140) + silence(3))
        await lst.idle()
        return log
    log = run(go)
    assert log["commands"] == ["what time is it?"]
    assert log["events"] == []                             # room speech never animates the UI


def test_wake_word_alone_answers_and_opens_a_window():
    stt = FakeSTT("Hey Eva.", "what's the weather tomorrow")

    async def go():
        lst, log, _ = make(stt)
        lst.set_wake(True)
        await lst.feed(speech(10) + silence(3))
        await lst.idle()
        assert log["wakes"] == 1 and lst.armed()
        await lst.feed(speech(12, 140) + silence(3))
        await lst.idle()
        return log
    assert run(go)["commands"] == ["what's the weather tomorrow"]


def test_armed_wake_word_alone_is_a_wake_not_a_command():
    stt = FakeSTT("Eva")

    async def go():
        lst, log, _ = make(stt)
        lst.arm()
        await lst.feed(speech(10) + silence(3))
        await lst.idle()
        return log
    log = run(go)
    assert log["commands"] == [] and log["wakes"] == 1


# ------------------------------------------------------------ barge-in and echo
def test_sustained_speech_while_she_talks_stops_her_and_becomes_a_command():
    stt = FakeSTT("stop, set a timer instead")

    async def go():
        lst, log, _ = make(stt, barge_in_secs=0.3)
        await lst.set_speaking(True)
        await lst.feed(speech(15) + silence(3))            # 480 ms of speech
        await lst.idle()
        return log
    log = run(go)
    assert log["barge"] == 1
    assert ("barge_in", "duck") in stages(log) and ("barge_in", "stop") in stages(log)
    assert log["commands"] == ["stop, set a timer instead"]


def test_a_cough_while_she_talks_only_ducks_her_for_a_moment():
    stt = FakeSTT("cough")

    async def go():
        lst, log, _ = make(stt, barge_in_secs=0.6, min_speech_ms=64)
        await lst.set_speaking(True)
        await lst.feed(speech(6) + silence(3))             # ~190 ms
        await lst.idle()
        return log
    log = run(go)
    assert log["barge"] == 0 and stt.heard == []
    assert stages(log) == [("barge_in", "duck"), ("barge_in", "resume")]


def test_barge_in_can_be_switched_off():
    stt = FakeSTT("echo of her own voice")

    async def go():
        lst, log, _ = make(stt, barge_in=False)
        await lst.set_speaking(True)
        await lst.feed(speech(30) + silence(3))
        await lst.idle()
        return log
    log = run(go)
    assert log["barge"] == 0 and log["commands"] == [] and stt.heard == []


def test_speech_that_outlasts_her_reply_counts_as_a_follow_up():
    stt = FakeSTT("and tomorrow?")

    async def go():
        lst, log, _ = make(stt, barge_in_secs=5)
        await lst.set_speaking(True)
        await lst.feed(speech(5))                          # starts during her last word
        await lst.set_speaking(False)
        lst.arm(7)                                         # the client opens the follow-up window
        await lst.feed(speech(10, 110) + silence(3))
        await lst.idle()
        return log
    log = run(go)
    assert ("barge_in", "resume") in stages(log)
    assert log["commands"] == ["and tomorrow?"] and log["barge"] == 0


def test_her_own_words_coming_back_are_dropped():
    stt = FakeSTT("tomorrow in Breda overcast", "Yes, sir?", "what about Friday")

    async def go():
        lst, log, _ = make(stt)
        lst.said("Tomorrow in Breda: overcast, 15 to 21 degrees.")
        for i, marker in enumerate((100, 140, 180)):
            lst.arm(8)
            await lst.feed(speech(10, marker) + silence(3))
            await lst.idle()
        return log
    assert run(go)["commands"] == ["what about Friday"]


def test_whisper_failure_tells_the_client_to_fall_back():
    async def go():
        lst, log, _ = make(FakeSTT(fail=True))
        lst.arm()
        await lst.feed(speech(10) + silence(3))
        await lst.idle()
        return lst, log
    lst, log = run(go)
    assert lst.failed
    assert {"type": "stt", "engine": "browser", "reason": "Whisper failed: no cuDNN"} in log["events"]


def test_config_from_settings():
    c = ListenConfig.from_settings({"pre_roll_ms": 900, "barge_in": False, "max_pause_secs": 2.5,
                                    "wake_words": ["Eva", "Kira"]})
    assert (c.pre_roll_ms, c.barge_in, c.max_pause_secs, c.wake_words) == (900, False, 2.5, ("eva", "kira"))
    assert ListenConfig.from_settings(None).command_window_secs == 8.0
