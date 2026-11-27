"""The brain's two backends, one shape (v1.0).

    step = await backend.step(system, transcript, tools, on_text, on_thinking)
    step.text, step.thinking, step.calls [{id, name, input}], step.cost_eur, step.model

The transcript is neutral, so a turn can move from the local model to Claude halfway:
    {"role": "user", "text": ..., "images": [{"media_type", "b64"}]}
    {"role": "assistant", "text": ..., "thinking": ..., "calls": [...], "raw": <the backend's own message>}
    {"role": "tool", "id": ..., "name": ..., "text": ..., "is_error": bool, "images": [...]}

LocalBackend: Ollama /api/chat with tools and `think` (checked against docs.ollama.com, Nov 2026: thinking arrives
in message.thinking before message.content; tool results go back as role "tool" with tool_name). CloudBackend: the
Claude client E.V.A. already uses (streaming, retries, budget, prompt cache).
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

import httpx
from loguru import logger


class BackendError(RuntimeError):
    """The backend couldn't answer (not running, too slow, refused). The loop may hand the turn over."""


@dataclass
class Step:
    text: str = ""
    thinking: str = ""
    calls: list = field(default_factory=list)
    cost_eur: float = 0.0
    model: str = ""
    raw: Any = None
    seconds: float = 0.0


TextSink = Optional[Callable[[str], None]]


# ---------------------------------------------------------------- local (Ollama)
class LocalBackend:
    kind = "local"

    def __init__(self, base_url: str, model: str, fallback: str = "", think: bool = True, num_ctx: int = 12288,
                 keep_alive: str = "30m", timeout_s: float = 150.0, http: Optional[httpx.AsyncClient] = None):
        self.base_url, self.want, self.fallback = base_url.rstrip("/"), model, fallback
        self.model = model
        self.think, self.num_ctx, self.keep_alive, self.timeout_s = think, num_ctx, keep_alive, timeout_s
        self.http = http
        self._checked = False

    async def _client(self) -> httpx.AsyncClient:
        return self.http or httpx.AsyncClient(timeout=httpx.Timeout(self.timeout_s, connect=3.0))

    async def ready(self) -> bool:
        """Is Ollama up, and which model do we use? Logs once what to pull when the chosen one is missing."""
        if self._checked:
            return True
        http = await self._client()
        try:
            r = await http.get(f"{self.base_url}/api/tags", timeout=3.0)
            names = {m.get("name", "") for m in (r.json().get("models") or [])}
        except Exception as e:
            raise BackendError(f"Ollama isn't answering at {self.base_url} ({type(e).__name__})") from e
        finally:
            if self.http is None:
                await http.aclose()

        def have(m: str) -> bool:
            return bool(m) and (m in names or f"{m}:latest" in names or any(n.split(":")[0] == m for n in names))
        if have(self.want):
            self.model = self.want
        elif have(self.fallback):
            logger.warning(f"BRAIN: {self.want} isn't on this PC (run: ollama pull {self.want}); using "
                           f"{self.fallback} meanwhile")
            self.model, self.think = self.fallback, self.think and self.fallback.startswith(("qwen3", "deepseek-r1",
                                                                                              "gpt-oss"))
        else:
            raise BackendError(f"no local model: run 'ollama pull {self.want}'")
        self._checked = True
        return True

    @staticmethod
    def messages(system: str, transcript: list[dict]) -> list[dict]:
        out = [{"role": "system", "content": system}]
        for m in transcript:
            if m["role"] == "user":
                msg = {"role": "user", "content": m.get("text", "")}
                if m.get("images"):
                    msg["images"] = [i["b64"] for i in m["images"]]
                out.append(msg)
            elif m["role"] == "assistant":
                msg = {"role": "assistant", "content": m.get("text", "")}
                if m.get("thinking"):
                    msg["thinking"] = m["thinking"]
                if m.get("calls"):
                    msg["tool_calls"] = [{"type": "function", "function": {"index": i, "name": c["name"],
                                                                           "arguments": c.get("input") or {}}}
                                         for i, c in enumerate(m["calls"])]
                out.append(msg)
            elif m["role"] == "tool":
                out.append({"role": "tool", "tool_name": m.get("name", ""), "content": m.get("text", "")})
                if m.get("images"):          # a picture from a tool (his screen): shown as the next user message
                    out.append({"role": "user", "content": "(the picture the tool returned)",
                                "images": [i["b64"] for i in m["images"]]})
        return out

    async def step(self, system: str, transcript: list[dict], tools: list[dict], on_text: TextSink = None,
                   on_thinking: TextSink = None, think: Optional[bool] = None, max_tokens: int = 1500,
                   text_only: bool = False, **_) -> Step:
        await self.ready()
        if text_only:
            tools = []
        body = {"model": self.model, "stream": True, "keep_alive": self.keep_alive,
                "messages": self.messages(system, transcript),
                "options": {"temperature": 0.3, "num_ctx": self.num_ctx, "num_predict": max_tokens}}
        if tools:
            body["tools"] = tools
        use_think = self.think if think is None else (think and self.think)
        if self.model.startswith(("qwen3", "deepseek-r1", "gpt-oss")) or use_think:
            body["think"] = bool(use_think)
        t0 = time.time()
        text, thinking, calls = [], [], []
        http = await self._client()
        try:
            async with http.stream("POST", f"{self.base_url}/api/chat", json=body,
                                   timeout=httpx.Timeout(self.timeout_s, connect=3.0)) as r:
                if r.status_code >= 400:
                    detail = (await r.aread()).decode("utf-8", "replace")[:200]
                    if "does not support tools" in detail and tools:
                        raise BackendError(f"{self.model} can't use tools (pull qwen3.5:4b)")
                    raise BackendError(f"Ollama error {r.status_code}: {detail}")
                async for line in r.aiter_lines():
                    if not line.strip():
                        continue
                    try:
                        chunk = json.loads(line)
                    except ValueError:
                        continue
                    msg = chunk.get("message") or {}
                    if msg.get("thinking"):
                        thinking.append(msg["thinking"])
                        if on_thinking:
                            on_thinking(msg["thinking"])
                    if msg.get("content"):
                        text.append(msg["content"])
                        if on_text:
                            on_text(msg["content"])
                    for tc in msg.get("tool_calls") or []:
                        fn = tc.get("function") or {}
                        args = fn.get("arguments") or {}
                        if isinstance(args, str):
                            try:
                                args = json.loads(args)
                            except ValueError:
                                args = {}
                        calls.append({"id": f"local_{len(calls)}_{int(t0 * 1000) % 100000}",
                                      "name": fn.get("name", ""), "input": args if isinstance(args, dict) else {}})
                    if chunk.get("done"):
                        break
                    if time.time() - t0 > self.timeout_s:
                        raise BackendError(f"the local model took over {self.timeout_s:.0f} s")
        except httpx.HTTPError as e:
            raise BackendError(f"the local model didn't answer ({type(e).__name__})") from e
        finally:
            if self.http is None:
                await http.aclose()
        return Step(text="".join(text), thinking="".join(thinking), calls=calls, model=self.model,
                    seconds=time.time() - t0)


# ---------------------------------------------------------------- cloud (Claude)
def claude_tools(schemas: list[dict]) -> list[dict]:
    out = []
    for t in schemas:
        if "function" not in t:                     # a server tool or toolset entry: as it is
            out.append(t)
            continue
        f = t["function"]
        out.append({"name": f["name"], "description": (f.get("description") or "")[:900],
                    "input_schema": f.get("parameters") or {"type": "object", "properties": {}}})
    return out


class CloudBackend:
    kind = "cloud"

    def __init__(self, model: str = "claude-haiku-5-5", effort: Optional[str] = "low", claude=None):
        self.model, self.effort = model, effort
        self._claude = claude

    @property
    def claude(self):
        if self._claude is None:
            from core.claude import get_claude
            self._claude = get_claude()
        return self._claude

    def available(self) -> bool:
        try:
            return self.claude.available()
        except Exception:
            return False

    @staticmethod
    def messages(transcript: list[dict]) -> list[dict]:
        out: list[dict] = []

        def push(role: str, blocks: list) -> None:
            if out and out[-1]["role"] == role:
                out[-1]["content"].extend(blocks)
            else:
                out.append({"role": role, "content": list(blocks)})
        for m in transcript:
            if m["role"] == "user":
                blocks = [{"type": "text", "text": m.get("text") or "..."}]
                for i in m.get("images") or []:
                    blocks.append({"type": "image", "source": {"type": "base64", "media_type": i["media_type"],
                                                               "data": i["b64"]}})
                push("user", blocks)
            elif m["role"] == "assistant":
                raw = m.get("raw")
                if m.get("backend") == "cloud" and isinstance(raw, list) and raw:
                    push("assistant", raw)
                    continue
                blocks = [{"type": "text", "text": m["text"]}] if m.get("text") else []
                for c in m.get("calls") or []:
                    blocks.append({"type": "tool_use", "id": c["id"], "name": c["name"], "input": c.get("input") or {}})
                push("assistant", blocks or [{"type": "text", "text": "..."}])
            elif m["role"] == "tool":
                content: Any = m.get("text") or "(no result)"
                if m.get("images"):
                    content = [{"type": "text", "text": content}] + [
                        {"type": "image", "source": {"type": "base64", "media_type": i["media_type"], "data": i["b64"]}}
                        for i in m["images"]]
                block = {"type": "tool_result", "tool_use_id": m["id"], "content": content}
                if m.get("toolset_name"):
                    block["toolset_name"] = m["toolset_name"]
                if m.get("is_error"):
                    block["is_error"] = True
                push("user", [block])
        while out and out[0]["role"] != "user":
            out.pop(0)
        return out

    async def step(self, system: str, transcript: list[dict], tools: list[dict], on_text: TextSink = None,
                   on_thinking: TextSink = None, think: Optional[bool] = None, max_tokens: int = 3000,
                   model: Optional[str] = None, effort: Optional[str] = None, text_only: bool = False) -> Step:
        from core.budget import BudgetExceeded
        from core.claude import ClaudeUnavailable
        t0 = time.time()
        try:                       # text_only: the tools stay defined (earlier tool blocks need them), none is used
            out = await asyncio.to_thread(
                self.claude.message, system=system, messages=self.messages(transcript),
                tools=claude_tools(tools) or None, model=model or self.model, max_tokens=max_tokens,
                effort=(effort or self.effort) if think is not False else None, purpose="brain", cache=True,
                on_text=on_text, tool_choice={"type": "none"} if text_only and tools else None)
        except (BudgetExceeded, ClaudeUnavailable) as e:
            raise BackendError(str(e)) from e
        if out.get("thinking") and on_thinking:
            on_thinking(out["thinking"])
        calls = [{"id": u["id"], "name": u["name"], "input": u.get("input") or {},
                  **({"toolset_name": u["toolset_name"]} if u.get("toolset_name") else {})}
                 for u in out.get("tool_uses") or []]
        return Step(text=out.get("text") or "", thinking=out.get("thinking") or "", calls=calls,
                    cost_eur=float(out.get("cost_eur") or 0), model=model or self.model, raw=out.get("content"),
                    seconds=time.time() - t0)


# ---------------------------------------------------------------- scripted (tests, the JARVIS run's dry mode)
class ScriptedBackend:
    """Plays back steps: a list of Step (or dicts), or a function(transcript, tools) -> Step."""
    kind = "scripted"

    def __init__(self, script, name: str = "scripted"):
        self.script, self.model, self.calls_seen = script, name, []

    async def step(self, system: str, transcript: list[dict], tools: list[dict], on_text: TextSink = None,
                   on_thinking: TextSink = None, think: Optional[bool] = None, max_tokens: int = 1500,
                   text_only: bool = False, **_) -> Step:
        self.calls_seen.append({"transcript": [dict(m) for m in transcript], "tools": [] if text_only else [
            t.get("function", t).get("name") for t in tools]})
        if callable(self.script):
            s = self.script(transcript, tools)
        elif self.script:
            s = self.script.pop(0)
        else:
            s = Step(text="")
        if isinstance(s, Exception):
            raise s
        if isinstance(s, dict):
            s = Step(**s)
        if s.thinking and on_thinking:
            on_thinking(s.thinking)
        if s.text and on_text:
            for word in s.text.split(" "):
                on_text(word + " ")
        s.model = s.model or self.model
        return s
