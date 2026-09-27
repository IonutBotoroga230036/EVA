"""
Conversation lane (v0.2.5 milestone 4).

Tasks keep the fast lane (tool, exact short answer). When NO tool is needed and you're talking rather than
commanding ("what would make you more useful to me?", "how are you?", "should I take the Tilburg flat?"),
she answers in the conversation lane: warmer, a little longer, may ask one question back, and she knows her
own abilities and roadmap from docs/CAPABILITIES.md and docs/ROADMAP.md instead of guessing.

It never decides WHETHER a task runs: the lane is only chosen after the tool decision said "none", so a
misroute can cost a sentence of style, never an action.

On / off, three ways (whichever you used last wins):
    config/settings.local.yaml   conversation: enabled: false      (the default when nothing else is set)
    status panel                 Brain > Conversation lane: On / Off   (saved in data/conversation.json)
    voice                        "turn conversation mode off" / "on"
Delete data/conversation.json to go back to what the settings file says.

Safety kept in this lane: the claim guard still runs, nothing is stated as done or built unless it is in
the capability list, and facts are NOT learned in the background from these chats (say "remember that..."
if you want something kept).
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Optional

STATE = Path("data/conversation.json")
DOCS = Path("docs")

_REFLECTIVE = re.compile(
    r"\b(what do you think|what'?s your (?:opinion|take|view)|how do you feel|do you (?:think|like|enjoy|feel|ever)|"
    r"how are you|how'?s it going|how have you been|what would (?:you|make you)|would you rather|"
    r"what can you do|what are you (?:able|good|bad)|who are you|what are you|tell me about (?:yourself|you)|"
    r"your (?:abilities|capabilities|limits|roadmap|plans|future)|what are your|are you (?:happy|ok|okay|alive|real)|"
    r"should i\b|what should i|help me (?:think|decide|figure)|advice|what do you know about me|"
    r"let'?s talk|can we talk|i feel\b|i'?m feeling|i'?ve been thinking|be honest|why do you|"
    r"more useful|improve yourself|what'?s next for you)\b", re.I)
_TASKISH = re.compile(r"\b(play|pause|turn|set|open|remind|add|draft|send|delete|remove|schedule|search|find|"
                      r"lights?|volume|spotify|email|calendar|weather|timer|alarm|note)\b", re.I)


def _settings() -> dict:
    from core.settings import get_settings
    return get_settings().get("conversation", {}) or {}


def enabled() -> bool:
    try:
        st = json.loads(STATE.read_text(encoding="utf-8"))
        if isinstance(st.get("enabled"), bool):
            return st["enabled"]
    except Exception:
        pass
    return bool(_settings().get("enabled", True))


def set_enabled(on: bool) -> bool:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps({"enabled": bool(on), "changed": time.time()}), encoding="utf-8")
    return bool(on)


def sticky_turns() -> int:
    return int(_settings().get("sticky_turns", 2))


def wants(text: str) -> bool:
    """Talking, not commanding. Checked only after the tool decision already said 'no tool'."""
    t = (text or "").strip()
    if _REFLECTIVE.search(t):
        return True
    words = t.split()
    return len(words) >= 16 and "?" in t and not _TASKISH.search(t)      # a long, open question


# ---------------------------------------------------------------- self-knowledge
_cache: dict = {"key": None, "text": ""}


def _read(name: str) -> str:
    try:
        return (DOCS / name).read_text(encoding="utf-8")
    except OSError:
        return ""


def self_knowledge(limit: int = 2200) -> str:
    """What she can do (built) and what's planned, from the docs. Cached until a doc changes."""
    key = tuple((DOCS / n).stat().st_mtime if (DOCS / n).exists() else 0 for n in ("CAPABILITIES.md", "ROADMAP.md"))
    if _cache["key"] == key:
        return _cache["text"]
    caps, road = _read("CAPABILITIES.md"), _read("ROADMAP.md")
    sections = re.findall(r"^## (.+)$", caps, re.M)
    built = [s.split("(")[0].strip() for s in sections if s.strip()]
    rows = re.findall(r"^\| *(\d+) *\| *\*\*(.+?)\*\*(?: \(([^)]*)\))? *\|", road, re.M)
    done = [f"{name}" for n, name, st in rows if st and "built" in st.lower()]
    planned = [f"{name}" for n, name, st in rows if not st]
    text = ("What you can do today (from your user guide): " + "; ".join(built) + ".\n"
            + ("Recently built, being tested: " + "; ".join(done) + ".\n" if done else "")
            + ("Planned, NOT built yet: " + "; ".join(planned) + "." if planned else ""))
    _cache.update(key=key, text=text[:limit])
    return _cache["text"]


STYLE = (
    "You are in a conversation, not executing a task.\n"
    "- Be warm, direct and honest. No flattery, no sugar-coating, no pretending to have feelings you don't have; "
    "you may say what you notice, prefer, or find interesting as an AI.\n"
    "- {length}\n"
    "- You may ask ONE short follow-up question at the end when it genuinely moves the conversation forward.\n"
    "- About yourself, use only the lists below. Say clearly what is built and what is only planned. "
    "Never say you did, set, sent, or changed anything in this conversation.\n"
    "- Use what you know about the user when it helps; don't recite it.\n"
    "- Plain spoken English: no lists, no markdown, no emojis.\n"
)


def system_prompt(persona: str, facts: list, voice: bool = True) -> str:
    cfg = _settings()
    n = int(cfg.get("voice_sentences", 4) if voice else cfg.get("max_sentences", 6))
    length = f"Answer in at most {n} sentences; shorter is fine."
    known = "\n".join(f"- {f.get('text', f) if isinstance(f, dict) else f}" for f in (facts or [])[:8])
    return (persona.strip() + "\n\n" + STYLE.format(length=length) + "\n" + self_knowledge()
            + (f"\n\nWhat you know about the user:\n{known}" if known else ""))


def follow_window_ms(answer: str) -> Optional[int]:
    """She asked a question: give the user longer to think before the mic closes."""
    return int(_settings().get("question_window_ms", 12000)) if (answer or "").rstrip().endswith("?") else None
