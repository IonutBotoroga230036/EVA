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
    echo_tail_secs: float = 1.5         # v1.2: speech starting later than this after her voice stopped is him
    wake_words: tuple[str, ...] = ("eva", "eve", "ava", "iva", "evah")
    wake_phrase: str = "Yes, sir?"
    carry_secs: float = 8.0             # "and..." waits this long for the rest of the sentence
    early_wake_ms: int = 900            # wake mode: after this much speech, peek for "Eva" so the UI reacts at once
    barge_peek_ms: int = 1200           # wake-barge mode: how often speech over her voice is checked for "Eva"
    barge_tail_ms: int = 2400           # ...and how much of the latest audio each check hears
    patience_secs: float = 0.0          # v1.1: after Smart Turn says "done", wait this long (settings: 0.7)

    @classmethod
    def from_settings(cls, cfg: dict) -> "ListenConfig":
        cfg = cfg or {}
        c = cls()
        for key in ("pre_roll_ms", "min_speech_ms"):
            if key in cfg:
                setattr(c, key, int(cfg[key]))
        for key in ("max_utterance_secs", "max_pause_secs", "barge_in_secs", "command_window_secs", "echo_secs",
                    "patience_secs", "carry_secs"):
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


_TRAILING = re.compile(r"(?:\.\.\.|\u2026)\s*$|\b(?:and|or|but|so|also|then|because|like|plus|can you|could you|"
                       r"would you|will you|and can you|and also|can you also|"
                       r"uh+|um+|uhm+|erm|er|hmm+|ah+|you know|i mean|so like|sort of|the|an)\s*[,.\-]?\s*$", re.I)     # v1.1: "uh", "so like", "the..." = still talking


def unfinished(text: str) -> bool:
    """Whisper's "..." or a sentence that ends on "and" / "can you": the rest is still coming."""
    return bool(_TRAILING.search((text or "").strip()))


# ------------------------------------------------------------------ listener
def _live_overrides() -> dict:
    """v1.1: the biography's slower listening while it records (longer pauses, longer turns)."""
    try:
        from core import memoir
        return memoir.listening() or {}
    except Exception:
        return {}


@dataclass
class _Utterance:
    started: float
    chunks: list = field(default_factory=list)
    speech_ms: float = 0.0
    barge: Optional[str] = None             # None | "duck" | "stopped"
    wake_checked: int = 0                   # early "Eva?" peeks done (at most two)
    woke: bool = False                      # a peek heard the wake word: this utterance is for her
    over_her: bool = False                  # started while her voice played, in wake-barge mode
    peeked_ms: float = 0.0                  # wake-barge mode: speech already checked for "Eva"
    ending_at: float = 0.0                  # v1.1: Smart Turn said done; ends here unless he talks again
    near_her: bool = True                   # v1.2: began while her voice played or right after (echo possible)


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
        # "voice": any sustained speech interrupts her (needs echo cancellation, i.e. the PC or call mode).
        # "wake": only "Eva" interrupts her. The phone in media mode has no echo cancellation, so the mic hears
        # her too; plain speech can't tell you apart, her name can (Nov 22).
        self.barge_mode = "voice"
        self._ring: deque[bytes] = deque(maxlen=max(1, round(self.cfg.pre_roll_ms / FRAME_MS)))
        self._pending = b""
        self._vad_speaking = False
        self._utt: Optional[_Utterance] = None
        self._said: deque[tuple[float, str]] = deque(maxlen=4)
        self._jobs: asyncio.Queue = asyncio.Queue()
        self._worker: Optional[asyncio.Task] = None
        self.failed = False                 # Whisper could not load: the client should fall back
        self.frames = 0                     # diagnostics: frames received, when, and how loud recently
        self.last_audio = 0.0
        self._peaks: deque[tuple[float, float]] = deque(maxlen=200)
        self._carry: Optional[tuple[float, str]] = None
        self._over: dict = {}
        self.settings_source: Optional[Callable[[], dict]] = None     # v1.1: the settings, re-read per utterance
        self._quiet_since = 0.0             # v1.2: when her voice last stopped playing
        self._speak_known = False           # the client reports when she speaks (else every utterance may be echo)

    def _refresh(self) -> None:
        """New settings from the window (patience, pause length) and the biography's overrides, at each new
        utterance: no restart needed."""
        self._over = _live_overrides()
        if self.settings_source is None:
            return
        try:
            fresh = ListenConfig.from_settings(self.settings_source() or {})
            pause = max(fresh.max_pause_secs, float(self._over.get("patience_secs", 0)) + 1.0)
            if pause != self.cfg.max_pause_secs and hasattr(self.turn, "set_max_pause"):
                self.turn.set_max_pause(pause)
            for key in ("patience_secs", "max_utterance_secs", "max_pause_secs", "carry_secs", "barge_in_secs"):
                setattr(self.cfg, key, getattr(fresh, key))
            self.cfg.max_pause_secs = pause
        except Exception:
            pass

    # ---------------------------------------------------------------- control
    def arm(self, secs: float | None = None) -> None:
        secs = self.cfg.command_window_secs if secs is None else secs
        logger.debug(f"ECHO: listening window open for {secs:.1f}s")
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
        self._speak_known = True
        if self.speaking and not on:
            self._quiet_since = self.clock()
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
    def mic_problem(self) -> Optional[str]:
        """Why a listening window can't hear anything, in plain words, or None when the mic looks fine."""
        now = self.clock()
        if not self.frames:
            return "a listening window opened, but this device has sent no mic audio at all (mic off or blocked)"
        age = now - self.last_audio
        if age > 2.0:
            return f"a listening window opened, but no mic audio has arrived for {age:.0f} s (the device's mic stopped)"
        recent = [p for t, p in self._peaks if now - t <= 5.0]
        if recent and max(recent) == 0.0:             # exact digital zeros: muted or held by another app
                                                      # (a quiet room with noise suppression is merely very soft)
            return "mic audio is arriving but it is silent (another app may hold the mic, or it is muted)"
        return None

    async def feed(self, data: bytes) -> None:
        """Any number of PCM16 bytes; they are re-cut into 32 ms frames."""
        if not self.frames:
            logger.info("ECHO: mic audio is arriving")
        self.frames += 1
        self.last_audio = self.clock()
        if len(data) >= 2:
            a = np.frombuffer(data[: len(data) // 2 * 2], dtype="<i2")
            self._peaks.append((self.last_audio, float(np.abs(a).max()) / 32768.0))
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
            self._refresh()
            utt = self._utt = _Utterance(started=now, chunks=list(self._ring))
            utt.near_her = (not self._speak_known or self.speaking
                            or now - self._quiet_since <= self.cfg.echo_tail_secs)
            if self.speaking and self.barge_mode == "wake":
                utt.over_her = True                                   # judged by her name only, never ducked
            elif self.speaking and self.cfg.barge_in:
                utt.barge = "duck"
                await self._emit({"type": "barge_in", "stage": "duck"})
            elif self.armed():                                        # unarmed room speech stays silent in the UI
                await self._emit({"type": "listen", "state": "speech"})
        if utt is not None:
            utt.chunks.append(chunk)
            if speaking:
                utt.speech_ms += FRAME_MS
                if (self.wake_mode and not utt.woke and utt.barge is None and not self.speaking and not self.armed()
                        and utt.wake_checked < 2
                        and utt.speech_ms >= self.cfg.early_wake_ms * (1 + utt.wake_checked * 1.2)):
                    utt.wake_checked += 1               # peek at ~0.9 s and ~2 s: is this "Eva, ..."?
                    asyncio.create_task(self._early_wake(utt, b"".join(utt.chunks)))
                if (utt.over_her and not utt.woke and self.speaking
                        and utt.speech_ms - utt.peeked_ms >= self.cfg.barge_peek_ms):
                    utt.peeked_ms = utt.speech_ms          # every ~1.2 s of speech over her: was "Eva" in it?
                    tail = b"".join(utt.chunks[-int(self.cfg.barge_tail_ms / FRAME_MS):])
                    asyncio.create_task(self._early_wake(utt, tail, anywhere=True))
                if utt.barge == "duck" and utt.speech_ms >= self.cfg.barge_in_secs * 1000:
                    utt.barge = "stopped"
                    logger.info("ECHO: barge-in, stopping her reply")
                    await self._emit({"type": "barge_in", "stage": "stop"})
                    await self.on_barge_in()
            ended = False
            patience = max(self.cfg.patience_secs, float(self._over.get("patience_secs", 0)))
            if speaking and utt.ending_at:                            # he went on: the same turn
                utt.ending_at = 0.0
            if was and not speaking:                                  # VAD heard them stop
                if await self.turn.analyze():
                    if patience > 0:
                        utt.ending_at = now + patience
                    else:
                        ended = True
                else:
                    await self._emit({"type": "listen", "state": "pause"})
            if utt.ending_at and not speaking and now >= utt.ending_at:
                ended = True
            limit = max(self.cfg.max_utterance_secs, float(self._over.get("max_utterance_secs", 0)))
            if silence_done or (now - utt.started) >= limit:
                ended = True
            if ended:
                await self._end_utterance()
        elif self.armed_until and not self._armed_notified and now >= self.armed_until:
            self._armed_notified = True                               # the window closed in silence
            if self._carry:                   # v1.1: a held "can you-" that got no rest still goes to her
                text, self._carry = self._carry[1], None
                text = re.sub(r"[,\s]*\b(?:uh+|um+|uhm+|erm|er|hmm+|ah+)\W*$", "", text, flags=re.I).strip() or text
                logger.info(f"ECHO heard (unfinished, nothing followed): {text}")
                await self.on_command(text)
            else:
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
        armed = utt.barge == "stopped" or utt.started < self.armed_until or utt.woke
        if utt.speech_ms < self.cfg.min_speech_ms:
            if armed:
                await self._emit({"type": "listen", "state": "noise"})
            return
        if utt.over_her and not armed:
            pcm = b"".join(utt.chunks)                                # over her voice: only her name counts
            await self._jobs.put((pcm, False, 0.0, True))
            return
        if not armed and (not self.wake_mode or self.speaking):
            return                                                    # nobody asked; never transcribed
        window_end = self.armed_until
        if armed:
            self.disarm()                                             # one command per window
            await self._emit({"type": "listen", "state": "end"})
        pcm = b"".join(utt.chunks)
        await self._jobs.put((pcm, armed, window_end, False, utt.near_her))

    async def _early_wake(self, utt: "_Utterance", pcm: bytes, anywhere: bool = False) -> None:
        """Wake mode: transcribe the first second or two while you're still talking. If it starts with her name,
        say so right away (the listening animation starts now, not after you finish). anywhere=True (over her
        voice): her name anywhere in the last couple of seconds stops her at once."""
        try:
            audio = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0
            text = await asyncio.to_thread(self.stt.transcribe, audio)
        except Exception:
            return
        words = re.findall(r"[A-Za-z']+", (text or "").replace("E.V.A.", "Eva"))
        if not anywhere:
            words = words[:3]
        if any(w.lower() in self.cfg.wake_words for w in words) and not utt.woke:
            utt.woke = True
            logger.info(f"ECHO: early wake ({text!r})")
            if anywhere and self.speaking:
                await self._stop_her()
            if self._utt is utt:
                await self._emit({"type": "listen", "state": "speech"})

    async def _stop_her(self) -> None:
        logger.info("ECHO: 'Eva' over her voice, stopping her reply")
        await self._emit({"type": "barge_in", "stage": "stop"})
        await self.on_barge_in()

    # ---------------------------------------------------------------- transcription
    async def _transcribe_loop(self) -> None:
        while True:
            job = await self._jobs.get()
            pcm, armed, window_end = job[:3]
            over_her = len(job) > 3 and job[3]
            near_her = job[4] if len(job) > 4 else True
            try:
                audio = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0
                try:
                    text = await asyncio.to_thread(self._transcribe, audio)
                except Exception as e:
                    logger.error(f"ECHO: transcription failed: {e}")
                    self.failed = True
                    await self._emit({"type": "stt", "engine": "browser", "reason": f"Whisper failed: {e}"})
                    continue
                if over_her:
                    await self._handle_over_her(text)
                else:
                    await self._handle_text(text, armed, window_end, near_her)
            except Exception as e:                                   # one bad utterance must not end listening
                logger.exception(f"ECHO: utterance handling failed: {e}")
            finally:
                self._jobs.task_done()

    def _transcribe(self, audio) -> str:
        """v1.2: long speech (a story) in pieces of at most ~24 s, cut where he paused."""
        from voice.stt import split_long
        pieces = split_long(audio)
        if len(pieces) == 1:
            return self.stt.transcribe(audio)
        texts = [t for t in (self.stt.transcribe(p) for p in pieces) if t]
        logger.info(f"ECHO: {len(audio) / SAMPLE_RATE:.0f} s of speech, read in {len(pieces)} pieces")
        return " ".join(texts)

    async def idle(self) -> None:
        """Wait until every finished utterance has been transcribed and handled (tests, shutdown)."""
        await self._jobs.join()

    async def _handle_text(self, text: str, armed: bool, window_end: float, near_her: bool = True) -> None:
        now = self.clock()
        if not text or (near_her and self._is_echo(text)):
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

    async def _handle_over_her(self, text: str) -> None:
        """Speech that began while she was talking (wake-barge mode). Her own voice, heard by the mic, is mixed in,
        so only what follows the LAST "Eva" counts. No "Eva": it was her, or the room, and it's dropped."""
        words = re.findall(r"\S+", (text or "").replace("E.V.A.", "Eva"))
        hits = [i for i, w in enumerate(words) if re.sub(r"[^a-z']", "", w.lower()) in self.cfg.wake_words]
        if not hits:
            logger.debug(f"ECHO: over her voice, no 'Eva': dropped {text!r}")
            return
        if self.speaking:                                            # the peek didn't catch it in time
            await self._stop_her()
        cmd = " ".join(words[hits[-1] + 1:]).strip(" ,.!?")
        if len(_norm(cmd)) < 2:
            logger.info("ECHO: wake word (over her voice)")
            self.arm(self.cfg.command_window_secs)
            await self.on_wake()
            return
        await self._command(cmd)

    async def _command(self, text: str) -> None:
        now = self.clock()
        if self._carry and now - self._carry[0] <= self.cfg.carry_secs:
            text = f"{self._carry[1]} {text}".strip()
        self._carry = None
        if unfinished(text) and len(text.split()) <= 40:
            self._carry = (now, text.rstrip(" .\u2026"))       # "and...", "can you also...": wait for the rest
            self.arm(self.cfg.carry_secs)
            logger.info(f"ECHO: holding an unfinished sentence: {text!r}")
            await self._emit({"type": "listen", "state": "pause"})
            return
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
    listener = Listener(vad, turn, get_stt(), lc, on_event=on_event, on_command=on_command,
                        on_wake=on_wake, on_barge_in=on_barge_in)
    listener.settings_source = pipeline_config
    return listener
