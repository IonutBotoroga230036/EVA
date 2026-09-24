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
    "- Address the user as 'sir'."
)
MISSING_SKILL = ("I don't have a skill for that yet, sir. Once FORGE is live I can build "
                 "one, with your approval.")
_CAPABILITY_Q = re.compile(r"what (?:else )?can you (?:do|help)|what (?:else )?are you (?:able|capable)"
                           r"|your (?:capabilities|skills|features|abilities)|what do you do\b", re.I)
_SMALLTALK_Q = re.compile(          # questions about her: small talk at any length
    r"^\s*(?:and |so |well |okay |ok |yeah )?(?:how are you|how(?:'s| is) it going|how do you (?:feel|like)|"
    r"do you (?:like|enjoy|feel|think|want|love|prefer)|are you (?:ok|okay|happy|sad|alive|conscious|there)|"
    r"what do you think|what would you like|would you like to have|who are you|what(?:'s| is) your name)\b", re.I)
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

    def guard_ok(self, name: str, text: str) -> bool:
        rx = self.guards.get(name)
        return True if not rx else bool(re.search(rx, text, re.I))

    def needs_confirm(self, name: str) -> bool:
        return name in self.confirm_text or (self.is_mcp(name) and self.mcp.needs_confirm(name))

    def describe(self, name: str, args: dict) -> str:
        if name in self.confirm_text:
            try:
                return self.confirm_text[name].format(**{"minutes": 15, **(args or {})})
            except Exception:
                return self.confirm_text[name]
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
    polite = r"(?:(?:i want you to|i need you to|can you|could you|would you|please|and|also|now|don't forget)\s+)*"

    m = re.search(r"(?:set |turn |change )?(?:the )?volume (?:to |at )?(\d{1,3})\s*%?", t, re.I)
    if m and belt.has("set_volume"):
        return "set_volume", {"level": int(m.group(1))}
    if belt.has("set_volume") and re.search(r"\b(max|full|maximum) volume\b", t, re.I):
        return "set_volume", {"level": 100}

    if belt.has("forget_recent_facts") and re.search(
            r"\b(?:delete|forget|remove|erase)\b.{0,40}\b(?:just|recently)\s+"
            r"(?:remembered|learned|saved|stored|noted)", t, re.I):
        return "forget_recent_facts", {"minutes": 15}
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
        self._bg: set[asyncio.Task] = set()
        hours = float(self.mem_cfg.get("continuity_hours", 24))
        self.history: list[dict] = self.cortex.recent_turns(limit=6, within_hours=hours)
        if self.history:
            logger.info(f"CORTEX: resumed {len(self.history)} recent turns")

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
        persona = self.persona
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
        only_actions = gathered and all(self.belt.is_action(t) or '"error"' in out.get("result", "")
                                        for t, out in gathered)
        if only_actions and all(says):                    # exact lines, no LLM: instant and truthful
            yield {"type": "final", "text": " ".join(dict.fromkeys(says))}
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
                if fallback and _DENIAL.search(final):
                    logger.warning(f"denial guard: replaced {final!r}")
                    ev = {"type": "final", "text": fallback}
            yield ev

    def _finish(self, user_input: str, answer: str, used: set[str] | None = None) -> None:
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
        self.history.append({"role": "user", "content": user_input})
        self.bus.publish("turn.user", {"session": self.session_id, "text": user_input})
        gathered: list = []
        used: set[str] = set()
        ctx: dict = {}

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
                    self._finish(user_input, "Cancelled, sir.", used)
                    yield {"type": "final", "text": "Cancelled, sir."}
                    return

            # 1. fast path: no LLM, no embeddings
            fp = fast_path(user_input, self.belt)
            if fp:
                tool, args = fp
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
                    if tool == "unavailable":
                        logger.info(f"no tool covers: {user_input!r}")
                        self.bus.publish("capability.missing", {"request": user_input})
                        self._finish(user_input, MISSING_SKILL, used)
                        yield {"type": "final", "text": MISSING_SKILL}
                        return
                    if tool == "none" or not self.belt.has(tool) or tool in succeeded:
                        break                      # a tool that already answered is not asked again
                    if not self.belt.guard_ok(tool, user_input):
                        logger.info(f"guard: {tool} blocked, the request doesn't ask for it")
                        break
                    schema = self.belt.by_name[tool]
                    args = args_for(schema, decision, self.belt.names())
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
                        self._finish(user_input, MISSING_SKILL, used)
                        yield {"type": "final", "text": MISSING_SKILL}
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
