"""
orchestrator_hybrid.py  ->  core/orchestrator_hybrid.py

Milestone A: reliable tool-calling on a small local model.

Design (harvested from im4peace/Jarvis intent_router.py + our constrained decoding):

  1. FAST PATH. A tiny table of unambiguous snap commands (time, volume,
     transport) runs the tool directly with NO llm call. Anything that
     doesn't match falls through to the model, so nuance is never capped:
     "weather tomorrow in Portugal" is not a snap command, so it goes to
     the LLM with full understanding.

  2. CONSTRAINED DECISION. For everything else, the model picks a tool by
     emitting JSON that is *constrained* to a fixed schema via Ollama's
     `format` parameter. Constrained decoding masks any token that would
     break the schema, so the model can no longer ramble instead of
     choosing. This is what fixes the "sometimes it works" problem, and
     it's also faster because no tokens are spent on formatting.

  3. BOUNDED TOOL LOOP. After a tool runs, the model may choose another
     (composite tasks), up to MAX_TOOL_ITERS. When the bound is hit we
     answer with what we have, so it never hangs.

  4. STREAMED ANSWER. The final reply streams token by token. The decision
     step emits JSON (not speech), so there is no "double speaking" where
     she narrates a step and the tool also announces it.

Same class interface as before (process_stream yielding ack/token/widget/final),
so server_stream.py only needs its import line changed.
"""

from __future__ import annotations
import json
from pydoc import text
import re
from pathlib import Path
from typing import AsyncIterator

import httpx
import yaml
from loguru import logger

from core.tools_native import TOOL_SCHEMAS, REGISTRY, execute_tool, ack_for

OLLAMA_URL = "http://localhost:11434"
DECISION_MODEL = "qwen2.5:3b-instruct"   # fits your 6GB, fast; constrained so it's reliable
ANSWER_MODEL = "qwen2.5:3b-instruct"     # same model phrases the reply
KEEP_ALIVE = "30m"
MAX_TOOL_ITERS = 3
MAX_HISTORY = 12

TOOL_NAMES = [t["function"]["name"] for t in TOOL_SCHEMAS]
TOOL_LIST_TEXT = "\n".join(
    f"- {t['function']['name']}: {t['function']['description']}" for t in TOOL_SCHEMAS
)

# Flat schema (flat on purpose: small models choke on deeply nested schemas).
# The model picks a tool and fills only the fields that tool needs.
DECISION_SCHEMA = {
    "type": "object",
    "properties": {
        "tool": {"type": "string", "enum": TOOL_NAMES + ["none"]},
        "city": {"type": "string"},
        "query": {"type": "string"},
        "what": {"type": "string"},
        "name": {"type": "string"},
        "level": {"type": "integer"},
        "action": {"type": "string",
                   "enum": ["playpause", "next", "previous", "volup", "voldown", "mute"]},
    },
    "required": ["tool"],
}

DECISION_SYSTEM = (
    "You are the tool-router for E.V.A. Decide whether a tool is needed to "
    "answer the user's latest message. Choose exactly one tool from the list, "
    'or "none" to answer directly without a tool. Fill only the parameter '
    "fields the chosen tool needs. Do not answer the user here; only choose.\n\n"
    f"Tools:\n{TOOL_LIST_TEXT}"
)

STYLE_RULES = (
    "\n\nRESPONSE RULES:\n"
    "- Reply in one or two short sentences. Every word earns its place.\n"
    "- Give ONLY the final answer. Never narrate your steps or which tool you used.\n"
    "- Use the real data provided. Never invent facts, times, or weather.\n"
    "- If a tool result shows an action was completed, confirm it plainly and briefly, "
    "for example 'Volume set to 85, sir.' or 'Playing on Spotify, sir.'\n"
    "- Never tell the user to do it themselves, and never say you cannot do something "
    "that a tool result shows was already done.\n"
    "- Address the user as 'sir'."
)


def _load_persona(name: str = "eva") -> str:
    try:
        return yaml.safe_load(Path(f"personas/{name}.yaml").read_text())["personality"]
    except Exception:
        return "You are E.V.A., a concise JARVIS-style assistant. Address the user as 'sir'."


# --- fast path: only unambiguous commands; everything else -> the model ------
FAST_PATHS = [
    (re.compile(r"what time is it|what'?s the time|^\s*time\s*\??$", re.I), ("get_datetime", {})),
    (re.compile(r"\bvolume up\b|\blouder\b|\bturn it up\b", re.I), ("media_control", {"action": "volup"})),
    (re.compile(r"\bvolume down\b|\bquieter\b|\bturn it down\b", re.I), ("media_control", {"action": "voldown"})),
    (re.compile(r"\b(mute|unmute)\b", re.I), ("media_control", {"action": "mute"})),
    (re.compile(r"\b(pause|resume)\b", re.I), ("media_control", {"action": "playpause"})),
    (re.compile(r"\bnext (track|song)\b|\bskip( this)?( song| track)?\b", re.I), ("media_control", {"action": "next"})),
    (re.compile(r"\b(previous|last) (track|song)\b|\bgo back a (track|song)\b", re.I), ("media_control", {"action": "previous"})),
]


def _args_for(tool: str, d: dict) -> dict:
    if tool == "get_weather": return {"city": d.get("city", "")}
    if tool == "web_search": return {"query": d.get("query", "")}
    if tool == "spotify_play": return {"what": d.get("what", "")}
    if tool == "media_control": return {"action": d.get("action", "playpause")}
    if tool == "open_app": return {"name": d.get("name", "")}
    if tool == "open_website": return {"name": d.get("name", "")}
    if tool == "set_volume": return {"level": d.get("level", 50)}
    return {}


class HybridOrchestrator:
    def __init__(self, persona: str = "eva"):
        self.persona = _load_persona(persona)
        self.history: list[dict] = []

    def _fast_path(self, text: str):
        m = re.search(r"(?:set |turn |change )?(?:the )?volume (?:to |at )?(\d{1,3})", text, re.I)
        if m:
            return ("set_volume", {"level": int(m.group(1))})
        if re.search(r"\b(max|full|maximum) volume\b|\bvolume (?:all the way )?up\b", text, re.I):
            return ("set_volume", {"level": 100})
        for rx, action in FAST_PATHS:
            if rx.search(text):
                return action
        return None

    async def _decide(self, client, gathered: list) -> dict:
        context = ""
        if gathered:
            context = ("\n\nData already gathered (do NOT call the same tool "
                       'again; if this answers the user, choose "none"):\n'
                       + "\n".join(out["result"] for _, out in gathered))
        messages = [
            {"role": "system", "content": DECISION_SYSTEM + context},
            *self.history[-MAX_HISTORY:],
        ]
        try:
            r = await client.post(f"{OLLAMA_URL}/api/chat", json={
                "model": DECISION_MODEL, "messages": messages, "stream": False,
                "format": DECISION_SCHEMA, "keep_alive": KEEP_ALIVE,
                "options": {"temperature": 0},
            })
            content = r.json()["message"]["content"]
            return json.loads(content)
        except Exception as e:
            logger.warning(f"decision failed, answering directly: {e}")
            return {"tool": "none"}

    async def _final_answer(self, client, gathered: list) -> AsyncIterator[dict]:
        data = ""
        if gathered:
            data = "\n\nReal data to use in your answer:\n" + "\n".join(
                out["result"] for _, out in gathered)
        messages = [
            {"role": "system", "content": self.persona + STYLE_RULES + data},
            *self.history[-MAX_HISTORY:],
        ]
        full = []
        try:
            async with client.stream("POST", f"{OLLAMA_URL}/api/chat", json={
                "model": ANSWER_MODEL, "messages": messages, "stream": True,
                "keep_alive": KEEP_ALIVE, "options": {"temperature": 0.3},
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
        answer = "".join(full).strip() or "I had trouble forming that reply, sir."
        self.history.append({"role": "assistant", "content": answer})
        yield {"type": "final", "text": answer}

    async def process_stream(self, user_input: str) -> AsyncIterator[dict]:
        self.history.append({"role": "user", "content": user_input})
        gathered: list = []

        async with httpx.AsyncClient(timeout=120) as client:
            # 1. fast path (no LLM)
            fp = self._fast_path(user_input)
            if fp:
                tool, args = fp
                if ack_for(tool):
                    yield {"type": "ack", "text": ack_for(tool)}
                out = execute_tool(tool, args)
                if out.get("widget"):
                    yield {"type": "widget", "data": out["widget"]}
                gathered.append((tool, out))
                async for ev in self._final_answer(client, gathered):
                    yield ev
                return

            # 2. bounded constrained-decision loop
            called = set()
            for _ in range(MAX_TOOL_ITERS):
                decision = await self._decide(client, gathered)
                tool = decision.get("tool", "none")
                if tool == "none" or tool not in REGISTRY:
                    break
                args = _args_for(tool, decision)
                sig = (tool, json.dumps(args, sort_keys=True))
                if sig in called:      # already have this exact result
                    break
                called.add(sig)
                if ack_for(tool):
                    yield {"type": "ack", "text": ack_for(tool)}
                out = execute_tool(tool, args)
                if out.get("missing"):
                    text = ("I don't have a skill for that yet, sir. "
                            "Once FORGE is live I can build one, with your approval.")
                    self.history.append({"role": "assistant", "content": text})
                    yield {"type": "final", "text": text}
                    return
                if out.get("widget"):
                    yield {"type": "widget", "data": out["widget"]}
                gathered.append((tool, out))

            # 3. streamed final answer
            async for ev in self._final_answer(client, gathered):
                yield ev


# quick manual test:  python -m core.orchestrator_hybrid
if __name__ == "__main__":
    import asyncio

    async def main():
        eva = HybridOrchestrator()
        for q in ["what time is it", "what's the weather in Breda right now",
                  "search the web for news about the Netherlands", "pause"]:
            print(f"\n>>> {q}")
            async for ev in eva.process_stream(q):
                if ev["type"] == "ack": print(f"[ack] {ev['text']}")
                elif ev["type"] == "token": print(ev["text"], end="", flush=True)
                elif ev["type"] == "widget": print(f"\n[widget:{ev['data'].get('kind')}]")
            print()

    asyncio.run(main())
