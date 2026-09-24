import asyncio
import base64
import io
import time
import wave

import numpy as np
import pytest

import voice.speech as speech
from voice.speech import KokoroTTS, SentenceChunker, TurnSpeaker, speakable, to_wav_bytes


@pytest.mark.parametrize("raw,spoken", [
    ("It's **17°C** in Breda.", "It's 17 degrees in Breda."),
    ("Wind 12 km/h, 60% rain.", "Wind 12 kilometres per hour, 60 percent rain."),
    ("See https://example.com for more 🌧", "See the link for more"),
    ("- first\n- second", "first second"),
    ("Run `pip install ddgs` now.", "Run now."),
    ("It is 22:41, sir.", "It is 10 41 PM, sir."),
    ("Tomorrow at 18:00 in Tilburg.", "Tomorrow at 6 PM in Tilburg."),
    ("Meet at 6:00 PM.", "Meet at 6 PM."),
    ("Wake at 06:05 am.", "Wake at 6 oh 5 AM."),
    ("Midnight is 00:00.", "Midnight is 12 AM."),
    ("Rain on 2026-09-26.", "Rain on September 26."),
    ("The score was 3:2.", "The score was 3:2."),
])
def test_speakable(raw, spoken):
    assert speakable(raw) == spoken


def stream(text, chunker=None):
    c = chunker or SentenceChunker()
    out = []
    for tok in text.split(" "):
        out += c.feed(tok + " ")
    return out, c.flush()


def test_chunker_splits_sentences_but_not_decimals_or_abbreviations():
    out, rest = stream("Good evening, sir. It is 3.5 degrees and Dr. Smith called. Rain later")
    assert out == ["Good evening, sir.", "It is 3.5 degrees and Dr. Smith called."]
    assert rest == "Rain later"


def test_chunker_handles_newlines_and_questions():
    c = SentenceChunker()
    assert c.feed("Shall I play jazz? ") == ["Shall I play jazz?"]
    assert c.feed("Line one\nLine two") == ["Line one"]
    assert c.flush() == "Line two"


def test_chunker_breaks_long_run_on_at_a_comma():
    long = ("I checked the forecast for the whole week and it looks mostly dry, with some clouds "
            "on Tuesday and Wednesday, a little wind on Thursday and warmer air arriving over the weekend")
    out, rest = stream(long, SentenceChunker(long_clause=100))
    assert out and out[0].endswith(",") and len(out[0]) <= 100
    assert rest


def test_wav_bytes_are_valid():
    wav = to_wav_bytes(np.sin(np.linspace(0, 100, 24000)).astype(np.float32))
    with wave.open(io.BytesIO(wav)) as w:
        assert (w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()) == (1, 2, 24000, 24000)


class _Result:
    def __init__(self, audio):
        self.audio = audio


def test_kokoro_wrapper_concatenates_segments_and_caches(monkeypatch):
    tts = KokoroTTS()
    calls = []

    def fake_pipe(text, voice, speed):
        calls.append(text)
        yield ("g", "p", np.zeros(1200, dtype=np.float32))     # old tuple API
        yield _Result(np.ones(800, dtype=np.float32) * 0.1)      # new Result API
    tts._pipe = fake_pipe
    wav = tts.synth("Hello there, sir. Two sentences.")
    with wave.open(io.BytesIO(wav)) as w:
        assert w.getnframes() == 2000                             # both segments, not just the first
    tts.phrase("Yes, sir?")
    tts.phrase("Yes, sir?")
    assert calls.count("Yes, sir?") == 1                          # cached
    assert tts.synth("   ") is None


def test_get_tts_is_none_without_kokoro(monkeypatch):
    monkeypatch.setattr(speech, "_tts_checked", False)
    monkeypatch.setattr(speech, "_tts", None)
    import builtins
    real_import = builtins.__import__

    def no_kokoro(name, *a, **k):
        if name == "kokoro":
            raise ImportError("not installed")
        return real_import(name, *a, **k)
    monkeypatch.setattr(builtins, "__import__", no_kokoro)
    assert speech.get_tts() is None


class FakeTTS:
    """Synthesis 'audio' is just the text; later sentences finish FASTER to test ordering."""

    def __init__(self, fail_on=None):
        self.fail_on = fail_on

    def synth(self, text):
        time.sleep(0.05 if len(text) > 12 else 0.01)
        if self.fail_on and self.fail_on in text:
            raise RuntimeError("boom")
        return text.encode()


def run_speaker(tts, sentences):
    sent = []

    async def send(msg):
        sent.append(msg)

    async def go():
        sp = TurnSpeaker(tts, send, turn=7)
        for s in sentences:
            await sp.say(s)
        await sp.finish()
    asyncio.run(go())
    return sent


def test_turn_speaker_sends_audio_in_order_then_end():
    sent = run_speaker(FakeTTS(), ["A much longer first sentence.", "Short.", "", "Third one here."])
    audio = [m for m in sent if m["type"] == "audio"]
    assert [m["seq"] for m in audio] == [0, 1, 2]
    assert [base64.b64decode(m["audio"]).decode() for m in audio] == [
        "A much longer first sentence.", "Short.", "Third one here."]
    assert sent[-1] == {"type": "audio_end", "turn": 7, "count": 3}


def test_turn_speaker_survives_a_failed_sentence():
    sent = run_speaker(FakeTTS(fail_on="bad"), ["Fine one.", "This is bad.", "Fine again."])
    assert [m["seq"] for m in sent if m["type"] == "audio"] == [0, 1]
    assert sent[-1]["count"] == 2
