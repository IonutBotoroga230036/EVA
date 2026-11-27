"""Settings in the window (v0.7): the switches you'd otherwise say or edit in YAML.

    GET  /api/settings          -> the groups and fields with their current values, and today's spend
    POST /api/settings          {"key": "budget.daily_limit_euros", "value": 3}   (header X-EVA: 1)
    POST /api/settings/reset    {"key": ...}                                      back to the files' value

Only the fields listed here can be changed, each checked (type, range, choices). Values go to
data/settings_ui.json, layered on top of config/settings.yaml and config/settings.local.yaml: your hand-written
file is never rewritten. The brain switches use the same state as "Eva, use Claude for the mind"; test mode the
same as "test mode on". Budget limits apply at once.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from core import settings as settings_mod

FEATURE_LABELS = {"understanding": "Understanding what you mean", "mind": "The mind (thinking things through)",
                  "conversation": "Conversation (personal chat)", "agent": "Background tasks",
                  "planning": "Planning", "thinking": "Deep thinking", "forge": "Building skills (FORGE)"}
BRAIN_CHOICES = [("default", "Follow the overall mode"), ("auto", "Auto (Claude when useful)"),
                 ("cloud", "Always Claude"), ("local", "Local only")]

# key, label, kind, extra   (kind: select / number / bool / words)
FIELDS: list[tuple[str, list[tuple]]] = [
    ("Her brain", [
        ("@one.backend", "Who thinks", "select", {"choices": [
            ("local_first", "Local first, Claude when it gets stuck"), ("local", "Local only (free, nothing leaves "
                                                                             "the PC)"),
            ("cloud", "Claude (fastest, a fraction of a cent a turn)")]}),
        ("one.local_think", "Thinking steps on the local model (slower, better)", "bool", {}),
        ("one.mode", "How she decides", "select", {"choices": [("one", "One brain (v1.0)"),
                                                                ("classic", "The old routing (v0.9)")]}),
        ("risk.ask_above_eur", "Ask before one action spends more than (euros)", "number",
         {"min": 0, "max": 50, "step": 0.5}),
        ("risk.messages_ask", "Always ask before sending anything as you", "bool", {}),
        ("jarvis_test.nightly", "Run the JARVIS test every night (local, free)", "bool", {}),
    ]),
    ("Budget", [
        ("budget.daily_limit_euros", "Daily cloud limit (euros)", "number", {"min": 0.1, "max": 50, "step": 0.5}),
        ("budget.monthly_limit_euros", "Monthly cloud limit (euros)", "number", {"min": 1, "max": 500, "step": 5}),
        ("@testmode", "Test mode (caps spending at a few cents for 12 hours)", "bool", {}),
    ]),
    ("Brain", [("@brain.mode", "Overall mode", "select", {"choices": [c for c in BRAIN_CHOICES if c[0] != "default"]})]
     + [(f"@brain.{f}", label, "select", {"choices": BRAIN_CHOICES}) for f, label in FEATURE_LABELS.items()]),
    ("Mind", [
        ("mind.enabled", "Use the mind for open questions", "bool", {}),
        ("mind.effort", "How hard it thinks", "select", {"choices": [("low", "Low (fastest)"), ("medium", "Medium"),
                                                                     ("high", "High (slowest)")]}),
        ("mind.chat_effort", "How hard plain talk thinks", "select", {"choices": [("low", "Low (fastest answers)"),
                                                                              ("medium", "Medium"), ("high", "High")]}),
        ("mind.max_steps", "Tool rounds per answer", "number", {"min": 1, "max": 12, "step": 1}),
    ]),
    ("Tasks", [
        ("agent.budget_eur", "Budget per task (euros) before she asks", "number", {"min": 0.1, "max": 10, "step": 0.1}),
        ("agent.max_steps", "Steps before she asks to keep going", "number", {"min": 5, "max": 100, "step": 5}),
        ("agent.effort", "How hard tasks think", "select", {"choices": [("low", "Low"), ("medium", "Medium"),
                                                                       ("high", "High")]}),
    ]),
    ("Her browser", [
        ("browser.enabled", "She may use her own browser", "bool", {}),
        ("browser.visible", "Show her browser window on the PC", "bool", {}),
    ]),
    ("Using your computer", [
        ("computer.mode", "Computer use", "select", {"choices": [
            ("off", "Off"), ("watch", "Watch only (screenshots)"), ("ask", "Ask before each task, then act"),
            ("auto", "Act without asking (corner stop and the never-list stay)")]}),
    ]),
    ("Focus", [
        ("focus.enabled", "Focus mode during calendar events", "bool", {}),
        ("focus.min_minutes", "Only events at least this long (minutes)", "number", {"min": 5, "max": 240, "step": 5}),
        ("focus.skip_words", "Events with these words never count (comma separated)", "words", {}),
    ]),
]
_SPEC = {k: (label, kind, extra) for _, fields in FIELDS for k, label, kind, extra in fields}


def _ui() -> dict:
    try:
        d = json.loads(settings_mod.UI_PATH.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _save_ui(d: dict) -> None:
    settings_mod.UI_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = settings_mod.UI_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, indent=1), encoding="utf-8")
    tmp.replace(settings_mod.UI_PATH)
    settings_mod.get_settings.cache_clear()


def _get(d: dict, key: str) -> Any:
    for part in key.split("."):
        if not isinstance(d, dict) or part not in d:
            return None
        d = d[part]
    return d


def _defaults(key: str) -> Any:
    """Values the code falls back to when neither file sets them."""
    from core.agent.runner import DEFAULTS as agent
    from core.mind import DEFAULTS as mind
    from core.one import DEFAULTS as one
    from core.one.risk import DEFAULTS as risk
    sec, _, name = key.partition(".")
    return {"mind": mind, "agent": agent, "one": one, "risk": risk,
            "jarvis_test": {"nightly": True, "hour": 3, "backend": "local"}}.get(sec, {}).get(name, {
        "browser.enabled": True, "browser.visible": False, "computer.mode": "ask", "focus.enabled": True,
        "focus.min_minutes": 30, "focus.skip_words": [], "budget.daily_limit_euros": 1.5,
        "budget.monthly_limit_euros": 50.0}.get(key))


def value(key: str) -> Any:
    if key == "@testmode":
        from core import testmode
        return testmode.is_on()
    if key == "@one.backend":
        from core import one
        return one.backend_choice()
    if key.startswith("@brain."):
        from core.brain import get_brain
        b, f = get_brain(), key.split(".", 1)[1]
        if f == "mode":
            return b.mode()
        said = (b._state().get("features") or {}).get(f)
        return said if said in ("auto", "cloud", "local", "default") else (b._cfg()["features"].get(f) or "default")
    v = _get(settings_mod.get_settings(), key)
    return _defaults(key) if v is None else v


def snapshot() -> dict:
    ui = _ui()
    groups = []
    for title, fields in FIELDS:
        out = []
        for key, label, kind, extra in fields:
            v = value(key)
            if kind == "words" and isinstance(v, list):
                v = ", ".join(map(str, v))
            out.append({"key": key, "label": label, "kind": kind, "value": v,
                        "choices": [{"value": c, "label": lab} for c, lab in extra.get("choices", [])],
                        **{k: extra[k] for k in ("min", "max", "step") if k in extra},
                        "from_window": key.startswith("@") or _get(ui, key) is not None})
        groups.append({"title": title, "fields": out})
    try:
        from core.budget import get_budget
        spend = get_budget().today_summary()
    except Exception:
        spend = {}
    return {"groups": groups, "spend": spend}


def _check(key: str, raw: Any) -> Any:
    if key not in _SPEC:
        raise ValueError("that setting can't be changed from the window")
    _, kind, extra = _SPEC[key]
    if kind == "bool":
        if not isinstance(raw, bool):
            raise ValueError("expected on or off")
        return raw
    if kind == "number":
        v = float(raw)
        if not (extra.get("min", float("-inf")) <= v <= extra.get("max", float("inf"))):
            raise ValueError(f"between {extra.get('min')} and {extra.get('max')}")
        return int(v) if float(extra.get("step", 1)).is_integer() and v.is_integer() else round(v, 2)
    if kind == "select":
        if raw not in [c for c, _ in extra["choices"]]:
            raise ValueError("not one of the choices")
        return raw
    if kind == "words":
        items = raw if isinstance(raw, list) else str(raw or "").split(",")
        return [w.strip()[:40] for w in items if w.strip()][:30]
    raise ValueError("unknown kind")


def change(key: str, raw: Any) -> dict:
    v = _check(key, raw)
    if key == "@testmode":
        from core import testmode
        testmode.set_mode(bool(v))
    elif key == "@one.backend":
        from core import one
        one.set_backend(v)
    elif key.startswith("@brain."):
        from core.brain import get_brain
        f = key.split(".", 1)[1]
        get_brain().set_mode(v) if f == "mode" else get_brain().set_feature(f, v)
    else:
        ui = _ui()
        d = ui
        parts = key.split(".")
        for part in parts[:-1]:
            d = d.setdefault(part, {})
        d[parts[-1]] = v
        _save_ui(ui)
        _apply_live(key, v)
    return {"key": key, "value": value(key) if _SPEC[key][1] != "words" else ", ".join(value(key) or [])}


def reset(key: str) -> dict:
    if key not in _SPEC or key.startswith("@"):
        raise ValueError("that setting has no file value to go back to")
    ui = _ui()
    parts, d, stack = key.split("."), ui, []
    for part in parts[:-1]:
        if not isinstance(d.get(part), dict):
            return {"key": key, "value": value(key)}
        stack.append((d, part))
        d = d[part]
    d.pop(parts[-1], None)
    for parent, part in reversed(stack):                 # tidy empty sections
        if not parent[part]:
            parent.pop(part)
    _save_ui(ui)
    _apply_live(key, value(key))
    return {"key": key, "value": value(key)}


def _apply_live(key: str, v: Any) -> None:
    if key.startswith("budget."):
        from core.budget import get_budget
        b = get_budget()
        if key == "budget.daily_limit_euros":
            b.daily_limit = float(v)
        elif key == "budget.monthly_limit_euros":
            b.monthly_limit = float(v)
