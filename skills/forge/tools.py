"""FORGE skill: the conversation side of core/forge_engine.py."""

import json

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
    forge = get_forge()
    try:
        p = forge.build(request)
    except BudgetExceeded as e:
        return {"result": json.dumps({"error": str(e)}), "exact": True,
                "say": f"That would go over your budget, sir. {e}."}
    except Exception as e:
        return {"result": json.dumps({"error": str(e)}), "exact": True, "say": f"The build failed, sir: {str(e)[:160]}."}
    cost = (f"It cost {p.cost_eur * 100:.0f} cents." if p.cost_eur >= 0.005
            else f"Built locally with {p.provider}, at no cost." if p.provider and p.provider != "Claude" else "")
    if p.status == "infeasible":
        return {"result": json.dumps({"status": p.status, "reason": p.reason}), "exact": True,
                "say": f"I can't build that safely as a skill, sir. {p.reason} {cost}".strip()}
    if p.status != "ready":
        return {"result": json.dumps({"status": p.status, "reason": p.reason}), "exact": True,
                "say": f"My draft didn't pass its own checks, sir, so I've kept it out. {cost}".strip(),
                "widget": {"kind": "note", "title": f"Draft rejected · {_pretty(p.name)}", "text": p.reason[:240]}}
    net = (f" It needs internet access to {', '.join(p.network_hosts)}." if p.network_hosts
           else " It works fully offline.")
    say = (f"I've drafted a skill called {_pretty(p.name)}. {p.summary}{net} It passed the security review and "
           f"{p.tests_passed} tests. {cost} Shall I install it, sir?").replace("  ", " ")
    return {"result": json.dumps({"status": "ready", "name": p.name, "tools": p.tools}), "exact": True, "say": say,
            "widget": {"kind": "forge", "name": _pretty(p.name), "summary": p.summary, "tools": p.tools,
                       "hosts": p.network_hosts, "tests": p.tests_passed, "review_passed": True,
                       "cost": (f"EUR {p.cost_eur:.2f}" if p.cost_eur >= 0.005 else "Free, built locally")},
            "confirm_next": {"tool": "forge_install", "args": {"name": p.name},
                             "desc": f"install the {_pretty(p.name)} skill"}}


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
             "forge_discard": forge_discard, "forge_list": forge_list}
ACKS = {"forge_build": "Drafting it now, sir. This takes about a minute."}
ACTIONS = ["forge_build", "forge_install", "forge_discard"]
GUARDS = {
    "forge_build": r"\b(skills?|abilit(?:y|ies)|capabilit(?:y|ies)|forge)\b|\bbuild it\b|\b(?:learn|teach yourself)(?: how)? to\b",
    "forge_install": r"\b(install|yes|enable|activate|add it)\b",
    "forge_discard": r"\b(discard|delete|remove|drop|reject|throw)\b",
}
def _build_confirm(args: dict) -> str:
    from core.brain import get_brain
    return f"draft a new skill for: {args.get('request', '')}, {get_brain().describe('code')}"


CONFIRM = {
    "forge_build": _build_confirm,              # says whether it runs on Claude or locally, and the cost
    "forge_install": "install the {name} skill",
}
