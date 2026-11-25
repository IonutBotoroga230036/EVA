"""Tasks in conversation (v0.4): what you say to start, check, answer and steer a task, and your permissions.

    "Eva, design me a guitar stand I can 3D print"      -> task_start (asks first: it costs up to a euro)
    "How's the guitar stand going?"                     -> task_status
    (her question) "Electric, about 30 cm tall"         -> task_answer (the open question takes your words)
    (her "may I install OpenSCAD?") "yes" / "yes, always" / "no"   -> task_answer
    "Pause / resume / cancel the guitar stand"          -> task_pause / task_resume / task_cancel
    "What tasks are you working on?"                    -> task_list
    "Show me the guitar stand" / "can I see it in 3D?"  -> task_show (pictures and a 3D view in the window)
    "Always allow Python packages" / "what are my permissions?" / "forget your permissions"
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from core.agent import consent


def _m():
    from core.agent.runner import get_manager
    return get_manager()


def _ok(say: str, **data) -> dict:
    return {"result": json.dumps(data or {"ok": True}), "say": say, "exact": True}


def tool_task_start(goal: str = "", budget_euros: float = 0.0, **_):
    goal = " ".join((goal or "").split())
    if len(goal) < 8:
        return _ok("What should the task be, sir?", error="no goal")
    t = _m().start(goal, float(budget_euros or 0))
    return _ok(f"On it, sir: the {t.title}. I'll work on it in the background with Claude, ask you when a choice "
               f"matters or before I install anything, and tell you when it's done. Ask me 'how's the {t.title} "
               f"going?' any time.", task=t.id, title=t.title)


def describe_start(args: dict) -> str:
    from core.agent.runner import cfg
    budget = float(args.get("budget_euros") or 0) or float(cfg()["budget_eur"])
    return (f"start a task: {args.get('goal', '')}. I'll work on it with Claude in the background, up to "
            f"{budget:.2f} euros before I ask for more")


def _find(which: str):
    from core.agent.tasks import get_store
    return get_store().find(which)


def models_widget(t) -> dict | None:
    """The 3D models a task made, as a card: a 3D view you can turn, pictures, sizes, the write-up."""
    from core.agent.pc import PCError, get_pc
    try:
        pc = get_pc()
        files = [f["path"] for f in pc.list_files(t.id)["files"]]
    except PCError:
        return None
    stls = sorted((f for f in files if f.lower().endswith(".stl")), key=lambda f: ("assembl" not in f.lower(), f))
    models = []
    for f in stls[:6]:
        try:
            info = pc.preview(t.id, f)
        except Exception:
            continue
        models.append({"name": Path(f).name, "path": f, "png": info["png"], "size_mm": info["size_mm"]})
    if not models:
        return None
    return {"kind": "model", "title": t.title, "task": t.id, "models": models,
            "text": (t.details or t.summary or "")[:4000]}


def _open_on_pc(t) -> str:
    """Your E.V.A. folder for this task (or its work folder) in Explorer, on this PC."""
    import os
    from core.agent.tasks import get_store
    folder = Path(t.files[0]).parent if t.files else get_store().workspace(t.id)
    starter = getattr(os, "startfile", None)
    if starter is None or not folder.exists():
        return ""
    starter(str(folder))
    return str(folder)


def tool_task_show(which: str = "", on_pc: bool = False, **_):
    from core.agent.tasks import get_store
    t = _find(which)
    if t and not which:                         # "show me": the newest task that made something to show
        made = [x for x in get_store().all() if x.status == "done"]
        t = max(made, key=lambda x: x.updated) if made else t
    if not t:
        return _ok("I haven't made anything to show yet, sir.", shown=0)
    opened = _open_on_pc(t) if on_pc else ""
    w = models_widget(t)
    if not w:
        say = f"The {t.title} has no 3D models to show, sir." + (" I opened its folder." if opened else "")
        out = _ok(say, task=t.id, shown=0, opened=opened)
        if t.details or t.summary:
            out["widget"] = {"kind": "note", "title": t.title, "text": (t.details or t.summary)[:4000]}
        return out
    n = len(w["models"])
    say = (f"Here's the {t.title}, sir: {n} {'model' if n == 1 else 'models'} in the window. Drag to turn it."
           + (" I opened the folder on your PC too." if opened else ""))
    out = _ok(say, task=t.id, shown=n, opened=opened)
    out["widget"] = w
    return out


def tool_task_status(which: str = "", **_):
    t = _find(which)
    if not t:
        return _ok("I'm not working on any tasks, sir.", tasks=0)
    return _ok(_m().status_text(t), task=t.id, status=t.status)


def tool_task_answer(answer: str = "", task: str = "", **_):
    t, say = _m().answer(answer, task)
    return _ok(say, task=t.id if t else "")


def tool_task_pause(which: str = "", **_):
    t = _find(which)
    return _ok(_m().pause(t) if t else "There's no task to pause, sir.")


def tool_task_resume(which: str = "", **_):
    t = _find(which)
    return _ok(_m().resume(t) if t else "There's no task to resume, sir.")


def tool_task_cancel(which: str = "", **_):
    t = _find(which)
    return _ok(_m().cancel(t) if t else "There's no task to cancel, sir.")


def tool_task_list(**_):
    from core.agent.tasks import get_store
    tasks = get_store().all()[-8:]
    if not tasks:
        return _ok("No tasks yet, sir. Give me a bigger job, like designing something, and I'll take it on.")
    live = [t for t in tasks if t.status in ("queued", "running", "waiting", "paused")]
    words = "; ".join(f"the {t.title} ({t.status})" for t in (live or tasks[-3:]))
    return _ok(("Working on: " if live else "Recent tasks: ") + words + ", sir.",
               tasks=[{"id": t.id, "title": t.title, "status": t.status} for t in tasks])


_KIND_WORDS = [("pip_install", r"python|package|pip|librar"), ("install_program", r"program|app|software|install"),
               ("export_file", r"cop(?:y|ies)|deliver|files? out|export|documents"),
               ("run_program", r"run(?:ning)? (?:a |the )?programs?")]


def kind_from(words: str) -> str:
    for kind, rx in _KIND_WORDS:
        if re.search(rx, words or "", re.I):
            return kind
    return ""


def tool_permission_grant(what: str = "", **_):
    kind = kind_from(what)
    if not kind:
        return _ok("Which kind, sir: installing Python packages, installing programs, copying files out of a task, "
                   "or running programs on a task's files?", error="unknown kind")
    consent.grant(kind, f"you said '{what[:80]}'")
    return _ok(f"From now on I won't ask before {consent.describe(kind)}, sir. I'll still write each one in the "
               f"task's log. Say 'forget your permissions' to undo it.", kind=kind)


def describe_grant(args: dict) -> str:
    kind = kind_from(args.get("what", ""))
    return f"stop asking before {consent.describe(kind)}" if kind else "change a permission"


def tool_permission_revoke(what: str = "", **_):
    kind = kind_from(what) if what and not re.search(r"\b(all|every|permissions)\b", what, re.I) else None
    gone = consent.revoke(kind)
    if not gone:
        return _ok("There was nothing to forget, sir: I ask first for all of it.")
    return _ok("Done, sir. I'll ask first again before " + "; ".join(consent.describe(k) for k in gone) + ".")


def tool_permission_list(**_):
    return _ok(consent.summary(), standing=list(consent.standing()))


WHICH = {"which": {"type": "string", "description": "Words naming the task ('guitar stand'); empty = the latest."}}
SCHEMAS = [
    {"type": "function", "function": {
        "name": "task_start",
        "description": ("Take on a BIG job that needs several steps, research, files or programs and takes a while: "
                        "designing something (a 3D-printable part, a logo), researching and comparing, planning a "
                        "trip or a project, writing a document, building a small app. Not for one quick action."),
        "parameters": {"type": "object", "properties": {
            "goal": {"type": "string", "description": "The job in the user's words, with every detail they gave."},
            "budget_euros": {"type": "number", "description": "Only if the user names a budget."}},
            "required": ["goal"]}}},
    {"type": "function", "function": {
        "name": "task_status", "description": "How a background task is going ('how's the guitar stand going?').",
        "parameters": {"type": "object", "properties": WHICH}}},
    {"type": "function", "function": {
        "name": "task_answer",
        "description": "The user's answer to a question a background task asked (or yes/no to its request).",
        "parameters": {"type": "object", "properties": {"answer": {"type": "string"}, "task": {"type": "string"}},
                       "required": ["answer"]}}},
    {"type": "function", "function": {"name": "task_pause", "description": "Pause a background task.",
                                      "parameters": {"type": "object", "properties": WHICH}}},
    {"type": "function", "function": {"name": "task_resume", "description": "Resume a paused background task, or try a failed one again.",
                                      "parameters": {"type": "object", "properties": WHICH}}},
    {"type": "function", "function": {"name": "task_cancel", "description": "Cancel a background task.",
                                      "parameters": {"type": "object", "properties": WHICH}}},
    {"type": "function", "function": {
        "name": "task_show",
        "description": "Show what a task made: its 3D models as a 3D view and pictures in the window, its files. "
                       "For 'show me the stand', 'can I see it in 3D / before I print it', 'show me the files "
                       "rendered', 'open the files'. on_pc: also open the folder on the PC.",
        "parameters": {"type": "object", "properties": {**WHICH, "on_pc": {"type": "boolean"}}}}},
    {"type": "function", "function": {"name": "task_list", "description": "Which tasks E.V.A. is working on.",
                                      "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "permission_grant",
        "description": "A standing yes: stop asking before a kind of task action (installing Python packages, "
                       "installing programs, copying files out of a task, running programs).",
        "parameters": {"type": "object", "properties": {"what": {"type": "string"}}, "required": ["what"]}}},
    {"type": "function", "function": {
        "name": "permission_revoke", "description": "Take back standing permissions ('forget your permissions').",
        "parameters": {"type": "object", "properties": {"what": {"type": "string"}}}}},
    {"type": "function", "function": {
        "name": "permission_list", "description": "What E.V.A. may do in tasks without asking first.",
        "parameters": {"type": "object", "properties": {}}}},
]
FUNCTIONS = {"task_start": tool_task_start, "task_status": tool_task_status, "task_answer": tool_task_answer,
             "task_show": tool_task_show,
             "task_pause": tool_task_pause, "task_resume": tool_task_resume, "task_cancel": tool_task_cancel,
             "task_list": tool_task_list, "permission_grant": tool_permission_grant,
             "permission_revoke": tool_permission_revoke, "permission_list": tool_permission_list}
GUARDS = {"task_pause": r"\b(pause|hold|wait|stop)\b", "task_resume": r"\b(resume|continue|carry on|go on|unpause|try (?:it )?again|retry)\b",
          "task_cancel": r"\b(cancel|stop|abort|drop|forget)\b",
          "permission_grant": r"\b(always|allow|permission|don'?t ask|without asking|stop asking)\b",
          "permission_revoke": r"\b(forget|revoke|take back|remove|withdraw|reset)\b"}
ACTIONS = {"task_start", "task_answer", "task_pause", "task_resume", "task_cancel", "permission_grant",
           "permission_revoke"}
EXACT = set(FUNCTIONS)
CONFIRM = {"task_start": describe_start, "permission_grant": describe_grant}
