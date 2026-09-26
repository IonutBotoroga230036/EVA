"""
Multi-step commands (v0.2.5 milestone 3).

    "lights red, play The Weeknd, Spotify at 50 and the PC at 100"
    "read Tom's last email and draft him a warm reply saying Friday works"

1. looks_multi(): a cheap check, no model. Only a sentence with two or more parts that each carry a
   command word goes further, so ordinary sentences cost nothing extra.
2. plan(): the local model splits it into standalone commands (constrained JSON), marking the ones
   that need the result of the step before ("draft him a reply" needs "read Tom's email").
3. grounded(): every planned step must come from the user's own words. A step the model invented
   is dropped, so the planner can never add an action you didn't ask for.

If the planner fails, returns one step, or returns something unusable, the turn runs exactly as
before (single command). The orchestrator executes the steps: independent ones in parallel when they
touch different things, dependent ones in order with the earlier results in view.
"""

from __future__ import annotations

import json
import re
from typing import Optional

from loguru import logger

MAX_STEPS = 6

_SEP = re.compile(r"\s*(?:[,;]|\band then\b|\bthen\b|\band also\b|\balso\b|\band\b|\bplus\b)\s*", re.I)
_CUE = re.compile(
    r"\b(play|pause|resume|skip|next|open|launch|set|turn|switch|put|add|remind|remember|note|read|draft|write|"
    r"reply|send|text|message|check|find|search|look up|show|dim|brighten|mute|unmute|start|stop|cancel|"
    r"delete|remove|create|make|schedule|book|lower|raise|increase|decrease|lights?|lamps?|spotify|volume|music|"
    r"calendar|e-?mails?|inbox|reminders?|weather|forecast|pc|computer|mood|shopping list|grocer(?:y|ies))\b", re.I)
_STOP = {"the", "a", "an", "to", "and", "of", "my", "me", "at", "in", "on", "for", "it", "is", "please", "set",
         "turn", "make", "put", "him", "her", "them", "then", "with", "that", "this", "your", "up", "down"}
_WORD = re.compile(r"[a-z0-9']+")

PLAN_SCHEMA = {
    "type": "object",
    "properties": {"steps": {"type": "array", "maxItems": MAX_STEPS, "items": {
        "type": "object",
        "properties": {"command": {"type": "string"}, "uses_previous": {"type": "boolean"}},
        "required": ["command", "uses_previous"]}}},
    "required": ["steps"],
}

PLAN_PROMPT = (
    "Split the user's message into the separate commands it contains, for a voice assistant.\n"
    "Rules:\n"
    "- One entry per distinct action or question, in the order the user said them.\n"
    "- Rewrite each as a short standalone command in the user's own words, filling in what a fragment "
    "refers to. Example: 'lights red, play The Weeknd, Spotify at 50 and the PC at 100' -> "
    "'turn the lights red', 'play The Weeknd', 'set the Spotify volume to 50', 'set the PC volume to 100'.\n"
    "- Keep details with their command: 'draft him a reply saying X, Y and Z' is ONE command.\n"
    "- uses_previous is true only when a command needs the result of the command before it, e.g. "
    "'read Tom's last email' then 'draft him a warm reply saying Friday works'.\n"
    "- Never add a command the user did not ask for. If the message is really one request, return one entry."
)


def segments(text: str) -> list[str]:
    return [p for p in (s.strip(" .!?") for s in _SEP.split(text or "")) if p]


def looks_multi(text: str) -> bool:
    """Two or more separated parts that each contain a command word. No model call."""
    if len((text or "").split()) < 4:
        return False
    parts = segments(text)
    return len(parts) >= 2 and sum(1 for p in parts if _CUE.search(p)) >= 2


def _content(text: str) -> set[str]:
    return {w for w in _WORD.findall(text.lower()) if (len(w) >= 3 or w.isdigit()) and w not in _STOP}


def grounded(step: str, original: str) -> bool:
    """At least half of the step's content words (and every number) must appear in what the user said."""
    words = _content(step)
    if not words:
        return False
    said = set(_WORD.findall(original.lower()))
    nums = {w for w in words if w.isdigit()}
    if nums - said:
        return False                                   # an invented number is never ok
    hits = sum(1 for w in words if w in said or any(w[:5] == s[:5] for s in said if len(s) >= 5))
    return hits * 2 >= len(words)


def parse_plan(raw: dict, original: str) -> Optional[list[dict]]:
    """Validated steps, or None when the turn should run as a single command."""
    steps = []
    for s in (raw or {}).get("steps", [])[:MAX_STEPS]:
        cmd = " ".join(str(s.get("command", "")).split()).strip(" .")
        if not cmd:
            continue
        if not grounded(cmd, original):
            logger.warning(f"MULTI: dropped a step not in your words: {cmd!r}")
            continue
        steps.append({"command": cmd, "uses_previous": bool(s.get("uses_previous")) and bool(steps)})
    if len(steps) < 2:
        return None
    seen, out = set(), []
    for s in steps:                                    # the same command twice runs once
        k = s["command"].lower()
        if k not in seen:
            seen.add(k)
            out.append(s)
    return out if len(out) >= 2 else None


async def plan(client, cfg: dict, text: str) -> Optional[list[dict]]:
    try:
        r = await client.post(f"{cfg['base_url']}/api/chat", json={
            "model": cfg["decision_model"], "stream": False, "format": PLAN_SCHEMA,
            "keep_alive": cfg["keep_alive"], "options": {"temperature": 0},
            "messages": [{"role": "system", "content": PLAN_PROMPT}, {"role": "user", "content": text}]})
        raw = json.loads(r.json()["message"]["content"])
    except Exception as e:
        logger.warning(f"MULTI: planner failed ({e}); running as one command")
        return None
    steps = parse_plan(raw, text)
    if steps:
        logger.info("MULTI: " + " | ".join(("-> " if s["uses_previous"] else "") + s["command"] for s in steps))
    return steps


def family(tool: str) -> str:
    """Tools of one family touch the same thing (spotify_play, spotify_volume) and run in order."""
    return tool.split("_")[0]
