"""Calendar skill (TEMPO): thin voice wrappers over core/tempo.py."""

import json

from core import tempo
from core.google_api import NeedAuth, auth_message, get_google

TOOLS = [
    {"type": "function", "function": {
        "name": "calendar_agenda",
        "description": "What is on the user's calendar for a day or week: 'today', 'tomorrow', 'friday', 'this week'.",
        "parameters": {"type": "object", "properties": {"day": {"type": "string"}}}}},
    {"type": "function", "function": {
        "name": "calendar_next",
        "description": "What's happening now and what's next on the calendar today.",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "calendar_free",
        "description": "Find free time in the calendar. day may include 'morning', 'afternoon', or 'evening'.",
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
        "description": "Move all remaining events of a day later or earlier, e.g. 'push everything 1 hour later'.",
        "parameters": {"type": "object", "properties": {
            "minutes": {"type": "integer", "description": "Positive = later, negative = earlier."},
            "day": {"type": "string"}}, "required": ["minutes"]}}},
    {"type": "function", "function": {
        "name": "calendar_delete",
        "description": "Remove an event from the calendar.",
        "parameters": {"type": "object", "properties": {
            "title": {"type": "string"}, "day": {"type": "string"}}, "required": ["title"]}}},
]


def _run(fn, *a, **k):
    try:
        out = fn(get_google(), *a, **k)
    except NeedAuth:
        return {"result": json.dumps({"error": "not connected to Google"}), "say": auth_message(), "exact": True}
    except Exception as e:
        return {"result": json.dumps({"error": str(e)}), "say": f"Google didn't answer, sir: {str(e)[:120]}.",
                "exact": True}
    res = {"result": json.dumps({k: v for k, v in out.items() if k not in ("say", "widget")}, default=str),
           "say": out["say"], "exact": True}
    if out.get("widget"):
        res["widget"] = out["widget"]
    return res


def calendar_agenda(day: str = "today", **_):
    return _run(tempo.agenda, day or "today")


def calendar_next(**_):
    return _run(tempo.next_up)


def calendar_free(day: str = "today", duration_minutes: int = 30, **_):
    return _run(tempo.free_slots, day or "today", int(duration_minutes or 30))


def calendar_add(title: str = "", day: str = "today", time: str = "", duration_minutes: int = 60, **_):
    return _run(tempo.add_event, title, day or "today", time, int(duration_minutes or 60))


def calendar_shift(minutes: int = 60, day: str = "today", **_):
    return _run(tempo.shift_events, int(minutes or 60), day or "today")


def calendar_delete(title: str = "", day: str = "today", **_):
    return _run(tempo.delete_event, title, day or "today")


FUNCTIONS = {"calendar_agenda": calendar_agenda, "calendar_next": calendar_next, "calendar_free": calendar_free,
             "calendar_add": calendar_add, "calendar_delete": calendar_delete, "calendar_shift": calendar_shift}
ACKS = {"calendar_agenda": "One moment, sir. Pulling up your calendar.",
        "calendar_free": "Let me look at your calendar, sir.",
        "calendar_next": "One moment, sir."}
ACTIONS = ["calendar_add", "calendar_delete", "calendar_shift"]
GUARDS = {
    "calendar_agenda": r"\b(calendar|schedule|agenda|plans?|meetings?|events?|appointments?|busy|doing|booked|on for)\b",
    "calendar_free": r"\b(free|opening|gap|available|slot|time for|room for)\b",
    "calendar_next": r"\b(next|now|coming up|upcoming|later)\b",
    "calendar_add": r"\b(add|schedule|book|put|create|set up|plan|block)\b",
    "calendar_delete": r"\b(delete|remove|cancel|clear|drop)\b",
    "calendar_shift": r"\b(move|push|shift|delay|postpone|bring forward)\b",
}
CONFIRM = {
    "calendar_add": lambda a: f"add {a.get('title', 'an event')} to your calendar "
                              f"{a.get('day') or 'today'}{(' at ' + a['time']) if a.get('time') else ''}",
    "calendar_delete": lambda a: f"remove {a.get('title', 'that event')} from your calendar",
    "calendar_shift": lambda a: f"move everything still ahead {a.get('day') or 'today'} "
                                f"{abs(int(a.get('minutes', 60)))} minutes {'later' if int(a.get('minutes', 60)) > 0 else 'earlier'}",
}
FILLERS = {"calendar_add": tempo.fill_args, "calendar_free": tempo.fill_args, "calendar_agenda": tempo.fill_args}
