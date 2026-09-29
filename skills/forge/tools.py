"""FORGE skill: the conversation side of core/forge_engine.py."""

import json
import time

from core.budget import BudgetExceeded
from core.events.bus import get_bus
from core.forge_engine import get_forge

TOOLS = [
    {"type": "function", "function": {
        "name": "forge_build",
        "description": "Draft a new skill (an ability you don't have yet) when the user asks you to build or learn one.",
        "parameters": {"type": "object", "properties": {
            "request": {"type": "string", "description": "What the skill should do, in the user's words."}},
            "required": ["request"]}}},
    {"type": "function", "function": {
        "name": "forge_install",
        "description": "Install a drafted skill after the user approved it.",
        "parameters": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}}},
    {"type": "function", "function": {
        "name": "forge_discard",
        "description": "Throw away a drafted skill the user doesn't want.",
        "parameters": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}}},
    {"type": "function", "function": {
        "name": "forge_list",
        "description": "List drafted skills waiting for approval.",
        "parameters": {"type": "object", "properties": {}}}},
]


def _pretty(name: str) -> str:
    return name.replace("_", " ")


def _latest_ready_name() -> str:
    ready = sorted(get_forge().pending(), key=lambda p: p.created)
    return ready[-1].name if ready else ""


def forge_build(request: str = "", **_):
    """v0.2.5: the build runs in the background; she tells you when it's done (here and on Telegram)."""
    from core import telegram_bridge
    from core.brain import get_brain
    from core.forge_jobs import get_jobs
    request = " ".join((request or "").split())
    if not request:
        return {"result": json.dumps({"error": "no request"}), "say": "What should the skill do, sir?"}
    jobs = get_jobs()
    job = jobs.submit(request)
    ahead = jobs.queued_before(job)
    where = "with Claude" if get_brain().pick("forge") == "cloud" else "locally"
    also = " and on Telegram" if telegram_bridge.active() else ""
    queue = f" It's number {ahead + 1} in line." if ahead else ""
    say = (f"I'm building it in the background {where}, sir.{queue} I'll tell you here{also} when it's ready, "
           f"and you can keep talking to me meanwhile.")
    return {"result": json.dumps({"job": job.id, "status": job.status, "ahead": ahead}), "exact": True, "say": say}


def forge_status(**_):
    from core.forge_jobs import get_jobs, pretty
    jobs = get_jobs()
    run, wait = jobs.running(), jobs.waiting()
    if run:
        mins = max(1, round((time.time() - run.started) / 60))
        more = f" {len(wait)} more waiting." if wait else ""
        return {"result": json.dumps({"running": run.request, "minutes": mins, "waiting": len(wait)}), "exact": True,
                "say": f"Still building \"{run.request}\", sir, {mins} minute{'s' if mins != 1 else ''} so far"
                       f"{(': ' + run.stage) if run.stage else ''}.{more}"}
    last = jobs.latest()
    if not last:
        return {"result": json.dumps({"jobs": 0}), "exact": True, "say": "No skill builds so far, sir."}
    if last.status == "done" and last.result_status == "ready":
        say = f"The last build finished: {pretty(last.result_name)} is waiting for your approval, sir."
    elif last.status == "interrupted":
        say = f"The last build, \"{last.request}\", was interrupted by a restart, sir. Ask me again to rebuild it."
    else:
        say = last.say or f"The last build ended as {last.status}, sir."
    return {"result": json.dumps({"last": last.status, "name": last.result_name}), "exact": True, "say": say}


def forge_cancel(**_):
    from core.forge_jobs import get_jobs
    job = get_jobs().cancel()
    if not job:
        return {"result": json.dumps({"error": "nothing to cancel"}), "exact": True,
                "say": "There's no skill build running, sir."}
    if job.status == "cancelled":
        return {"result": json.dumps({"cancelled": job.id}), "exact": True,
                "say": f"Cancelled the build of \"{job.request}\", sir."}
    return {"result": json.dumps({"cancelling": job.id}), "exact": True,
            "say": "I'll stop as soon as the current step finishes, sir, and throw the result away."}


def forge_install(name: str = "", **_):
    name = (name or _latest_ready_name()).strip().lower().replace(" ", "_")
    try:
        p = get_forge().install(name)
    except Exception as e:
        return {"result": json.dumps({"error": str(e)}), "say": f"I couldn't install it, sir: {e}."}
    from skills.registry import get_registry
    get_registry().discover()                                   # hot reload: no restart needed
    get_bus().publish("skills.changed", {"installed": p.name})
    return {"result": json.dumps({"installed": p.name, "tools": p.tools}),
            "say": f"Installed, sir. {_pretty(p.name).capitalize()} is ready, you can use it right away."}


def forge_discard(name: str = "", **_):
    name = (name or _latest_ready_name()).strip().lower().replace(" ", "_")
    try:
        get_forge().discard(name)
    except Exception as e:
        return {"result": json.dumps({"error": str(e)}), "say": f"There's no draft called {name}, sir."}
    return {"result": json.dumps({"discarded": name}), "say": "Discarded, sir. Nothing was installed."}


def forge_list(**_):
    ready = get_forge().pending()
    if not ready:
        return {"result": json.dumps({"drafts": []}), "exact": True, "say": "No drafted skills are waiting, sir."}
    names = ", ".join(_pretty(p.name) for p in ready)
    return {"result": json.dumps({"drafts": [p.name for p in ready]}), "exact": True,
            "say": f"Waiting for your approval, sir: {names}."}


FUNCTIONS = {"forge_build": forge_build, "forge_install": forge_install,
             "forge_discard": forge_discard, "forge_list": forge_list,
             "forge_status": forge_status, "forge_cancel": forge_cancel}
TOOLS = TOOLS + [
    {"type": "function", "function": {"name": "forge_status", "description": "How the skill build in the background "
     "is going, or how the last one ended.", "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "forge_cancel", "description": "Cancel the skill build in the background.",
     "parameters": {"type": "object", "properties": {}}}},
]
ACKS = {}
ACTIONS = ["forge_build", "forge_install", "forge_discard", "forge_cancel"]
GUARDS = {
    "forge_build": r"\b(skills?|abilit(?:y|ies)|capabilit(?:y|ies)|forge)\b|\bbuild it\b|\b(?:learn|teach yourself)(?: how)? to\b",
    "forge_install": r"\b(install|yes|enable|activate|add it)\b",
    "forge_discard": r"\b(discard|delete|remove|drop|reject|throw)\b",
    "forge_status": r"\b(build|building|forge|skill)\b",
    "forge_cancel": r"\b(cancel|stop|abort)\b.*\b(build|building|forge|skill)\b|\b(build|forge)\b.*\b(cancel|stop|abort)\b",
}
def _build_confirm(args: dict) -> str:
    from core.brain import get_brain
    return f"draft a new skill for: {args.get('request', '')}, {get_brain().describe('code')}"


CONFIRM = {
    "forge_build": _build_confirm,              # says whether it runs on Claude or locally, and the cost
    "forge_install": "install the {name} skill",
}
