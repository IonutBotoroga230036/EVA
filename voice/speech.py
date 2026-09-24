"""
ECHO v0.2: sentence-by-sentence streaming speech with Kokoro.

Pipeline per turn (all on this machine):

    orchestrator tokens -> SentenceChunker -> TurnSpeaker queue
        -> Kokoro synth in a worker thread (one sentence at a time)
        -> WAV bytes -> base64 -> WebSocket {"type": "audio", "turn", "seq"}
        -> browser plays chunks strictly in seq order

Sentence 1 is being synthesized while the model is still generating sentence 2,
so she starts speaking before she has finished thinking.

Kokoro runs on the CPU by default (voice.tts.device in settings). Kokoro-82M is
fast on a 12-core CPU, and this keeps all 6 GB of VRAM for the language and
vision models. Set device: "cuda" to try the GPU.
"""

from __future__ import annotations

import asyncio
import base64
import io
import re
import threading
import wave
from typing import Awaitable, Callable, Optional

import numpy as np
from loguru import logger

SAMPLE_RATE = 24000

# ------------------------------------------------------------ text for speech
_URL = re.compile(r"https?://\S+|www\.\S+")
_EMOJI = re.compile("[\U0001F000-\U0001FAFF\u2600-\u27BF\uFE0F]")
_REPLACE = [
    (re.compile(r"°\s*C\b"), " degrees"), (re.compile(r"°\s*F\b"), " degrees Fahrenheit"),
    (re.compile(r"°"), " degrees"), (re.compile(r"\bkm/h\b"), " kilometres per hour"),
    (re.compile(r"(\d)\s*%"), r"\1 percent"), (re.compile(r"\s&\s"), " and "),
    (re.compile(r"\be\.g\."), "for example"), (re.compile(r"\bi\.e\."), "that is"),
    (re.compile(r"\bvs\.?\b"), "versus"),
]


_TIME = re.compile(r"\b([01]?\d|2[0-3]):([0-5]\d)(?:\s*([AaPp])(?:\.\s?[Mm]\.|\s?[Mm]\b))?(?![\d:])")
_ISO_DATE = re.compile(r"\b(20\d\d)-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])\b")
_MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August",
           "September", "October", "November", "December"]


def _say_time(m: re.Match) -> str:
    """'18:00' -> '6 PM', '22:41' -> '10 41 PM', '6:05 am' -> '6 oh 5 AM'."""
    h, mi, half = int(m.group(1)), int(m.group(2)), m.group(3)
    suffix = ("AM" if half.lower() == "a" else "PM") if half else ("AM" if h < 12 else "PM")
    h12 = h % 12 or 12
    if mi == 0:
        return f"{h12} {suffix}"
    return f"{h12} oh {mi} {suffix}" if mi < 10 else f"{h12} {mi} {suffix}"


def _say_date(m: re.Match) -> str:
    return f"{_MONTHS[int(m.group(2)) - 1]} {int(m.group(3))}"


def speakable(text: str) -> str:
    """Turn display text into something that sounds right when read aloud."""
    t = _URL.sub("the link", text)
    t = _ISO_DATE.sub(_say_date, t)
    t = _TIME.sub(_say_time, t)
    t = re.sub(r"`{1,3}[^`]*`{1,3}", " ", t)                 # inline code is not for ears
    t = re.sub(r"[*_#>|~]+", " ", t)                          # markdown emphasis/headings
    t = re.sub(r"^\s*[-•]\s+", "", t, flags=re.M)             # list bullets
    t = _EMOJI.sub("", t)
    for rx, sub in _REPLACE:
        t = rx.sub(sub, t)
    t = " ".join(t.split())
    return re.sub(r"\s+([,.!?;:])", r"\1", t)


# ------------------------------------------------------------ sentence chunker
_ABBREV = {"mr", "mrs", "ms", "dr", "st", "prof", "sr", "jr", "vs", "etc", "no", "approx", "min", "max"}
_BOUNDARY = re.compile(r"([.!?]+)(?=\s)|\n+")


class SentenceChunker:
    """Feed streamed tokens, get back complete sentences as soon as they exist."""

    def __init__(self, long_clause: int = 140):
        self.buf = ""
        self.long_clause = long_clause

    def feed(self, token: str) -> list[str]:
        self.buf += token
        out: list[str] = []
        while True:
            cut = self._find_cut()
            if cut is None:
                break
            sentence, self.buf = self.buf[:cut].strip(), self.buf[cut:].lstrip()
            if sentence:
                out.append(sentence)
        return out

    def _find_cut(self) -> Optional[int]:
        for m in _BOUNDARY.finditer(self.buf):
            if m.group(0).startswith("\n"):
                return m.end()
            before = self.buf[:m.start()]
            word = re.findall(r"(\w+)$", before)
            if m.group(1) == "." and word and (word[0].lower() in _ABBREV or len(word[0]) == 1):
                continue                                          # "Dr." or an initial, not an ending
            return m.end()
        if len(self.buf) > self.long_clause:                     # run-on: break at the last comma
            i = self.buf.rfind(", ", 0, self.long_clause)
            if i > 40:
                return i + 1
        return None

    def flush(self) -> str:
        rest, self.buf = self.buf.strip(), ""
        return rest


# ------------------------------------------------------------ audio encoding
def to_wav_bytes(samples, rate: int = SAMPLE_RATE) -> bytes:
    """float32 [-1, 1] mono samples -> 16-bit PCM WAV bytes (standard library only)."""
    a = np.asarray(samples, dtype=np.float32).reshape(-1)
    pcm = (np.clip(a, -1.0, 1.0) * 32767.0).astype("<i2")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm.tobytes())
    return buf.getvalue()


# ------------------------------------------------------------ Kokoro engine
class KokoroTTS:
    """Thread-safe Kokoro wrapper. Loads lazily; synth() returns WAV bytes or None."""

    def __init__(self, voice: str = "af_heart", lang_code: str = "a", speed: float = 1.0,
                 device: str = "cpu", repo_id: str = "hexgrad/Kokoro-82M"):
        self.voice, self.lang_code, self.speed = voice, lang_code, speed
        self.device, self.repo_id = device, repo_id
        self._pipe = None
        self._lock = threading.Lock()
        self._cache: dict[str, bytes] = {}

    def _load(self):
        if self._pipe is not None:
            return self._pipe
        from kokoro import KPipeline
        try:
            self._pipe = KPipeline(lang_code=self.lang_code, repo_id=self.repo_id, device=self.device)
        except TypeError:                         # older kokoro without repo_id/device kwargs
            self._pipe = KPipeline(lang_code=self.lang_code)
        logger.info(f"ECHO: Kokoro loaded (voice {self.voice}, device {self.device})")
        return self._pipe

    def warm(self) -> bool:
        try:
            return self.phrase("Yes, sir?") is not None
        except Exception as e:
            logger.error(f"ECHO: Kokoro warm-up failed: {e}")
            return False

    def synth(self, text: str) -> Optional[bytes]:
        text = speakable(text)
        if not text:
            return None
        with self._lock:
            pipe = self._load()
            parts = []
            for r in pipe(text, voice=self.voice, speed=self.speed):
                audio = getattr(r, "audio", None)
                if audio is None and isinstance(r, tuple):
                    audio = r[2]
                if audio is None:
                    continue
                if hasattr(audio, "detach"):
                    audio = audio.detach().cpu().numpy()
                parts.append(np.asarray(audio, dtype=np.float32).reshape(-1))
        return to_wav_bytes(np.concatenate(parts)) if parts else None

    def phrase(self, text: str) -> Optional[bytes]:
        """Cached synthesis for fixed lines like the wake reply."""
        if text not in self._cache:
            wav = self.synth(text)
            if wav:
                self._cache[text] = wav
        return self._cache.get(text)


_tts: Optional[KokoroTTS] = None
_tts_checked = False


def get_tts() -> Optional[KokoroTTS]:
    """The shared Kokoro engine, or None when Kokoro isn't installed or disabled."""
    global _tts, _tts_checked
    if _tts_checked:
        return _tts
    _tts_checked = True
    from core.settings import get_settings
    cfg = get_settings().get("voice", {}).get("tts", {})
    if cfg.get("engine", "kokoro") != "kokoro":
        logger.info("ECHO: server TTS disabled in settings; browser voice will be used")
        return None
    try:
        import kokoro  # noqa: F401
    except Exception as e:
        logger.warning(f"ECHO: Kokoro not available ({e}); browser voice will be used")
        return None
    _tts = KokoroTTS(voice=cfg.get("voice_eva", "af_heart"), lang_code=cfg.get("lang_code", "a"),
                     speed=float(cfg.get("speed", 1.0)), device=cfg.get("device", "cpu"))
    return _tts


# ------------------------------------------------------------ per-turn speaker
Send = Callable[[dict], Awaitable[None]]


class TurnSpeaker:
    """Speaks one turn: queue sentences, synthesize in order, send audio chunks."""

    def __init__(self, tts, send: Send, turn: int):
        self.tts, self.send, self.turn = tts, send, turn
        self.queue: asyncio.Queue[Optional[str]] = asyncio.Queue()
        self.sent = 0
        self.task = asyncio.create_task(self._worker())

    async def say(self, text: str) -> None:
        if text and text.strip():
            await self.queue.put(text)

    async def _worker(self) -> None:
        while True:
            text = await self.queue.get()
            if text is None:
                return
            try:
                wav = await asyncio.to_thread(self.tts.synth, text)
            except Exception as e:
                logger.error(f"ECHO: synthesis failed for {text[:40]!r}: {e}")
                wav = None
            if wav:
                await self.send({"type": "audio", "turn": self.turn, "seq": self.sent,
                                 "text": text, "audio": base64.b64encode(wav).decode()})
                self.sent += 1

    async def finish(self) -> None:
        """Wait for every queued sentence, then tell the browser how many chunks to expect."""
        await self.queue.put(None)
        await self.task
        await self.send({"type": "audio_end", "turn": self.turn, "count": self.sent})

    def cancel(self) -> None:
        self.task.cancel()
