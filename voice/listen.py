"""
ECHO v0.2.5: server-side listening.

    mic (browser now, phone app in v0.3) -> PCM16 mono 16 kHz, binary WebSocket frames
      -> pre-roll ring buffer (always filling, so the first words are never lost)
      -> Silero VAD (Pipecat)            the user started / stopped speaking
      -> Smart Turn v3 (Pipecat)         finished, or only a thinking pause?
      -> faster-whisper (local)          text
      -> gate                            armed window, wake word, barge-in, echo guard
      -> on_command(text) | on_wake()

One Listener per connection. It never talks to the network itself: it reports through
callbacks, and the server turns those into WebSocket events.

Gate rules (who is E.V.A. listening to?):
  - Armed: after a tap on the mic, a follow-up window, or "Eva" on its own, the next
    utterance that STARTS inside the window is a command. No wake word needed.
  - Wake mode: any utterance that has "Eva" (or "Hey Eva") in its first three words, or as
    its last word, is a command; the rest of the room is ignored. "Eva" alone -> on_wake
    and an armed command window.
  - Barge-in: while her voice is playing (the client says so with "speaking"), user speech
    first ducks her volume; if it lasts barge_in_secs she stops and the utterance becomes a
    command. Shorter blips (a cough, her own echo) restore the volume and are dropped.
  - Nothing armed and wake mode off: speech is ignored and never transcribed.
  - Echo guard: a transcript that is just a piece of what she said a moment ago is dropped.
"""

from __future__ import annotations

import asyncio
import re
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Optional

import numpy as np
from loguru import logger

SAMPLE_RATE = 16000
FRAME_SAMPLES = 512                     # 32 ms, Silero's window at 16 kHz
FRAME_BYTES = FRAME_SAMPLES * 2
FRAME_MS = FRAME_SAMPLES / SAMPLE_RATE * 1000

Event = Callable[[dict], Awaitable[None]]
TextCb = Callable[[str], Awaitable[None]]
VoidCb = Callable[[], Awaitable[None]]


@dataclass
class ListenConfig:
    pre_roll_ms: int = 700              # audio kept from before VAD confirms speech
    min_speech_ms: int = 250            # shorter bursts are noise, never transcribed
    max_utterance_secs: float = 30.0    # one utterance is cut here, whatever happens
    max_pause_secs: float = 3.0         # a thinking pause may last this long (Smart Turn fallback)
    barge_in: bool = True
    barge_in_secs: float = 0.6          # sustained speech needed to stop her mid-sentence
    command_window_secs: float = 8.0    # after "Eva" alone, or a tap on the mic
    echo_secs: float = 20.0             # how long her own words count as possible echo
    wake_words: tuple[str, ...] = ("eva", "eve", "ava", "iva", "evah")
    wake_phrase: str = "Yes, sir?"

    @classmethod
    def from_settings(cls, cfg: dict) -> "ListenConfig":
        cfg = cfg or {}
        c = cls()
        for key in ("pre_roll_ms", "min_speech_ms"):
            if key in cfg:
                setattr(c, key, int(cfg[key]))
        for key in ("max_utterance_secs", "max_pause_secs", "barge_in_secs", "command_window_secs", "echo_secs"):
            if key in cfg:
                setattr(c, key, float(cfg[key]))
        if "barge_in" in cfg:
            c.barge_in = bool(cfg["barge_in"])
        if cfg.get("wake_words"):
            c.wake_words = tuple(str(w).lower() for w in cfg["wake_words"])
        return c


# ------------------------------------------------------------------ wake word
_WORD = re.compile(r"[a-z']+")


def strip_wake(text: str, wake_words: tuple[str, ...] = ListenConfig.wake_words) -> Optional[str]:
    """The command inside a wake-word utterance, "" for the wake word alone, None without one.

    "Hey Eva, what time is it?" -> "what time is it?"     (wake word in the first three words)
    "What's the weather, Eva?"   -> "What's the weather"   (wake word as the last word)
    "Eva."                       -> ""
    "I told Ava about it"        -> None                   (a name in the middle of a sentence)
    """
    t = text.replace("E.V.A.", "Eva").replace("E.V.A", "Eva")
    words = [(m.group(0), m.start(), m.end()) for m in re.finditer(r"[A-Za-z']+", t)]
    if not words:
        return None
    wake = set(wake_words)
    for i, (w, _s, e) in enumerate(words[:3]):
        if w.lower() in wake:
            before = [x[0].lower() for x in words[:i]]
            if all(b in {"hey", "hi", "okay", "ok", "so", "yo", "hello", "oh"} for b in before):
                return t[e:].lstrip(" ,.!?;:-").strip()
    w, s, _e = words[-1]
    if w.lower() in wake and len(words) > 1:
        return t[:s].rstrip(" ,.!?;:-").strip()
    return None


def _norm(text: str) -> str:
    return " ".join(_WORD.findall(text.lower()))


# ------------------------------------------------------------------ listener
@dataclass
class _Utterance:
    started: float
    chunks: list = field(default_factory=list)
    speech_ms: float = 0.0
    barge: Optional[str] = None             # None | "duck" | "stopped"


class Listener:
    """Turns a stream of PCM16 frames into commands. See the module docstring for the rules."""

    def __init__(self, vad, turn, stt, cfg: ListenConfig | None = None, *,
                 on_event: Event, on_command: TextCb, on_wake: VoidCb, on_barge_in: VoidCb,
                 clock: Callable[[], float] = time.monotonic):
        self.vad, self.turn, self.stt = vad, turn, stt
        self.cfg = cfg or ListenConfig()
        self.on_event, self.on_command, self.on_wake, self.on_barge_in = on_event, on_command, on_wake, on_barge_in
        self.clock = clock
        self.wake_mode = False
        self.armed_until = 0.0              # utterances starting before this need no wake word
        self._armed_notified = True         # "timeout" already sent for the current window
        self.speaking = False               # her voice is playing on the client
        self._ring: deque[bytes] = deque(maxlen=max(1, round(self.cfg.pre_roll_ms / FRAME_MS)))
        self._pending = b""
        self._vad_speaking = False
        self._utt: Optional[_Utterance] = None
        self._said: deque[tuple[float, str]] = deque(maxlen=4)
        self._jobs: asyncio.Queue = asyncio.Queue()
        self._worker: Optional[asyncio.Task] = None
        self.failed = False                 # Whisper could not load: the client should fall back

    # ---------------------------------------------------------------- control
    def arm(self, secs: float | None = None) -> None:
        secs = self.cfg.command_window_secs if secs is None else secs
        self.armed_until = self.clock() + max(0.0, secs)
        self._armed_notified = secs <= 0

    def disarm(self) -> None:
        self.armed_until = 0.0
        self._armed_notified = True

    def armed(self) -> bool:
        return self.clock() < self.armed_until

    def set_wake(self, on: bool) -> None:
        self.wake_mode = bool(on)

    def said(self, text: str) -> None:
        """Remember what she just said, for the echo guard."""
        if text:
            self._said.append((self.clock(), _norm(text)))

    async def set_speaking(self, on: bool) -> None:
        self.speaking = bool(on)
        if not on and self._utt is not None and self._utt.barge == "duck":
            self._utt.barge = None          # she finished first: judge it as a normal utterance
            await self._emit({"type": "barge_in", "stage": "resume"})

    async def close(self) -> None:
        if self._worker:
            self._worker.cancel()
            try:
                await self._worker
            except (asyncio.CancelledError, Exception):
                pass
        for part in (self.vad, self.turn):
            try:
                await part.close()
            except Exception:
                pass

    # ---------------------------------------------------------------- audio in
    async def feed(self, data: bytes) -> None:
        """Any number of PCM16 bytes; they are re-cut into 32 ms frames."""
        if self._worker is None or self._worker.done():
            self._worker = asyncio.create_task(self._transcribe_loop(), name="listener-stt")
        self._pending += data
        while len(self._pending) >= FRAME_BYTES:
            chunk, self._pending = self._pending[:FRAME_BYTES], self._pending[FRAME_BYTES:]
            await self._frame(chunk)

    async def _frame(self, chunk: bytes) -> None:
        now = self.clock()
        was = self._vad_speaking
        speaking = await self.vad.analyze(chunk)
        self._vad_speaking = speaking
        silence_done = self.turn.append(chunk, speaking)
        utt = self._utt

        if speaking and not was and utt is None:                      # a new utterance begins
            utt = self._utt = _Utterance(started=now, chunks=list(self._ring))
            if self.speaking and self.cfg.barge_in:
                utt.barge = "duck"
                await self._emit({"type": "barge_in", "stage": "duck"})
            elif self.armed():                                        # unarmed room speech stays silent in the UI
                await self._emit({"type": "listen", "state": "speech"})
        if utt is not None:
            utt.chunks.append(chunk)
            if speaking:
                utt.speech_ms += FRAME_MS
                if utt.barge == "duck" and utt.speech_ms >= self.cfg.barge_in_secs * 1000:
                    utt.barge = "stopped"
                    logger.info("ECHO: barge-in, stopping her reply")
                    await self._emit({"type": "barge_in", "stage": "stop"})
                    await self.on_barge_in()
            ended = False
            if was and not speaking:                                  # VAD heard them stop
                if await self.turn.analyze():
                    ended = True
                else:
                    await self._emit({"type": "listen", "state": "pause"})
            if silence_done or (now - utt.started) >= self.cfg.max_utterance_secs:
                ended = True
            if ended:
                await self._end_utterance()
        elif self.armed_until and not self._armed_notified and now >= self.armed_until:
            self._armed_notified = True                               # the window closed in silence
            await self._emit({"type": "listen", "state": "timeout"})
        self._ring.append(chunk)

    async def _end_utterance(self) -> None:
        utt, self._utt = self._utt, None
        self.turn.clear()
        if utt is None:
            return
        if utt.barge == "duck":                                       # too short to be a real interruption
            await self._emit({"type": "barge_in", "stage": "resume"})
            return
        armed = utt.barge == "stopped" or utt.started < self.armed_until
        if utt.speech_ms < self.cfg.min_speech_ms:
            if armed:
                await self._emit({"type": "listen", "state": "noise"})
            return
        if not armed and (not self.wake_mode or self.speaking):
            return                                                    # nobody asked; never transcribed
        window_end = self.armed_until
        if armed:
            self.disarm()                                             # one command per window
            await self._emit({"type": "listen", "state": "end"})
        pcm = b"".join(utt.chunks)
        await self._jobs.put((pcm, armed, window_end))

    # ---------------------------------------------------------------- transcription
    async def _transcribe_loop(self) -> None:
        while True:
            pcm, armed, window_end = await self._jobs.get()
            try:
                audio = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0
                try:
                    text = await asyncio.to_thread(self.stt.transcribe, audio)
                except Exception as e:
                    logger.error(f"ECHO: transcription failed: {e}")
                    self.failed = True
                    await self._emit({"type": "stt", "engine": "browser", "reason": f"Whisper failed: {e}"})
                    continue
                await self._handle_text(text, armed, window_end)
            except Exception as e:                                   # one bad utterance must not end listening
                logger.exception(f"ECHO: utterance handling failed: {e}")
            finally:
                self._jobs.task_done()

    async def idle(self) -> None:
        """Wait until every finished utterance has been transcribed and handled (tests, shutdown)."""
        await self._jobs.join()

    async def _handle_text(self, text: str, armed: bool, window_end: float) -> None:
        now = self.clock()
        if not text or self._is_echo(text):
            if text:
                logger.info(f"ECHO: dropped as her own echo: {text!r}")
            if armed:
                if window_end > now:
                    self.armed_until, self._armed_notified = window_end, False   # still their turn
                await self._emit({"type": "listen", "state": "noise"})
            return
        cmd = strip_wake(text, self.cfg.wake_words)
        if armed and cmd is None:
            await self._command(text)
            return
        if cmd is None:
            logger.debug(f"ECHO: ignored (no wake word): {text!r}")
            return
        if len(_norm(cmd)) < 2:
            logger.info("ECHO: wake word")
            self.arm(self.cfg.command_window_secs)
            await self.on_wake()
            return
        await self._command(cmd)

    async def _command(self, text: str) -> None:
        logger.info(f"ECHO heard: {text}")
        await self.on_command(text)

    def _is_echo(self, text: str) -> bool:
        n = _norm(text)
        if not n:
            return True
        if n == _norm(self.cfg.wake_phrase):
            return True
        now = self.clock()
        for t, said in self._said:
            if now - t <= self.cfg.echo_secs and len(n.split()) >= 2 and n in said:
                return True
        return False

    async def _emit(self, event: dict) -> None:
        try:
            await self.on_event(event)
        except Exception as e:                                       # a closed socket must not kill listening
            logger.debug(f"ECHO: event not delivered ({e})")


# ------------------------------------------------------------------ other sample rates
class Resampler:
    """PCM16 mono at any rate -> 16 kHz, streaming. The browser bridge already sends 16 kHz;
    this is for clients (an app, a satellite) that can only send 44.1 or 48 kHz."""

    def __init__(self, rate: int):
        self.rate = int(rate)
        self._stream = None
        if self.rate != SAMPLE_RATE:
            try:
                import soxr                                           # ships with pipecat-ai
                self._stream = soxr.ResampleStream(self.rate, SAMPLE_RATE, 1, dtype="int16")
            except Exception:
                self._stream = None

    def __call__(self, data: bytes) -> bytes:
        if self.rate == SAMPLE_RATE or not data:
            return data
        x = np.frombuffer(data[: len(data) // 2 * 2], dtype="<i2")
        if self._stream is not None:
            return self._stream.resample_chunk(x).astype("<i2").tobytes()
        n = int(len(x) * SAMPLE_RATE / self.rate)                    # fallback: linear, fine for speech
        if n <= 0:
            return b""
        y = np.interp(np.linspace(0, len(x) - 1, n), np.arange(len(x)), x.astype(np.float32))
        return y.astype("<i2").tobytes()


# ------------------------------------------------------------------ building one
def pipeline_config() -> dict:
    from core.settings import get_settings
    return get_settings().get("voice", {}).get("pipeline", {}) or {}


def pipeline_status() -> dict:
    """What the status panel and the client need: server listening on, or the browser fallback and why."""
    cfg = pipeline_config()
    if not cfg.get("enabled", True):
        return {"engine": "browser", "reason": "server listening is off in settings (voice.pipeline.enabled)"}
    from voice import pipecat_audio, stt
    for ok, reason in (pipecat_audio.available(), stt.available()):
        if not ok:
            return {"engine": "browser", "reason": reason}
    engine = stt.get_stt()
    if engine is None:
        return {"engine": "browser", "reason": "Whisper is not available"}
    if engine.load_error:
        return {"engine": "browser", "reason": f"Whisper could not load: {engine.load_error}"}
    return {"engine": "server", "rate": SAMPLE_RATE,
            "detail": f"Silero VAD, Smart Turn v3, {engine.describe()}"}


def build_listener(*, on_event: Event, on_command: TextCb, on_wake: VoidCb, on_barge_in: VoidCb) -> Listener:
    """A Listener with Pipecat's Silero VAD, Smart Turn v3 (or silence fallback), and the shared Whisper.

    Blocking (loads two small ONNX models, ~0.3 s): call it in a worker thread.
    """
    from voice import pipecat_audio
    from voice.stt import get_stt
    cfg = pipeline_config()
    lc = ListenConfig.from_settings(cfg)
    vcfg = cfg.get("vad", {}) or {}
    vad = pipecat_audio.PipecatVAD(confidence=float(vcfg.get("confidence", 0.7)),
                                   start_secs=float(vcfg.get("start_secs", 0.2)),
                                   stop_secs=float(vcfg.get("stop_secs", 0.2)),
                                   min_volume=float(vcfg.get("min_volume", 0.6)))
    turn = None
    if cfg.get("smart_turn", True):
        try:
            turn = pipecat_audio.PipecatSmartTurn(max_pause_secs=lc.max_pause_secs, vad_start_secs=vad.start_secs)
        except Exception as e:
            logger.warning(f"ECHO: Smart Turn unavailable ({e}); ending turns after silence instead")
    if turn is None:
        turn = pipecat_audio.SilenceTurn(float(cfg.get("silence_secs", 0.8)))
    return Listener(vad, turn, get_stt(), lc, on_event=on_event, on_command=on_command,
                    on_wake=on_wake, on_barge_in=on_barge_in)
