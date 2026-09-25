"""TEMPO: calendar logic. Exact spoken lines from real events; the LLM never paraphrases them."""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from difflib import SequenceMatcher
from typing import Optional

from core.timeparse import day_window, parse_clock, part_of_day
from core.weather import extract_when, resolve_day

WORK_START, WORK_END = 8, 20


def _hm(dt: datetime) -> str:
    return dt.strftime("%H:%M")


def _join(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + ", and " + items[-1]


def next_up(google, now: Optional[datetime] = None) -> dict:
    """What's happening now and what's next, today; or how tomorrow starts."""
    now = now or datetime.now()
    start, end, _ = day_window("today", now.date())
    timed = [e for e in google.list_events(start, end, max_results=50) if not e["all_day"] and e["start"]]
    current = [e for e in timed if e["start"] <= now < (e["end"] or e["start"])]
    upcoming = [e for e in timed if e["start"] > now]
    parts = []
    if current:
        c = current[-1]
        parts.append(f"Right now: {c['title']}, until {_hm(c['end'])}.")
    if upcoming:
        n = upcoming[0]
        mins = round((n["start"] - now).total_seconds() / 60)
        when = f"in {mins} minute{'s' if mins != 1 else ''}" if mins < 90 else f"at {_hm(n['start'])}"
        parts.append(f"Next is {n['title']} {when}" + (f" at {_hm(n['start'])}." if mins < 90 else "."))
        if len(upcoming) > 1:
            parts.append(f"After that, {upcoming[1]['title']} at {_hm(upcoming[1]['start'])}.")
        widget_events = upcoming[:5]
    else:
        t_start, t_end, _ = day_window("tomorrow", now.date())
        tomorrow = [e for e in google.list_events(t_start, t_end, max_results=10) if not e["all_day"] and e["start"]]
        parts.append("Nothing else today." + (f" Tomorrow starts at {_hm(tomorrow[0]['start'])} with "
                                              f"{tomorrow[0]['title']}." if tomorrow else ""))
        widget_events = tomorrow[:5]
    say = " ".join(parts)
    return {"say": say[:-1] + ", sir." if say.endswith(".") else say + ", sir.",
            "widget": {"kind": "calendar", "title": "Up next",
                       "events": [{"time": _hm(e["start"]), "title": e["title"]} for e in widget_events]}}


def agenda(google, day: str = "today", now: Optional[datetime] = None) -> dict:
    now = now or datetime.now()
    start, end, label = day_window(day, now.date())
    events = google.list_events(start, end)
    if label == "today":                           # today: what's still coming matters, not what's done
        remaining = [e for e in events if e["all_day"] or not e["start"] or (e["end"] or e["start"]) > now]
        if len(remaining) < len(events):
            done = len(events) - len(remaining)
            rows = [{"time": "all day" if e["all_day"] else _hm(e["start"]), "title": e["title"]} for e in remaining]
            widget = {"kind": "calendar", "title": "Still today", "events": rows}
            if not remaining:
                return {"events": [], "widget": widget,
                        "say": f"That's everything for today, sir. All {done} things are behind you."}
            parts = [f"all day {r['title']}" if r["time"] == "all day" else f"{r['time']} {r['title']}" for r in rows[:5]]
            more = f", plus {len(rows) - 5} more" if len(rows) > 5 else ""
            return {"events": rows, "widget": widget,
                    "say": f"You have {len(rows)} thing{'s' if len(rows) != 1 else ''} still to come today, sir: "
                           f"{_join(parts)}{more}."}
    rows = [{"time": "all day" if e["all_day"] else _hm(e["start"]), "title": e["title"],
             "day": e["start"].strftime("%a") if e["start"] else ""} for e in events]
    widget = {"kind": "calendar", "title": label.capitalize(), "events": rows}
    if not events:
        return {"events": [], "say": f"Your calendar is clear {label}, sir.", "widget": widget}
    multi_day = (end - start).days > 1
    parts = [(f"{r['day']} " if multi_day else "") + (f"all day {r['title']}" if r["time"] == "all day"
                                                     else f"{r['time']} {r['title']}") for r in rows[:6]]
    more = f", plus {len(rows) - 6} more" if len(rows) > 6 else ""
    n = len(rows)
    return {"events": rows, "widget": widget,
            "say": f"You have {n} thing{'s' if n != 1 else ''} {label}, sir: {_join(parts)}{more}."}


def free_slots(google, day: str = "today", duration_minutes: int = 30, now: Optional[datetime] = None) -> dict:
    now = now or datetime.now()
    start, end, label = day_window(day)
    lo, hi = part_of_day(day) or (WORK_START, WORK_END)
    window_start = max(start.replace(hour=lo), now if start.date() == now.date() else start.replace(hour=lo))
    window_end = start.replace(hour=hi) if hi < 24 else end
    busy = sorted((e["start"], e["end"]) for e in google.list_events(start, end) if not e["all_day"] and e["start"])
    gaps, cursor = [], window_start
    for s, e in busy:
        if s > cursor and (s - cursor) >= timedelta(minutes=duration_minutes):
            gaps.append((cursor, min(s, window_end)))
        cursor = max(cursor, e)
        if cursor >= window_end:
            break
    if window_end - cursor >= timedelta(minutes=duration_minutes):
        gaps.append((cursor, window_end))
    gaps = [(a, b) for a, b in gaps if b > a]
    span = f"{label} {'in the ' + ('evening' if lo >= 18 else 'afternoon' if lo >= 12 else 'morning') if part_of_day(day) else ''}".strip()
    if not gaps:
        return {"gaps": [], "say": f"I don't see a free {duration_minutes} minutes {span}, sir."}
    words = [f"from {_hm(a)} to {_hm(b)}" for a, b in gaps[:3]]
    return {"gaps": [(_hm(a), _hm(b)) for a, b in gaps],
            "say": f"You're free {span} {_join(words)}, sir."}


def shift_events(google, minutes: int = 60, day: str = "today", now: Optional[datetime] = None) -> dict:
    """Move every timed event still ahead (today, or the given day) by N minutes."""
    now = now or datetime.now()
    start, end, label = day_window(day, now.date())
    events = [e for e in google.list_events(start, end, max_results=50)
              if not e["all_day"] and e["start"] and (label != "today" or e["start"] > now)]
    if not events:
        return {"moved": 0, "say": f"There's nothing left to move {label}, sir."}
    delta = timedelta(minutes=int(minutes))
    for e in events:
        google.move_event(e["id"], e["start"] + delta, (e["end"] or e["start"]) + delta)
    first = events[0]
    amount = f"{abs(int(minutes)) // 60} hour{'s' if abs(int(minutes)) >= 120 else ''}" if int(minutes) % 60 == 0 \
        else f"{abs(int(minutes))} minutes"
    return {"moved": len(events), "say": f"Moved {len(events)} event{'s' if len(events) != 1 else ''} {amount} "
                                         f"{'later' if minutes > 0 else 'earlier'}, sir. {first['title']} is now at "
                                         f"{_hm(first['start'] + delta)}."}


def resolve_start(day: str, time_text: str, now: Optional[datetime] = None) -> Optional[datetime]:
    now = now or datetime.now()
    phrase, _ = extract_when(day or "")
    d = resolve_day(phrase or day or "today", now.date()) or now.date()
    clock = parse_clock(time_text or "") or parse_clock(day or "")
    if not clock:
        return None
    return datetime(d.year, d.month, d.day, clock[0], clock[1])


def add_event(google, title: str, day: str, time_text: str, duration_minutes: int = 60) -> dict:
    start = resolve_start(day, time_text)
    if not start:
        return {"error": "no time given", "say": f"What time should I put {title} in, sir?"}
    end = start + timedelta(minutes=int(duration_minutes or 60))
    google.insert_event(title, start, end)
    return {"added": title, "start": start.isoformat(),
            "say": f"Added {title} on {start:%A} at {_hm(start)}, sir."}


def find_event(google, title: str, day: str = "today") -> Optional[dict]:
    start, end, _ = day_window(day or "today")
    if (end - start).days < 2:                        # search a week if the day wasn't clear
        end = max(end, start + timedelta(days=7))
    best, score = None, 0.0
    for e in google.list_events(start, end, max_results=50):
        s = SequenceMatcher(None, title.lower(), e["title"].lower()).ratio()
        if title.lower() in e["title"].lower():
            s = max(s, 0.9)
        if s > score:
            best, score = e, s
    return best if score >= 0.55 else None


def delete_event(google, title: str, day: str = "today") -> dict:
    e = find_event(google, title, day)
    if not e:
        return {"error": "not found", "say": f"I couldn't find {title} in your calendar, sir."}
    google.delete_event(e["id"])
    when = "" if e["all_day"] else f" at {_hm(e['start'])}"
    return {"deleted": e["title"], "say": f"Removed {e['title']} on {e['start']:%A}{when}, sir."}


_LEAD = re.compile(r"^(?:please |can you |could you |i want you to )*(?:add|put|create|schedule|book|make|plan)"
                   r"(?: an?| the| new)?\s+", re.I)
_GENERIC = re.compile(r"^(?:event|appointment|block|entry|item)\b(?:\s+(?:for|called|named|titled|about))?\s*", re.I)
_NOW = re.compile(r"\b(right now|now|immediately)\b", re.I)


def clean_title(title: str) -> str:
    """'add a meeting with Tom to my calendar' -> 'Meeting with Tom'; 'add an event for right now' -> 'Event'."""
    t = _LEAD.sub("", (title or "").strip())
    t = re.sub(r"\s*\b(?:to|in|on|into) (?:my |the )?calendar\b", "", t, flags=re.I)
    t = _GENERIC.sub("", t)
    t = re.sub(r"\s*\b(?:for |at |on )?(?:right now|now|today|tomorrow|tonight|this (?:morning|afternoon|evening))\b.*$",
               "", t, flags=re.I)
    t = re.sub(r"\s+at\s+\d.*$", "", t, flags=re.I).strip(" ,.")
    return t[:1].upper() + t[1:] if t else "Event"


def fill_args(args: dict, text: str) -> dict:
    """Your words fill the day and time if the model left them out; titles lose the command words."""
    out = dict(args)
    if "title" in out or "calendar_add" in (text or ""):
        out["title"] = clean_title(out.get("title", ""))
    if _NOW.search(text or "") or _NOW.fullmatch(str(out.get("time", "")).strip()):
        now = datetime.now()
        start = now + timedelta(minutes=(5 - now.minute % 5) % 5)
        out["day"], out["time"] = "today", f"{start:%H:%M}"
    phrase, _ = extract_when(text)
    if phrase and not out.get("day"):
        out["day"] = phrase
    if "time" in out or re.search(r"\b(at|around)\b|\d(am|pm)|\d:\d\d", text or "", re.I):
        if not out.get("time") and parse_clock(text):
            h, m, _ = parse_clock(text)
            out["time"] = f"{h:02d}:{m:02d}"
    return out
