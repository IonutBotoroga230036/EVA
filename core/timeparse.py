"""Shared time parsing for calendar and reminders: clock times, relative times, day windows."""

from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta
from typing import Optional

from core.weather import PARTS_OF_DAY, extract_when, resolve_day, resolve_hour_ex

_CLOCK = re.compile(r"\b(?:at |around )?(\d{1,2})(?:[:.](\d{2}))?\s*(am|pm|a\.m\.|p\.m\.)?(?![\d:])", re.I)
_IN = re.compile(r"\bin\s+(?:(an?|one|half an?|a couple of|\d+(?:[.,]\d+)?)\s*)?(minutes?|mins?|hours?|hrs?|seconds?|days?)\b", re.I)
_WORDS = {"a": 1, "an": 1, "one": 1, "a couple of": 2, "half an": 0.5, "half a": 0.5}


def parse_clock(text: str) -> Optional[tuple[int, int, bool]]:
    """'15:30' -> (15, 30, False); '3pm' -> (15, 0, False); 'at 3' -> (15, 0, True); 'noon' -> (12, 0, False)."""
    t = (text or "").lower()
    if re.search(r"\bnoon|midday\b", t):
        return 12, 0, False
    if re.search(r"\bmidnight\b", t):
        return 0, 0, False
    for m in _CLOCK.finditer(t):
        h, mins, half = int(m.group(1)), int(m.group(2) or 0), (m.group(3) or "").replace(".", "")
        if h > 23 or mins > 59:
            continue
        if not (m.group(2) or half or m.group(0).lower().startswith(("at ", "around "))):
            continue                                     # a bare number ("the 10 km run") isn't a time
        if half == "pm" and h < 12:
            h += 12
        elif half == "am" and h == 12:
            h = 0
        assumed = False
        if not half and 1 <= h <= 6:
            h, assumed = h + 12, True                    # "at 3" means the afternoon
        return h, mins, assumed
    for word, hour in PARTS_OF_DAY.items():
        if re.search(rf"\b{word}\b", t):
            return hour, 0, True
    return None


def parse_when(text: str, now: Optional[datetime] = None) -> Optional[datetime]:
    """'in 20 minutes', 'at 18:00', 'tomorrow at 9', 'tonight', 'friday morning' -> datetime (local)."""
    now = now or datetime.now()
    t = (text or "").lower()
    m = _IN.search(t)
    if m:
        qty = m.group(1) or "1"
        n = _WORDS.get(qty.strip(), None)
        n = float(qty.replace(",", ".")) if n is None else n
        unit = m.group(2)[0]
        delta = {"m": timedelta(minutes=n), "h": timedelta(hours=n), "s": timedelta(seconds=n),
                 "d": timedelta(days=n)}[unit]
        return now + delta
    day_phrase, _ = extract_when(t)
    day = resolve_day(day_phrase, now.date()) if day_phrase else now.date()
    clock = parse_clock(t)
    if clock is None:
        if not day_phrase:
            return None
        clock = (9, 0, True)                              # "tomorrow" alone: morning
    when = datetime.combine(day or now.date(), time(clock[0], clock[1]))
    if when <= now and not day_phrase:
        when += timedelta(days=1)                         # "at 7" when it's already 8: tomorrow
    return when


def day_window(text: str, today: Optional[date] = None) -> tuple[datetime, datetime, str]:
    """A spoken day or week -> (start, end, label for speech)."""
    today = today or date.today()
    t = (text or "today").lower().strip()
    if re.search(r"\bnext week\b", t):
        start = today + timedelta(days=7 - today.weekday())
        return datetime.combine(start, time()), datetime.combine(start + timedelta(days=7), time()), "next week"
    if re.search(r"\b(this week|the week|week)\b", t):
        return (datetime.combine(today, time()), datetime.combine(today + timedelta(days=7 - today.weekday()), time()),
                "this week")
    phrase, _ = extract_when(t)
    day = resolve_day(phrase, today) if phrase else today
    day = day or today
    label = "today" if day == today else "tomorrow" if day == today + timedelta(1) else day.strftime("on %A the ") + \
        f"{day.day}{'th' if 11 <= day.day % 100 <= 13 else {1: 'st', 2: 'nd', 3: 'rd'}.get(day.day % 10, 'th')}"
    return datetime.combine(day, time()), datetime.combine(day + timedelta(days=1), time()), label


def part_of_day(text: str) -> Optional[tuple[int, int]]:
    t = (text or "").lower()
    for word, rng in (("morning", (8, 12)), ("afternoon", (12, 18)), ("evening", (18, 22)), ("tonight", (18, 23))):
        if word in t:
            return rng
    return None
