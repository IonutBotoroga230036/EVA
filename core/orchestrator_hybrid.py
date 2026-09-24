"""
HybridOrchestrator: E.V.A.'s brain (v0.2, Milestone B).

Per turn:
  1. Embed the message ONCE (Ollama nomic-embed-text). That one vector drives
     both CORTEX memory recall and SKILL.md matching.
  2. FAST PATH for unambiguous commands (time, volume, media, remember/forget,
     screen). No LLM call. Anything else falls through to the model, so nuance
     is never capped.
  3. CONSTRAINED DECISION: the model picks a tool by emitting JSON constrained
     to a schema built automatically from every loaded tool (built-ins + skills).
     Adding a skill never requires editing this file.
  4. BOUNDED TOOL LOOP (max 3), with a guard against repeating the same call.
  5. STREAMED ANSWER with persona + EVA.md + relevant facts + matched skill
     instructions + real tool data.
  6. AFTER the answer: log both turns to CORTEX and extract durable facts in
     the background, so memory never adds latency.

Event interface is unchanged (ack / token / widget / final), so the server and
UI keep working as before.
"""

from __future__ import annotations

import asyncio
import json
import re
import uuid
from pathlib import Path
from typing import AsyncIterator, Callable

import httpx
import yaml
from loguru import logger

from core.events.bus import get_bus
from core.memory.cortex import Cortex, get_cortex
from core.memory.extractor import extract_and_store
from core.prompt_builder import build_answer_system, build_decision_system
from core.security.audit import audit
from core.settings import get_settings, local_cfg
from core.tools_native import REGISTRY as BUILTIN_FUNCS
from core.tools_native import TOOL_SCHEMAS as BUILTIN_SCHEMAS
from core.tools_native import ack_for as builtin_ack
from core.tools_native import execute_tool as builtin_execute
from skills.registry import SkillRegistry, get_registry

MAX_TOOL_ITERS = 3
MAX_HISTORY = 12

STYLE_RULES = (
    "RESPONSE RULES:\n"
    "- Reply in one or two short sentences. Every word earns its place.\n"
    "- Give ONLY the final answer. Never narrate your steps or which tool you used.\n"
    "- Use the real data provided. Never invent facts, times, weather, or memories.\n"
    "- If a tool result shows an action was completed, confirm it plainly, for example "
    "'Volume set to 85, sir.' or 'Noted, sir.'\n"
    "- Never tell the user to do it themselves, and never say you cannot do something "
    "that a tool result shows was already done.\n"
    "- Use remembered facts only when they genuinely help; don't recite them unprompted.\n"
    "- Address the user as 'sir'."
)
MISSING_SKILL = ("I don't have a skill for that yet, sir. Once FORGE is live I can build "
                 "one, with your approval.")
_CAPABILITY_Q = re.compile(r"what can you do|your (capabilities|skills)|what are you able", re.I)
_WAKE_PREFIX = re.compile(r"^\s*(?:hey\s+)?(?:eva\b|e\.v\.a\.?)[\s,:!.-]*", re.I)


def load_persona(name: str = "eva") -> str:
    try:
        return yaml.safe_load(Path(f"personas/{name}.yaml").read_text(encoding="utf-8"))["personality"]
    except Exception:
        return "You are E.V.A., a concise JARVIS-style assistant. Address the user as 'sir'."


# ============================================================ tool belt
class ToolBelt:
    """One view over built-in tools and skill tools: schemas, acks, execution."""

    def __init__(self, registry: SkillRegistry | None = None):
        self.registry = registry
        self.skill_funcs: dict[str, Callable] = registry.functions() if registry else {}
        self.skill_acks: dict[str, str] = registry.acks() if registry else {}
        skill_schemas = registry.tool_schemas() if registry else []
        builtin_names = {t["function"]["name"] for t in BUILTIN_SCHEMAS}
        # built-ins win on a name collision, so a skill can never shadow a trusted tool
        self.schemas = list(BUILTIN_SCHEMAS) + [t for t in skill_schemas
                                                if t["function"]["name"] not in builtin_names]
        self.by_name = {t["function"]["name"]: t for t in self.schemas}

    def has(self, name: str) -> bool:
        return name in self.by_name

    def names(self) -> list[str]:
        return list(self.by_name)

    def list_text(self) -> str:
        return "\n".join(f"- {n}: {t['function']['description']}" for n, t in self.by_name.items())

    def ack(self, name: str) -> str | None:
        if name in BUILTIN_FUNCS:
            return builtin_ack(name)
        return self.skill_acks.get(name)

    def execute(self, name: str, args: dict) -> dict:
        audit.log("tool_call", "orchestrator", {"tool": name, "args": args})
        if name in BUILTIN_FUNCS:
            return builtin_execute(name, args)
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
                    logger.info(f"TOOL {name}({args}) ok")
                return out
            except Exception as e:
                last = e
                logger.warning(f"TOOL {name} attempt {attempt + 1} failed: {e}")
        return {"result": json.dumps({"error": str(last)}), "failed": True}


def build_decision_schema(schemas: list[dict]) -> dict:
    """Flat union of every tool's parameters (flat on purpose: small models
    struggle with nested schemas). Enums survive only if no other tool reuses
    the same parameter name with a different meaning."""
    props: dict[str, dict] = {}
    owners: dict[str, int] = {}
    for t in schemas:
        for pname, spec in t["function"].get("parameters", {}).get("properties", {}).items():
            owners[pname] = owners.get(pname, 0) + 1
            if pname not in props:
                entry = {"type": spec.get("type", "string")}
                if "enum" in spec:
                    entry["enum"] = list(spec["enum"])
                props[pname] = entry
            elif props[pname].get("enum") != spec.get("enum"):
                props[pname].pop("enum", None)
    names = [t["function"]["name"] for t in schemas]
    return {"type": "object",
            "properties": {"tool": {"type": "string", "enum": names + ["none"]}, **props},
            "required": ["tool"]}


def args_for(schema: dict, decision: dict) -> dict:
    """Keep only the parameters the chosen tool declares, dropping empty values."""
    allowed = schema["function"].get("parameters", {}).get("properties", {})
    return {k: v for k, v in decision.items() if k in allowed and v not in ("", None)}


# ============================================================ fast path
def fast_path(text: str, belt: ToolBelt) -> tuple[str, dict] | None:
    t = _WAKE_PREFIX.sub("", text).strip()
    m = re.search(r"(?:set |turn |change )?(?:the )?volume (?:to |at )?(\d{1,3})\s*%?", t, re.I)
    if m and belt.has("set_volume"):
        return "set_volume", {"level": int(m.group(1))}
    if belt.has("set_volume") and re.search(r"\b(max|full|maximum) volume\b", t, re.I):
        return "set_volume", {"level": 100}

    m = re.match(r"remember(?: that)?\s+(.{4,})$", t, re.I)
    if m and belt.has("remember_fact") and not re.match(r"remember (to|when)\b", t, re.I):
        return "remember_fact", {"text": m.group(1).strip()}
    m = re.match(r"(?:from now on|going forward)[,\s]+(.{4,})$", t, re.I)
    if m and belt.has("add_instruction"):
        return "add_instruction", {"text": m.group(1).strip()}
    m = re.match(r"forget(?: that| about)?\s+(.{4,})$", t, re.I)
    if m and belt.has("forget_memory") and m.group(1).lower() not in ("about it", "it", "that"):
        return "forget_memory", {"query": m.group(1).strip()}
    if belt.has("recall_memory") and re.search(r"what do you (?:know|remember) about me", t, re.I):
        return "recall_memory", {"query": ""}

    if belt.has("see_screen") and re.search(
            r"\b(what'?s on|look at|read|check|describe) (?:my |the |this )?screen\b"
            r"|what am i looking at", t, re.I):
        return "see_screen", {"question": t}

    simple = [
        (r"what time is it|what'?s the time|^\s*time\s*\??$", "get_datetime", {}),
        (r"\bvolume up\b|\blouder\b|\bturn it up\b", "media_control", {"action": "volup"}),
        (r"\bvolume down\b|\bquieter\b|\bturn it down\b", "media_control", {"action": "voldown"}),
        (r"^\s*(mute|unmute)\b", "media_control", {"action": "mute"}),
        (r"^\s*(pause|resume)\b", "media_control", {"action": "playpause"}),
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
                 cortex: Cortex | None = None, registry: SkillRegistry | None = None):
        self.cfg = local_cfg()
        self.mem_cfg = get_settings().get("memory", {})
        self.persona = load_persona(persona)
        self.session_id = session_id or uuid.uuid4().hex[:12]
        self.cortex = cortex or get_cortex()
        self.registry = registry or get_registry()
        self.belt = ToolBelt(self.registry)
        self.schema = build_decision_schema(self.belt.schemas)
        self.bus = get_bus()
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

    async def _answer(self, client, facts, skill_bodies, gathered, user_input) -> AsyncIterator[dict]:
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
    def _run(self, tool: str, args: dict, gathered: list) -> tuple[list[dict], dict]:
        events = []
        ack = self.belt.ack(tool)
        if ack:
            events.append({"type": "ack", "text": ack})
        out = self.belt.execute(tool, args)
        self.bus.publish("tool.executed", {"tool": tool, "args": args,
                                           "ok": '"error"' not in (out.get("result") or "")})
        if out.get("widget"):
            events.append({"type": "widget", "data": out["widget"]})
        gathered.append((tool, out))
        return events, out

    def _finish(self, user_input: str, answer: str) -> None:
        self.history.append({"role": "assistant", "content": answer})
        self.cortex.log_turn(self.session_id, "user", user_input)
        self.cortex.log_turn(self.session_id, "assistant", answer)
        self.bus.publish("turn.completed", {"session": self.session_id})
        if self.mem_cfg.get("extract_facts", True):
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

        qvec = self.cortex.embed_query(user_input)
        facts = self.cortex.recall(user_input, k=int(self.mem_cfg.get("recall_k", 6)), query_vec=qvec)
        skill_bodies = self.registry.bodies(self.registry.match(user_input, qvec))

        async with httpx.AsyncClient(timeout=120) as client:
            fp = fast_path(user_input, self.belt)
            if fp:
                events, _ = self._run(fp[0], fp[1], gathered)
                for ev in events:
                    yield ev
            else:
                called = set()
                for _ in range(MAX_TOOL_ITERS):
                    decision = await self._decide(client, skill_bodies, gathered)
                    tool = decision.get("tool", "none")
                    if tool == "none" or not self.belt.has(tool):
                        break
                    args = args_for(self.belt.by_name[tool], decision)
                    sig = (tool, json.dumps(args, sort_keys=True))
                    if sig in called:
                        break
                    called.add(sig)
                    events, out = self._run(tool, args, gathered)
                    for ev in events:
                        yield ev
                    if out.get("missing"):
                        self._finish(user_input, MISSING_SKILL)
                        yield {"type": "final", "text": MISSING_SKILL}
                        return

            answer = ""
            async for ev in self._answer(client, facts, skill_bodies, gathered, user_input):
                if ev["type"] == "final":
                    answer = ev["text"]
                yield ev
        self._finish(user_input, answer)


# quick manual test:  python -m core.orchestrator_hybrid
if __name__ == "__main__":
    async def main():
        eva = HybridOrchestrator()
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
            print()
        await asyncio.sleep(2)   # let background fact extraction finish

    asyncio.run(main())
