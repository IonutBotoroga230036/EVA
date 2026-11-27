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


def _late(label: str, now: datetime) -> str:
    """After midnight, 'today' is easy to mishear as the day that just ended: name it (Nov 27, 00:39)."""
    if label == "today" and now.hour < 4:
        return f"today, {now.strftime('%A')} the {now.day}{'th' if 11 <= now.day % 100 <= 13 else {1: 'st', 2: 'nd', 3: 'rd'}.get(now.day % 10, 'th')},"
    return label


def agenda(google, day: str = "today", now: Optional[datetime] = None, everything: bool = False,
           include_past: bool = False) -> dict:
    now = now or datetime.now()
    start, end, label = day_window(day, now.date())
    events = google.list_events(start, end)
    routine = [e for e in events if e.get("routine")]
    if routine and not everything:              # your daily routine only when you ask for everything
        main = [e for e in events if not e.get("routine")]
        if main:
            out = agenda(_Fixed(main), day, now, everything=True, include_past=include_past)
            r = len(routine)
            out["say"] = out["say"].rstrip(".") + f". Plus your daily routine ({r} item{'s' if r != 1 else ''})."
            out["routine"] = r
            return out
        events = routine
    if label == "today" and not include_past:      # today: what's still coming matters, not what's done
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
                    "say": f"You have {len(rows)} thing{'s' if len(rows) != 1 else ''} still to come "
                           f"{_late('today', now).rstrip(',')}, sir: {_join(parts)}{more}."}
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
    past = "yesterday" in label or (start.date() < now.date())
    return {"events": rows, "widget": widget,
            "say": (f"You had {n} thing{'s' if n != 1 else ''} {label}, sir: {_join(parts)}{more}." if past else
                    f"You have {n} thing{'s' if n != 1 else ''} {_late(label, now).rstrip(',')}, sir: "
                    f"{_join(parts)}{more}.")}


class _Fixed:
    """A calendar that only has these events (agenda without the routine)."""
    def __init__(self, events):
        self.events = events

    def list_events(self, *_a, **_k):
        return self.events


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
    span = f"{_late(label, now).rstrip(',')} {'in the ' + ('evening' if lo >= 18 else 'afternoon' if lo >= 12 else 'morning') if part_of_day(day) else ''}".strip()
    if not gaps:
        return {"gaps": [], "say": f"I don't see a free {duration_minutes} minutes {span}, sir."}
    words = [f"from {_hm(a)} to {_hm(b)}" for a, b in gaps[:3]]
    return {"gaps": [(_hm(a), _hm(b)) for a, b in gaps],
            "say": f"You're free {span} {_join(words)}, sir."}


def clock_on(day_start: datetime, text: str, now: datetime) -> Optional[datetime]:
    """'9', '09:30', '3pm', 'now' on that day (v0.9: "move the items from 9 until now")."""
    t = (text or "").strip().lower()
    if not t:
        return None
    if re.fullmatch(r"(?:until |till |to )?now|right now", t):
        return now
    if re.fullmatch(r"\d{1,2}", t):
        t = f"{int(t):02d}:00"
    c = parse_clock(t)
    if not c:
        return None
    h = c[0]
    if len(c) > 2 and c[2] and h >= 12 and day_start.date() == now.date() and h - 12 <= now.hour < h:
        h -= 12                                     # "from 9" at 09:52 is this morning, not 21:00
    return day_start.replace(hour=h, minute=c[1])


def shift_events(google, minutes: int = 60, day: str = "today", now: Optional[datetime] = None,
                 from_time: str = "", until_time: str = "") -> dict:
    """Move every timed event still ahead (today, or the given day) by N minutes; or only those starting between
    from_time and until_time ("from 9 until now", v0.9)."""
    now = now or datetime.now()
    start, end, label = day_window(day, now.date())
    lo, hi = clock_on(start, from_time, now), clock_on(start, until_time, now)
    ranged = bool(lo or hi)
    events = [e for e in google.list_events(start, end, max_results=50)
              if not e["all_day"] and e["start"] and not e.get("readonly")
              and ((lo or start) <= e["start"] < (hi or end) if ranged else (label != "today" or e["start"] > now))]
    if not events:
        span = f"between {_hm(lo) if lo else 'the start of the day'} and {'now' if hi == now else _hm(hi) if hi else 'the end of the day'}"
        return {"moved": 0, "say": f"There's nothing {span if ranged else 'left'} to move {label}, sir." if ranged else
                f"There's nothing left to move {label}, sir."}
    delta = timedelta(minutes=int(minutes))
    changes = [{"what": e["title"], "before": _hm(e["start"]), "after": _hm(e["start"] + delta),
                "day": e["start"].date().isoformat()} for e in events]      # v1.0: before anything moves
    undo = {"tool": "calendar_restore", "what": f"put {len(events)} event{'s' if len(events) != 1 else ''} back",
            "args": {"events": [{"id": e["id"], "title": e["title"], "start": e["start"].isoformat(),
                                 "end": (e["end"] or e["start"]).isoformat()} for e in events]}}
    starts = [e["start"] for e in events]
    for e in events:
        google.move_event(e["id"], e["start"] + delta, (e["end"] or e["start"]) + delta)
    first = {**events[0], "start": starts[0]}
    amount = f"{abs(int(minutes)) // 60} hour{'s' if abs(int(minutes)) >= 120 else ''}" if int(minutes) % 60 == 0 \
        else f"{abs(int(minutes))} minutes"
    names = ", ".join(e["title"] for e in events[:4]) + (f" and {len(events) - 4} more" if len(events) > 4 else "")
    return {"moved": len(events), "titles": [e["title"] for e in events], "changes": changes, "undo": undo,
            "say": f"Moved {len(events)} event{'s' if len(events) != 1 else ''} {amount} "
                   f"{'later' if minutes > 0 else 'earlier'}, sir{f' ({names})' if ranged else ''}. {first['title']} is now at "
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
    made = google.insert_event(title, start, end)
    eid = made.get("id") if isinstance(made, dict) else None
    out = {"added": title, "start": start.isoformat(),
           "changes": [{"what": title, "before": "(not there)", "after": f"{start:%a} {_hm(start)}"}],
           "say": f"Added {title} on {start:%A} at {_hm(start)}, sir."}
    if eid:
        out["undo"] = {"tool": "calendar_restore", "what": f"remove {title} again", "args": {"remove": [eid]}}
    return out


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


def move_event_to(google, title: str, time_text: str, day: str = "") -> dict:
    """'Move coffee with Tom to 4pm': that one event, same length. The day stays unless you name one."""
    e = find_event(google, title, day or "today")
    if not e:
        return {"error": "not found", "say": f"I couldn't find {title} in your calendar, sir."}
    if e.get("readonly"):
        return {"error": "read only", "say": f"{e['title']} is in your {e.get('calendar') or 'other'} calendar, which I "
                                             f"can only read, sir."}
    if e.get("all_day"):
        return {"error": "all day", "say": f"{e['title']} is an all-day event, sir; I can't give it a time."}
    base = resolve_start(day, time_text) if day else resolve_start("today", time_text)
    if not base:
        return {"error": "no time given", "say": f"What time should {e['title']} move to, sir?"}
    start = base if day else e["start"].replace(hour=base.hour, minute=base.minute, second=0, microsecond=0)
    length = (e["end"] - e["start"]) if e.get("end") else timedelta(hours=1)
    before = dict(e)
    google.move_event(e["id"], start, start + length)
    e = before
    return {"moved": e["title"], "start": start.isoformat(),
            "changes": [{"what": e["title"], "before": f"{e['start']:%a} {_hm(e['start'])}",
                         "after": f"{start:%a} {_hm(start)}"}],
            "undo": {"tool": "calendar_restore", "what": f"put {e['title']} back at {_hm(e['start'])}",
                     "args": {"events": [{"id": e["id"], "title": e["title"], "start": e["start"].isoformat(),
                                          "end": (e.get("end") or e["start"] + length).isoformat()}]}},
            "say": f"Moved {e['title']} to {start:%A} at {_hm(start)}, sir."}


def delete_event(google, title: str, day: str = "today") -> dict:
    e = find_event(google, title, day)
    if not e:
        return {"error": "not found", "say": f"I couldn't find {title} in your calendar, sir."}
    if e.get("readonly"):
        return {"error": "read only", "say": f"{e['title']} is in your {e.get('calendar') or 'other'} calendar, which I "
                                             f"can only read, sir."}
    google.delete_event(e["id"])
    when = "" if e["all_day"] else f" at {_hm(e['start'])}"
    out = {"deleted": e["title"], "say": f"Removed {e['title']} on {e['start']:%A}{when}, sir.",
           "changes": [{"what": e["title"], "before": f"{e['start']:%a}{when}", "after": "(removed)"}]}
    if not e["all_day"] and e.get("start"):
        out["undo"] = {"tool": "calendar_restore", "what": f"put {e['title']} back",
                       "args": {"recreate": [{"title": e["title"], "start": e["start"].isoformat(),
                                              "end": (e.get("end") or e["start"] + timedelta(hours=1)).isoformat()}]}}
    return out


def restore(google, events: Optional[list] = None, remove: Optional[list] = None,
            recreate: Optional[list] = None) -> dict:
    """v1.0 undo: events back at their old times, added ones removed, removed ones put back."""
    done = []
    for e in events or []:
        google.move_event(e["id"], datetime.fromisoformat(e["start"]), datetime.fromisoformat(e["end"]))
        done.append(f"{e.get('title', 'an event')} back at {_hm(datetime.fromisoformat(e['start']))}")
    for eid in remove or []:
        google.delete_event(eid)
        done.append("the added event removed")
    for e in recreate or []:
        google.insert_event(e["title"], datetime.fromisoformat(e["start"]), datetime.fromisoformat(e["end"]))
        done.append(f"{e['title']} put back")
    if not done:
        return {"error": "nothing to restore", "say": "There was nothing to put back, sir."}
    return {"restored": len(done), "say": "Done: " + "; ".join(done[:4]) + (f" and {len(done) - 4} more" if len(done) > 4
                                                                           else "") + "."}


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
