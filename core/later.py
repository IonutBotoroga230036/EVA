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

_UNIT = {"s": 1, "sec": 1, "second": 60 / 60, "m": 60, "min": 60, "minute": 60, "h": 3600, "hr": 3600, "hour": 3600}
_AMOUNT = {"a": 1, "an": 1, "one": 1, "half an": 0.5, "half a": 0.5, "a couple of": 2, "a few": 3, "two": 2,
           "three": 3, "five": 5, "ten": 10, "fifteen": 15, "twenty": 20, "thirty": 30}
_DELAY = re.compile(r"[\s,]*\b(?:in|after)\s+(half an?|an?|one|a couple of|a few|two|three|five|ten|fifteen|twenty|"
                    r"thirty|\d+(?:[.,]\d+)?)\s+(seconds?|secs?|minutes?|mins?|hours?|hrs?)\b(?:\s+from now)?[\s,.!?]*",
                    re.I)
_OWN_TIME = re.compile(r"\b(remind|reminder|timer|alarm|wake me|schedule|calendar|meeting|appointment)\b", re.I)


def split_delay(text: str) -> tuple[str, Optional[float]]:
    """'turn the lights off in 5 minutes' -> ('turn the lights off', 300). Reminders, timers and alarms keep
    their own time words, so they are never split here."""
    if not text or _OWN_TIME.search(text):
        return text, None
    m = _DELAY.search(text)
    if not m:
        return text, None
    amount = _AMOUNT.get(m.group(1).lower())
    if amount is None:
        amount = float(m.group(1).replace(",", "."))
    unit = m.group(2).lower().rstrip("s")
    secs = amount * _UNIT.get(unit, _UNIT.get(unit[:3], 60))
    clean = (text[:m.start()] + " " + text[m.end():]).strip(" ,.")
    return (clean, secs) if clean and 0 < secs <= 24 * 3600 else (text, None)


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

    def add(self, tool: str, args: dict, delay_s: float, text: str) -> dict:
        item = {"id": uuid.uuid4().hex[:8], "tool": tool, "args": args, "due": time.time() + delay_s,
                "text": text, "status": "pending"}
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
        if self.announce:
            await self.announce(f"As planned: {say}" if ok else f"The planned \"{item['text']}\" didn't work, sir. {say}")


_later: Optional[Later] = None


def get_later() -> Later:
    global _later
    if _later is None:
        _later = Later()
    return _later
