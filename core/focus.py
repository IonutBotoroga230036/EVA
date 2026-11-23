"""Focus mode (Nov 23): during a calendar event she holds her announcements, except Mom and urgent ones, and tells
you what came in when it ends.

- An event counts when it has started, lasts focus.min_minutes or longer (default 30: your 5-minute reminder
  events like creatine never count), isn't all-day and isn't part of your daily routine calendar.
  focus.only_words (empty = every such event) limits it to events whose title has one of those words;
  focus.skip_words leaves some out.
- Held: WhatsApp/Instagram announcements (unless messages.vip or an urgent word: the same rules as "important")
  and new-email alerts. Still said: reminders you set, "leave for your train", the next meeting's heads-up.
- Afterwards: "Sir, while you were in Neural Networks: 2 messages from Andrei and an email from Tom."
  The messages stay unread, so "any messages?" reads them.
- "Focus mode off" / "focus mode on" (data/focus.json), "am I in focus mode?". Manual quiet stays "do not
  disturb" (everything held, Mom too).
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

from loguru import logger

PATH = Path("data/focus.json")
DEFAULTS = {"enabled": True, "min_minutes": 30, "only_words": [], "skip_words": [], "let_through": ["vip", "urgent"]}
ACTIVE: dict = {}                     # the event you're in: {"id", "title", "end"}
HELD: list = []                       # [(kind, who)] while in it


def cfg() -> dict:
    try:
        from core.settings import get_settings
        return {**DEFAULTS, **(get_settings().get("focus") or {})}
    except Exception:
        return dict(DEFAULTS)


def reset() -> None:
    ACTIVE.clear()
    HELD.clear()


def enabled() -> bool:
    try:
        st = json.loads(PATH.read_text(encoding="utf-8")) if PATH.exists() else {}
    except (OSError, ValueError):
        st = {}
    return bool(st["on"]) if "on" in st else bool(cfg().get("enabled", True))


def set_enabled(on: bool) -> None:
    PATH.parent.mkdir(parents=True, exist_ok=True)
    PATH.write_text(json.dumps({"on": bool(on)}), encoding="utf-8")


def counts(e: dict, c: Optional[dict] = None) -> bool:
    c = c or cfg()
    if e.get("all_day") or e.get("routine") or not e.get("start") or not e.get("end"):
        return False
    if (e["end"] - e["start"]).total_seconds() < float(c["min_minutes"]) * 60:
        return False
    title = (e.get("title") or "").lower()
    if any(w and w.lower() in title for w in (c.get("skip_words") or [])):
        return False
    only = [w for w in (c.get("only_words") or []) if w]
    return not only or any(w.lower() in title for w in only)


def current(events: list[dict], now: Optional[datetime] = None) -> Optional[dict]:
    """The event that puts you in focus right now, or None."""
    if not enabled():
        return None
    now = now or datetime.now()
    c = cfg()
    live = [e for e in events if counts(e, c) and e["start"] <= now < e["end"]]
    return max(live, key=lambda e: e["start"]) if live else None


def _events() -> list[dict]:
    try:
        from core.oracle import get_oracle
        return list(get_oracle()._events)
    except Exception:
        return []


def holds(m, events: Optional[list[dict]] = None, now: Optional[datetime] = None) -> bool:
    """Should this message's announcement wait? Mom and urgent ones never do."""
    e = current(_events() if events is None else events, now)
    if not e:
        return False
    return getattr(m, "why", "") not in (cfg().get("let_through") or [])


def note_held(kind: str, who: str) -> None:
    HELD.append((kind, who))


def held_summary() -> str:
    """'2 messages from Andrei and an email from Tom', or ''."""
    groups: dict = {}
    for kind, who in HELD:
        groups[(kind, who)] = groups.get((kind, who), 0) + 1
    parts = []
    for (kind, who), n in groups.items():
        parts.append((f"{n} {kind}s" if n > 1 else ("an " if kind[0] in "aeiou" else "a ") + kind) + f" from {who}")
    if len(parts) <= 1:
        return "".join(parts)
    return ", ".join(parts[:-1]) + " and " + parts[-1]


def tick(events: list[dict], now: Optional[datetime] = None) -> Optional[str]:
    """Called by ORACLE every few seconds: the line to say when an event you were focused in has ended."""
    now = now or datetime.now()
    e = current(events, now)
    if e and ACTIVE.get("id") == e.get("id"):
        return None
    line = None
    if ACTIVE and HELD:
        summary = held_summary()
        hint = " Say 'any messages' to hear them." if any(k == "message" for k, _ in HELD) else ""
        line = f"Sir, while you were in {ACTIVE['title']}: {summary}.{hint}"
    if ACTIVE:
        logger.info(f"FOCUS: {ACTIVE['title']} ended")
    reset()
    if e:
        ACTIVE.update(id=e.get("id"), title=e.get("title") or "your event", end=e["end"])
        logger.info(f"FOCUS: in {ACTIVE['title']} until {e['end']:%H:%M}; holding announcements except Mom/urgent")
    return line


def tool_focus_mode(on: Optional[bool] = None, **_):
    c = cfg()
    if on is not None:
        set_enabled(bool(on))
        if not on:
            return {"result": json.dumps({"focus": False}), "exact": True,
                    "say": "Focus mode is off, sir. I'll tell you about messages during your events too."}
    if not enabled():
        return {"result": json.dumps({"focus": False}), "exact": True,
                "say": "Focus mode is off, sir. Say 'focus mode on' to turn it back on."}
    from core import messages
    vip = ", ".join(messages.cfg().get("vip") or ["Mom"])
    say = (f"Focus mode is on, sir. During events of {int(c['min_minutes'])} minutes or longer I hold messages "
           f"and emails, except {vip} and urgent ones, and tell you what came in afterwards.")
    e = current(_events())
    if e:
        say += f" Right now you're in {e['title']} until {e['end']:%H:%M}."
    return {"result": json.dumps({"focus": True, "now": e["title"] if e else ""}), "exact": True, "say": say}


_SWITCH = re.compile(r"^(?:(?:hey )?eva[,.]?\s*)?(?:(?:turn|switch) (on|off) (?:the )?focus mode|focus mode (on|off)|"
                     r"(start|stop|end|enable|disable) focus mode)(?: please)?[.!]*$", re.I)


def from_words(text: str) -> Optional[bool]:
    m = _SWITCH.match((text or "").strip())
    if not m:
        return None
    w = (m.group(1) or m.group(2) or m.group(3)).lower()
    return w in ("on", "start", "enable")


SCHEMAS = [{"type": "function", "function": {
    "name": "focus_mode",
    "description": ("Focus mode: during calendar events E.V.A. holds message and email announcements except Mom or "
                    "urgent ones. on=true/false switches it ('focus mode off'); leave 'on' out to ask whether it's on "
                    "and whether the user is in focus now."),
    "parameters": {"type": "object", "properties": {"on": {"type": "boolean"}}}}}]
FUNCTIONS = {"focus_mode": tool_focus_mode}
GUARDS = {"focus_mode": r"\bfocus\b"}
ACTIONS = {"focus_mode"}
EXACT = {"focus_mode"}
