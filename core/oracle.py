"""
ORACLE: E.V.A. speaks first. Reminders, meeting heads-ups, new-email alerts, and briefings.

A background loop inside the server. Each finding becomes a proactive turn that is spoken
(and shown) in every open E.V.A. window, exactly like a reply, so you can answer it
("snooze it", "read it").

    reminders   fire at their time; held during do-not-disturb and delivered after
    meetings    one heads-up `lead_minutes` before each event (needs Google)
    email       new unread mail since the last check (needs Google); the first check only
                sets a baseline, so she never reads out the whole backlog
    quiet hours no calendar or email chatter (settings oracle.quiet_hours, default 23:00-08:00);
                reminders still come, because you asked for them
If no window is open, due reminders wait and are spoken the moment you connect.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Awaitable, Callable, Optional

from loguru import logger

Deliver = Callable[[str, Optional[dict]], Awaitable[bool]]


class ReminderStore:
    def __init__(self, path: str | Path = "data/reminders.json"):
        self.path = Path(path)
        self._lock = threading.Lock()

    def _load(self) -> list[dict]:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return []

    def _save(self, items: list[dict]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(items, indent=2), encoding="utf-8")

    def add(self, text: str, due: datetime) -> dict:
        with self._lock:
            items = self._load()
            r = {"id": uuid.uuid4().hex[:8], "text": text.strip().rstrip("."), "due": due.timestamp(), "done": False}
            items.append(r)
            self._save(items)
            return r

    def open(self) -> list[dict]:
        return sorted((r for r in self._load() if not r["done"]), key=lambda r: r["due"])

    def take_due(self, now: float) -> list[dict]:
        with self._lock:
            items, due = self._load(), []
            for r in items:
                if not r["done"] and r["due"] <= now:
                    r["done"] = True
                    due.append(r)
            if due:
                self._save(items)
            return due

    def cancel(self, query: str) -> Optional[dict]:
        with self._lock:
            items = self._load()
            q = query.lower().strip()
            for r in sorted((r for r in items if not r["done"]), key=lambda r: r["due"]):
                if not q or q in r["text"].lower() or any(w in r["text"].lower() for w in q.split() if len(w) > 3):
                    r["done"] = True
                    self._save(items)
                    return r
            return None


_DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


def parse_routine(text: str) -> Optional[dict]:
    """'every weekday at 8 brief me' -> {"days": [0..4], "hour": 8, "minute": 0, "action": "briefing", ...}."""
    import re
    from core.timeparse import parse_clock
    t = (text or "").lower()
    if not re.search(r"\b(every|each|daily|weekdays?|weekends?)\b", t):
        return None
    if re.search(r"\bweekdays?\b|\bworkdays?\b|monday to friday", t):
        days = [0, 1, 2, 3, 4]
    elif re.search(r"\bweekends?\b", t):
        days = [5, 6]
    else:
        days = [i for i, d in enumerate(_DAYS) if re.search(rf"\b{d}s?\b", t)]
        if not days and re.search(r"\b(every ?day|daily|each day|every morning|every evening|every night)\b", t):
            days = list(range(7))
    if not days:
        return None
    clock = parse_clock(t)
    if not clock:
        return None
    action = ("briefing" if re.search(r"\b(brief|briefing|good morning|my day)\b", t) else
              "agenda" if re.search(r"\b(calendar|agenda|schedule)\b", t) else
              "email" if re.search(r"\b(e-?mails?|inbox|mail)\b", t) else "remind")
    what = re.sub(r"^.*?\b(?:remind me to|remind me about|tell me to|to)\s+", "", t) if action == "remind" else action
    what = re.sub(r"\s*\b(every|each)\b.*$", "", what).strip(" ,.") or t
    return {"days": days, "hour": clock[0], "minute": clock[1], "action": action, "text": what}


class RoutineStore:
    def __init__(self, path: str | Path = "data/routines.json"):
        self.path = Path(path)

    def all(self) -> list[dict]:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return []

    def _save(self, items: list[dict]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(items, indent=2), encoding="utf-8")

    def add(self, routine: dict) -> dict:
        items = self.all()
        r = {"id": uuid.uuid4().hex[:8], "last": "", **routine}
        items.append(r)
        self._save(items)
        return r

    def cancel(self, query: str) -> Optional[dict]:
        items = self.all()
        q = (query or "").lower()
        for r in items:
            if not q or q in r["text"].lower() or q in r["action"]:
                items.remove(r)
                self._save(items)
                return r
        return None

    def due(self, now: datetime, window_min: int = 30) -> list[dict]:
        """Routines whose time today has come (within a window) and that haven't run today."""
        items, fired = self.all(), []
        today = now.date().isoformat()
        for r in items:
            at = now.replace(hour=r["hour"], minute=r["minute"], second=0, microsecond=0)
            if now.weekday() in r["days"] and r.get("last") != today and at <= now < at + timedelta(minutes=window_min):
                r["last"] = today
                fired.append(r)
        if fired:
            self._save(items)
        return fired


def describe_routine(r: dict) -> str:
    days = r["days"]
    when = ("every day" if len(days) == 7 else "on weekdays" if days == [0, 1, 2, 3, 4] else
            "on weekends" if days == [5, 6] else "every " + " and ".join(_DAYS[d].capitalize() for d in days))
    what = {"briefing": "your briefing", "agenda": "your agenda", "email": "your important email"}.get(
        r["action"], f"a reminder to {r['text']}")
    return f"{what} {when} at {r['hour']:02d}:{r['minute']:02d}"


def when_words(due: datetime, now: Optional[datetime] = None) -> str:
    now = now or datetime.now()
    mins = round((due - now).total_seconds() / 60)
    if 0 < mins < 60:
        return f"in {mins} minute{'s' if mins != 1 else ''}"
    if due.date() == now.date():
        return f"at {due:%H:%M}"
    if due.date() == (now + timedelta(days=1)).date():
        return f"tomorrow at {due:%H:%M}"
    return f"on {due:%A} at {due:%H:%M}"


class Oracle:
    def __init__(self, store: Optional[ReminderStore] = None, deliver: Optional[Deliver] = None,
                 interval: float = 15.0, lead_minutes: int = 10, poll_seconds: int = 300,
                 quiet_hours: tuple[int, int] = (23, 8), google_getter: Optional[Callable] = None,
                 announce_email: bool = True, email_filter: str = "important"):
        self.store = store or ReminderStore()
        self.routines = RoutineStore(self.store.path.with_name("routines.json"))
        self.deliver = deliver
        self.interval, self.lead, self.poll = interval, lead_minutes, poll_seconds
        self.quiet = quiet_hours
        self.announce_email = announce_email
        self.email_filter = email_filter
        self.dnd_until = 0.0
        self.held: list[dict] = []           # reminders held by do-not-disturb
        self.undelivered: list[tuple[str, Optional[dict]]] = []   # nobody was connected
        self.last_reminder: Optional[dict] = None
        self._announced: set[str] = set()
        self._events: list[dict] = []
        self._seen_mail: Optional[set[str]] = None
        self._last_cal = self._last_mail = 0.0
        self._google_getter = google_getter
        self._stop = False

    # ------------------------------------------------------------ state
    def _google(self):
        if self._google_getter:
            return self._google_getter()
        from core.google_api import connected, get_google
        return get_google() if connected() else None

    def set_dnd(self, minutes: Optional[float]) -> None:
        self.dnd_until = time.time() + minutes * 60 if minutes else 0.0

    def in_dnd(self, now: Optional[float] = None) -> bool:
        return (now or time.time()) < self.dnd_until

    def in_quiet_hours(self, now: Optional[datetime] = None) -> bool:
        h = (now or datetime.now()).hour
        start, end = self.quiet
        return (h >= start or h < end) if start > end else (start <= h < end)

    async def _say(self, text: str, widget: Optional[dict] = None) -> None:
        logger.info(f"ORACLE: {text}")
        ok = False
        if self.deliver:
            try:
                ok = await self.deliver(text, widget)
            except Exception as e:
                logger.error(f"ORACLE: delivery failed: {e}")
        if not ok:
            self.undelivered.append((text, widget))

    async def on_client_connected(self) -> None:
        pending, self.undelivered = self.undelivered, []
        for text, widget in pending:
            await self._say(text, widget)

    # ------------------------------------------------------------ the loop
    async def run(self) -> None:
        logger.info("ORACLE: online")
        while not self._stop:
            try:
                await self.tick()
            except Exception as e:
                logger.error(f"ORACLE: tick failed: {e}")
            await asyncio.sleep(self.interval)

    def stop(self) -> None:
        self._stop = True

    async def tick(self, now: Optional[datetime] = None) -> None:
        now = now or datetime.now()
        ts = now.timestamp()
        dnd = self.in_dnd(ts)

        for r in self.store.take_due(ts):                       # reminders
            if dnd:
                self.held.append(r)
            else:
                self.last_reminder = r
                await self._say(f"Sir, a reminder: {r['text']}.", {"kind": "note", "title": "Reminder", "text": r["text"]})
        if not dnd and self.held:
            held, self.held = self.held, []
            self.last_reminder = held[-1]
            texts = "; ".join(r["text"] for r in held)
            await self._say(f"Welcome back, sir. While you were busy: {texts}.",
                            {"kind": "note", "title": "Held reminders", "text": texts})

        for r in ([] if dnd else self.routines.due(now)):          # scheduled routines
            await self._say(*await asyncio.to_thread(run_routine, r, self))

        if dnd or self.in_quiet_hours(now):
            return
        google = None
        if ts - self._last_cal >= self.poll or ts - self._last_mail >= self.poll:
            try:
                google = await asyncio.to_thread(self._google)
            except Exception:
                google = None
        if google and ts - self._last_cal >= self.poll:          # refresh upcoming events
            self._last_cal = ts
            try:
                self._events = await asyncio.to_thread(google.list_events, now, now + timedelta(hours=3))
            except Exception as e:
                logger.warning(f"ORACLE: calendar check failed: {e}")
        for e in self._events:                                   # meeting heads-up
            if e.get("all_day") or not e.get("start") or e["id"] in self._announced:
                continue
            mins = (e["start"] - now).total_seconds() / 60
            if 0 <= mins <= self.lead:
                self._announced.add(e["id"])
                n = max(1, round(mins))
                await self._say(f"Sir, {e['title']} starts in {n} minute{'s' if n != 1 else ''}.",
                                {"kind": "calendar", "title": "Coming up",
                                 "events": [{"time": e["start"].strftime("%H:%M"), "title": e["title"]}]})
        if google and self.announce_email and ts - self._last_mail >= self.poll:   # new mail
            self._last_mail = ts
            try:
                _, msgs = await asyncio.to_thread(google.list_messages, "is:unread in:inbox newer_than:1d", 10)
            except Exception as e:
                logger.warning(f"ORACLE: email check failed: {e}")
                msgs = None
            if msgs is not None:
                ids = {m["id"] for m in msgs}
                if self._seen_mail is None:
                    self._seen_mail = ids                        # first look: baseline only
                else:
                    new = [m for m in msgs if m["id"] not in self._seen_mail]
                    self._seen_mail |= ids
                    if new and self.email_filter == "important":
                        from core.triage import classify
                        new = [m for m in await asyncio.to_thread(classify, new, google) if m["important"]]
                    if new:
                        from core.scribe import who
                        m = new[0]
                        more = f", and {len(new) - 1} more" if len(new) > 1 else ""
                        await self._say(f"Sir, an important email from {who(m['from'])} about {m['subject']}{more}."
                                        if self.email_filter == "important" else
                                        f"Sir, a new email from {who(m['from'])} about {m['subject']}{more}.",
                                        {"kind": "email", "title": "New email",
                                         "emails": [{"from": who(x["from"]), "subject": x["subject"],
                                                     "snippet": x.get("snippet", "")[:120]} for x in new[:3]]})


def run_routine(r: dict, oracle: "Oracle") -> tuple[str, Optional[dict]]:
    if r["action"] == "briefing":
        return briefing(oracle=oracle)
    if r["action"] in ("agenda", "email"):
        try:
            from core import scribe, tempo
            from core.google_api import get_google
            out = tempo.agenda(get_google(), "today") if r["action"] == "agenda" else scribe.important(get_google())
            return out["say"], out.get("widget")
        except Exception as e:
            return f"Sir, I couldn't run your scheduled {r['action']} check: {e}.", None
    return f"Sir, your routine reminder: {r['text']}.", {"kind": "note", "title": "Routine", "text": r["text"]}


def briefing(now: Optional[datetime] = None, oracle: Optional[Oracle] = None) -> tuple[str, dict]:
    """Deterministic morning briefing: time, weather, calendar, email, reminders."""
    from core.tools_native import HOME_CITY, tool_get_datetime, tool_get_weather
    now = now or datetime.now()
    part = "morning" if now.hour < 12 else "afternoon" if now.hour < 18 else "evening"
    lines = [f"Good {part}, sir."]
    lines.append(tool_get_datetime()["say"].replace(", sir.", "."))
    lines.append(tool_get_weather(city=HOME_CITY)["say"].replace(", sir.", "."))
    try:
        from core import scribe, tempo
        from core.google_api import connected, get_google
        if connected():
            g = get_google()
            lines.append(tempo.agenda(g, "today", now=now)["say"].replace(", sir:", ":").replace(", sir.", "."))
            lines.append(scribe.important(g)["say"].replace(", sir:", ":").replace(", sir.", "."))
    except Exception as e:
        logger.warning(f"briefing: Google part skipped ({e})")
    o = oracle or get_oracle()
    today = [r for r in o.store.open() if datetime.fromtimestamp(r["due"]).date() == now.date()]
    if today:
        nxt = datetime.fromtimestamp(today[0]["due"])
        lines.append(f"You have {len(today)} reminder{'s' if len(today) != 1 else ''} today, "
                     f"the next at {nxt:%H:%M}: {today[0]['text']}.")
    text = " ".join(lines)
    return text, {"kind": "note", "title": f"Good {part}", "text": text[:300]}


_oracle: Optional[Oracle] = None


def get_oracle() -> Oracle:
    global _oracle
    if _oracle is None:
        from core.settings import get_settings
        c = get_settings().get("oracle", {})
        q = str(c.get("quiet_hours", "23-8")).replace(":00", "").split("-")
        _oracle = Oracle(lead_minutes=int(c.get("lead_minutes", 10)), poll_seconds=int(c.get("poll_seconds", 300)),
                         quiet_hours=(int(q[0]), int(q[1])),
                         announce_email=str(c.get("email_alerts", "important")) != "off",
                         email_filter=str(c.get("email_alerts", "important")))
    return _oracle
