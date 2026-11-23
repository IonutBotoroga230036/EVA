"""
Your phone as E.V.A.'s hands (v0.3 milestone 9c, first part: alarms and timers).

The Android app announces what it can do ({"type": "device", "kind": "android", "caps": ["alarm", "timer"]}).
A tool here asks the phone ({"type": "device_action", ...}), the app does it with Android's own clock app,
and answers ({"type": "device_result", "ok": ...}). She only says it's done when the phone said so.
If no phone app is open, she says that plainly.
"""

from __future__ import annotations

import asyncio
import contextvars
import json
import re
from datetime import datetime
from typing import Optional

LOOP: Optional[asyncio.AbstractEventLoop] = None       # the server's loop, set at startup
TIMEOUT_S = 12.0
# Every open E.V.A. window (the server adds and removes them). It lives here, not in the server module: run as
# `python -m interfaces.web.server_stream`, that module exists twice, and a second empty copy found no phone.
CONNECTIONS: set = set()
# The window this turn came from (set by the server for each turn; worker threads inherit it). Nov 23: an alarm
# asked from the overlay went to the app page sleeping in the background, which never answered.
CURRENT: contextvars.ContextVar = contextvars.ContextVar("eva_window", default=None)
PHONE_CAPS = ("alarm", "timer", "reply", "volume", "send", "location", "record")


def phones(cap: str) -> list:
    """Connected phone windows that can do `cap`, the most recently connected last."""
    found = [c for c in list(CONNECTIONS) if cap in (getattr(c, "device_caps", None) or ())]
    return sorted(found, key=lambda c: getattr(c, "connected_at", 0.0))


def _ask(cap: str, action: str, args: dict) -> dict:
    """From a tool's worker thread: ask the most recently connected phone, wait for its answer."""
    targets = phones(cap)
    if not targets or LOOP is None:
        return {"ok": False, "error": "no phone"}
    here = CURRENT.get()
    order = ([here] if here in targets else []) + [t for t in reversed(targets) if t is not here]
    out: dict = {}
    for target in order[:2]:              # the window you're talking to first, then the newest other one
        fut = asyncio.run_coroutine_threadsafe(target.request_device(action, args, TIMEOUT_S), LOOP)
        try:
            out = fut.result(TIMEOUT_S + 2)
        except Exception as e:
            out = {"ok": False, "error": f"the phone didn't answer ({e.__class__.__name__})"}
        if out.get("ok") or "didn't answer" not in str(out.get("error", "")):
            return out
    return out


NO_PHONE = ("Your phone app isn't open, sir, so I can't reach your phone right now. Open E.V.A. on it and ask me "
            "again.")


def parse_clock(text: str) -> Optional[tuple[int, int]]:
    """'7', '7:30', '7.30 am', '19:05', 'half past 7' -> (hour, minute)."""
    t = (text or "").lower().strip()
    m = re.search(r"half past (\d{1,2})", t)
    if m:
        return int(m.group(1)) % 24, 30
    m = re.search(r"\b(\d{1,2})(?:[:.h](\d{2}))?\s*(am|pm|a\.m\.|p\.m\.|o'?clock)?\b", t)
    if not m:
        return None
    h, mi, suf = int(m.group(1)), int(m.group(2) or 0), (m.group(3) or "").replace(".", "")
    if h > 23 or mi > 59:
        return None
    if suf == "pm" and h < 12:
        h += 12
    if suf == "am" and h == 12:
        h = 0
    return h, mi


def alarm_time(text: str, now: Optional[datetime] = None) -> Optional[tuple[int, int]]:
    """The clock time for an alarm: '7:30', '6 o'clock in the morning', 'in 6 hours', '6 hours from now'."""
    t = (text or "").lower()
    now = now or datetime.now()
    if re.search(r"\bfrom now\b|^\s*(?:for\s+)?in\s+(?:\d|an?\b|half)", t):
        secs = parse_duration(t)
        if secs:
            from datetime import timedelta
            at = now + timedelta(seconds=secs)
            return at.hour, at.minute
    hm = parse_clock(re.sub(r"^\s*(?:for|at)\s+", "", t))
    if not hm:
        return None
    h, mi = hm
    if re.search(r"\b(evening|night|afternoon|pm)\b", t) and h < 12:
        h += 12
    if re.search(r"\b(morning|am)\b", t) and h == 12:
        h = 0
    return h, mi


def parse_duration(text: str) -> Optional[int]:
    """'10 minutes', '1 hour 30 minutes', '90 seconds', 'half an hour' -> seconds."""
    t = (text or "").lower()
    if "half an hour" in t:
        return 1800
    t = re.sub(r"\ban hour\b|\ba hour\b", "1 hour", t)
    total = 0
    for n, unit in re.findall(r"(\d+(?:[.,]\d+)?)\s*(hours?|hrs?|h\b|minutes?|mins?|m\b|seconds?|secs?|s\b)", t):
        v = float(n.replace(",", "."))
        total += int(v * (3600 if unit.startswith("h") else 60 if unit.startswith("m") else 1))
    return total or None


def tool_phone_alarm(time: str = "", label: str = "", **_):
    hm = alarm_time(time)
    if not hm:
        return {"result": json.dumps({"error": "no time"}), "say": "What time should the alarm be, sir?"}
    out = _ask("alarm", "set_alarm", {"hour": hm[0], "minute": hm[1], "label": label or "E.V.A."})
    if out.get("error") == "no phone":
        return {"result": json.dumps({"error": "no phone"}), "say": NO_PHONE}
    if not out.get("ok"):
        return {"result": json.dumps({"error": out.get("error", "failed")}),
                "say": f"Your phone couldn't set the alarm, sir: {out.get('error', 'no reason given')}."}
    return {"result": json.dumps({"alarm": f"{hm[0]:02d}:{hm[1]:02d}", "label": label}),
            "say": f"Alarm set on your phone for {hm[0]:02d}:{hm[1]:02d}, sir."}


def tool_phone_timer(duration: str = "", label: str = "", **_):
    secs = parse_duration(duration)
    if not secs:
        return {"result": json.dumps({"error": "no duration"}), "say": "How long should the timer be, sir?"}
    out = _ask("timer", "set_timer", {"seconds": secs, "label": label or "E.V.A."})
    if out.get("error") == "no phone":
        return {"result": json.dumps({"error": "no phone"}), "say": NO_PHONE}
    if not out.get("ok"):
        return {"result": json.dumps({"error": out.get("error", "failed")}),
                "say": f"Your phone couldn't start the timer, sir: {out.get('error', 'no reason given')}."}
    from core.later import human
    return {"result": json.dumps({"timer_seconds": secs}), "say": f"Timer set on your phone for {human(secs)}, sir."}


def tool_phone_stay_dark(on: bool = True, **_):
    """'Eva, stay dark': with the phone's screen off she answers by voice only. 'Stay dark off': she lights it."""
    from core import phone_prefs
    phone_prefs.set(stay_dark=bool(on))
    say = ("Staying dark, sir. With the screen off I'll answer by voice only." if on
           else "Alright, sir. I'll light the screen when you call me.")
    return {"result": json.dumps({"stay_dark": bool(on)}), "say": say, "exact": True}


def tool_phone_ask_unlock(on: bool = True, **_):
    """'Eva, work while locked' (on=False): email, calendar, notes and memories answer with the phone locked too.
    'Ask me to unlock again' (on=True). Turning the asking OFF needs an unlocked phone once (orchestrator)."""
    from core import phone_prefs
    phone_prefs.set(ask_unlock=bool(on))
    say = ("Alright, sir. I'll ask you to unlock for private things again." if on else
           "Done, sir. Everything works with your phone locked now, private things too. Anyone who says my name "
           "near it can hear them, so say 'ask me to unlock again' to switch it back.")
    return {"result": json.dumps({"ask_unlock": bool(on)}), "say": say, "exact": True}


def tool_phone_volume(level: int = 50, **_):
    """The phone's own media volume (not Spotify's, not the PC's)."""
    try:
        level = max(0, min(100, int(level)))
    except (TypeError, ValueError):
        return {"result": json.dumps({"error": "no level"}), "say": "What level, sir?"}
    out = _ask("volume", "set_volume", {"level": level})
    if out.get("error") == "no phone":
        return {"result": json.dumps({"error": "no phone"}), "say": NO_PHONE}
    if not out.get("ok"):
        return {"result": json.dumps({"error": out.get("error", "failed")}),
                "say": f"Your phone couldn't change the volume, sir: {out.get('error', 'no reason given')}."}
    return {"result": json.dumps({"phone_volume": level}), "say": f"Phone volume at {level} percent, sir."}


SCHEMAS = [
    {"type": "function", "function": {"name": "phone_alarm", "description": "Set an alarm on the user's phone.",
     "parameters": {"type": "object", "properties": {"time": {"type": "string", "description": "e.g. 7:30 am"},
                                                     "label": {"type": "string"}}, "required": ["time"]}}},
    {"type": "function", "function": {"name": "phone_timer", "description": "Start a countdown timer on the user's phone.",
     "parameters": {"type": "object", "properties": {"duration": {"type": "string", "description": "e.g. 10 minutes"},
                                                     "label": {"type": "string"}}, "required": ["duration"]}}},
]
SCHEMAS.append({"type": "function", "function": {
    "name": "phone_stay_dark",
    "description": "Whether the phone's screen stays off when the user calls E.V.A. with the screen off ('stay dark', "
                   "'stay dark off', 'light the screen when I call you').",
    "parameters": {"type": "object", "properties": {"on": {"type": "boolean"}}, "required": ["on"]}}})
SCHEMAS.append({"type": "function", "function": {
    "name": "phone_ask_unlock",
    "description": "Whether private things (email, calendar, notes, memories) need the phone unlocked: 'work while "
                   "locked' / 'stop asking me to unlock' (on=false), 'ask me to unlock again' (on=true).",
    "parameters": {"type": "object", "properties": {"on": {"type": "boolean"}}, "required": ["on"]}}})
SCHEMAS.append({"type": "function", "function": {
    "name": "phone_volume",
    "description": "Set the volume of the user's PHONE itself, 0 to 100 ('my phone volume to 50%'). Not Spotify's "
                   "and not the computer's volume.",
    "parameters": {"type": "object", "properties": {"level": {"type": "integer"}}, "required": ["level"]}}})
FUNCTIONS = {"phone_alarm": tool_phone_alarm, "phone_timer": tool_phone_timer, "phone_stay_dark": tool_phone_stay_dark,
             "phone_ask_unlock": tool_phone_ask_unlock, "phone_volume": tool_phone_volume}
GUARDS = {"phone_alarm": r"\b(alarm|wake me)\b", "phone_timer": r"\btimer\b",
          "phone_stay_dark": r"\b(dark|screen)\b", "phone_ask_unlock": r"\b(unlock|locked)\b",
          "phone_volume": r"phone\b.*\b(volume|louder|quieter)\b|\b(volume|louder|quieter)\b.*phone\b"}
