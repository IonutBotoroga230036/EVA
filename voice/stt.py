"""
ECHO v0.2.5: local speech to text with faster-whisper.

    WhisperSTT.transcribe(float32 16 kHz mono) -> cleaned text ("" when it was noise)

- Device "auto": tries the GPU (CUDA, int8_float16), falls back to the CPU (int8) and
  says why in the log. A missing cuBLAS/cuDNN on Windows is the usual reason.
- On Windows, NVIDIA's pip wheels (nvidia-cublas-cu12, nvidia-cudnn-cu12) put their DLLs
  in site-packages/nvidia/*/bin, where CTranslate2 can't see them. add_nvidia_dll_dirs()
  registers those folders before the model loads, so the pip route just works.
- Hint words: the terms in the Vocabulary sections of EVA.md / EVA.local.md are passed as
  Whisper hotwords, so "Radboud" and "Nijmegen" are heard right in the first place. The
  wake word "Eva" is always a hint. Vocabulary correction still runs afterwards.
- The audio already went through Silero VAD, so Whisper's own VAD stays off, and
  condition_on_previous_text is off: each utterance stands alone, which avoids the
  classic runaway repetition.
- clean_transcript() drops Whisper's well-known hallucinations on near-silence
  ("Thanks for watching!", a lone "you") and segments Whisper itself marks as no speech.
"""

from __future__ import annotations

import importlib.util
import os
import re
import sys
import threading
from pathlib import Path
from typing import Optional

import numpy as np
from loguru import logger

SAMPLE_RATE = 16000

# Whole-transcript hallucinations Whisper produces on silence or noise. "Thank you" is NOT here:
# people really say it to her.
_HALLUCINATIONS = {
    "you", "thanks for watching", "thank you for watching", "thanks for watching and see you next time",
    "please subscribe", "subtitles by the amaraorg community", "subtitles by amaraorg", "bye", "so",
    "hmm", "mm", "uh", "um", "ah", "oh", "the end", "music", "applause", "silence",
}
_WORDISH = re.compile(r"[a-z0-9']+")


def _norm(text: str) -> str:
    return " ".join(_WORDISH.findall(text.lower().replace(".", "")))


def clean_transcript(segments: list[dict]) -> str:
    """Join Whisper segments, skipping ones it marks as no speech, and drop pure hallucinations.

    Each segment dict has text, no_speech_prob, avg_logprob (as faster-whisper reports them).
    """
    kept = []
    for s in segments:
        text = (s.get("text") or "").strip()
        if not text:
            continue
        if s.get("no_speech_prob", 0.0) > 0.6 and s.get("avg_logprob", 0.0) < -1.0:
            continue                                            # Whisper's own "this was silence" rule
        kept.append(text)
    text = " ".join(kept).strip()
    text = re.sub(r"^[\s.,!?-]+", "", text)
    text = re.sub(r"\[[^\]]*\]|\([^)]*\)", " ", text)          # [Music], (applause)
    text = " ".join(text.split())
    if not _WORDISH.search(text.lower()):
        return ""
    if _norm(text) in _HALLUCINATIONS:
        return ""
    return text


def hint_words(vocab_text: str, extra: tuple[str, ...] = ("Eva",)) -> str:
    """Vocabulary terms (not their misheard aliases) as a Whisper hotword string."""
    from core.vocab import parse_vocabulary
    terms = [t for t, _ in parse_vocabulary(vocab_text or "")]
    seen, out = set(), []
    for t in (*extra, *terms):
        k = t.lower()
        if k not in seen:
            seen.add(k)
            out.append(t)
    return ", ".join(out)


def add_nvidia_dll_dirs() -> list[str]:
    """Windows only: make DLLs from the nvidia-* pip wheels visible to CTranslate2."""
    if sys.platform != "win32" or not hasattr(os, "add_dll_directory"):
        return []
    added = []
    for base in {Path(p) for p in sys.path if p and p.endswith("site-packages")}:
        for bin_dir in sorted((base / "nvidia").glob("*/bin")):
            try:
                os.add_dll_directory(str(bin_dir))
                os.environ["PATH"] = str(bin_dir) + os.pathsep + os.environ.get("PATH", "")
                added.append(str(bin_dir))
            except OSError:
                pass
    return added


def available() -> tuple[bool, str]:
    try:
        if importlib.util.find_spec("faster_whisper") is None:
            return False, "faster-whisper is not installed (pip install faster-whisper)"
    except Exception as e:
        return False, f"faster-whisper could not be inspected ({e})"
    return True, ""


class WhisperSTT:
    """Thread-safe faster-whisper wrapper. Loads lazily; one transcription at a time."""

    def __init__(self, model: str = "small", device: str = "auto", compute_type: str = "auto",
                 language: str = "en", beam_size: int = 5, vocab_source=None, model_factory=None):
        self.model_name, self.device, self.compute_type = model, device, compute_type
        self.language, self.beam_size = (language or None), int(beam_size)
        self._vocab_source = vocab_source                 # callable -> raw Vocabulary text
        self._factory = model_factory                     # tests inject a fake WhisperModel
        self._model = None
        self._lock = threading.Lock()
        self.active_device: Optional[str] = None
        self.load_error: Optional[str] = None

    # ---------------------------------------------------------------- loading
    def _candidates(self) -> list[tuple[str, str]]:
        dev = (self.device or "auto").lower()
        ct = (self.compute_type or "auto").lower()
        gpu = ("cuda", "int8_float16" if ct == "auto" else ct)
        cpu = ("cpu", "int8" if ct == "auto" else ct)
        if dev == "cuda":
            return [gpu, ("cpu", "int8")]                  # still fall back: a wrong DLL must not mute her
        if dev == "cpu":
            return [cpu]
        return [gpu, ("cpu", "int8")]

    def _load(self):
        if self._model is not None:
            return self._model
        factory = self._factory
        if factory is None:
            add_nvidia_dll_dirs()
            from faster_whisper import WhisperModel
            factory = WhisperModel
        errors = []
        for device, compute in self._candidates():
            try:
                model = factory(self.model_name, device=device, compute_type=compute)
                if device == "cuda":                       # CUDA DLLs load lazily: prove it works now
                    list(model.transcribe(np.zeros(SAMPLE_RATE // 2, dtype=np.float32), language=self.language,
                                          beam_size=1, without_timestamps=True)[0])
                self._model, self.active_device, self.load_error = model, f"{device} {compute}", None
                logger.info(f"ECHO: Whisper {self.model_name} loaded on {device} ({compute})")
                return model
            except Exception as e:
                errors.append(f"{device}: {e}")
                logger.warning(f"ECHO: Whisper on {device} ({compute}) failed: {e}")
        self.load_error = "; ".join(errors) or "unknown error"
        raise RuntimeError(f"Whisper could not load ({self.load_error})")

    def warm(self) -> bool:
        try:
            with self._lock:
                self._load()
            return True
        except Exception as e:
            logger.error(f"ECHO: Whisper warm-up failed: {e}")
            return False

    # ---------------------------------------------------------------- transcription
    def hotwords(self) -> Optional[str]:
        try:
            text = self._vocab_source() if self._vocab_source else ""
        except Exception:
            text = ""
        return hint_words(text) or None

    def transcribe(self, audio) -> str:
        """float32 [-1, 1] mono 16 kHz -> cleaned text. Raises only if Whisper can't load at all."""
        a = np.asarray(audio, dtype=np.float32).reshape(-1)
        if a.size < SAMPLE_RATE // 10:
            return ""
        with self._lock:
            model = self._load()
            segments, _info = model.transcribe(
                a, language=self.language, beam_size=self.beam_size, vad_filter=False,
                condition_on_previous_text=False, without_timestamps=True, hotwords=self.hotwords())
            segs = [{"text": s.text, "no_speech_prob": getattr(s, "no_speech_prob", 0.0),
                     "avg_logprob": getattr(s, "avg_logprob", 0.0)} for s in segments]
        return clean_transcript(segs)

    def describe(self) -> str:
        where = self.active_device or (self.device or "auto")
        return f"Whisper {self.model_name} ({where})"


_stt: Optional[WhisperSTT] = None
_stt_lock = threading.Lock()


def get_stt() -> Optional[WhisperSTT]:
    """The shared Whisper engine (one model for every connection), or None if not installed."""
    global _stt
    with _stt_lock:
        if _stt is not None:
            return _stt
        ok, reason = available()
        if not ok:
            logger.warning(f"ECHO: {reason}; browser speech recognition will be used")
            return None
        from core.prompt_builder import vocabulary_text
        from core.settings import get_settings
        cfg = get_settings().get("voice", {}).get("stt", {}) or {}
        _stt = WhisperSTT(model=str(cfg.get("model", "small")), device=str(cfg.get("device", "auto")),
                          compute_type=str(cfg.get("compute_type", "auto")), language=cfg.get("language", "en"),
                          beam_size=int(cfg.get("beam_size", 5)), vocab_source=vocabulary_text)
        return _stt
