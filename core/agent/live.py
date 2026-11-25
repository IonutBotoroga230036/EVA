"""The workbench (v0.5): what a task is doing, live, for the window. Like EDITH showing Peter the design as it forms.

Every step of a task becomes an event: what she's doing now ("Writing stand.py", "Looking at assembly.stl"), the
plan as it ticks off, the code she writes, the output it prints, each version of the model, what she says
between steps. The window keeps a stage open with all of it (and full screen for a second monitor). Nothing here
is spoken; her spoken updates stay rare (tell_user, questions, the result).

    emit(task, "doing", label="Writing stand.py", tool="write_file")
    -> PULSE "agent.live" {"task": id, "event": {...}} -> every window ("task_live")
    state(task_id) -> everything so far, for a window that opens mid-task
"""

from __future__ import annotations

import threading
import time
from collections import deque
from typing import Optional
from urllib.parse import urlparse

MAX_EVENTS = 80
MAX_CODE = 12000
_lock = threading.RLock()
_states: dict[str, dict] = {}


def _blank(task) -> dict:
    return {"task": task.id, "title": task.title, "goal": task.goal, "status": task.status, "plan": list(task.plan),
            "now": "", "events": deque(maxlen=MAX_EVENTS), "code": None, "output": None, "model": None, "screen": None,
            "models": [], "cost_eur": task.cost_eur, "steps": task.steps, "updated": time.time()}


def label(name: str, args: dict) -> str:
    """'write_file', {'path': 'stand.py'} -> 'Writing stand.py'."""
    a = args or {}
    host = urlparse(str(a.get("url") or "")).hostname or "a page"
    names = a.get("paths") or ([a["path"]] if a.get("path") else [])
    return {
        "update_plan": "Planning",
        "mark_step": "Ticking off a step",
        "ask_user": "Asking you",
        "tell_user": str(a.get("message") or "Telling you how it's going")[:120],
        "list_files": "Looking through the folder",
        "read_file": f"Reading {a.get('path', 'a file')}",
        "write_file": f"Writing {a.get('path', 'a file')}",
        "run_python": "Running the code",
        "install_python_packages": f"Installing {', '.join(map(str, a.get('packages') or [])) or 'packages'}",
        "check_program": f"Checking for {a.get('name', 'a program')}",
        "install_program": f"Installing {a.get('name') or a.get('winget_id') or 'a program'}",
        "run_program": f"Running {a.get('program', 'a program')}",
        "web_search": f"Searching: {a.get('query', '')}"[:120],
        "fetch_page": f"Reading {host}",
        "find_places": f"Looking for {a.get('what', 'places')} near you",
        "list_eva_tools": "Looking through my tools",
        "use_eva_tool": f"Using {a.get('tool', 'a tool')}",
        "preview_model": f"Looking at {a.get('path', 'the model')}",
        "deliver_file": f"Delivering {', '.join(map(str, names)) or 'the files'}",
        "finish": "Wrapping up",
    }.get(name, name.replace("_", " ").capitalize())


def emit(task, kind: str, **data) -> dict:
    """Record one event for the task and tell the windows. Never raises: the workbench is a view, not the work."""
    ev = {"ts": time.time(), "kind": kind, **data}
    try:
        with _lock:
            st = _states.get(task.id) or _blank(task)
            _states[task.id] = st
            st.update(title=task.title, status=task.status, plan=list(task.plan), cost_eur=round(task.cost_eur, 4),
                      steps=task.steps, updated=ev["ts"])
            if kind == "doing":
                st["now"] = data.get("label", "")
            elif kind == "code":
                st["code"] = {"path": data.get("path", ""), "text": str(data.get("text") or "")[:MAX_CODE]}
                ev["text"] = st["code"]["text"]
            elif kind == "output":
                st["output"] = {"text": str(data.get("text") or "")[-MAX_CODE:], "ok": data.get("ok", True)}
                ev["text"] = st["output"]["text"]
            elif kind == "model":
                st["model"] = {"path": data.get("path", ""), "png": data.get("png", ""),
                               "size_mm": data.get("size_mm")}
                st["models"] = [m for m in st["models"] if m["path"] != st["model"]["path"]][-5:] + [st["model"]]
            elif kind == "screen":                         # v0.7: what she sees (her browser, your screen)
                st["screen"] = {"png": data.get("png", ""), "where": str(data.get("where") or "")[:160],
                                "ts": ev.get("ts", time.time())}
            elif kind == "status":
                st["now"] = {"done": "Done", "waiting": "Waiting for you", "paused": "Paused",
                             "failed": "Stopped on an error", "cancelled": "Cancelled"}.get(task.status, st["now"])
            if kind in ("doing", "did", "say", "status", "model", "plan", "output"):
                st["events"].append({k: v for k, v in ev.items() if k != "text" or kind in ("say",)})
        ev["state"] = {k: st[k] for k in ("title", "status", "plan", "now", "cost_eur", "steps")}
        from core.events.bus import get_bus
        get_bus().publish("agent.live", {"task": task.id, "event": ev})
    except Exception:
        pass
    return ev


def state(task_id: str) -> Optional[dict]:
    """Everything so far, for a window that opens mid-task (or after a restart: rebuilt from the task)."""
    with _lock:
        st = _states.get(task_id)
        if st is not None:
            out = dict(st)
            out["events"] = list(st["events"])
            return out
    try:
        from core.agent.tasks import get_store
        t = get_store().get(task_id)
    except Exception:
        t = None
    if t is None:
        return None
    out = _blank(t)
    out["events"] = [{"ts": x["ts"], "kind": "did", "label": x["what"]} for x in t.log[-30:]]
    out["now"] = {"done": "Done", "waiting": "Waiting for you"}.get(t.status, "")
    return out


def recent(limit: int = 5) -> list[dict]:
    """The tasks a window should offer: running and waiting ones first, then the last finished."""
    try:
        from core.agent.tasks import get_store
        tasks = sorted(get_store().all(), key=lambda t: (t.status in ("running", "queued", "waiting"), t.updated),
                       reverse=True)
    except Exception:
        return []
    return [{"task": t.id, "title": t.title, "status": t.status, "updated": t.updated} for t in tasks[:limit]]


def reset() -> None:
    with _lock:
        _states.clear()
