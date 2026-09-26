"""
HybridOrchestrator: E.V.A.'s brain (v0.2).

Per turn:
  0. PENDING CONFIRMATION. If the last turn asked "shall I go ahead?", a yes runs
     the held action, a no cancels it, anything else drops it and continues.
  1. FAST PATH for unambiguous commands (time, volume, media, open X, remember,
     forget, notes, screen). No LLM, and no embedding call either.
  2. Otherwise embed the message ONCE; the vector drives CORTEX recall and SKILL.md
     matching. Small talk and capability questions skip tools entirely.
  3. CONSTRAINED DECISION over a flat schema built from every loaded tool: built-ins,
     skills, and MCP servers. "unavailable" is an explicit choice -> honest answer.
  4. Before a tool runs: INTENT GUARD (the user's words must plausibly ask for that
     action), argument hygiene (an argument equal to a tool name is discarded),
     ARGUMENT REPAIR for missing required fields, repeat and dedup guards, and the
     CONFIRMATION GATE for tools that change things (MCP non-read-only, bulk deletes).
  5. Tools run in worker threads (MCP tools on the event loop), so the spoken ack
     goes out BEFORE the tool runs and audio keeps streaming while it works.
  6. ANSWER. If the turn used only actions that returned an exact "say" line, those
     lines are spoken verbatim with no LLM (instant, and never misreported).
     Otherwise the model phrases the answer; a DENIAL GUARD replaces "I can't see your
     screen" with the real observation when a tool just succeeded.
  7. Log both turns to CORTEX; extract facts in the background.

Events: ack / token / widget / final (unchanged).
"""

from __future__ import annotations

import asyncio
import json
import re
import time
import uuid
from pathlib import Path
from typing import AsyncIterator, Callable

import httpx
import yaml
from loguru import logger

from core.events.bus import get_bus
from core.mcp_client import MCPManager, get_mcp
from core.memory.cortex import Cortex, get_cortex
from core.memory.extractor import extract_and_store
from core.prompt_builder import build_answer_system, build_decision_system
from core.security.audit import audit
from core.settings import get_settings, local_cfg
from core.tools_native import ACTION_TOOLS as BUILTIN_ACTIONS
from core.tools_native import ARG_FILLERS as BUILTIN_FILLERS
from core.tools_native import EXACT_TOOLS as BUILTIN_EXACT
from core.tools_native import GUARDS as BUILTIN_GUARDS
from core.tools_native import REGISTRY as BUILTIN_FUNCS
from core.tools_native import TOOL_SCHEMAS as BUILTIN_SCHEMAS
from core.tools_native import ack_for as builtin_ack
from core.tools_native import execute_tool as builtin_execute
from core.tools_native import snip
from skills.registry import SkillRegistry, get_registry

MAX_TOOL_ITERS = 3
MAX_HISTORY = 12
CONFIRM_TTL_S = 120

STYLE_RULES = (
    "RESPONSE RULES:\n"
    "- Reply in one or two short sentences. Every word earns its place.\n"
    "- Give ONLY the final answer. Never narrate your steps or which tool you used.\n"
    "- Use the real data provided. Never invent facts, times, weather, notes, or memories.\n"
    "- If a tool result shows you did something or looked at something, you DID. Never say you can't.\n"
    "- Use remembered facts only when they genuinely help; don't recite them unprompted.\n"
    "- For weather, always say the place and the time the numbers are for, use the numbers "
    "exactly as given, and if the data says a time was assumed, say that time.\n"
    "- Only offer abilities you actually have (listed below). If asked for something you can't do, say so and "
    "offer to build it as a new skill. Never say you did something unless a tool result shows it.\n"
    "- Address the user as 'sir'."
)
# She claims an action happened ("has been added", "I've sent") but no action tool succeeded this turn
_CLAIM = re.compile(r"\b(?:has|have|had) been (?:added|scheduled|created|booked|sent|deleted|removed|cancel+ed|set|"
                    r"saved|installed|moved|updated)\b|\bi(?:'ve| have| just)? (?:added|scheduled|created|booked|sent|"
                    r"deleted|removed|cancel+ed|set up|saved|installed|moved|updated)\b|^\s*yes,? (?:it|that|an event)"
                    r"[^.]*\b(?:added|scheduled|sent|done)\b|^\s*(?:now )?playing\b|^\s*(?:i'?m |i am )?opening\b", re.I)
NOT_DONE = "No, sir, I haven't done that. Nothing was changed. Tell me exactly what you'd like and I'll do it."
MISSING_SKILL = ("I don't have a skill for that yet, sir. Once FORGE is live I can build "
                 "one, with your approval.")
MISSING_SKILL_FORGE = "I don't have a skill for that yet, sir. Say 'build it' and I'll draft one for your approval."
# "unavailable" is only believed when the request actually names an ability we lack
_ABILITY_HINT = re.compile(
    r"\b(calendar|schedule|meeting|appointment|email|e-mail|mail|inbox|message|text|sms|whatsapp|call|phone|"
    r"light|lights|lamp|thermostat|heating|alarm|remind|reminder|timer|order|buy|book|reserve|pay|install|"
    r"download|print|3d|convert|translate|record|photo|picture|camera|upload|send|post|tweet|contact)\b", re.I)
_CAPABILITY_Q = re.compile(r"what (?:else )?can you (?:do|help)|what (?:else )?are you (?:able|capable)"
                           r"|your (?:capabilities|skills|features|abilities)|what do you do\b", re.I)
_SMALLTALK_Q = re.compile(          # questions about her: small talk at any length
    r"^\s*(?:and |so |well |okay |ok |yeah )?(?:how are you|how(?:'s| is) it going|how do you (?:feel|like)|"
    r"do you (?:like|enjoy|feel|think|want|love|prefer)|are you (?:ok|okay|happy|sad|alive|conscious|there)|"
    r"what do you think|what would you like|would you (?:like|want) to|who are you|what(?:'s| is) your name|"
    r"(?:it'?s|it is|that'?s|this is) (?:so |really |very |actually |pretty |super )*"
    r"(?:cool|nice|great|amazing|awesome|impressive|good|funny|interesting|crazy|wild))\b", re.I)
_INTERJECTION = re.compile(         # "thanks", "great" ... only when that's (nearly) the whole message
    r"^\s*(?:thank(?:s| you)|good (?:job|work)|well done|nice|cool|great|awesome|perfect|"
    r"that'?s (?:better|right|good|great|perfect)|alright|all right|okay|ok)\b", re.I)


def is_small_talk(text: str) -> bool:
    return bool(_SMALLTALK_Q.search(text)) or (bool(_INTERJECTION.search(text)) and len(text.split()) <= 4)
_DENIAL = re.compile(r"\b(?:i (?:can ?not|can't|am unable to|do not have|don't have)|i'?m (?:unable|not able)|"
                     r"as an ai)\b.{0,40}\b(?:see|access|view|look|open|do|help|read)\b", re.I)
_YES = re.compile(r"^\s*(?:yes|yeah|yep|yup|sure|do it|go ahead|go on|confirm(?:ed)?|ok(?:ay)?|please do|"
                  r"affirmative|correct|absolutely|of course)\b", re.I)
_NO = re.compile(r"^\s*(?:no|nope|nah|cancel|don'?t|stop|never ?mind|abort|forget it|leave it)\b", re.I)
_WAKE_PREFIX = re.compile(r"^\s*(?:hey\s+)?(?:eva\b|e\.v\.a\.?)[\s,:!.-]*", re.I)


_NUM = re.compile(r"(?<![\w.])\d{1,3}(?:[,.\s]\d{3})+(?:[.,]\d+)?|\d+(?:[.,]\d+)?")


def _to_float(tok: str) -> float | None:
    t = tok.replace(" ", "")
    if "," in t and "." in t:                     # the later separator is the decimal one
        t = t.replace(",", "") if t.rfind(".") > t.rfind(",") else t.replace(".", "").replace(",", ".")
    elif "," in t:
        t = t.replace(",", "") if re.fullmatch(r"\d{1,3}(,\d{3})+", t) else t.replace(",", ".")
    elif re.fullmatch(r"\d{1,3}(\.\d{3}){2,}", t):
        t = t.replace(".", "")
    try:
        return float(t)
    except ValueError:
        return None


def ungrounded_numbers(answer: str, data: str) -> list[str]:
    """Significant numbers in the answer that appear nowhere in the tool data (rounding allowed).
    Small numbers, years, and times are ignored; they are rarely what a model invents."""
    have = [v for v in (_to_float(m.group(0)) for m in _NUM.finditer(data)) if v is not None]
    bad = []
    for m in _NUM.finditer(answer):
        v = _to_float(m.group(0))
        if v is None or v < 100 or (1900 <= v <= 2100 and v.is_integer()):
            continue
        if not any(abs(v - d) <= max(0.006 * abs(d), 0.5) for d in have):
            bad.append(m.group(0))
    return bad


def load_persona(name: str = "eva") -> str:
    try:
        return yaml.safe_load(Path(f"personas/{name}.yaml").read_text(encoding="utf-8"))["personality"]
    except Exception:
        return "You are E.V.A., a concise JARVIS-style assistant. Address the user as 'sir'."


# ============================================================ tool belt
class ToolBelt:
    """One view over built-in, skill, and MCP tools: schemas, guards, safety, execution."""

    def __init__(self, registry: SkillRegistry | None = None, mcp: MCPManager | None = None):
        self.registry, self.mcp = registry, mcp
        self.skill_funcs: dict[str, Callable] = registry.functions() if registry else {}
        self.skill_acks: dict[str, str] = registry.acks() if registry else {}
        self.guards: dict[str, str] = {**BUILTIN_GUARDS, **(registry.guards() if registry else {})}
        self.confirm_text: dict[str, str] = registry.confirmations() if registry else {}
        self.actions: set[str] = set(BUILTIN_ACTIONS) | (registry.actions() if registry else set())
        self.exact: set[str] = set(BUILTIN_EXACT)
        self.fillers: dict[str, Callable] = {**BUILTIN_FILLERS, **(registry.fillers() if registry else {})}
        schemas = list(BUILTIN_SCHEMAS)
        taken = {t["function"]["name"] for t in schemas}
        # built-ins first, then skills, then MCP: nothing can shadow a trusted tool
        for extra in ((registry.tool_schemas() if registry else []), (mcp.tool_schemas() if mcp else [])):
            for t in extra:
                if t["function"]["name"] not in taken:
                    schemas.append(t)
                    taken.add(t["function"]["name"])
        if mcp:
            self.actions |= mcp.actions()
        self.schemas = schemas
        self.by_name = {t["function"]["name"]: t for t in schemas}

    def has(self, name: str) -> bool:
        return name in self.by_name

    def names(self) -> list[str]:
        return list(self.by_name)

    def is_action(self, name: str) -> bool:
        return name in self.actions

    def is_mcp(self, name: str) -> bool:
        return bool(self.mcp) and self.mcp.has(name)

    def required(self, name: str) -> list[str]:
        return list(self.by_name[name]["function"].get("parameters", {}).get("required", []))

    def fill(self, name: str, args: dict, text: str) -> dict:
        fn = self.fillers.get(name)
        try:
            return fn(args, text) if fn else args
        except Exception as e:
            logger.warning(f"argument filler for {name} failed: {e}")
            return args

    def guard_ok(self, name: str, text: str) -> bool:
        rx = self.guards.get(name)
        return True if not rx else bool(re.search(rx, text, re.I))

    def needs_confirm(self, name: str) -> bool:
        return name in self.confirm_text or (self.is_mcp(name) and self.mcp.needs_confirm(name))

    def describe(self, name: str, args: dict) -> str:
        if name in self.confirm_text:
            text = self.confirm_text[name]
            try:
                return text(args or {}) if callable(text) else text.format(**{"minutes": 15, **(args or {})})
            except Exception:
                return text if isinstance(text, str) else f"run {name}"
        if self.is_mcp(name):
            return self.mcp.describe(name, args)
        return f"run {name}"

    def list_text(self) -> str:
        return "\n".join(f"- {n}: {t['function']['description']}" for n, t in self.by_name.items())

    def ack(self, name: str) -> str | None:
        if name in BUILTIN_FUNCS:
            return builtin_ack(name)
        if self.is_mcp(name):
            return "One moment, sir."
        return self.skill_acks.get(name)

    def _execute_skill(self, name: str, args: dict) -> dict:
        fn = self.skill_funcs.get(name)
        if fn is None:
            return {"result": json.dumps({"missing_skill": name}), "missing": True}
        last = None
        for attempt in range(2):
            try:
                out = fn(**(args or {}))
                if '"error"' in (out.get("result") or ""):
                    logger.warning(f"TOOL {name}({args}) returned an error: {out.get('result')}")
                else:
                    logger.info(f"TOOL {name}({args}) ok -> {snip(out.get('result'))}")
                return out
            except Exception as e:
                last = e
                logger.warning(f"TOOL {name} attempt {attempt + 1} failed: {e}")
        return {"result": json.dumps({"error": str(last)}), "failed": True}

    def execute(self, name: str, args: dict) -> dict:
        """Synchronous path for built-in and skill tools (tests, scripts)."""
        audit.log("tool_call", "orchestrator", {"tool": name, "args": args})
        if name in BUILTIN_FUNCS:
            return builtin_execute(name, args)
        return self._execute_skill(name, args)

    async def aexecute(self, name: str, args: dict) -> dict:
        """Turn path: MCP on the loop, everything else in a worker thread."""
        if self.is_mcp(name):
            audit.log("tool_call", "mcp", {"tool": name, "args": args})
            out = await self.mcp.call(name, args)
            level = "warning" if '"error"' in out["result"] else "info"
            getattr(logger, level)(f"TOOL {name}({args}) -> {snip(out['result'])}")
            return out
        return await asyncio.to_thread(self.execute, name, args)


def build_decision_schema(schemas: list[dict]) -> dict:
    """Flat union of every tool's parameters (flat on purpose: small models
    struggle with nested schemas). Enums survive only if no other tool reuses
    the same parameter name with a different meaning."""
    props: dict[str, dict] = {}
    for t in schemas:
        for pname, spec in t["function"].get("parameters", {}).get("properties", {}).items():
            if pname == "tool":
                continue
            if pname not in props:
                entry = {"type": spec.get("type", "string")}
                if "enum" in spec:
                    entry["enum"] = list(spec["enum"])
                props[pname] = entry
            elif props[pname].get("enum") != spec.get("enum"):
                props[pname].pop("enum", None)
    names = [t["function"]["name"] for t in schemas]
    return {"type": "object",
            "properties": {"tool": {"type": "string", "enum": names + ["none", "unavailable"]}, **props},
            "required": ["tool"]}


def tool_arg_schema(schema: dict) -> dict:
    """Schema containing ONLY one tool's parameters, all its required ones enforced."""
    params = schema["function"].get("parameters", {})
    return {"type": "object", "properties": dict(params.get("properties", {})),
            "required": list(params.get("required", []))}


def missing_required(schema: dict, args: dict) -> list[str]:
    req = schema["function"].get("parameters", {}).get("required", [])
    return [r for r in req if args.get(r) in (None, "")]


def args_for(schema: dict, decision: dict, tool_names: list[str] | None = None) -> dict:
    """Keep only the parameters the chosen tool declares, dropping empty values and
    values that are just a tool name (the 3B sometimes writes the tool into a field)."""
    allowed = schema["function"].get("parameters", {}).get("properties", {})
    names = {n.lower() for n in (tool_names or [])}
    return {k: v for k, v in decision.items()
            if k in allowed and v not in ("", None)
            and not (isinstance(v, str) and v.strip().lower() in names)}


# ============================================================ fast path
def fast_path(text: str, belt: ToolBelt) -> tuple[str, dict] | None:
    t = _WAKE_PREFIX.sub("", text).strip()
    polite = r"(?:(?:i want you to|i need you to|can you|could you|would you|please|and|also|now|don't forget|i)\s+)*"

    m = re.search(r"(?:set |turn |change )?(?:the )?volume (?:to |at )?(\d{1,3})\s*%?", t, re.I)
    if m and belt.has("set_volume"):
        return "set_volume", {"level": int(m.group(1))}
    if belt.has("set_volume") and re.search(r"\b(max|full|maximum) volume\b", t, re.I):
        return "set_volume", {"level": 100}

    if belt.has("forget_recent_facts") and re.search(
            r"\b(?:delete|forget|remove|erase)\b.{0,40}\b(?:just|recently)\s+"
            r"(?:remembered|learned|saved|stored|noted)", t, re.I):
        return "forget_recent_facts", {"minutes": 15}
    if belt.has("schedule_routine") and re.match(polite + r"(?:every|each)\s+\S+", t, re.I) and \
            re.search(r"\b(brief|remind|tell|read|check|give|summari[sz]e)\b", t, re.I):
        return "schedule_routine", {"request": t}
    if belt.has("list_routines") and re.search(r"\b(what are|list|show) (?:my )?routines\b", t, re.I):
        return "list_routines", {}
    m = re.search(r"\b(?:send|text|message) me(?: a message| a text)?(?: on| via| through| to)? (?:telegram|my phone)"
                  r"(?:[:,]?\s*(?:saying|that says|that|with)?\s*(.*))?$", t, re.I) or \
        re.search(r"\bsend (?:this |that )?to my phone[:,]?\s*(?:saying|that)?\s*(.*)$", t, re.I)
    if m and belt.has("send_to_phone"):
        return "send_to_phone", {"text": (m.group(1) or "").strip().rstrip("?.!")}
    m = re.match(polite + r"remind me (in .+?|at .+?|tomorrow(?: at [^ ]+)?|tonight|this (?:evening|afternoon)) to (.+)$", t, re.I)
    if m and belt.has("set_reminder"):
        return "set_reminder", {"text": m.group(2).strip().rstrip("?.!"), "when": m.group(1).strip()}
    m = re.match(polite + r"(?:remind me|remember) to (.+?)\s+((?:in|at|on|tomorrow|tonight|this|next)\b.*)$", t, re.I)
    if m and belt.has("set_reminder"):
        return "set_reminder", {"text": m.group(1).strip(), "when": m.group(2).strip().rstrip("?.!")}
    if belt.has("snooze_reminder") and re.match(r"^\s*snooze(?: it| that)?(?: for (\d+) minutes?)?", t, re.I):
        m = re.match(r"^\s*snooze(?: it| that)?(?: for (\d+) minutes?)?", t, re.I)
        return "snooze_reminder", {"minutes": int(m.group(1) or 10)}
    if belt.has("do_not_disturb"):
        if re.search(r"\b(i'?m back|you can (?:talk|speak) again|end do not disturb|disturb me again)\b", t, re.I):
            return "do_not_disturb", {"minutes": 0}
        m = re.search(r"\b(?:do not disturb|don'?t disturb me|quiet mode|leave me alone|let me focus)\b"
                      r"(?:.*?(?:for|next)\s+(\d+|an?|one|two)\s*(hours?|minutes?|mins?))?", t, re.I)
        if m:
            qty = {"a": 1, "an": 1, "one": 1, "two": 2}.get((m.group(1) or "").lower(), None)
            qty = qty if qty is not None else int(m.group(1)) if m.group(1) else 60
            mins = qty * 60 if (m.group(2) or "").startswith("hour") else (qty if m.group(1) else 60)
            return "do_not_disturb", {"minutes": mins}
    if belt.has("list_reminders") and re.search(r"\b(what are|list|show) (?:my )?reminders\b|any reminders", t, re.I):
        return "list_reminders", {}
    if belt.has("morning_briefing") and re.match(r"^\s*(?:good morning|morning briefing|brief me|daily briefing|"
                                                 r"what'?s my day(?: look)?(?: like)?)\b", t, re.I):
        return "morning_briefing", {}
    m = re.match(r"^(?:please )?(?:don'?t (?:let me )?forget to|remind me to)\s+(.+)$", t, re.I)
    if m and belt.has("set_reminder"):
        return "set_reminder", {"text": m.group(1).strip().rstrip("?.!")}       # no time given: she asks when
    m = re.match(polite + r"remember(?: that)?\s+(.{4,})$", t, re.I)
    if m and belt.has("remember_fact") and not re.match(r"(to|when|what|who|where|if|how)\b", m.group(1), re.I):
        return "remember_fact", {"text": m.group(1).strip().rstrip("?.!")}
    m = re.match(r"(?:from now on|going forward)[,\s]+(.{4,})$", t, re.I)
    if m and belt.has("add_instruction"):
        return "add_instruction", {"text": m.group(1).strip()}
    m = re.match(polite + r"(?:forget|delete|remove)(?: the fact| that| about)?\s+(.{4,})$", t, re.I)
    vague = ("about it", "it", "that", "this", "that one", "all of it", "everything")
    if m and belt.has("forget_memory") and m.group(1).lower().strip(" .!?") not in vague \
            and not re.search(r"\bnote\b", m.group(1), re.I):
        return "forget_memory", {"query": m.group(1).strip().rstrip("?.!")}
    m = re.search(r"what (?:did|have) i (?:tell|told|say|said to) you about (.{2,})$"
                  r"|do you remember (?:anything )?about (.{2,})$", t, re.I)
    if m and belt.has("recall_memory"):
        return "recall_memory", {"query": (m.group(1) or m.group(2)).strip().rstrip("?.!")}
    if belt.has("recall_memory") and re.search(r"what do you (?:know|remember) about me", t, re.I):
        return "recall_memory", {"query": ""}

    m = re.match(polite + r"(?:make|create|start|write|add)\s+(?:a |an |new )*(project|people|person|idea)\s+note"
                          r"\s+(?:about|on|for|called|titled)\s+(.{2,})$", t, re.I)
    if m and belt.has("obsidian_write"):
        folder = {"project": "Projects", "people": "People", "person": "People", "idea": "Ideas"}[m.group(1).lower()]
        return "obsidian_write", {"title": m.group(2).strip().rstrip("?.!"), "content": "", "folder": folder}
    m = re.match(polite + r"(?:note that|make a note(?: that)?|take a note(?: that)?|add a note(?: that)?|"
                          r"write (?:a |me a )?note(?: that)?|write down(?: that)?|jot down(?: that)?)[:,]?\s+(.{3,})$",
                 t, re.I)
    if m and belt.has("obsidian_quick_note"):
        return "obsidian_quick_note", {"text": m.group(1).strip()}
    m = re.search(r"(?:search|check|look (?:in|through)) my notes (?:for|about|on) (.{2,})$"
                  r"|what (?:did|have) i (?:note|noted|written|write|jotted)(?: down)? (?:about|on) (.{2,})$", t, re.I)
    if m and belt.has("obsidian_search"):
        return "obsidian_search", {"query": (m.group(1) or m.group(2)).strip().rstrip("?.!")}

    m = re.match(polite + r"(?:think (?:hard|deeply|carefully|properly|it through|this through)|deep ?think|"
                          r"ask claude|use claude)(?: about| on| to| whether)?[:,]?\s+(.{5,})$", t, re.I)
    if m and belt.has("think_deeply"):
        return "think_deeply", {"question": m.group(1).strip()}
    m = re.match(polite + r"(?:i want you to |i'd like you to )?(?:build|make|create|write|develop) (?:a |an |yourself a |"
                          r"me a |your own )?(?:new )?(?:skill|ability|tool|capability)(?: that| to| for| which| so you can)?"
                          r"\s+(.{5,})$", t, re.I) or \
        re.match(polite + r"(?:learn|teach yourself)(?: how)? to\s+(.{5,})$", t, re.I)
    if m and belt.has("forge_build"):
        return "forge_build", {"request": m.group(1).strip()}
    if belt.has("forge_list") and re.search(r"\b(pending|drafted|waiting) skills?\b|skills? (?:waiting|pending)", t, re.I):
        return "forge_list", {}
    m = re.search(r"\b(?:switch|go|change|set|use|stay)(?: back)?(?: to| on)?(?: the)?\s+(local|offline|cloud|online|claude|auto|automatic)"
                  r"(?: mode| models?)?\b|\b(local|cloud|auto) mode\b", t, re.I)
    if m and belt.has("set_brain_mode"):
        word = (m.group(1) or m.group(2)).lower()
        mode = {"offline": "local", "online": "cloud", "claude": "cloud", "automatic": "auto"}.get(word, word)
        return "set_brain_mode", {"mode": mode}
    if belt.has("budget_status") and re.search(r"\b(budget|how much (?:have you|did you|did we) spen[dt]|api costs?)\b", t, re.I):
        return "budget_status", {}

    m = re.search(r"\b(?:push|move|shift|delay|postpone)\s+(?:everything|all(?: of)?(?: my)?(?: events| meetings| appointments)?|"
                  r"my (?:schedule|calendar|day)|the rest of (?:my|the) day)(?: back| forward)?\s+(?:by\s+)?"
                  r"(an?|one|two|three|half an?|\d+)\s*(hours?|minutes?|mins?)(?:\s+(later|earlier|back|forward))?", t, re.I)
    if m and belt.has("calendar_shift"):
        qty = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "half a": 0.5, "half an": 0.5}.get(m.group(1).lower())
        qty = qty if qty is not None else int(m.group(1))
        mins = int(qty * 60) if m.group(2).lower().startswith("hour") else int(qty)
        return "calendar_shift", {"minutes": -mins if (m.group(3) or "").lower() in ("earlier", "forward") else mins,
                                  "day": "today"}
    m = re.search(r"\b(?:price|value|worth) of (bitcoin|btc|ethereum|eth|solana|dogecoin|cardano|xrp)\b|"
                  r"\b(bitcoin|btc|ethereum|eth|solana|dogecoin)\b (?:price|at|worth)", t, re.I)
    if m and belt.has("crypto_price"):
        cur = "USD" if re.search(r"\b(dollars?|usd)\b", t, re.I) else "EUR"
        return "crypto_price", {"coin": (m.group(1) or m.group(2)).lower(), "currency": cur}
    m = re.search(r"([\d][\d.,]*)\s*(dollars?|usd|\$|euros?|eur|€|pounds?|gbp|£|lei|ron)\s+(?:in|to|into)\s+"
                  r"(euros?|eur|dollars?|usd|pounds?|gbp|lei|ron)\b", t, re.I)
    if m and belt.has("convert_currency"):
        return "convert_currency", {"amount": float(m.group(1).replace(",", "")), "from_currency": m.group(2),
                                    "to_currency": m.group(3)}
    if belt.has("spotify_now_playing") and re.search(r"what(?:'s| is) (?:this song|playing|this track)|what song is this|"
                                                     r"who (?:sings|is singing) this", t, re.I):
        return "spotify_now_playing", {}
    m = re.match(polite + r"(?:add|put)\s+(.+?)\s+(?:to|in|on)\s+(?:the\s+)?queue$|^queue\s+(?:up\s+)?(.+)$", t, re.I)
    if m and belt.has("spotify_queue"):
        return "spotify_queue", {"what": (m.group(1) or m.group(2)).strip()}
    if belt.has("spotify_play") and re.match(r"^\s*(?:play|resume|continue)(?: the music| it| playing)?[.!?]?\s*$", t, re.I):
        return "spotify_play", {"what": ""}                         # bare "play": resume, don't pick liked songs
    m = re.match(r"^(?:and |now )?(?:(?:play|put|move) )?(?:it |the music |this )?(?:to|on)\s+(?:my\s+|the\s+)?"
                 r"(phone|mobile|laptop|computer|pc|desktop|speaker|tv)[.!?]?$", t, re.I)
    if m and belt.has("spotify_play"):
        return "spotify_play", {"what": "", "device": m.group(1).lower()}
    m = re.match(polite + r"(?:play|put on)(?: me)?\s+(.+?)\s+on\s+(?:my\s+|the\s+)?(phone|mobile|laptop|computer|pc|desktop|speaker|tv)[.!?]?$",
                 t, re.I)
    if m and belt.has("spotify_play"):
        return "spotify_play", {"what": m.group(1).strip(), "device": m.group(2).lower()}
    m = re.match(polite + r"(?:play|put on)(?: me)?(?: some| a bit of)?\s+(.{2,})$", t, re.I)
    if m and belt.has("spotify_play") and not re.search(r"\b(game|chess|video|movie|film|youtube|role|part|piano|guitar)\b",
                                                         m.group(1), re.I):
        return "spotify_play", {"what": m.group(1).strip().rstrip("?.!")}
    # lights and moods
    m = re.search(r"\bi(?:'m| am) home\b(?:.*?\bi (?:feel|am feeling|'m feeling)\s+(?:like\s+)?(\w+(?:\s\w+)?))?|"
                  r"^(?:eva,?\s*)?i (?:feel|am feeling|'m feeling)\s+(?:like\s+)?(\w+(?:\s\w+)?)[.!]?$|"
                  r"\b(?:set|change|switch) (?:the )?mood (?:to\s+)?(\w+(?:\s\w+)?)|\b(\w+) mood(?: please)?[.!]?$", t, re.I)
    if m and belt.has("set_mood") and not re.search(r"\b(create|make|new|save|add)\b", t, re.I):
        from core.lights import find_mood
        name = (next((g for g in m.groups() if g), None) or "home").strip().lower()
        explicit = bool(re.search(r"\b(mood|i'?m home|i am home)\b", t, re.I))
        if name not in ("my", "the", "a", "your", "this", "good", "bad", "what") and (explicit or find_mood(name)):
            return "set_mood", {"name": name}                # "I feel tired" stays a conversation
    if belt.has("list_moods") and re.search(r"\b(what|which|list|show)\b.*\bmoods\b", t, re.I):
        return "list_moods", {}
    m = re.search(r"\b(?:turn|switch)\s+(on|off)\s+(?:the\s+)?(?:(\w+)\s+)?(?:lights?|leds?|strips?)\b|"
                  r"\b(?:lights?|leds?|strips?)\s+(on|off)\b|\b(?:turn|switch)\s+(?:the\s+)?(?:(\w+)\s+)?"
                  r"(?:lights?|leds?|strips?)\s+(on|off)\b", t, re.I)
    if m and belt.has("lights_power"):
        state = (m.group(1) or m.group(3) or m.group(6)).lower()
        which = (m.group(2) or m.group(5) or "").lower()
        which = "" if which in ("the", "all", "my") else which
        return "lights_power", {"on": state == "on", **({"which": which} if which else {})}
    if belt.has("lights_set") and re.search(r"\b(lights?|leds?|strips?)\b", t, re.I) and \
            re.search(r"\b(make|set|turn|change|put|dim|brighten|go)\b|\bto\s+\d|%|percent", t, re.I):
        from core.lights import parse_brightness, parse_color
        c, b = parse_color(t), parse_brightness(t)
        if c or b:
            return "lights_set", {**({"color": c["name"]} if c else {}), **({"brightness": b} if b else {})}
    if belt.has("calendar_next") and re.search(
            r"what(?:'s| is) (?:coming )?(?:up )?next|my next (?:meeting|event|appointment|thing|call)|"
            r"what do i have next|what(?:'s| is) coming up\b", t, re.I):
        return "calendar_next", {}
    if belt.has("calendar_agenda") and re.search(
            r"what(?:'s| is) (?:on )?my (?:calendar|schedule|agenda)|what am i doing\b|"
            r"what do i have (?:on|planned)|do i have (?:anything|any meetings|any plans|plans)\b|my agenda\b", t, re.I):
        from core.weather import extract_when
        week = re.search(r"\b(next week|this week|the week)\b", t, re.I)
        return "calendar_agenda", {"day": week.group(1) if week else (extract_when(t)[0] or "today")}
    if belt.has("calendar_free") and re.search(
            r"\b(?:any|an) (?:opening|gap|free (?:time|slot))|when am i free|am i free\b|do i have time", t, re.I):
        from core.weather import extract_when
        d, h = extract_when(t)
        day = d or ""
        if h and h.lower() not in day.lower():
            day = f"{day} {h}".strip()
        return "calendar_free", {"day": day or "today"}
    if belt.has("email_important") and re.search(
            r"\b(?:anything|something|any) (?:interesting|important|urgent|worth)\b.{0,20}\b(?:e-?mails?|mail|inbox)\b|"
            r"\b(?:important|interesting|urgent) (?:e-?mails?|mail)\b|what(?:'s| is) important in my (?:e-?mail|inbox)", t, re.I):
        return "email_important", {}
    m = re.search(r"\b(?:e-?mails?|mail) (?:from|about) (.+?) (?:are|is) (important|not important|spam|noise)\b", t, re.I)
    if m and belt.has("email_sender_pref"):
        return "email_sender_pref", {"who": m.group(1).strip(), "important": m.group(2).lower() == "important"}
    m = re.search(r"\b(?:don'?t|do not|never) (?:tell|bother|notify) me (?:about|with) (?:e-?mails? (?:from|about) )?(.+?)(?: e-?mails?)?[.!]?$", t, re.I)
    if m and belt.has("email_sender_pref"):
        return "email_sender_pref", {"who": m.group(1).strip(), "important": False}
    if belt.has("email_unread") and re.search(
            r"\b(?:check|read|any|new|unread)(?: new| unread)? (?:my )?(?:e-?mails?|mail|inbox)\b|"
            r"do i have (?:any )?(?:new |unread )?e-?mails?|what(?:'s| is) in my inbox", t, re.I):
        return "email_unread", {}

    if belt.has("see_screen") and re.search(
            r"\b(what'?s on|look at|read|check|describe|see) (?:my |the |this )?screen\b"
            r"|what am i looking at", t, re.I):
        return "see_screen", {"question": t}

    m = re.match(polite + r"(?:open|launch|start|pull up|go to)\s+(?:the |my )?(.{2,40}?)[.!?]?$", t, re.I)
    if m:
        from core.tools_native import _APPS, _SITES
        target = re.sub(r"\s+(website|site|page|app|application)$", "", m.group(1).strip().lower())
        if target in _APPS and belt.has("open_app"):          # installed apps win over websites
            return "open_app", {"app": target}
        if (target in _SITES or re.match(r"^[a-z0-9-]+\.[a-z]{2,}$", target)) and belt.has("open_website"):
            return "open_website", {"site": target}

    m = re.match(r"^(?:what'?s|what is|how much is|calculate|compute)?\s*"
                 r"((?:[\d.,]+\s*%\s*of\s*[\d.,]+)|(?:square root of\s*[\d.]+)|"
                 r"(?:[-(]*\s*[\d.,]+\s*(?:[-+*/x×÷^]|\*\*|plus|minus|times|divided by)\s*[-\d.,() +*/x×÷^]*))\s*[?=.]*$", t, re.I)
    if m and belt.has("calculate"):
        return "calculate", {"expression": m.group(1).strip()}

    simple = [
        (r"what time is it|what'?s the time|^\s*time\s*\??$", "get_datetime", {}),
        (r"\bvolume up\b|\blouder\b|\bturn it up\b", "media_control", {"action": "volup"}),
        (r"\bvolume down\b|\bquieter\b|\bturn it down\b", "media_control", {"action": "voldown"}),
        (r"^\s*(mute|unmute)\b", "media_control", {"action": "mute"}),
        (r"^\s*(pause|resume)\b", "media_control", {"action": "playpause"}),
        (r"\b(stop|pause) (the |this )?(music|song|playback)\b|\bstop playing\b", "media_control",
         {"action": "playpause"}),
        (r"\bnext (track|song)\b|^\s*skip\b", "media_control", {"action": "next"}),
        (r"\b(previous|last) (track|song)\b", "media_control", {"action": "previous"}),
    ]
    for rx, tool, args in simple:
        if belt.has(tool) and re.search(rx, t, re.I):
            return tool, args
    return None


# ============================================================ orchestrator
class HybridOrchestrator:
    def __init__(self, persona: str = "eva", session_id: str | None = None,
                 cortex: Cortex | None = None, registry: SkillRegistry | None = None,
                 mcp: MCPManager | None = None):
        self.cfg = local_cfg()
        self.mem_cfg = get_settings().get("memory", {})
        self.persona = load_persona(persona)
        self.session_id = session_id or uuid.uuid4().hex[:12]
        self.cortex = cortex or get_cortex()
        self.registry = registry or get_registry()
        self.belt = ToolBelt(self.registry, mcp if mcp is not None else get_mcp())
        self.schema = build_decision_schema(self.belt.schemas)
        self.bus = get_bus()
        self.pending: dict | None = None
        self.last_tools: set[str] = set()          # tools used last turn: follow-ups pass the intent guard
        self.last_missing: str | None = None       # what FORGE would build if the user says "build it"
        self._stale = False
        self._bg: set[asyncio.Task] = set()
        self.bus.subscribe("skills.changed", lambda _e: setattr(self, "_stale", True))
        hours = float(self.mem_cfg.get("continuity_hours", 24))
        self.history: list[dict] = self.cortex.recent_turns(limit=6, within_hours=hours)
        if self.history:
            logger.info(f"CORTEX: resumed {len(self.history)} recent turns")

    def refresh_tools(self) -> None:
        """Pick up skills FORGE installed (hot reload, no restart)."""
        self.belt = ToolBelt(self.registry, self.belt.mcp)
        self.schema = build_decision_schema(self.belt.schemas)
        self._stale = False
        logger.info(f"tools refreshed: {len(self.belt.schemas)} available")

    def _missing_answer(self, user_input: str) -> str:
        self.last_missing = user_input
        forge_ready = self.belt.has("forge_build")
        return MISSING_SKILL_FORGE if forge_ready else MISSING_SKILL

    # ------------------------------------------------------------ llm calls
    async def _decide(self, client, skill_bodies, gathered) -> dict:
        system = build_decision_system(self.belt.list_text(), skill_bodies,
                                       [out["result"] for _, out in gathered])
        try:
            r = await client.post(f"{self.cfg['base_url']}/api/chat", json={
                "model": self.cfg["decision_model"], "stream": False, "format": self.schema,
                "keep_alive": self.cfg["keep_alive"], "options": {"temperature": 0},
                "messages": [{"role": "system", "content": system}, *self.history[-MAX_HISTORY:]],
            })
            return json.loads(r.json()["message"]["content"])
        except Exception as e:
            logger.warning(f"decision failed, answering directly: {e}")
            return {"tool": "none"}

    async def _repair_args(self, client, tool: str) -> dict:
        """Second tiny call constrained to ONE tool's schema, so required fields get filled."""
        fn = self.belt.by_name[tool]["function"]
        params = fn.get("parameters", {}).get("properties", {})
        spec = "\n".join(f"- {k}: {v.get('description', v.get('type', 'string'))}" for k, v in params.items())
        system = (f"Fill in the arguments for the tool '{tool}' ({fn['description']}) from the user's "
                  f"latest message and the conversation. Copy the user's words; never invent content. "
                  f"Parameters:\n{spec}")
        try:
            r = await client.post(f"{self.cfg['base_url']}/api/chat", json={
                "model": self.cfg["decision_model"], "stream": False,
                "format": tool_arg_schema(self.belt.by_name[tool]),
                "keep_alive": self.cfg["keep_alive"], "options": {"temperature": 0},
                "messages": [{"role": "system", "content": system}, *self.history[-MAX_HISTORY:]],
            })
            return json.loads(r.json()["message"]["content"])
        except Exception as e:
            logger.warning(f"argument repair failed for {tool}: {e}")
            return {}

    async def _stream_answer(self, client, facts, skill_bodies, gathered, user_input) -> AsyncIterator[dict]:
        persona = self.persona + "\n\nWhat you can do: " + ", ".join(
            s.name for s in self.registry.enabled()) + ", weather, time, calculator, web search, Spotify play and " \
            "media keys, volume, opening apps and websites, messages to the user's phone (Telegram)."
        if _CAPABILITY_Q.search(user_input):
            persona += "\n\nYour current tools:\n" + self.belt.list_text() + \
                       "\n\nYour skills:\n" + self.registry.index_text()
        system = build_answer_system(persona, STYLE_RULES, facts, skill_bodies,
                                     [out["result"] for _, out in gathered])
        full: list[str] = []
        try:
            async with client.stream("POST", f"{self.cfg['base_url']}/api/chat", json={
                "model": self.cfg["answer_model"], "stream": True,
                "keep_alive": self.cfg["keep_alive"], "options": {"temperature": 0.3},
                "messages": [{"role": "system", "content": system}, *self.history[-MAX_HISTORY:]],
            }) as resp:
                async for line in resp.aiter_lines():
                    if not line.strip():
                        continue
                    chunk = json.loads(line)
                    tok = chunk.get("message", {}).get("content", "")
                    if tok:
                        full.append(tok)
                        yield {"type": "token", "text": tok}
                    if chunk.get("done"):
                        break
        except Exception as e:
            logger.error(f"answer stream failed: {e}")
        yield {"type": "final", "text": "".join(full).strip() or "I had trouble forming that reply, sir."}

    # ------------------------------------------------------------ helpers
    def _context(self, user_input: str, ctx: dict) -> dict:
        """Embedding, recall, and skill matching, computed at most once and only when needed."""
        if "facts" not in ctx:
            qvec = self.cortex.embed_query(user_input)
            ctx["facts"] = self.cortex.recall(user_input, k=int(self.mem_cfg.get("recall_k", 6)), query_vec=qvec)
            ctx["skills"] = self.registry.bodies(self.registry.match(user_input, qvec))
        return ctx

    async def _run(self, tool: str, args: dict, gathered: list) -> AsyncIterator[dict]:
        ack = self.belt.ack(tool)
        if ack:
            yield {"type": "ack", "text": ack}             # spoken BEFORE the tool runs
        out = await self.belt.aexecute(tool, args)
        ok = '"error"' not in (out.get("result") or "")
        if not ok and self.belt.is_action(tool) and "say" not in out:
            try:
                err = json.loads(out["result"]).get("error", "")
            except Exception:
                err = ""
            out["say"] = f"That didn't work, sir. {err[:160]}".strip()
        self.bus.publish("tool.executed", {"tool": tool, "args": args, "ok": ok})
        nxt = out.get("confirm_next")
        if nxt and ok:
            self.pending = {"tool": nxt["tool"], "args": nxt.get("args", {}), "desc": nxt.get("desc", ""), "ts": time.time()}
        if out.get("widget"):
            yield {"type": "widget", "data": out["widget"]}
        gathered.append((tool, out))

    def _ask_confirmation(self, tool: str, args: dict, user_input: str, used: set) -> list[dict]:
        desc = self.belt.describe(tool, args)
        self.pending = {"tool": tool, "args": args, "desc": desc, "ts": time.time()}
        text = f"Just to confirm, sir: {desc}. Shall I go ahead?"
        audit.log("confirm_requested", "orchestrator", {"tool": tool, "args": args})
        self.bus.publish("confirm.requested", {"tool": tool, "desc": desc})
        self._finish(user_input, text, used)
        return [{"type": "widget", "data": {"kind": "confirm", "title": "Confirm", "text": desc}},
                {"type": "final", "text": text}]

    async def _answer(self, client, gathered, user_input, ctx) -> AsyncIterator[dict]:
        says = [out.get("say") for _, out in gathered]
        only_exact = gathered and all(self.belt.is_action(t) or t in self.belt.exact or out.get("exact")
                                      or '"error"' in out.get("result", "") for t, out in gathered)
        if only_exact and all(says):                      # exact lines, no LLM: instant and truthful
            yield {"type": "final", "text": " ".join(dict.fromkeys(says))}
            return
        if any(t == "web_search" for t, _ in gathered):   # web answers are checked BEFORE they are spoken
            self._context(user_input, ctx)
            text = await self._grounded_web_answer(client, ctx, gathered, user_input)
            acted = any(self.belt.is_action(t) and '"error"' not in out.get("result", "") for t, out in gathered)
            if not acted and _CLAIM.search(text):
                logger.warning(f"claim guard (web): {text!r} but no action ran")
                text = NOT_DONE
            yield {"type": "final", "text": text}
            return
        if len(gathered) == 1 and gathered[0][0] in ("forget_memory",) and says[0]:
            yield {"type": "final", "text": says[0]}       # ambiguity questions are exact too
            return
        self._context(user_input, ctx)
        final = ""
        async for ev in self._stream_answer(client, ctx["facts"], ctx["skills"], gathered, user_input):
            if ev["type"] == "final":
                final = ev["text"]
                fallback = next((out.get("say") for _, out in reversed(gathered)
                                 if out.get("say") and '"error"' not in out.get("result", "")), None)
                if fallback and (_DENIAL.search(final) or re.search(r"\b(?:see|found|have) nothing\b|\bnothing (?:on|to see)\b", final, re.I)):
                    logger.warning(f"denial guard: replaced {final!r}")
                    ev = {"type": "final", "text": fallback}
                acted = any(self.belt.is_action(t) and '"error"' not in out.get("result", "") for t, out in gathered)
                if not acted and _CLAIM.search(ev["text"]):
                    logger.warning(f"claim guard: {ev['text']!r} but no action ran")
                    ev = {"type": "final", "text": NOT_DONE}
            yield ev

    async def _collect(self, client, facts, skills, gathered, user_input) -> str:
        text = ""
        async for ev in self._stream_answer(client, facts, skills, gathered, user_input):
            if ev["type"] == "final":
                text = ev["text"]
        return text

    async def _grounded_web_answer(self, client, ctx, gathered, user_input) -> str:
        data = " ".join(out.get("result", "") for _, out in gathered)
        text = await self._collect(client, ctx["facts"], ctx["skills"], gathered, user_input)
        bad = ungrounded_numbers(text, data)
        if not bad:
            return text
        logger.warning(f"grounding: {bad} not in the search results; retrying")
        note = [{"result": json.dumps({"correction": f"The numbers {', '.join(bad)} do not appear in the search "
                                                     "results. Use only numbers that appear in them, or say the "
                                                     "results don't give that number."})}]
        retry = await self._collect(client, ctx["facts"], ctx["skills"], gathered + [("note", note[0])], user_input)
        if not ungrounded_numbers(retry, data):
            return retry
        logger.warning("grounding: retry still ungrounded; quoting the source instead")
        try:
            top = next(r for _, out in gathered for r in json.loads(out.get("result", "{}")).get("results", []))
            return f"I found this, sir, from {top['title'][:60]}: {top['snippet'][:200]}"
        except (StopIteration, Exception):
            return "The search results didn't give a clear number, sir."

    def _finish(self, user_input: str, answer: str, used: set[str] | None = None) -> None:
        self.last_tools = set(used or ())
        self.history.append({"role": "assistant", "content": answer})
        self.cortex.log_turn(self.session_id, "user", user_input)
        self.cortex.log_turn(self.session_id, "assistant", answer)
        self.bus.publish("turn.completed", {"session": self.session_id})
        memory_ops = {"remember_fact", "forget_memory", "forget_recent_facts", "add_instruction", "recall_memory"}
        if self.mem_cfg.get("extract_facts", True) and not (used and used & memory_ops):
            task = asyncio.create_task(self._extract(user_input))
            self._bg.add(task)
            task.add_done_callback(self._bg.discard)

    async def _extract(self, user_input: str) -> None:
        stored = await extract_and_store(self.cortex, user_input, model=self.cfg["decision_model"],
                                         base_url=self.cfg["base_url"], keep_alive=self.cfg["keep_alive"])
        for f in stored:
            self.bus.publish("memory.fact_stored", f)

    # ------------------------------------------------------------ main loop
    async def process_stream(self, user_input: str) -> AsyncIterator[dict]:
        if self._stale:
            self.refresh_tools()
        self.history.append({"role": "user", "content": user_input})
        self.bus.publish("turn.user", {"session": self.session_id, "text": user_input})
        gathered: list = []
        used: set[str] = set()
        ctx: dict = {}
        if self.last_missing and self.belt.has("forge_build") and re.match(
                r"^\s*(?:yes,? )?(?:please )?(?:build it|build that|make it|go build it|draft it|create it)\b", user_input, re.I):
            user_input_for_forge = self.last_missing
            self.last_missing = None
            fp_override = ("forge_build", {"request": user_input_for_forge})
        else:
            fp_override = None

        async with httpx.AsyncClient(timeout=120) as client:
            # 0. an answer to "shall I go ahead?"
            if self.pending:
                p, self.pending = self.pending, None
                fresh = time.time() - p["ts"] < CONFIRM_TTL_S
                if fresh and _YES.search(user_input):
                    audit.log("confirm_granted", "orchestrator", {"tool": p["tool"]})
                    used.add(p["tool"])
                    async for ev in self._run(p["tool"], p["args"], gathered):
                        yield ev
                    answer = ""
                    async for ev in self._answer(client, gathered, user_input, ctx):
                        answer = ev["text"] if ev["type"] == "final" else answer
                        yield ev
                    self._finish(user_input, answer, used)
                    return
                if fresh and _NO.search(user_input):
                    audit.log("confirm_denied", "orchestrator", {"tool": p["tool"]})
                    rest = _NO.sub("", user_input, count=1).lstrip(" ,.!")
                    if len(rest.split()) < 3:                      # a plain "no"
                        self._finish(user_input, "Cancelled, sir.", used)
                        yield {"type": "final", "text": "Cancelled, sir."}
                        return
                    user_input = rest                              # "no, make a skill that ...": handle the rest

            # 1. fast path: no LLM, no embeddings
            fp = fp_override or fast_path(user_input, self.belt)
            if fp:
                tool, args = fp[0], self.belt.fill(fp[0], fp[1], user_input)
                if self.belt.needs_confirm(tool):
                    for ev in self._ask_confirmation(tool, args, user_input, used):
                        yield ev
                    return
                used.add(tool)
                async for ev in self._run(tool, args, gathered):
                    yield ev

            # 2-4. the model decides, behind guards
            elif not (_CAPABILITY_Q.search(user_input) or is_small_talk(user_input)):
                self._context(user_input, ctx)
                called, succeeded = set(), set()
                for _ in range(MAX_TOOL_ITERS):
                    decision = await self._decide(client, ctx["skills"], gathered)
                    tool = decision.get("tool", "none")
                    if tool == "unavailable" and (gathered or not _ABILITY_HINT.search(user_input)):
                        tool = "none"                      # conversation, or we already have data: just answer
                    if tool == "unavailable":
                        logger.info(f"no tool covers: {user_input!r}")
                        self.bus.publish("capability.missing", {"request": user_input})
                        text = self._missing_answer(user_input)
                        self._finish(user_input, text, used)
                        yield {"type": "final", "text": text}
                        return
                    if tool == "none" or not self.belt.has(tool) or tool in succeeded:
                        break                      # a tool that already answered is not asked again
                    if not (self.belt.guard_ok(tool, user_input) or tool in self.last_tools):
                        logger.info(f"guard: {tool} blocked, the request doesn't ask for it")
                        break
                    schema = self.belt.by_name[tool]
                    args = self.belt.fill(tool, args_for(schema, decision, self.belt.names()), user_input)
                    if missing_required(schema, args):
                        args = {**args, **args_for(schema, await self._repair_args(client, tool), self.belt.names())}
                        if missing_required(schema, args):
                            logger.warning(f"{tool}: still missing {missing_required(schema, args)}; skipping")
                            break
                    sig = (tool, json.dumps(args, sort_keys=True).lower())
                    if sig in called:
                        break
                    called.add(sig)
                    if self.belt.needs_confirm(tool):
                        for ev in self._ask_confirmation(tool, args, user_input, used):
                            yield ev
                        return
                    used.add(tool)
                    async for ev in self._run(tool, args, gathered):
                        yield ev
                    out = gathered[-1][1]
                    if out.get("missing"):
                        text = self._missing_answer(user_input)
                        self._finish(user_input, text, used)
                        yield {"type": "final", "text": text}
                        return
                    ok = '"error"' not in (out.get("result") or "")
                    if ok:
                        succeeded.add(tool)
                    if self.belt.is_action(tool) and ok:
                        break                      # an action is the whole job; don't chain more

            # 5-6. answer
            answer = ""
            async for ev in self._answer(client, gathered, user_input, ctx):
                if ev["type"] == "final":
                    answer = ev["text"]
                yield ev
        self._finish(user_input, answer, used)


# quick manual test:  python -m core.orchestrator_hybrid
if __name__ == "__main__":
    async def main():
        eva = HybridOrchestrator(mcp=MCPManager())
        for q in ["what time is it", "remember that my favourite music for work is jazz",
                  "what do you know about me", "what's the weather in Breda right now"]:
            print(f"\n>>> {q}")
            async for ev in eva.process_stream(q):
                if ev["type"] == "ack":
                    print(f"[ack] {ev['text']}")
                elif ev["type"] == "token":
                    print(ev["text"], end="", flush=True)
                elif ev["type"] == "widget":
                    print(f"\n[widget:{ev['data'].get('kind')}]")
                elif ev["type"] == "final":
                    print(f"\n[final] {ev['text']}")
        await asyncio.sleep(2)

    asyncio.run(main())
