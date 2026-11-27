"""The brain's own tools (v1.0).

Ordinary tools (in the belt, so tasks and skills can use them too):
    what_i_did(day)              her journal in words: what she changed, when, and whether it was undone
    change_setting(key, value)   any setting the window has (the daily limit, computer use...), with an undo
    schedule_later(minutes, tool, args, what)   do one of her actions later ("turn the lights off in 20 minutes")

Handled by the brain loop itself (core/one/loop.py), because they need it:
    make_skill, run_skill, skill_<name>, find_tools, undo, hand_to_cloud, think_harder
"""

from __future__ import annotations

import json
from datetime import date, timedelta


def _r(data: dict, say: str = "", **extra) -> dict:
    out = {"result": json.dumps(data, ensure_ascii=False, default=str), **extra}
    if say:
        out["say"] = say
    return out


def tool_what_i_did(day: str = "today", **_):
    from core.one import journal
    d = (day or "today").strip().lower()
    when = date.today() - timedelta(days=1) if d == "yesterday" else date.today()
    recs = list(reversed(journal.day(when.isoformat()))) if d in ("today", "yesterday") else journal.recent(40)
    return _r({"day": when.isoformat(), "changes": journal.as_text(recs, 30)})


def tool_change_setting(key: str = "", value=None, **_):
    from core import ui_settings
    key = (key or "").strip()
    try:
        old = ui_settings.value(key)
        if isinstance(value, str) and ui_settings._SPEC.get(key, ("", "", {}))[1] == "number":
            value = float(value.replace(",", ".").split()[0])
        if isinstance(value, str) and ui_settings._SPEC.get(key, ("", "", {}))[1] == "bool":
            value = value.strip().lower() in ("true", "on", "yes", "1")
        out = ui_settings.change(key, value)
    except Exception as e:
        keys = ", ".join(k for k in ui_settings._SPEC)
        return _r({"error": str(e)[:200], "keys": keys})
    return _r({"key": key, "was": old, "now": out["value"]},
              changes=[{"what": ui_settings._SPEC[key][0], "before": old, "after": out["value"]}],
              undo={"tool": "change_setting", "args": {"key": key, "value": old},
                    "what": f"set {ui_settings._SPEC[key][0].lower()} back to {old}"})


def tool_schedule_later(minutes: float = 0, tool: str = "", args: dict | None = None, what: str = "", **_):
    from core import later as later_mod
    from core.one import risk
    try:
        mins = float(minutes)
    except (TypeError, ValueError):
        mins = 0
    if mins <= 0 or not tool:
        return _r({"error": "say how many minutes from now, and which action"})
    level, why = risk.judge(tool, args or {})
    if level == "ask":
        return _r({"error": f"{tool} needs his yes at the moment it happens ({why}); offer a reminder instead"})
    item = later_mod.get_later().add(tool, dict(args or {}), mins * 60, what or tool.replace("_", " "))
    return _r({"planned": item.get("text"), "due": item.get("due"), "id": item.get("id")},
              undo={"tool": "later_cancel", "args": {"what": item.get("text", "")},
                    "what": f"cancel '{item.get('text', '')}'"})


def _settings_keys() -> str:
    try:
        from core import ui_settings
        return ", ".join(f"{k} ({v[0]})" for k, v in ui_settings._SPEC.items())[:1500]
    except Exception:
        return ""


SCHEMAS = [
    {"type": "function", "function": {
        "name": "what_i_did", "description": "What you changed and did today (or yesterday, or lately), from your "
                                             "journal: 'what did you change?', 'what did you do to my calendar?'",
        "parameters": {"type": "object", "properties": {"day": {"type": "string", "enum": ["today", "yesterday",
                                                                                          "lately"]}}}}},
    {"type": "function", "function": {
        "name": "change_setting", "description": "Change one of your settings, e.g. 'raise the daily limit to 5 "
                                                 "euros' -> budget.daily_limit_euros 5. Keys: " + _settings_keys(),
        "parameters": {"type": "object", "properties": {"key": {"type": "string"}, "value": {}},
                       "required": ["key", "value"]}}},
    {"type": "function", "function": {
        "name": "schedule_later", "description": "Do one of your actions later: minutes from now, the tool and its "
                                                 "arguments ('lights off in 20 minutes').",
        "parameters": {"type": "object", "properties": {
            "minutes": {"type": "number"}, "tool": {"type": "string"}, "args": {"type": "object"},
            "what": {"type": "string", "description": "a few words, e.g. 'turn the lights off'"}},
            "required": ["minutes", "tool"]}}},
]
FUNCTIONS = {"what_i_did": tool_what_i_did, "change_setting": tool_change_setting,
             "schedule_later": tool_schedule_later}
ACTIONS = {"change_setting", "schedule_later"}

# ---------------------------------------------------------------- the loop's own (not in the belt)
LOOP_TOOLS = [
    {"type": "function", "function": {
        "name": "make_skill",
        "description": "Write a small skill when none of your tools (alone or a few in a row) can do it. code = "
                       "Python with `def run(eva, <params with defaults>):` that uses eva.call('<tool>', **args) for "
                       "your tools and returns a short result. Only plain Python (json, re, math, datetime, "
                       "collections...); no files, processes or network except through eva.call. It is checked and "
                       "DRY-RUN with example_args: reads are real, actions are only planned. Then run_skill.",
        "parameters": {"type": "object", "properties": {
            "name": {"type": "string", "description": "lowercase_with_underscores"},
            "purpose": {"type": "string", "description": "one sentence: what it does"},
            "code": {"type": "string"},
            "parameters": {"type": "object", "description": "JSON schema of run's parameters"},
            "example_args": {"type": "object", "description": "the arguments for this request (dry run)"}},
            "required": ["name", "purpose", "code"]}}},
    {"type": "function", "function": {
        "name": "run_skill", "description": "Run a skill you made (after its dry run looked right).",
        "parameters": {"type": "object", "properties": {"name": {"type": "string"}, "args": {"type": "object"}},
                       "required": ["name"]}}},
    {"type": "function", "function": {
        "name": "find_tools", "description": "Find more of your tools for a need, e.g. 'calendar events', "
                                             "'send a message'. The ones found become available.",
        "parameters": {"type": "object", "properties": {"need": {"type": "string"}}, "required": ["need"]}}},
    {"type": "function", "function": {
        "name": "undo", "description": "Undo a change you made: the last one, or a step id from what_i_did.",
        "parameters": {"type": "object", "properties": {"step": {"type": "string", "description": "'last' or an id"}}}}},
]
HAND_TO_CLOUD = {"type": "function", "function": {
    "name": "hand_to_cloud", "description": "Hand this request to Claude when it's beyond you (hard reasoning, "
                                            "long writing, many steps, or you're unsure).",
    "parameters": {"type": "object", "properties": {"why": {"type": "string"}}}}}
THINK_HARDER = {"type": "function", "function": {
    "name": "think_harder", "description": "Switch to a stronger model for the rest of this answer: designing, "
                                           "tricky planning, debugging, careful comparisons. Not for simple things.",
    "parameters": {"type": "object", "properties": {"why": {"type": "string"}}}}}
LOOP_NAMES = {t["function"]["name"] for t in LOOP_TOOLS} | {"hand_to_cloud", "think_harder"}
