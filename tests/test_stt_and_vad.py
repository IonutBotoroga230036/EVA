"""faster-whisper wrapper (fake model, never downloads) and Pipecat's real Silero + Smart Turn models.

The Pipecat tests run the actual ONNX models that ship inside the pipecat-ai wheel on test_voice.wav
(a Kokoro recording in the repo). They are skipped when pipecat-ai is not installed.
"""

import asyncio
import importlib.util
import sys
import wave
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

import voice.stt as stt_mod
from voice.stt import WhisperSTT, clean_transcript, hint_words

ROOT = Path(__file__).resolve().parents[1]
HAS_PIPECAT = importlib.util.find_spec("pipecat") is not None


# ------------------------------------------------------------ transcript cleaning
@pytest.mark.parametrize("segments,expected", [
    ([{"text": " What time is it?"}], "What time is it?"),
    ([{"text": " Lights"}, {"text": " purple."}], "Lights purple."),
    ([{"text": " Thanks for watching!"}], ""),
    ([{"text": " you"}], ""),
    ([{"text": " [Music]"}], ""),
    ([{"text": " ..."}], ""),
    ([{"text": " Thank you."}], "Thank you."),                          # people really say it
    ([{"text": " hello", "no_speech_prob": 0.9, "avg_logprob": -1.5}], ""),
    ([{"text": " hello", "no_speech_prob": 0.9, "avg_logprob": -0.2}], "hello"),
])
def test_clean_transcript(segments, expected):
    assert clean_transcript(segments) == expected


def test_hint_words_use_terms_not_aliases_and_always_include_eva():
    vocab = "- Radboud University: roundabout university\n- Nijmegen: neymar can\n- Breda\n- eva\n"
    assert hint_words(vocab) == "Eva, Radboud University, Nijmegen, Breda"
    assert hint_words("") == "Eva"


# ------------------------------------------------------------ the wrapper with a fake WhisperModel
class FakeModel:
    instances = []

    def __init__(self, name, device, compute_type, fail_on=()):
        if device in fail_on:
            raise RuntimeError("cublas64_12.dll not found")
        self.name, self.device, self.compute_type = name, device, compute_type
        self.calls = []
        FakeModel.instances.append(self)

    def transcribe(self, audio, **kw):
        self.calls.append(kw)
        segs = [SimpleNamespace(text=" Radboud is in Nijmegen.", no_speech_prob=0.01, avg_logprob=-0.2)]
        return iter(segs), SimpleNamespace(duration=len(audio) / 16000)


def factory(fail_on=()):
    return lambda name, device, compute_type: FakeModel(name, device, compute_type, fail_on)


def test_gpu_first_with_hotwords_and_no_whisper_vad():
    stt = WhisperSTT("small", device="auto", vocab_source=lambda: "- Nijmegen\n", model_factory=factory())
    text = stt.transcribe(np.zeros(16000, dtype=np.float32))
    model = FakeModel.instances[-1]
    assert text == "Radboud is in Nijmegen."
    assert (model.device, model.compute_type) == ("cuda", "int8_float16")
    kw = model.calls[-1]
    assert kw["hotwords"] == "Eva, Nijmegen" and kw["vad_filter"] is False
    assert kw["condition_on_previous_text"] is False and kw["language"] == "en"
    assert stt.describe() == "Whisper small (cuda int8_float16)"


def test_falls_back_to_cpu_when_cuda_libraries_are_missing():
    stt = WhisperSTT("small", device="cuda", model_factory=factory(fail_on=("cuda",)))
    assert stt.warm() is True
    assert stt.active_device == "cpu int8" and stt.load_error is None


def test_reports_a_clear_error_when_nothing_loads():
    stt = WhisperSTT("small", device="auto", model_factory=factory(fail_on=("cuda", "cpu")))
    assert stt.warm() is False
    assert "cublas64_12.dll" in stt.load_error
    with pytest.raises(RuntimeError):
        stt.transcribe(np.zeros(16000, dtype=np.float32))


def test_too_short_audio_is_not_sent_to_whisper():
    stt = WhisperSTT("small", device="cpu", model_factory=factory())
    assert stt.transcribe(np.zeros(800, dtype=np.float32)) == ""
    assert stt._model is None                                            # never even loaded


def test_nvidia_dll_dirs_are_registered_on_windows(tmp_path, monkeypatch):
    site = tmp_path / "Lib" / "site-packages"
    for pkg in ("cublas", "cudnn"):
        (site / "nvidia" / pkg / "bin").mkdir(parents=True)
    added = []
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(stt_mod.os, "add_dll_directory", lambda p: added.append(p), raising=False)
    monkeypatch.setattr(sys, "path", [str(site)])
    monkeypatch.setenv("PATH", "")
    got = stt_mod.add_nvidia_dll_dirs()
    assert sorted(Path(p).parent.name for p in got) == ["cublas", "cudnn"] and added == got


def test_nvidia_dll_dirs_do_nothing_elsewhere(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    assert stt_mod.add_nvidia_dll_dirs() == []


# ------------------------------------------------------------ Pipecat's real models
def _voice_16k() -> bytes:
    with wave.open(str(ROOT / "test_voice.wav"), "rb") as w:
        rate = w.getframerate()
        a = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(np.float32) / 32768.0
    n = int(len(a) * 16000 / rate)
    a16 = np.interp(np.linspace(0, len(a) - 1, n), np.arange(len(a)), a)   # plain resample, fine for VAD
    pad = np.zeros(8000, dtype=np.float32)
    return (np.clip(np.concatenate([pad, a16, pad, pad, pad]), -1, 1) * 32767).astype("<i2").tobytes()


@pytest.mark.skipif(not HAS_PIPECAT, reason="pipecat-ai not installed")
def test_pipecat_silero_hears_speech_and_silence():
    from voice.pipecat_audio import PipecatVAD
    from voice.listen import FRAME_BYTES

    async def go():
        vad = PipecatVAD()
        pcm = _voice_16k()
        flags = [await vad.analyze(pcm[i:i + FRAME_BYTES]) for i in range(0, len(pcm) - FRAME_BYTES, FRAME_BYTES)]
        await vad.close()
        return flags
    flags = asyncio.run(go())
    assert not any(flags[:10])                                 # the leading half second of silence
    assert sum(flags) > 20                                     # the voice
    assert not flags[-1]                                       # quiet again at the end


@pytest.mark.skipif(not HAS_PIPECAT, reason="pipecat-ai not installed")
def test_real_models_end_to_end_with_pre_roll():
    from voice.listen import FRAME_BYTES, ListenConfig, Listener
    from voice.pipecat_audio import PipecatSmartTurn, PipecatVAD

    class Recorder:
        def __init__(self):
            self.lengths = []

        def transcribe(self, audio):
            self.lengths.append(len(audio))
            return "Eva, this is a test."

    rec, commands = Recorder(), []

    async def noop(*_):
        pass

    async def on_command(text):
        commands.append(text)

    async def go():
        vad = PipecatVAD()
        lst = Listener(vad, PipecatSmartTurn(max_pause_secs=1.0), rec, ListenConfig(pre_roll_ms=700),
                       on_event=noop, on_command=on_command, on_wake=noop, on_barge_in=noop)
        lst.set_wake(True)
        pcm = _voice_16k()
        for i in range(0, len(pcm), FRAME_BYTES):
            await lst.feed(pcm[i:i + FRAME_BYTES])
        await lst.idle()
        await lst.close()
    asyncio.run(go())
    assert commands and commands[0] == "this is a test."
    assert len(rec.lengths) == 1                               # Smart Turn waited through the pause mid-clip
    # speech runs from 0.99 s to 3.23 s of the padded clip; with 0.7 s pre-roll Whisper gets ~0.45 s to ~3.4 s
    assert rec.lengths[0] / 16000 >= 2.8
