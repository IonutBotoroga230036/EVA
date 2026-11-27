"""One brain (v1.0): one model decides every turn, with her tools, in her own words.

Before v1.0 a sentence went through regex reflexes, five understanding modes, a local decision step, guards, canned
confirmations and then maybe the mind. Each layer could swallow or misroute it (Nov 27: "Stop the task" was taken
as an answer to a waiting task; a failed search fell back to a 3B model that made up a price). Now:

    reflex (lights, volume, "stop")  ->  done, instantly
    everything else                  ->  the brain: thinks, calls tools, the risk judge decides what may just
                                         happen, the journal records every decision (and how to undo it)

The brain is the same loop with two backends (core/one/backends.py):
    local   Ollama on this PC (qwen3.5:4b by default: tools, thinking, images), free, slower
    cloud   Claude Haiku 5.5 (Sonnet when she calls think_harder)
    local_first (default)  local tries; if it gets stuck, fails a check or hands over, Claude takes that turn

one.mode: classic brings back the old routing for this version, in case something feels worse.
"""

from __future__ import annotations

from typing import Any

DEFAULTS: dict[str, Any] = {
    "mode": "one",                         # one | classic
    "backend": "local_first",              # local | cloud | local_first
    "local_model": "qwen3.5:4b",
    "local_fallback_model": "",            # empty = inference.local.decision_model
    "local_think": True,                   # thinking steps on the local model (slower, better)
    "local_tools": 22,                     # tools shown to the local model each turn (it can ask for more)
    "local_ctx": 12288,
    "local_timeout_s": 150,                # the whole local attempt; then the cloud takes over (local_first)
    "cloud_model": "claude-haiku-5-5",
    "hard_model": "claude-sonnet-5-5",
    "cloud_effort": "low",
    "max_steps": 8,
    "max_tokens": 3000,
    "history_turns": 10,
    "corrections": 1,                      # times she's asked to fix an answer that fails a check
    "reflexes": ["lights_power", "lights_set", "set_volume", "media_control", "spotify_volume", "spotify_play",
                 "phone_ring", "focus_mode", "test_mode", "keep_listening", "task_cancel", "task_pause",
                 "task_resume", "get_datetime", "phone_timer", "set_mood", "do_not_disturb"],
}

# tools the brain never sees: routing leftovers of the old way, and helpers only undo uses
HIDDEN = {"work_it_out", "set_conversation_mode", "think_deeply", "calendar_restore", "shopping_restore"}


def cfg() -> dict:
    try:
        from core.settings import get_settings
        return {**DEFAULTS, **(get_settings().get("one") or {})}
    except Exception:
        return dict(DEFAULTS)


def mode() -> str:
    m = str(cfg().get("mode") or "one").lower()
    return m if m in ("one", "classic") else "one"


def enabled() -> bool:
    return mode() == "one"


def backend_choice() -> str:
    """What he picked (settings or by voice): local, cloud or local_first."""
    try:
        from core.brain import get_brain
        said = (get_brain()._state().get("features") or {}).get("one")
        if said in ("local", "cloud", "local_first"):
            return said
    except Exception:
        pass
    b = str(cfg().get("backend") or "local_first").lower()
    return b if b in ("local", "cloud", "local_first") else "local_first"


def set_backend(choice: str) -> str:
    """'Eva, use local for everything' / the window: remembered with the other brain switches."""
    choice = {"auto": "local_first", "claude": "cloud", "online": "cloud"}.get(choice, choice)
    if choice not in ("local", "cloud", "local_first"):
        raise ValueError("local, cloud or local_first")
    from core.brain import get_brain
    b = get_brain()
    st = b._state()
    st.setdefault("features", {})["one"] = choice
    b._save(st)
    return choice
