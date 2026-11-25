"""Tool retrieval (v0.4): when E.V.A. has many tools, understanding sees only the ones that fit the sentence.

Every tool's schema goes into the understanding call. At ~100 tools that's fine; as FORGE adds skills it gets slower
and costlier, and a long list makes picking harder. Past understanding.retrieve.above tools (150), only the best
understanding.retrieve.keep (50) are sent:
- words of the sentence (and, for follow-ups, the turn before) against each tool's name, description and
  parameter names, with simple word endings folded ("reminders" = "reminder", "playing" = "play")
- the tools used in the last few turns stay in ("turn it up" after "play jazz")
- understanding.retrieve.always: tools that must always be there (answers to questions, cancel, help)
The original order is kept. Off: understanding.retrieve.above: 0.
"""

from __future__ import annotations

import re
from typing import Optional

DEFAULTS = {"above": 150, "keep": 50, "always": ["work_it_out", "task_answer", "task_status", "keep_listening", "local_only",
                                                  "test_mode", "forge_build", "task_start"]}
_STOP = {"the", "a", "an", "to", "of", "and", "or", "in", "on", "for", "my", "me", "is", "it", "can", "you",
         "please", "what", "how", "do", "i", "eva", "hey", "with", "at", "this", "that", "be", "are", "will",
         "would", "could", "should", "your", "from", "about", "when", "use", "user", "tool", "sir"}


def cfg() -> dict:
    try:
        from core.understand.brains import cfg as ucfg
        return {**DEFAULTS, **(ucfg().get("retrieve") or {})}
    except Exception:
        return dict(DEFAULTS)


def stem(w: str) -> str:
    for end in ("ings", "ing", "ers", "er", "ies", "es", "s", "ed"):
        if len(w) > len(end) + 2 and w.endswith(end):
            return w[: -len(end)] + ("y" if end == "ies" else "")
    return w


def words(text: str) -> set[str]:
    return {stem(w) for w in re.findall(r"[a-z0-9]+", (text or "").lower().replace("_", " ")) if w not in _STOP}


def tool_words(t: dict) -> set[str]:
    f = t["function"]
    params = " ".join((f.get("parameters") or {}).get("properties", {}))
    return words(f"{f['name']} {f['name']} {f.get('description', '')} {params}")


def select(messages: list[dict], schemas: list[dict], used: Optional[list[str]] = None) -> list[dict]:
    c = cfg()
    above, keep = int(c.get("above") or 0), int(c.get("keep") or 50)
    if not above or len(schemas) <= above:
        return schemas
    turns = [m for m in messages if m.get("role") in ("user", "assistant") and m.get("content")]
    query = words(" ".join(str(m["content"]) for m in turns[-3:]))
    latest = words(next((str(m["content"]) for m in reversed(turns) if m["role"] == "user"), ""))
    always = set(c.get("always") or []) | set(used or [])
    scored = []
    for i, t in enumerate(schemas):
        name = t["function"]["name"]
        tw = tool_words(t)
        s = 2 * len(latest & tw) + len(query & tw)
        if name in always:
            s += 100
        scored.append((s, -i, name))
    best = {name for _, _, name in sorted(scored, reverse=True)[:keep]}
    return [t for t in schemas if t["function"]["name"] in best]
