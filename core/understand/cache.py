"""Understanding cache (Nov 23): the same sentence in the same situation gets the same decision, for free.

Testing her means saying the same things again and again (docs/VOICE_TESTS.md). The cloud decision for a
sentence is kept, keyed on:
- the sentence itself, without "Hey Eva", punctuation or case;
- for follow-ups ("and tomorrow?", "turn it up", "do that again") also the two turns before it, because those
  words mean something different after each answer;
- the tools she has right now (names and descriptions): a new or changed skill starts fresh.

Kept 7 days (understanding.cache.days). Decisions that carry personal text (a message to Mom, a note, a reminder's
words) stay in memory only and are never written to data/understand_cache.json. A decision with a date written
out ("2026-11-24") isn't reused: "tomorrow" as a date is only true today. "Local only" never reads the cache,
because nothing cloud-made should answer then. Switch it off with understanding.cache.enabled: false.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from pathlib import Path
from typing import Optional

from loguru import logger

PATH = Path("data/understand_cache.json")
DEFAULTS = {"enabled": True, "days": 7, "max": 2000}
_WAKE = re.compile(r"^\s*(?:hey\s+|ok(?:ay)?\s+)?(?:eva|e\.v\.a\.?)\b[\s,.!:-]*", re.I)
# words that point back at what was just said: then the turns before decide what the sentence means
_FOLLOW = re.compile(r"\b(?:it|its|that|this|those|these|them|they|there|then|again|also|too|same|instead|another|"
                     r"one more|the other|what about|how about|and|or|more|less|back|yes|no|ok|okay|sure|why|"
                     r"he|she|him|her|his)\b", re.I)
_PRIVATE_ARGS = {"text", "body", "message", "content", "note", "request", "question", "items"}
_DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")


def cfg() -> dict:
    try:
        from core.understand.brains import cfg as ucfg
        return {**DEFAULTS, **(ucfg().get("cache") or {})}
    except Exception:
        return dict(DEFAULTS)


def norm(text: str) -> str:
    t = _WAKE.sub("", text or "").lower()
    t = re.sub(r"[^\w\s']", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def is_follow_up(text: str) -> bool:
    t = norm(text)
    return len(t.split()) <= 2 or bool(_FOLLOW.search(t))


def tools_print(schemas: list[dict]) -> str:
    sig = sorted((t["function"]["name"], t["function"].get("description", ""),
                  json.dumps(t["function"].get("parameters") or {}, sort_keys=True)) for t in schemas)
    return hashlib.sha1(json.dumps(sig).encode("utf-8")).hexdigest()[:16]


def key(messages: list[dict], schemas: list[dict]) -> str:
    msgs = [m for m in messages if m.get("role") in ("user", "assistant") and m.get("content")]
    latest = next((m["content"] for m in reversed(msgs) if m["role"] == "user"), "")
    before = ""
    if is_follow_up(latest):
        rest = msgs[:-1] if msgs and msgs[-1]["role"] == "user" else msgs
        before = " | ".join(f"{m['role'][0]}:{norm(m['content'])[:200]}" for m in rest[-2:])
    raw = json.dumps([norm(latest), before, tools_print(schemas)])
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _private(decision: dict) -> bool:
    return any(k in _PRIVATE_ARGS and v not in (None, "", [], {}) for k, v in decision.items())


class Cache:
    def __init__(self, path: Path = PATH):
        self.path = Path(path)
        self.items: dict[str, dict] = {}
        self._lock = threading.Lock()
        try:
            self.items = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {}
        except (OSError, ValueError):
            logger.warning("UNDERSTAND cache: unreadable, starting fresh")

    def get(self, k: str) -> Optional[dict]:
        c = cfg()
        with self._lock:
            hit = self.items.get(k)
            if not hit:
                return None
            if time.time() - hit["t"] > float(c["days"]) * 86400:
                self.items.pop(k, None)
                return None
            hit["n"] = hit.get("n", 0) + 1
            return dict(hit["d"])

    def put(self, k: str, decision: dict) -> None:
        d = {kk: v for kk, v in decision.items() if not kk.startswith("_") or kk in ("_trusted", "_by", "_args_done")}
        if any(isinstance(v, str) and _DATE.search(v) for v in d.values()):
            return                                       # a written-out date is only true today
        c = cfg()
        with self._lock:
            self.items[k] = {"d": d, "t": time.time(), "n": 0, "mem": _private(d)}
            if len(self.items) > int(c["max"]):
                for old in sorted(self.items, key=lambda x: self.items[x]["t"])[:len(self.items) - int(c["max"])]:
                    self.items.pop(old, None)
            keep = {kk: v for kk, v in self.items.items() if not v.get("mem")}
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(keep), encoding="utf-8")
        except OSError as e:
            logger.warning(f"UNDERSTAND cache: couldn't save ({e})")


_cache: Optional[Cache] = None


def get_cache() -> Cache:
    global _cache
    if _cache is None:
        _cache = Cache(PATH)
    return _cache


def lookup(messages: list[dict], schemas: list[dict]) -> Optional[dict]:
    if not cfg().get("enabled", True):
        return None
    hit = get_cache().get(key(messages, schemas))
    if hit is None:
        return None
    was = hit.get("_by", "cloud")
    hit.update(_by="cache", _was=was, _conf=1.0, _ms=0.0)
    logger.info(f"UNDERSTAND cache -> {hit.get('tool')} (reused a {was} decision, free)")
    return hit


def remember(messages: list[dict], schemas: list[dict], decision: dict) -> None:
    if cfg().get("enabled", True) and decision.get("tool"):
        get_cache().put(key(messages, schemas), decision)
