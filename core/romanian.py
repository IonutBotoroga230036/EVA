"""Romanian (v0.3.2, milestone 9d): recognising it, translating into it, and saying it in a Romanian voice.

- is_romanian(text): diacritics or enough Romanian words. Used for what you say and for parts of what she says.
- split_languages(text): which parts of an answer to read in Romanian. A quoted message („Ajung la 7.") or a
  Romanian sentence goes to the Romanian voice; the rest stays in E.V.A.'s English voice.
- translate(text): into natural Romanian. Claude when understanding may use the cloud (good Romanian, about
  EUR 0.0001), otherwise the local model (weaker). Replies to family use the informal "tu".
- piper_audio(text): the Romanian voice, Piper "ro_RO-mihai-medium" (the only Romanian Piper voice, male; local,
  free). One-time setup: python -m piper.download_voices ro_RO-mihai-medium --download-dir data/voices
"""

from __future__ import annotations

import re
import threading
from pathlib import Path
from typing import Optional

import numpy as np
from loguru import logger

RO_CHARS = set("ăâîșşțţĂÂÎȘŞȚŢ")
RO_WORDS = {"și", "si", "este", "nu", "da", "sunt", "ești", "esti", "pentru", "acasă", "acasa", "bine",
            "mulțumesc", "multumesc", "ce", "cum", "unde", "când", "cand", "mama", "mami", "te", "iubesc", "ajung",
            "vin", "mâine", "maine", "azi", "acum", "sau", "la", "pe", "de", "cu", "în", "eu", "tu", "el",
            "ea", "noi", "voi", "ei", "să", "sa", "mă", "îți", "iti", "avem", "foarte",
            "salut", "bună", "buna", "ziua", "seara", "dimineața", "dimineata", "vreau", "poți", "poti", "spune",
            "spune-i", "zi", "ora", "cât", "cat", "care", "asta", "aici", "mai", "doar", "tot", "dacă", "daca",
            "trebuie", "pot", "lui", "mamei", "îi", "ii", "i-am", "mi-a", "ne", "vedem", "pa", "faci", "fac", "vii",
            "tine", "mine", "merg", "vorbim", "sun", "sună", "suna", "ajuns", "plecat", "ceva", "nimic", "oare",
            "spus", "zis", "mesaj", "răspunde", "raspunde", "spune-mi", "ți", "ti", "mulțumim", "noapte", "bună"}
EN_WORDS = {"the", "and", "is", "you", "to", "of", "it", "that", "what", "for", "on", "with", "are", "this", "i",
            "my", "your", "sir", "be", "at", "can", "will", "have", "do", "please", "tell", "she", "he", "we",
            "they", "me", "her", "him", "home", "time", "message", "reply", "good", "morning", "evening", "night",
            "hello", "hi", "yes", "no", "from", "about", "there", "here", "today", "tomorrow", "done", "set", "an",
            "a", "in", "by", "says", "said", "shall", "go", "ahead", "just", "confirm", "new", "whatsapp",
            "instagram", "has", "was", "not", "all", "would", "should", "could", "it's", "i'm", "you're"}
_WORD = re.compile(r"[A-Za-zĂÂÎȘŞȚŢăâîșşțţ'-]+")
# a quote may be cut by the sentence chunker ("„Ajung la 7." then "”. Shall I?"): an open „ runs to the end
_QUOTE = re.compile(r"„([^”\"]+)(?:[”\"]|$)|“([^”]+)”|«([^»]+)»")


def is_romanian(text: str) -> bool:
    if not text:
        return False
    words = [w.lower() for w in _WORD.findall(text)]
    marked = sum(any(c in RO_CHARS for c in w) for w in words)       # "acasă", but also a name like "Ionuț"
    ro = sum(w in RO_WORDS or any(c in RO_CHARS for c in w) for w in words)
    en = sum(w in EN_WORDS for w in words)
    if marked and en == 0:
        return True
    return ro >= 2 and ro > en * 2


def split_languages(text: str) -> list[tuple[str, str]]:
    """[('en', 'Reply to Mom on WhatsApp:'), ('ro', 'Ajung acasă la 7.'), ('en', 'Shall I go ahead?')]."""
    out: list[tuple[str, str]] = []

    def add(lang: str, seg: str):
        seg = seg.strip()
        if not seg:
            return
        if out and out[-1][0] == lang:
            out[-1] = (lang, out[-1][1] + " " + seg)
        else:
            out.append((lang, seg))

    def sentences(chunk: str):
        for s in re.split(r"(?<=[.!?])\s+", chunk):
            add("ro" if is_romanian(s) else "en", s)

    text = re.sub(r"^\s*[”\"]+[\s.,!?]*", "", text or "")          # the tail of a quote the chunker cut
    pos = 0
    for m in _QUOTE.finditer(text):
        sentences(text[pos:m.start()])
        q = next(g for g in m.groups() if g)
        add("ro" if is_romanian(q) else "en", q)
        pos = m.end()
    sentences(text[pos:])
    return out


# ------------------------------------------------------------ the Romanian voice (Piper)
_voice = None
_voice_lock = threading.Lock()
_voice_missing_logged = False


def _cfg() -> dict:
    from core.settings import get_settings
    return {"voice": "ro_RO-mihai-medium", "dir": "data/voices", **((get_settings().get("voice") or {}).get("romanian")
                                                                    or {})}


def _load_voice():
    global _voice, _voice_missing_logged
    if _voice is not None:
        return _voice
    c = _cfg()
    model = Path(c["dir"]) / f"{c['voice']}.onnx"
    if not model.exists():
        if not _voice_missing_logged:
            logger.warning(f"ROMANIAN: no voice at {model}; Romanian is read in the English voice until you run: "
                           f"python -m piper.download_voices {c['voice']} --download-dir {c['dir']}")
            _voice_missing_logged = True
        return None
    try:
        from piper import PiperVoice
        _voice = PiperVoice.load(str(model))
        logger.info(f"ROMANIAN: voice {c['voice']} loaded")
    except Exception as e:
        logger.warning(f"ROMANIAN: the voice couldn't load ({e})")
        _voice = None
    return _voice


def piper_audio(text: str, rate: int) -> Optional[np.ndarray]:
    """float32 mono at `rate` (E.V.A.'s voice rate, so the two voices join in one clip), or None."""
    with _voice_lock:
        v = _load_voice()
        if v is None or not text.strip():
            return None
        try:
            chunks = list(v.synthesize(text))
        except Exception as e:
            logger.warning(f"ROMANIAN: synthesis failed ({e})")
            return None
    if not chunks:
        return None
    audio = np.concatenate([np.asarray(c.audio_float_array, dtype=np.float32).reshape(-1) for c in chunks])
    src = int(chunks[0].sample_rate)
    if src != rate and len(audio):
        n = int(round(len(audio) * rate / src))
        audio = np.interp(np.linspace(0, len(audio) - 1, n), np.arange(len(audio)), audio).astype(np.float32)
    return audio


# ------------------------------------------------------------ translation
STYLES = {
    "family": ("a message from a son to his family, translated exactly: informal 'tu', natural, adding nothing "
               "(no greetings, no extra sentences); if it isn't a message, return it unchanged"),
    "assistant": "E.V.A., a polite AI assistant, speaking to Ionut (call him 'domnule'); keep times, names and numbers",
}


def translate(text: str, to: str = "ro", style: str = "assistant") -> Optional[str]:
    """Natural Romanian for `text`, or None when no model could do it. Already Romanian: returned as is."""
    if not text or not text.strip():
        return text
    if to == "ro" and is_romanian(text):
        return text
    prompt = (f"Translate into natural Romanian, as {STYLES.get(style, STYLES['assistant'])}. Use correct "
              f"diacritics (ă, â, î, ș, ț). Reply with the translation only.")
    got = None
    try:
        from core.understand import brains
        if brains.cloud_allowed():
            got = _claude_translate(prompt, text)
    except Exception as e:
        logger.info(f"ROMANIAN: Claude didn't translate ({str(e)[:80]}); trying the local model")
    if not got:
        try:
            got = _local_translate(prompt, text)
        except Exception as e:
            logger.warning(f"ROMANIAN: no translation ({e})")
    return got or None


def _clean(text: str) -> str:
    text = re.sub(r"<think>.*?</think>", "", text or "", flags=re.S)       # a thinking model's notes
    return text.strip().strip('"„”“').strip()


def _claude_translate(prompt: str, text: str) -> Optional[str]:
    from core.understand import brains
    out = brains.get_claude_light().message(system=prompt, messages=[{"role": "user", "content": text}],
                                            max_tokens=400, purpose="translate")
    return _clean(out.get("text") or "") or None


def _local_translate(prompt: str, text: str) -> Optional[str]:
    import httpx
    from core.settings import get_settings
    local = get_settings()["inference"]["local"]
    r = httpx.post(f"{local['base_url']}/api/chat", timeout=60, json={
        "model": local.get("answer_model") or local.get("decision_model"), "stream": False,
        "options": {"temperature": 0.2},
        "messages": [{"role": "system", "content": prompt}, {"role": "user", "content": text}]})
    return _clean(r.json().get("message", {}).get("content") or "") or None
