"""Consent tiers (v0.4): what E.V.A. may do on her own during a task, and what waits for your yes.

    free     looking: reading the task's files, searching the web, checking whether a program is installed
    logged   reversible work inside the task's own folder: writing files, running code in the sandbox, running
             a program you already have on the task's files. Done at once, written to the task's consent log.
    ask      anything that changes your computer or the world, or costs money: installing a program or a Python
             package, copying files out of the task folder, a task going over its budget, E.V.A. tools that act
             (sending, adding to the calendar), deleting. She stops and asks.

Standing permissions (data/permissions.json) turn an "ask" into "logged" for a kind of action: "always allow
Python packages", or "yes, always" to one of her questions. Some kinds never get a standing yes: spending more
money, sending messages, deleting. "Forget your permissions" removes them all.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Optional

PATH = Path("data/permissions.json")
FREE, LOGGED, ASK = "free", "logged", "ask"

# kind -> (default tier, what you'd call it, can it get a standing yes?)
KINDS = {
    "look": (FREE, "reading and searching", False),
    "workspace": (LOGGED, "working in the task's own folder", False),
    "run_code": (LOGGED, "running code in the task's sandbox", False),
    "run_program": (LOGGED, "running a program you have on the task's files", True),
    "pip_install": (ASK, "installing Python packages into my own environment", True),
    "install_program": (ASK, "installing programs on your PC", True),
    "export_file": (ASK, "copying files out of the task folder to your output folder", True),
    "eva_action": (ASK, "using my everyday tools that change something (calendar, reminders, messages)", False),
    "spend_more": (ASK, "going over a task's budget", False),
    "delete": (ASK, "deleting files", False),
}
_lock = threading.Lock()


def _load() -> dict:
    try:
        return json.loads(PATH.read_text(encoding="utf-8")) if PATH.exists() else {}
    except (OSError, ValueError):
        return {}


def _save(d: dict) -> None:
    PATH.parent.mkdir(parents=True, exist_ok=True)
    PATH.write_text(json.dumps(d, indent=1), encoding="utf-8")


def standing() -> dict:
    """{kind: {"since": ts, "note": "..."}} of your standing yeses."""
    return {k: v for k, v in _load().items() if k in KINDS}


def grant(kind: str, note: str = "") -> bool:
    if kind not in KINDS or not KINDS[kind][2]:
        return False
    with _lock:
        d = _load()
        d[kind] = {"since": time.time(), "note": note[:200]}
        _save(d)
    return True


def revoke(kind: Optional[str] = None) -> list[str]:
    """One kind, or all of them. The kinds that were removed."""
    with _lock:
        d = _load()
        gone = [k for k in d if kind in (None, k)]
        for k in gone:
            d.pop(k, None)
        _save(d)
    return gone


# v1.1: her own Python environment and her own output folder don't ask any more (Nov 27: a yes for every copy and
# every package). agent.quiet in the settings changes the list; [] brings the questions back.
QUIET = {"pip_install", "export_file"}


def _quiet() -> set:
    try:
        from core.settings import get_settings
        q = (get_settings().get("agent") or {}).get("quiet")
        return set(q) if isinstance(q, list) else QUIET
    except Exception:
        return QUIET


def tier(kind: str, task_grants: Optional[dict] = None) -> str:
    """The tier this kind of action gets right now: a standing yes (yours, or one for this task) lowers ask to
    logged."""
    base = KINDS.get(kind, (ASK, "", False))[0]
    if base != ASK:
        return base
    if kind in _quiet():
        return LOGGED
    if KINDS.get(kind, ("", "", False))[2] and (kind in standing() or kind in (task_grants or {})):
        return LOGGED
    return ASK


def describe(kind: str) -> str:
    return KINDS.get(kind, (ASK, kind, False))[1]


def can_stand(kind: str) -> bool:
    return bool(KINDS.get(kind, ("", "", False))[2])


def summary() -> str:
    st = standing()
    if not st:
        return "You haven't given me any standing permissions, sir. I ask before installing anything, " \
               "copying files out of a task, or spending more."
    return "Standing permissions: " + "; ".join(describe(k) for k in st) + ". Everything else still asks first."
