"""The risk judge (v1.0): what may just happen, and what needs his yes.

    free   reading, looking up, showing                         -> runs, nobody is asked
    tell   changes that can be undone or are his own space      -> runs; the journal keeps how to undo it
    ask    sending as him, deleting, installing, spending more  -> not run; she asks in her own words,
           than risk.ask_above_eur (2 euros) in one action,        his yes runs it
           anything that can't be undone

Known tools have their level here (with argument rules: a task's budget, a skill's planned actions). A tool she
doesn't know (an MCP tool, a skill she made) is judged once by Jev when it has a key, or by the local model, and the
verdict is kept in data/risk_judgments.json. When nothing can judge it: ask.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Optional

from loguru import logger

DEFAULTS = {"ask_above_eur": 2.0, "messages_ask": True, "always_ask": [], "never_ask": [], "bulk_ask_over": 15}
STORE = Path("data/risk_judgments.json")

ASK = {   # can't be undone, speaks for him, or goes outside his PC and accounts
    "message_reply", "email_send", "calendar_delete", "email_delete_drafts", "forget_memory", "forget_recent_facts",
    "project_delete", "shopping_clear_all", "place_forget", "forge_discard", "install_app", "permission_grant",
    "permission_revoke", "meeting_record", "location_tracking", "add_instruction",
}
TELL = {  # changes in his own space, mostly undoable
    "calendar_add", "calendar_move", "calendar_shift", "shopping_add", "shopping_remove", "shopping_tick",
    "shopping_clear_done", "set_reminder", "cancel_reminder", "snooze_reminder", "schedule_routine", "cancel_routine",
    "remember_fact", "obsidian_quick_note", "obsidian_write", "email_draft", "email_sender_pref", "task_start",
    "task_answer", "task_cancel", "task_pause", "task_resume", "forge_build", "forge_install", "forge_cancel",
    "project_start", "project_note", "project_add_part", "project_answer", "project_switch", "place_remember",
    "place_reminder", "later_cancel", "meeting_todos_add", "test_mode", "focus_mode", "do_not_disturb", "keep_listening",
    "open_app", "open_website", "open_file", "open_shown", "open_settings", "spotify_play", "spotify_queue",
    "spotify_volume", "media_control", "set_volume", "lights_power", "lights_set", "set_mood", "create_mood",
    "phone_ring", "phone_alarm", "phone_timer", "phone_volume", "phone_stay_dark", "phone_ask_unlock",
    "send_to_phone", "set_brain_mode", "use_computer", "change_setting", "undo", "make_skill", "run_skill",
    "schedule_later", "memoir_start", "memoir_stop", "memoir_pause", "memoir_add",
    "install_package", "code_change", "code_test", "code_reset", "place_action", "place_save",   # v1.2
}
_SENDS = re.compile(r"(?:^|_)(?:send|reply|post|publish|tweet|dm|message|mail|email|whatsapp|telegram|sms|invite|share)"
                    r"(?:_|$)", re.I)
_DELETES = re.compile(r"(?:^|_)(?:delete|remove|erase|wipe|destroy|drop|purge|clear|uninstall|format)(?:_|$)", re.I)
_PAYS = re.compile(r"(?:^|_)(?:buy|order|pay|purchase|checkout|transfer|book|subscribe)(?:_|$)", re.I)


def cfg() -> dict:
    try:
        from core.settings import get_settings
        return {**DEFAULTS, **(get_settings().get("risk") or {})}
    except Exception:
        return dict(DEFAULTS)


def _load() -> dict:
    try:
        d = json.loads(STORE.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _keep(key: str, verdict: dict) -> None:
    d = _load()
    d[key] = verdict
    try:
        STORE.parent.mkdir(parents=True, exist_ok=True)
        STORE.write_text(json.dumps(d, indent=1), encoding="utf-8")
    except OSError:
        pass


def _money(args: dict) -> float:
    for k in ("budget_euros", "budget_eur", "amount_eur", "price_eur", "euros", "amount"):
        try:
            if args.get(k) not in (None, ""):
                return float(args[k])
        except (TypeError, ValueError):
            pass
    return 0.0


def judge(tool: str, args: Optional[dict] = None, belt=None, approved: Optional[set] = None) -> tuple[str, str]:
    """-> (level, why). level: free | tell | ask."""
    args = args or {}
    c = cfg()
    if approved and tool in approved:
        return "tell", "he just said yes to it"
    if tool in set(c.get("always_ask") or []):
        return "ask", "you set it to always ask"
    limit = float(c.get("ask_above_eur") or 2.0)
    spend = _money(args)
    if spend > limit:
        return "ask", f"it would spend up to {spend:.2f} euros (more than {limit:.2f} in one go)"
    if tool in set(c.get("never_ask") or []) and tool not in ASK:
        return "tell", "you set it to never ask"
    if tool in ASK:
        if tool in ("message_reply", "email_send") or _SENDS.search(tool):
            return "ask", "it sends something as you"
        if _DELETES.search(tool) or tool.startswith("forget"):
            return "ask", "it deletes something"
        if tool in ("install_app",):
            return "ask", "it installs software"
        return "ask", "it can't be undone"
    if tool == "use_computer":
        try:
            from core import computer
            if computer.mode() == "auto":
                return "tell", "computer use is set to act without asking"
        except Exception:
            pass
        return "ask", "it uses your mouse and keyboard"
    if tool == "run_my_script":                 # v1.1: approved once per script; a changed file asks again
        from core.one import files
        if files.approved(args.get("path", "")):
            return "tell", "a script of yours that he already let run"
        return "ask", "it runs a script on your PC for the first time"
    if tool == "adopt_skill":
        return "ask", "it makes a script on your PC one of my skills"
    if tool == "code_apply":                    # v1.2: her real code changes only with his yes
        return "ask", "it changes my own code"
    if tool == "run_skill":
        from core.one import made
        return made.risk_of(args.get("name", ""), args.get("args") or {})
    if tool.startswith("skill_"):
        from core.one import made
        return made.risk_of(tool[len("skill_"):], args)
    if tool in TELL:
        return "tell", "it changes something you can undo"
    if belt is not None and belt.has(tool) and not belt.is_action(tool) and not belt.is_mcp(tool):
        return "free", "it only reads"
    if belt is not None and belt.has(tool) and not belt.is_mcp(tool):
        # one of her own actions this list doesn't name yet: its name says enough
        if c.get("messages_ask", True) and _SENDS.search(tool):
            return "ask", "it sends something as you"
        if _DELETES.search(tool):
            return "ask", "it deletes something"
        if _PAYS.search(tool):
            return "ask", "it may spend money"
        return "tell", "it changes something"
    return unknown(tool, (belt.by_name.get(tool, {}).get("function", {}).get("description", "") if belt else ""))


def unknown(tool: str, description: str, code: str = "") -> tuple[str, str]:
    """A tool from outside (MCP) or one she made: judged once, then remembered."""
    if _SENDS.search(tool) and cfg().get("messages_ask", True):
        return "ask", "it sends something as you"
    if _DELETES.search(tool) or _PAYS.search(tool):
        return "ask", "it deletes or spends"
    key = f"{tool}:{hashlib.sha1((description + code).encode()).hexdigest()[:12]}"
    kept = _load().get(key)
    if kept:
        return kept["level"], kept["why"]
    verdict = _ask_judge(tool, description, code)
    if verdict:
        _keep(key, verdict)
        return verdict["level"], verdict["why"]
    return "ask", "I don't know this tool yet"


_JUDGE_PROMPT = ("You judge the risk of an AI assistant's tool for its owner. Levels: free = only reads or shows; "
                 "tell = changes the owner's own things in a way that can be undone; ask = sends anything as the "
                 "owner, deletes, spends money, installs, or can't be undone. Answer JSON only: "
                 "{\"level\": \"free|tell|ask\", \"why\": \"a few words\"}")


def _ask_judge(tool: str, description: str, code: str) -> Optional[dict]:
    text = f"Tool: {tool}\nWhat it does: {description[:600]}\n" + (f"Code:\n{code[:3000]}" if code else "")
    try:                                              # Jev first: it's made for picking one of a few labels
        from core.understand.brains import cloud_allowed, get_jev
        jev = get_jev() if cloud_allowed() else None
        if jev is not None and jev.available():
            import asyncio
            choice, conf, _ = asyncio.run(jev.choose(text, "How risky is this tool for its owner?", {
                "free": "only reads or shows information", "tell": "changes the owner's own things, undoable",
                "ask": "sends as the owner, deletes, spends money, installs, or can't be undone"}))
            if choice in ("free", "tell", "ask") and conf >= 0.6:
                return {"level": choice, "why": f"Jev judged it ({conf:.0%} sure)", "by": "jev"}
    except Exception as e:
        logger.debug(f"RISK: Jev couldn't judge {tool} ({e})")
    try:
        import httpx
        from core.settings import local_cfg
        from core.one import cfg as one_cfg
        loc = local_cfg()
        r = httpx.post(f"{loc['base_url']}/api/chat", timeout=40, json={
            "model": one_cfg().get("local_model") or loc["decision_model"], "stream": False, "think": False,
            "format": {"type": "object", "properties": {"level": {"type": "string", "enum": ["free", "tell", "ask"]},
                                                        "why": {"type": "string"}}, "required": ["level", "why"]},
            "messages": [{"role": "system", "content": _JUDGE_PROMPT}, {"role": "user", "content": text}]})
        d = json.loads(r.json()["message"]["content"])
        if d.get("level") in ("free", "tell", "ask"):
            return {"level": d["level"], "why": str(d.get("why") or "")[:120], "by": "local"}
    except Exception as e:
        logger.debug(f"RISK: the local model couldn't judge {tool} ({e})")
    return None


def worst(levels: list[str]) -> str:
    order = {"free": 0, "tell": 1, "ask": 2}
    return max(levels or ["free"], key=lambda x: order.get(x, 2))
