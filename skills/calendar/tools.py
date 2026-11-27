"""Calendar skill (TEMPO): thin voice wrappers over core/tempo.py."""

import json

from core import tempo
from core.google_api import NeedAuth, auth_message, get_google

TOOLS = [
    {"type": "function", "function": {
        "name": "calendar_agenda",
        "description": "What is on the user's calendar for a day or week: 'today', 'tomorrow', 'yesterday', 'friday', 'the 26th', 'this week'. "
                       "Daily routine items (wake up, vitamins) are summed up unless everything is true.",
        "parameters": {"type": "object", "properties": {"day": {"type": "string"}, "everything": {
            "type": "boolean", "description": "true only when the user asks for everything, including the routine"},
            "include_past": {"type": "boolean", "description": "today: also what's already behind him (normally only "
                                                               "what's still to come)"}}}}},
    {"type": "function", "function": {
        "name": "calendar_next",
        "description": "What's happening now and what's next on the calendar today.",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "calendar_free",
        "description": "Find free time, empty slots or gaps in the calendar ('when am I free tomorrow', 'any empty slots on friday'). day may include 'morning', 'afternoon', or 'evening'.",
        "parameters": {"type": "object", "properties": {
            "day": {"type": "string"}, "duration_minutes": {"type": "integer"}}}}},
    {"type": "function", "function": {
        "name": "calendar_add",
        "description": "Add an event to the calendar.",
        "parameters": {"type": "object", "properties": {
            "title": {"type": "string", "description": "What the event is, in the user's words."},
            "day": {"type": "string"}, "time": {"type": "string", "description": "e.g. '15:00', '3pm'"},
            "duration_minutes": {"type": "integer"}}, "required": ["title"]}}},
    {"type": "function", "function": {
        "name": "calendar_shift",
        "description": "Move several events later or earlier: all remaining ones of a day ('push everything 1 hour "
                       "later'), or those starting in a time range ('move the items from 9 until now 30 minutes "
                       "later': from_time '9', until_time 'now').",
        "parameters": {"type": "object", "properties": {
            "minutes": {"type": "integer", "description": "Positive = later, negative = earlier. Ask if he didn't say."},
            "day": {"type": "string"},
            "from_time": {"type": "string", "description": "Only events starting at or after this time, e.g. '9', '14:30'"},
            "until_time": {"type": "string", "description": "Only events starting before this time, e.g. 'now', '12'"}},
            "required": ["minutes"]}}},
    {"type": "function", "function": {
        "name": "calendar_move",
        "description": "Move ONE event to a new time, e.g. 'move coffee with Tom to 4pm'.",
        "parameters": {"type": "object", "properties": {
            "title": {"type": "string"}, "time": {"type": "string"}, "day": {"type": "string"}},
            "required": ["title", "time"]}}},
    {"type": "function", "function": {
        "name": "calendar_delete",
        "description": "Remove an event from the calendar.",
        "parameters": {"type": "object", "properties": {
            "title": {"type": "string"}, "day": {"type": "string"}}, "required": ["title"]}}},
    {"type": "function", "function": {
        "name": "calendar_restore",
        "description": "Undo a calendar change (used by undo): events back at their old times, added ones removed.",
        "parameters": {"type": "object", "properties": {
            "events": {"type": "array", "items": {"type": "object"}}, "remove": {"type": "array", "items": {"type": "string"}},
            "recreate": {"type": "array", "items": {"type": "object"}}}}}},
]


def _run(fn, *a, **k):
    try:
        out = fn(get_google(), *a, **k)
    except NeedAuth:
        return {"result": json.dumps({"error": "not connected to Google"}), "say": auth_message(), "exact": True}
    except Exception as e:
        return {"result": json.dumps({"error": str(e)}), "say": f"Google didn't answer, sir: {str(e)[:120]}.",
                "exact": True}
    res = {"result": json.dumps({k: v for k, v in out.items() if k not in ("say", "widget", "undo")}, default=str),
           "say": out["say"], "exact": True}
    for k in ("widget", "changes", "undo"):            # v1.0: what changed and how to put it back, for the journal
        if out.get(k):
            res[k] = out[k]
    return res


def calendar_agenda(day: str = "today", everything: bool = False, include_past: bool = False, **_):
    return _run(tempo.agenda, day or "today", everything=bool(everything), include_past=bool(include_past))


def calendar_next(**_):
    return _run(tempo.next_up)


def calendar_free(day: str = "today", duration_minutes: int = 30, **_):
    return _run(tempo.free_slots, day or "today", int(duration_minutes or 30))


def calendar_add(title: str = "", day: str = "today", time: str = "", duration_minutes: int = 60, **_):
    return _run(tempo.add_event, title, day or "today", time, int(duration_minutes or 60))


def calendar_shift(minutes: int = 60, day: str = "today", from_time: str = "", until_time: str = "", **_):
    return _run(tempo.shift_events, int(minutes or 60), day if day and not str(day).strip().isdigit() else "today",
                from_time=str(from_time or ""), until_time=str(until_time or ""))


def calendar_move(title: str = "", time: str = "", day: str = "", **_):
    return _run(tempo.move_event_to, title, time, day or "")


def calendar_delete(title: str = "", day: str = "today", **_):
    return _run(tempo.delete_event, title, day or "today")


def calendar_restore(events: list | None = None, remove: list | None = None, recreate: list | None = None, **_):
    return _run(tempo.restore, events=events or [], remove=remove or [], recreate=recreate or [])


FUNCTIONS = {"calendar_agenda": calendar_agenda, "calendar_next": calendar_next, "calendar_free": calendar_free,
             "calendar_add": calendar_add, "calendar_delete": calendar_delete, "calendar_shift": calendar_shift,
             "calendar_move": calendar_move, "calendar_restore": calendar_restore}
ACKS = {"calendar_agenda": "One moment, sir. Pulling up your calendar.",
        "calendar_free": "Let me look at your calendar, sir.",
        "calendar_next": "One moment, sir."}
ACTIONS = ["calendar_add", "calendar_delete", "calendar_shift", "calendar_move", "calendar_restore"]
GUARDS = {
    "calendar_agenda": r"\b(calendar|schedule|agenda|plans?|meetings?|events?|appointments?|busy|doing|booked|on for)\b",
    "calendar_free": r"\b(free|openings?|gaps?|available|slots?|empty|time for|room for|fit (?:in|it))\b",
    "calendar_next": r"\b(next|now|coming up|upcoming|later)\b",
    "calendar_add": r"\b(add|schedule|book|put|create|set up|plan|block)\b",
    "calendar_delete": r"\b(delete|remove|cancel|clear|drop)\b",
    "calendar_shift": r"\b(everything|all|the rest|remaining|whole day|my day)\b",     # "push everything later"
    "calendar_move": r"\b(move|reschedule|push|shift|change|put)\b",
}
CONFIRM = {
    "calendar_add": lambda a: f"add {a.get('title', 'an event')} to your calendar "
                              f"{a.get('day') or 'today'}{(' at ' + a['time']) if a.get('time') else ''}",
    "calendar_delete": lambda a: f"remove {a.get('title', 'that event')} from your calendar",
    "calendar_move": lambda a: f"move {a.get('title', 'that event')} to {a.get('time', 'a new time')}"
                               f"{(' ' + a['day']) if a.get('day') else ''}",
    "calendar_shift": lambda a: _shift_words(a),
}
def _shift_words(a: dict) -> str:
    """Nov 27: 'move everything still ahead 9 60 minutes earlier'. Now: what, when, how far."""
    day = a.get("day") if a.get("day") and not str(a["day"]).strip().isdigit() else "today"
    m = int(a.get("minutes") or 60)
    lo, hi = str(a.get("from_time") or "").strip(), str(a.get("until_time") or "").strip()
    what = (f"the events {day} from {lo if ':' in lo or not lo.isdigit() else lo + ':00'}" if lo else
            f"the events {day}") + (f" until {hi}" if hi else "") if (lo or hi) else f"everything still ahead {day}"
    amount = (f"{abs(m) // 60} hour{'s' if abs(m) >= 120 else ''}" if m % 60 == 0 else f"{abs(m)} minutes")
    return f"move {what} {amount} {'later' if m > 0 else 'earlier'}"


FILLERS = {"calendar_add": tempo.fill_args, "calendar_free": tempo.fill_args, "calendar_agenda": tempo.fill_args}
