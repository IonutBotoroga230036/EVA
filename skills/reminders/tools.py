"""Reminders skill: the conversation side of core/oracle.py."""

import json
from datetime import datetime, timedelta

from core.oracle import briefing, describe_routine, get_oracle, parse_routine, when_words
from core.timeparse import parse_when


def _fill_reminder(args: dict, text: str) -> dict:
    """The model may invent a time ('in 2 minutes'). Keep it only if the user's words contain a time."""
    out = dict(args)
    if out.get("when") and parse_when(text or "") is None and parse_when(out["when"]) is not None \
            and not any(w in (text or "").lower() for w in ("minute", "hour", "tomorrow", "tonight", " at ", " in ")):
        out.pop("when")
    return out

TOOLS = [
    {"type": "function", "function": {
        "name": "set_reminder",
        "description": "Set a reminder. when: 'in 20 minutes', 'at 18:00', 'tomorrow at 9', 'tonight'.",
        "parameters": {"type": "object", "properties": {
            "text": {"type": "string", "description": "What to remind about, in the user's words."},
            "when": {"type": "string"}}, "required": ["text"]}}},
    {"type": "function", "function": {
        "name": "list_reminders", "description": "List upcoming reminders.",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "cancel_reminder", "description": "Cancel an upcoming reminder.",
        "parameters": {"type": "object", "properties": {"query": {"type": "string"}}}}},
    {"type": "function", "function": {
        "name": "snooze_reminder", "description": "Remind again about the reminder that was just spoken.",
        "parameters": {"type": "object", "properties": {"minutes": {"type": "integer"}}}}},
    {"type": "function", "function": {
        "name": "do_not_disturb",
        "description": "Hold proactive messages for a while (minutes), or end it with minutes=0.",
        "parameters": {"type": "object", "properties": {"minutes": {"type": "integer"}}}}},
    {"type": "function", "function": {
        "name": "schedule_routine",
        "description": "Set up something recurring: 'every weekday at 8 brief me', 'every sunday at 19:00 remind me to plan the week'.",
        "parameters": {"type": "object", "properties": {
            "request": {"type": "string", "description": "The whole request in the user's words."}},
            "required": ["request"]}}},
    {"type": "function", "function": {
        "name": "list_routines", "description": "List recurring routines.",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "cancel_routine", "description": "Stop a recurring routine.",
        "parameters": {"type": "object", "properties": {"query": {"type": "string"}}}}},
    {"type": "function", "function": {
        "name": "morning_briefing", "description": "Brief the user: time, weather, calendar, email, reminders.",
        "parameters": {"type": "object", "properties": {}}}},
]


def set_reminder(text: str = "", when: str = "", **_):
    text = (text or "").strip()
    if not text:
        return {"result": json.dumps({"error": "nothing to remind"}), "say": "What should I remind you about, sir?"}
    due = parse_when(when or text)
    if not due:
        return {"result": json.dumps({"error": "no time"}), "say": f"When should I remind you to {text}, sir?"}
    if due <= datetime.now():
        return {"result": json.dumps({"error": "in the past"}), "say": "That time has already passed, sir."}
    r = get_oracle().store.add(text, due)
    return {"result": json.dumps({"id": r["id"], "due": due.isoformat()}),
            "widget": {"kind": "note", "title": f"Reminder · {due:%a %H:%M}", "text": text},
            "say": f"I'll remind you {when_words(due)} to {text}, sir."}


def list_reminders(**_):
    items = get_oracle().store.open()
    if not items:
        return {"result": json.dumps({"reminders": []}), "say": "You have no upcoming reminders, sir.", "exact": True}
    words = [f"{when_words(datetime.fromtimestamp(r['due']))}, {r['text']}" for r in items[:5]]
    return {"result": json.dumps({"reminders": [r["text"] for r in items]}), "exact": True,
            "say": f"You have {len(items)} reminder{'s' if len(items) != 1 else ''}, sir: " + "; ".join(words) + "."}


def cancel_reminder(query: str = "", **_):
    r = get_oracle().store.cancel(query)
    if not r:
        return {"result": json.dumps({"error": "not found"}), "say": "I couldn't find that reminder, sir."}
    return {"result": json.dumps({"cancelled": r["text"]}), "say": f"Cancelled the reminder to {r['text']}, sir."}


def snooze_reminder(minutes: int = 10, **_):
    o = get_oracle()
    if not o.last_reminder:
        return {"result": json.dumps({"error": "nothing to snooze"}), "say": "There's nothing to snooze, sir."}
    minutes = int(minutes or 10)
    o.store.add(o.last_reminder["text"], datetime.now() + timedelta(minutes=minutes))
    return {"result": json.dumps({"snoozed": minutes}), "say": f"I'll remind you again in {minutes} minutes, sir."}


def do_not_disturb(minutes: int = 60, **_):
    o = get_oracle()
    if not minutes:
        o.set_dnd(None)
        return {"result": json.dumps({"dnd": False}), "say": "I'm back, sir. I'll speak up again when something comes up."}
    o.set_dnd(minutes)
    until = datetime.now() + timedelta(minutes=minutes)
    return {"result": json.dumps({"dnd_until": until.isoformat()}),
            "say": f"Understood, sir. I'll stay quiet until {until:%H:%M} and keep your reminders for you."}


def schedule_routine(request: str = "", **_):
    r = parse_routine(request)
    if not r:
        return {"result": json.dumps({"error": "couldn't parse"}),
                "say": "I didn't catch the days and the time, sir. For example: every weekday at 8, brief me."}
    saved = get_oracle().routines.add(r)
    return {"result": json.dumps(saved), "say": f"Done, sir. I'll give you {describe_routine(saved)}."}


def list_routines(**_):
    items = get_oracle().routines.all()
    if not items:
        return {"result": json.dumps({"routines": []}), "say": "You have no routines set up, sir.", "exact": True}
    return {"result": json.dumps({"routines": items}), "exact": True,
            "say": "Your routines, sir: " + "; ".join(describe_routine(r) for r in items) + "."}


def cancel_routine(query: str = "", **_):
    r = get_oracle().routines.cancel(query)
    if not r:
        return {"result": json.dumps({"error": "not found"}), "say": "I couldn't find that routine, sir."}
    return {"result": json.dumps({"cancelled": r["id"]}), "say": f"Stopped {describe_routine(r)}, sir."}


def morning_briefing(**_):
    text, widget = briefing()
    return {"result": json.dumps({"briefing": text}), "say": text, "widget": widget, "exact": True}


FUNCTIONS = {"set_reminder": set_reminder, "list_reminders": list_reminders, "cancel_reminder": cancel_reminder,
             "snooze_reminder": snooze_reminder, "do_not_disturb": do_not_disturb, "morning_briefing": morning_briefing,
             "schedule_routine": schedule_routine, "list_routines": list_routines, "cancel_routine": cancel_routine}
ACKS = {"morning_briefing": None}
FILLERS = {"set_reminder": _fill_reminder}
ACTIONS = ["set_reminder", "cancel_reminder", "snooze_reminder", "do_not_disturb", "schedule_routine", "cancel_routine"]
GUARDS = {"set_reminder": r"\b(remind|reminder|remember to|don'?t let me forget)\b",
          "cancel_reminder": r"\b(cancel|delete|remove|forget)\b.*\breminder|\breminder\b",
          "snooze_reminder": r"\b(snooze|again|later|remind me again)\b",
          "do_not_disturb": r"\b(disturb|quiet|silence|leave me|i'?m back|focus)\b",
          "schedule_routine": r"\b(every|each|daily|weekdays?|weekends?)\b",
          "cancel_routine": r"\b(stop|cancel|remove|delete)\b"}
