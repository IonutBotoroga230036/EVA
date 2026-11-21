"""
Actions later (v0.3 milestone 10b): "turn the lights off in 5 minutes, stop the music in 10".

The command is understood NOW (so you hear right away what she will do), the tool runs THEN, and afterwards
she tells you the tool's own result, so she never claims something that didn't happen. Anything that needs
your "yes" is not scheduled: you'd have to be there to say it, so she offers a reminder instead.

Kept in data/later.json, so a restart doesn't lose what's planned; anything that fell due while E.V.A. was off
runs at the next start, if it's less than 30 minutes late (lights off an hour late helps nobody).
"""

from __future__ import annotations

import asyncio
import json
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Awaitable, Callable, Optional

from loguru import logger

STORE = Path("data/later.json")
STALE_S = 30 * 60

_UNIT = {"second": 1, "sec": 1, "s": 1, "minute": 60, "min": 60, "m": 60, "hour": 3600, "hr": 3600, "h": 3600}
_ONES = {w: i for i, w in enumerate("zero one two three four five six seven eight nine ten eleven twelve thirteen "
                                    "fourteen fifteen sixteen seventeen eighteen nineteen".split())}
_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90}
_FUZZY = {"a": 1, "an": 1, "a couple of": 2, "a couple": 2, "couple of": 2, "a few": 3, "few": 3}
_WORD = "|".join(sorted([*_ONES, *_TENS], key=len, reverse=True))
_NUM = (rf"\d+(?:[.,]\d+)?|(?:{'|'.join(_TENS)})(?:[\s-](?:one|two|three|four|five|six|seven|eight|nine))?|{_WORD}|"
        r"a couple(?: of)?|couple of|a few|few|an?")
_UNITS = r"seconds?|secs?|minutes?|mins?|hours?|hrs?"
# 'about', 'like', 'roughly' and friends sit between 'in' and the number in how people actually talk
# (Nov 20: "stop the music in about 20 minutes" stopped it right away because 'about' wasn't expected here)
_HEDGE = r"(?:(?:about|around|roughly|approximately|approx\.?|like|maybe|say|just|some|another|exactly|precisely|only)\s+){0,2}"
_DELAY = re.compile(
    rf"[\s,]*\b(?:(?:in|after)\s+{_HEDGE}(?P<half>half an?\s+(?P<hunit>hour|minute))|"
    rf"(?:(?:in|after)\s+{_HEDGE})?(?P<num>{_NUM})\s+(?P<unit>{_UNITS})"
    rf"(?P<more>\s+and\s+a\s+half|\s+and\s+(?P<num2>{_NUM})\s+(?P<unit2>{_UNITS}))?)"
    rf"(?:'?s?\s+time)?(?P<fromnow>\s+from\s+now)?\b[\s,.!?]*", re.I)
_OWN_TIME = re.compile(r"\b(remind|reminder|timer|alarm|wake me|schedule|calendar|meeting|appointment)\b", re.I)


def _number(word: str) -> Optional[float]:
    w = word.lower().strip()
    if w in _FUZZY:
        return float(_FUZZY[w])
    if w in _ONES:
        return float(_ONES[w])
    parts = re.split(r"[\s-]+", w)
    if parts[0] in _TENS:
        return float(_TENS[parts[0]] + (_ONES.get(parts[1], 0) if len(parts) > 1 else 0))
    try:
        return float(w.replace(",", "."))
    except ValueError:
        return None


def _unit(word: str) -> float:
    u = word.lower().rstrip("s")
    return _UNIT.get(u, _UNIT.get(u[:3], _UNIT.get(u[:1], 60)))


def split_delay(text: str) -> tuple[str, Optional[float]]:
    """'turn the lights off in 5 minutes' -> ('turn the lights off', 300). Also 'in about 20 minutes',
    'in twenty-five minutes', 'in an hour and a half', '10 minutes from now'. Reminders, timers and alarms keep
    their own time words, so they are never split here."""
    if not text or _OWN_TIME.search(text):
        return text, None
    for m in _DELAY.finditer(text):
        lead = m.group(0).lower()
        if not (re.search(r"\b(in|after)\b", lead) or m.group("fromnow") or re.search(r"\btime\b", lead)):
            continue                      # a bare '20 minutes' ('play 20 minutes of jazz') is not a delay
        if m.group("half"):
            secs = 0.5 * _unit(m.group("hunit"))
        else:
            amount = _number(m.group("num"))
            if amount is None:
                continue
            secs = amount * _unit(m.group("unit"))
            if m.group("more"):
                if m.group("num2"):
                    secs += (_number(m.group("num2")) or 0) * _unit(m.group("unit2"))
                else:
                    secs += 0.5 * _unit(m.group("unit"))
        clean = (text[:m.start()] + " " + text[m.end():]).strip(" ,.?!")
        clean = re.sub(r"\s{2,}", " ", clean)
        if clean and 0 < secs <= 24 * 3600:
            return clean, secs
    return text, None


_POLITE = re.compile(r"^(?:(?:hey\s+)?eva[,.!]?\s+)?(?:(?:can|could|would|will)\s+you\s+(?:please\s+)?|please\s+)?", re.I)


def tidy(command: str) -> str:
    """'Can you stop the music' -> 'stop the music', for 'Okay, sir: stop the music in 20 minutes'."""
    c = _POLITE.sub("", command.strip()).strip(" ,.?!")
    c = re.sub(r"[\s,]+please$", "", c, flags=re.I)
    return (c[:1].lower() + c[1:]) if c and not c[:2].isupper() else c


# "No, not now, I said in 20 minutes": everything left once the delay is taken out is just the correction
_CORRECTION = re.compile(r"^(?:(?:no|nope|wait|stop|sorry|hey|eva|not now|not yet|not right now|i said|i meant|"
                         r"i told you|i asked|i asked for|i wanted|i want it|do it|that|it|you|like|only|later|"
                         r"please|but|and)\W*)+$", re.I)


def is_correction(rest: str) -> bool:
    """True when the words around a delay are only 'no / not now / I said': it re-times the previous command."""
    r = (rest or "").strip(" ,.?!")
    return bool(r) and bool(_CORRECTION.match(r)) and bool(re.search(r"\b(no|not|said|meant|told|wait)\b", r, re.I))


def human(secs: float) -> str:
    if secs < 60:
        return f"{int(secs)} seconds"
    if secs < 3600:
        m = round(secs / 60)
        return f"{m} minute{'s' if m != 1 else ''}"
    h = secs / 3600
    return f"{h:g} hour{'s' if h != 1 else ''}"


class Later:
    def __init__(self):
        self._lock = threading.Lock()
        self.items: list[dict] = self._load()
        self.execute: Optional[Callable[[str, dict], Awaitable[dict]]] = None
        self.announce: Optional[Callable[[str], Awaitable[None]]] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._tasks: dict[str, asyncio.TimerHandle] = {}

    def _load(self) -> list[dict]:
        try:
            return [i for i in json.loads(STORE.read_text(encoding="utf-8")) if i.get("status") == "pending"]
        except Exception:
            return []

    def _save(self) -> None:
        with self._lock:
            STORE.parent.mkdir(parents=True, exist_ok=True)
            STORE.write_text(json.dumps(self.items, indent=1), encoding="utf-8")

    def start(self, loop: asyncio.AbstractEventLoop, execute, announce) -> None:
        self._loop, self.execute, self.announce = loop, execute, announce
        now = time.time()
        for item in list(self.items):
            if item["due"] < now - STALE_S:
                item["status"] = "skipped"
                logger.info(f"LATER: skipped (too late now): {item['text']}")
            else:
                self._arm(item)
        self.items = [i for i in self.items if i["status"] == "pending"]
        self._save()

    def _arm(self, item: dict) -> None:
        if not self._loop:
            return

        def schedule() -> None:
            delay = max(0.0, item["due"] - time.time())
            self._tasks[item["id"]] = self._loop.call_later(delay, lambda: asyncio.ensure_future(self._fire(item["id"])))
        self._loop.call_soon_threadsafe(schedule)

    def add(self, tool: str, args: dict, delay_s: float, text: str, session: str = "") -> dict:
        item = {"id": uuid.uuid4().hex[:8], "tool": tool, "args": args, "due": time.time() + delay_s,
                "text": text, "status": "pending", "session": session}
        with self._lock:
            self.items.append(item)
        self._save()
        self._arm(item)
        logger.info(f"LATER: in {human(delay_s)}: {text} ({tool})")
        return item

    def pending(self) -> list[dict]:
        return sorted([i for i in self.items if i["status"] == "pending"], key=lambda i: i["due"])

    def cancel(self, query: str = "") -> Optional[dict]:
        q = (query or "").lower()
        for item in self.pending():
            if not q or any(w in item["text"].lower() for w in re.findall(r"[a-z]{3,}", q)
                            if w not in {"cancel", "the", "scheduled", "later", "that", "don't"}):
                item["status"] = "cancelled"
                h = self._tasks.pop(item["id"], None)
                if h:
                    h.cancel()
                self.items = [i for i in self.items if i["status"] == "pending"]
                self._save()
                return item
        return None

    def cancel_id(self, item_id: str) -> Optional[dict]:
        item = next((i for i in self.items if i["id"] == item_id and i["status"] == "pending"), None)
        if item:
            item["status"] = "cancelled"
            h = self._tasks.pop(item_id, None)
            if h:
                h.cancel()
            self.items = [i for i in self.items if i["status"] == "pending"]
            self._save()
        return item

    async def _fire(self, item_id: str) -> None:
        item = next((i for i in self.items if i["id"] == item_id and i["status"] == "pending"), None)
        if not item or not self.execute:
            return
        item["status"] = "done"
        self.items = [i for i in self.items if i["status"] == "pending"]
        self._save()
        try:
            out = await self.execute(item["tool"], item["args"])
            say = out.get("say") or "Done, sir."
            ok = '"error"' not in (out.get("result") or "")
        except Exception as e:
            say, ok = f"I couldn't do it: {e}", False
        logger.info(f"LATER: {'done' if ok else 'failed'}: {item['text']} -> {say}")
        if self.announce:                         # in the window you asked from, if it's still open
            await self.announce(f"As planned: {say}" if ok else f"The planned \"{item['text']}\" didn't work, sir. {say}",
                                item.get("session", ""))


_later: Optional[Later] = None


def get_later() -> Later:
    global _later
    if _later is None:
        _later = Later()
    return _later
