"""
orchestrator_stream.py  ->  core/orchestrator_stream.py

Streaming, tool-reliable orchestrator for E.V.A. Replaces the 2-3 call
freeform pipeline with the Ollama /api/chat native tool loop:

    1. one fast pass WITH tools attached (decides if a tool is needed)
    2. if a tool is called: emit a short spoken ack, run the tool, feed the
       real result back, then STREAM the final concise answer
    3. if no tool: STREAM the answer directly

Why this fixes what you saw:
  - native tool_calls means no more regex-parsing JSON out of prose, so no
    more hallucinated "22 degrees and a gentle breeze"
  - a strict "answer only, never narrate your steps" system rule stops the
    leaked chain-of-thought ("Since the user asked for the time...")
  - keep_alive keeps the model resident so there's no reload lag between turns
  - session history is actually passed back in, so she stops being amnesiac

It yields events you pipe to the UI / TTS:
  {"type":"ack",   "text": "..."}     # say immediately, aloud
  {"type":"token", "text": "..."}     # stream into the caption
  {"type":"widget","data": {...}}     # pop a widget on the core screen
  {"type":"final", "text": "..."}     # the full answer (for logging/memory)
"""

from __future__ import annotations
import json
from pathlib import Path
from typing import AsyncIterator

import httpx
import yaml
from loguru import logger

from core.tools_native import TOOL_SCHEMAS, execute_tool, ack_for

OLLAMA_URL = "http://localhost:11434"
MODEL = "qwen2.5:3b-instruct"     # strong local tool-caller; swap for llama3.1:8b if you prefer
KEEP_ALIVE = "30m"                 # keep the model hot -> no reload lag
MAX_HISTORY = 12                   # turns kept in-context; CORTEX handles older recall

# Answer-only guardrail appended to the persona. This is what stops narration.
STYLE_RULES = (
    "\n\nRESPONSE RULES:\n"
    "- Reply in one or two short sentences. Every word earns its place.\n"
    "- Give ONLY the final answer. Never narrate your steps, your reasoning, or "
    "which tool you used. Do not say things like 'let me check' in the final "
    "answer; that acknowledgement has already been spoken.\n"
    "- Use the real data from tool results. Never invent facts, times, or weather.\n"
    "- Address the user as 'sir'."
)


def _load_persona(name: str = "eva") -> str:
    try:
        with open(Path(f"personas/{name}.yaml")) as f:
            return yaml.safe_load(f)["personality"]
    except Exception:
        return "You are E.V.A., a concise JARVIS-style assistant. Address the user as 'sir'."


class StreamOrchestrator:
    def __init__(self, persona: str = "eva", model: str = MODEL):
        self.system = _load_persona(persona) + STYLE_RULES
        self.model = model
        self.history: list[dict] = []

    async def _chat(self, client, messages, *, stream: bool, tools=None):
        payload = {"model": self.model, "messages": messages, "stream": stream,
                   "keep_alive": KEEP_ALIVE, "options": {"temperature": 0.1}}
        if tools:
            payload["tools"] = tools
        return await client.post(f"{OLLAMA_URL}/api/chat", json=payload)

    async def process_stream(self, user_input: str) -> AsyncIterator[dict]:
        self.history.append({"role": "user", "content": user_input})
        messages = [{"role": "system", "content": self.system}, *self.history[-MAX_HISTORY:]]

        async with httpx.AsyncClient(timeout=120) as client:
            # --- Pass 1: decide on a tool (fast, non-streamed) ---
            try:
                r = await self._chat(client, messages, stream=False, tools=TOOL_SCHEMAS)
                msg = r.json().get("message", {})
            except Exception as e:
                logger.error(f"pass1 failed: {e}")
                yield {"type": "final", "text": "My connection to the local model failed, sir."}
                return

            tool_calls = msg.get("tool_calls") or []

            if tool_calls:
                messages.append(msg)  # record the assistant's tool request
                for call in tool_calls:
                    name = call["function"]["name"]
                    args = call["function"].get("arguments", {})
                    if isinstance(args, str):
                        try: args = json.loads(args)
                        except Exception: args = {}

                    ack = ack_for(name)
                    if ack:
                        yield {"type": "ack", "text": ack}

                    out = execute_tool(name, args)

                    if out.get("missing"):
                        # honest fallback -> no faking (his rule #5)
                        text = ("I don't have a skill for that yet, sir. "
                                "Once FORGE is live I can build one, with your approval.")
                        self.history.append({"role": "assistant", "content": text})
                        yield {"type": "final", "text": text}
                        return

                    if out.get("widget"):
                        yield {"type": "widget", "data": out["widget"]}

                    messages.append({"role": "tool", "content": out["result"]})

            # --- Pass 2: stream the final concise answer ---
            full = []
            try:
                async with client.stream("POST", f"{OLLAMA_URL}/api/chat", json={
                    "model": self.model, "messages": messages, "stream": True,
                    "keep_alive": KEEP_ALIVE, "options": {"temperature": 0.1},
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
                logger.error(f"stream failed: {e}")
                if not full:
                    yield {"type": "final", "text": "I had trouble forming that reply, sir."}
                    return

            answer = "".join(full).strip()
            self.history.append({"role": "assistant", "content": answer})
            yield {"type": "final", "text": answer}


# --- quick manual test: python -m core.orchestrator_stream ------------------
if __name__ == "__main__":
    import asyncio

    async def main():
        eva = StreamOrchestrator()
        for q in ["what time is it?", "what's the weather?", "play some jazz"]:
            print(f"\n>>> {q}")
            async for ev in eva.process_stream(q):
                if ev["type"] == "ack":
                    print(f"[ack] {ev['text']}")
                elif ev["type"] == "token":
                    print(ev["text"], end="", flush=True)
                elif ev["type"] == "widget":
                    print(f"\n[widget] {ev['data'].get('kind')}")
            print()

    asyncio.run(main())
