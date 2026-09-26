"""
ECHO v0.2.5: the Pipecat pieces of the listening pipeline.

E.V.A. uses Pipecat's own audio analyzers, the same ones a Pipecat Pipeline runs:

    SileroVADAnalyzer          is someone speaking? (local ONNX, CPU, ~2 MB)
    LocalSmartTurnAnalyzerV3   did they finish, or is it a thinking pause? (local ONNX, CPU, ~8 MB)

Both models ship inside the pipecat-ai wheel, so nothing is downloaded at runtime
and no audio leaves the PC. They run on onnxruntime on the CPU: all VRAM stays
with Ollama and Whisper.

Why not a full Pipecat Pipeline and transport: its output transport would replace
E.V.A.'s WebSocket protocol (ordered Kokoro sentences, captions, cards, confirmations)
and its LLM stage would bypass the orchestrator's guards and exact answers. So the
listening half is a small state machine in voice/listen.py that feeds these analyzers
exactly the way Pipecat's TurnAnalyzerUserTurnStopStrategy does, and everything after
the transcript stays E.V.A.'s own.

Everything here imports Pipecat lazily, so the server starts (with browser speech
recognition) even when pipecat-ai is not installed.
"""

from __future__ import annotations

import importlib.util

SAMPLE_RATE = 16000


def available() -> tuple[bool, str]:
    """(True, "") when pipecat-ai with the bundled Silero and Smart Turn models can be imported."""
    try:
        if importlib.util.find_spec("pipecat") is None:
            return False, "pipecat-ai is not installed (pip install pipecat-ai)"
        if importlib.util.find_spec("pipecat.audio.vad.silero") is None:
            return False, "this pipecat-ai has no Silero VAD"
    except Exception as e:                                   # a broken install must not stop the server
        return False, f"pipecat-ai could not be inspected ({e})"
    return True, ""


class PipecatVAD:
    """Silero VAD with Pipecat's hysteresis. analyze() says whether the user is speaking.

    Pipecat reports four states; like its transports, E.V.A. flips "speaking" on at
    SPEAKING and off at QUIET, and keeps the previous value through STARTING and STOPPING.
    """

    def __init__(self, confidence: float = 0.7, start_secs: float = 0.2, stop_secs: float = 0.2,
                 min_volume: float = 0.6):
        from pipecat.audio.vad.silero import SileroVADAnalyzer
        from pipecat.audio.vad.vad_analyzer import VADParams, VADState
        self._states = VADState
        self.start_secs = start_secs
        self._vad = SileroVADAnalyzer(sample_rate=SAMPLE_RATE, params=VADParams(
            confidence=confidence, start_secs=start_secs, stop_secs=stop_secs, min_volume=min_volume))
        self._vad.set_sample_rate(SAMPLE_RATE)
        self._speaking = False

    async def analyze(self, chunk: bytes) -> bool:
        state = await self._vad.analyze_audio(chunk)
        if state == self._states.SPEAKING:
            self._speaking = True
        elif state == self._states.QUIET:
            self._speaking = False
        return self._speaking

    async def close(self) -> None:
        await self._vad.cleanup()


class PipecatSmartTurn:
    """Smart Turn v3: a small audio model that hears whether a sentence sounds finished.

    append() is fed every chunk (speech or not) and returns True only when the silence
    fallback (max_pause_secs) runs out. analyze() is asked when VAD hears the user stop:
    True means the turn is complete, False means "that was a pause, keep listening".
    """

    def __init__(self, max_pause_secs: float = 3.0, pre_speech_ms: float = 500, max_duration_secs: float = 8,
                 vad_start_secs: float = 0.2, cpu_count: int = 1):
        from pipecat.audio.turn.base_turn_analyzer import EndOfTurnState
        from pipecat.audio.turn.smart_turn.base_smart_turn import SmartTurnParams
        from pipecat.audio.turn.smart_turn.local_smart_turn_v3 import LocalSmartTurnAnalyzerV3
        self._complete = EndOfTurnState.COMPLETE
        self._turn = LocalSmartTurnAnalyzerV3(sample_rate=SAMPLE_RATE, cpu_count=cpu_count, params=SmartTurnParams(
            stop_secs=max_pause_secs, pre_speech_ms=pre_speech_ms, max_duration_secs=max_duration_secs))
        self._turn.set_sample_rate(SAMPLE_RATE)
        self._turn.update_vad_start_secs(vad_start_secs)
        self.last_probability: float | None = None

    def append(self, chunk: bytes, is_speech: bool) -> bool:
        return self._turn.append_audio(chunk, is_speech) == self._complete

    async def analyze(self) -> bool:
        state, metrics = await self._turn.analyze_end_of_turn()
        self.last_probability = getattr(metrics, "probability", None)
        return state == self._complete

    def clear(self) -> None:
        self._turn.clear()

    async def close(self) -> None:
        await self._turn.cleanup()


class SilenceTurn:
    """Fallback end-of-turn when Smart Turn can't load: the turn ends after silence_secs of quiet."""

    def __init__(self, silence_secs: float = 0.8):
        self.silence_ms = silence_secs * 1000
        self._quiet_ms = 0.0
        self._heard = False
        self.last_probability: float | None = None

    def append(self, chunk: bytes, is_speech: bool) -> bool:
        if is_speech:
            self._heard, self._quiet_ms = True, 0.0
            return False
        if not self._heard:
            return False
        self._quiet_ms += len(chunk) / 2 / SAMPLE_RATE * 1000
        if self._quiet_ms >= self.silence_ms:
            self.clear()
            return True
        return False

    async def analyze(self) -> bool:
        return False                                       # only silence decides

    def clear(self) -> None:
        self._heard, self._quiet_ms = False, 0.0

    async def close(self) -> None:
        pass
