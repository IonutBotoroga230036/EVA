"""
Your phone as E.V.A.'s hands (v0.3 milestone 9c, first part: alarms and timers).

The Android app announces what it can do ({"type": "device", "kind": "android", "caps": ["alarm", "timer"]}).
A tool here asks the phone ({"type": "device_action", ...}), the app does it with Android's own clock app,
and answers ({"type": "device_result", "ok": ...}). She only says it's done when the phone said so.
If no phone app is open, she says that plainly.
"""

from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime
from typing import Optional

LOOP: Optional[asyncio.AbstractEventLoop] = None       # the server's loop, set at startup
TIMEOUT_S = 12.0


def phones(cap: str) -> list:
    from interfaces.web.server_stream import CONNECTIONS
    return [c for c in list(CONNECTIONS) if cap in (getattr(c, "device_caps", None) or ())]


def _ask(cap: str, action: str, args: dict) -> dict:
    """From a tool's worker thread: ask the most recently connected phone, wait for its answer."""
    targets = phones(cap)
    if not targets or LOOP is None:
        return {"ok": False, "error": "no phone"}
    fut = asyncio.run_coroutine_threadsafe(targets[-1].request_device(action, args, TIMEOUT_S), LOOP)
    try:
        return fut.result(TIMEOUT_S + 2)
    except Exception as e:
        return {"ok": False, "error": f"the phone didn't answer ({e.__class__.__name__})"}


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


def parse_duration(text: str) -> Optional[int]:
    """'10 minutes', '1 hour 30 minutes', '90 seconds', 'half an hour' -> seconds."""
    t = (text or "").lower()
    if "half an hour" in t:
        return 1800
    total = 0
    for n, unit in re.findall(r"(\d+(?:[.,]\d+)?)\s*(hours?|hrs?|h\b|minutes?|mins?|m\b|seconds?|secs?|s\b)", t):
        v = float(n.replace(",", "."))
        total += int(v * (3600 if unit.startswith("h") else 60 if unit.startswith("m") else 1))
    return total or None


def tool_phone_alarm(time: str = "", label: str = "", **_):
    hm = parse_clock(time)
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


SCHEMAS = [
    {"type": "function", "function": {"name": "phone_alarm", "description": "Set an alarm on the user's phone.",
     "parameters": {"type": "object", "properties": {"time": {"type": "string", "description": "e.g. 7:30 am"},
                                                     "label": {"type": "string"}}, "required": ["time"]}}},
    {"type": "function", "function": {"name": "phone_timer", "description": "Start a countdown timer on the user's phone.",
     "parameters": {"type": "object", "properties": {"duration": {"type": "string", "description": "e.g. 10 minutes"},
                                                     "label": {"type": "string"}}, "required": ["duration"]}}},
]
FUNCTIONS = {"phone_alarm": tool_phone_alarm, "phone_timer": tool_phone_timer}
GUARDS = {"phone_alarm": r"\b(alarm|wake me)\b", "phone_timer": r"\btimer\b"}
