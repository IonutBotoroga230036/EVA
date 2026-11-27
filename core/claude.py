"""
Claude API client for the few jobs a 3B local model can't do well: writing code
(FORGE) and careful multi-step reasoning (deep thinking).

- Plain HTTP to /v1/messages (no SDK version drift).
- API key from the ANTHROPIC_API_KEY environment variable or config/secrets.env
  (git-ignored). Never logged.
- Every call is checked against VAULT BEFORE it is sent (daily and monthly caps,
  using a worst-case estimate) and recorded AFTER with the real token counts.
- Thinking: Claude 5 models only accept ADAPTIVE thinking ({"type": "adaptive"})
  with depth set by output_config.effort (low | medium | high | max). Thinking
  tokens count toward max_tokens. If a model rejects it, the call is retried once
  without thinking.
- Tool-forced calls (tool_choice) return structured input: that is how FORGE gets
  a skill back as reliable JSON instead of parsing prose.
- v0.8: every call streams (server-sent events), so a long call never sits silent on the line (on 27 Nov
  something on his connection cut calls after about 15 quiet seconds: three tasks crashed). on_text gets the
  answer's words as they come (the mind speaks them). A call that breaks before any words arrived is sent
  again (twice, after 1.5 s and 4 s); once words were delivered it raises ClaudeCut with what came.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any, Callable, Optional

import httpx
from loguru import logger

from core.budget import BudgetExceeded, BudgetTracker, get_budget
from core.security.audit import audit

API_URL = "https://api.anthropic.com/v1/messages"
API_VERSION = "2023-06-01"
TOOLSET_API_VERSION = "2026-10-21"     # v0.7: the computer and browser toolsets' docs show this version


class ClaudeUnavailable(RuntimeError):
    pass


class ClaudeCut(RuntimeError):
    """The line broke after part of the answer was already delivered to on_text."""
    def __init__(self, msg: str, partial: str = ""):
        super().__init__(msg)
        self.partial = partial


class _StreamError(RuntimeError):
    def __init__(self, kind: str, msg: str):
        super().__init__(f"Claude API stream error {kind}: {msg}")
        self.kind = kind


TRANSIENT_STATUS = {408, 429, 500, 502, 503, 504, 529}
RETRY_WAITS = (1.5, 4.0)                 # tests set () to fail at once
STREAM = True                            # tests can switch it off
FIRST_BYTE_S = 9.0                       # v0.9: an answer that hasn't started by then is asked again
STREAM_READ_S = 90.0                     # once it streams: the longest quiet gap allowed


class _Resp:
    """What _send returns, streamed or not: status_code, text and json()."""
    def __init__(self, status_code: int, text: str = "", data: Optional[dict] = None):
        self.status_code, self.text, self._data = status_code, text, data

    def json(self) -> dict:
        return self._data if self._data is not None else json.loads(self.text)


def read_stream(lines, on_text: Optional[Callable[[str], None]] = None) -> dict:
    """Assemble a Messages API event stream into the same message a non-streamed call returns."""
    msg: dict = {"content": [], "usage": {}}
    blocks: dict[int, dict] = {}
    partial: dict[int, str] = {}
    done = False
    for line in lines:
        if not line or not line.startswith("data:"):
            continue
        try:
            ev = json.loads(line[5:].strip())
        except ValueError:
            continue
        kind = ev.get("type")
        if kind == "message_start":
            m = ev.get("message") or {}
            msg.update({k: v for k, v in m.items() if k != "content"})
            msg["usage"] = dict(m.get("usage") or {})
        elif kind == "content_block_start":
            b = dict(ev.get("content_block") or {})
            blocks[int(ev.get("index", len(blocks)))] = b
        elif kind == "content_block_delta":
            i, d = int(ev.get("index", 0)), ev.get("delta") or {}
            b = blocks.setdefault(i, {"type": "text", "text": ""})
            dt = d.get("type")
            if dt == "text_delta":
                b["text"] = b.get("text", "") + d.get("text", "")
                if on_text and d.get("text"):
                    on_text(d["text"])
            elif dt == "input_json_delta":
                partial[i] = partial.get(i, "") + d.get("partial_json", "")
            elif dt == "thinking_delta":
                b["thinking"] = b.get("thinking", "") + d.get("thinking", "")
            elif dt == "signature_delta":
                b["signature"] = b.get("signature", "") + d.get("signature", "")
            elif dt == "citations_delta" and d.get("citation"):
                b.setdefault("citations", []).append(d["citation"])
        elif kind == "content_block_stop":
            i = int(ev.get("index", 0))
            b = blocks.get(i)
            if b is not None and i in partial:
                try:
                    b["input"] = json.loads(partial.pop(i)) if partial.get(i, "").strip() else (b.get("input") or {})
                except ValueError:
                    b["input"] = b.get("input") or {}
        elif kind == "message_delta":
            for k, v in (ev.get("delta") or {}).items():
                msg[k] = v
            msg["usage"].update(ev.get("usage") or {})             # cumulative counts
            if ev.get("input_transformations"):
                msg["input_transformations"] = ev["input_transformations"]
        elif kind == "message_stop":
            done = True
            break
        elif kind == "error":
            err = ev.get("error") or {}
            raise _StreamError(str(err.get("type") or "error"), str(err.get("message") or "")[:200])
    if not done:
        raise httpx.RemoteProtocolError("the stream ended before message_stop")
    for i, raw in partial.items():                              # a tool block that never got its stop
        try:
            blocks[i]["input"] = json.loads(raw) if raw.strip() else {}
        except ValueError:
            blocks[i]["input"] = {}
    msg["content"] = [blocks[i] for i in sorted(blocks)]
    msg.setdefault("type", "message")
    return msg


def _tokens_in(content) -> int:
    """A worst-case token count for the budget check. A screenshot is about 1,600 tokens, not a third of its base64
    (Nov 27 10:03: a computer task 'could cost more' than the 91 cents left: each picture counted as 200k tokens)."""
    if isinstance(content, str):
        return len(content) // 3
    if isinstance(content, list):
        return sum(_tokens_in(b) for b in content)
    if isinstance(content, dict):
        kind = content.get("type")
        if kind == "image":
            return 1600
        if kind == "document":
            return 4000
        return sum(_tokens_in(v) for k, v in content.items() if k in ("text", "content", "input", "thinking"))
    return len(str(content)) // 3


def line_trouble(e: BaseException) -> bool:
    """A dropped connection, an overloaded API or a cut stream: worth waiting for, not a broken request."""
    import re
    if isinstance(e, (httpx.TransportError, ClaudeCut, _StreamError, TimeoutError, ConnectionError)):
        return True
    return isinstance(e, RuntimeError) and bool(re.search(r"Claude API error (5\d\d|429|408)", str(e)))


class ThinkingBound(RuntimeError):
    """Nov 25: a thinking block no longer matches what came before it (something earlier in the conversation
    changed). Sonnet/Opus/Haiku 5.5 bind each thinking block to its prefix: system, tools and earlier messages."""


BINDING_BETA = "thinking-binding-controls-2026-08-01"


def _load_key() -> Optional[str]:
    if os.environ.get("ANTHROPIC_API_KEY"):
        return os.environ["ANTHROPIC_API_KEY"]
    try:
        from core.security.vault import Vault
        v = Vault()
        v.load()
        return v.get_optional("ANTHROPIC_API_KEY")
    except Exception:
        return None


class ClaudeClient:
    def __init__(self, api_key: Optional[str] = None, budget: Optional[BudgetTracker] = None,
                 model: str = "claude-sonnet-5", http: Optional[httpx.Client] = None, timeout: float = 240.0):
        self._toolset_version_ok = True
        self.api_key = api_key if api_key is not None else _load_key()
        self.budget = budget or get_budget()
        self.model = model
        self.http = http or httpx.Client(timeout=timeout)

    def available(self) -> bool:
        return bool(self.api_key)

    def message(self, *, system: str, messages: list[dict], model: Optional[str] = None,
                max_tokens: int = 1500, thinking_budget: Optional[int] = None, effort: Optional[str] = None,
                tools: Optional[list[dict]] = None, tool_choice: Optional[dict] = None,
                purpose: str = "", cache: bool = False, thinking: Optional[dict] = None,
                betas: Optional[list[str]] = None, on_text: Optional[Callable[[str], None]] = None) -> dict:
        if not self.available():
            raise ClaudeUnavailable("no ANTHROPIC_API_KEY (set it in config/secrets.env)")
        model = model or self.model
        worst_in = sum(_tokens_in(m.get("content", "")) for m in messages) + len(system) // 3 + 2000
        worst = self.budget.estimate_eur(model, worst_in, max_tokens)
        if not self.budget.can_spend(worst):
            why = self.budget.why_not(worst) if hasattr(self.budget, "why_not") else ""
            raise BudgetExceeded(f"this call could cost up to EUR {worst:.3f}; the budget doesn't allow it "
                                 f"(EUR {self.budget.remaining_euros():.2f} left)" + (f": {why}" if why else ""))
        body: dict[str, Any] = {"model": model, "max_tokens": max_tokens, "system": system, "messages": messages}
        if tools:
            body["tools"] = tools
        if tool_choice:
            body["tool_choice"] = tool_choice
        if cache:                                        # v0.4: automatic prompt caching (long agent loops)
            body["cache_control"] = {"type": "ephemeral"}
        if thinking_budget and not effort:              # legacy callers: map a budget to an effort level
            effort = "high" if thinking_budget >= 8000 else "medium"
        if effort and not tool_choice:                   # forced tool use and thinking don't mix
            body["thinking"] = {"type": "adaptive"}
            body["output_config"] = {"effort": effort}
            body["max_tokens"] = max(max_tokens, 6000)   # room for thinking AND the answer
        if thinking:                                     # v0.4 tasks: adaptive thinking with block_binding
            body["thinking"] = dict(thinking)
            return self._finish(model, purpose, self._post_thinking(body, betas or [], on_text))
        data = self._post(body, on_text)
        if data is None and "thinking" in body:
            body.pop("thinking")
            body.pop("output_config", None)
            body["max_tokens"] = max_tokens
            data = self._post(body, on_text)
        if data is None:
            raise RuntimeError("Claude API rejected the request")
        return self._finish(model, purpose, data)

    def _finish(self, model: str, purpose: str, data: dict) -> dict:
        usage = data.get("usage", {})
        # cache writes cost 1.25x input, reads 0.1x (counted as input-token equivalents)
        eff_in = int(usage.get("input_tokens", 0)) + round(1.25 * int(usage.get("cache_creation_input_tokens") or 0)) \
            + round(0.1 * int(usage.get("cache_read_input_tokens") or 0))
        cost = self.budget.record_usage(model, eff_in, int(usage.get("output_tokens", 0)), purpose=purpose)
        audit.log("claude_call", "claude", {"model": model, "purpose": purpose, "cost_eur": round(cost, 5),
                                            "input_tokens": usage.get("input_tokens"),
                                            "output_tokens": usage.get("output_tokens")})
        searches = int((usage.get("server_tool_use") or {}).get("web_search_requests", 0) or 0)
        if searches:                                     # the web search tool: $10 per 1,000 searches
            from core.budget import USD_TO_EUR
            cost += self.budget.record_extra(searches * 0.01 * USD_TO_EUR, purpose=purpose, what="web_search")
        out = {"text": "", "thinking": "", "tool_input": None, "tool_name": None, "cost_eur": cost, "usage": usage,
               "stop_reason": data.get("stop_reason"), "searches": searches,
               "content": data.get("content", []), "tool_uses": [],     # v0.4: the whole turn, for tool loops
               "input_transformations": data.get("input_transformations") or []}
        for block in data.get("content", []):
            if block.get("type") == "text":
                out["text"] += block.get("text", "")
            elif block.get("type") == "thinking":
                out["thinking"] += block.get("thinking", "")
            elif block.get("type") == "tool_use":
                out["tool_uses"].append({"id": block.get("id"), "name": block.get("name"),
                                         "input": block.get("input") or {},
                                         **({"toolset_name": block["toolset_name"]} if block.get("toolset_name") else {})})
                if out["tool_input"] is None:
                    out["tool_input"] = block.get("input")
                    out["tool_name"] = block.get("name")
        logger.info(f"CLAUDE {model} [{purpose}] EUR {cost:.4f} "
                    f"({usage.get('input_tokens')} in / {usage.get('output_tokens')} out)")
        return out

    def _post_thinking(self, body: dict, betas: list[str], on_text=None) -> dict:
        """Thinking set by the caller: never silently dropped. The binding error becomes ThinkingBound, so the caller
        can switch that conversation to drop_block. If the beta isn't accepted, once more without block_binding."""
        r = self._send(body, betas, on_text)
        if r.status_code == 400 and "bound to a different conversation" in r.text:
            raise ThinkingBound(r.text[:300])
        if r.status_code == 400 and "block_binding" in body.get("thinking", {}) and (
                "anthropic-beta" in r.text or "block_binding" in r.text):
            logger.warning(f"Claude didn't take the thinking binding setting ({r.text[:160]}); sending without it")
            body["thinking"] = {k: v for k, v in body["thinking"].items() if k != "block_binding"}
            r = self._send(body, [b for b in betas if b != BINDING_BETA], on_text)
            if r.status_code == 400 and "bound to a different conversation" in r.text:
                raise ThinkingBound(r.text[:300])
        if r.status_code >= 400:
            raise RuntimeError(f"Claude API error {r.status_code}: {r.text[:300]}")
        return r.json()

    def _once(self, body: dict, headers: dict, on_text=None):
        """One request: streamed when the client can (every real one), else a plain POST (simple test fakes)."""
        if not (STREAM and hasattr(self.http, "stream")):
            return self.http.post(API_URL, json=body, headers=headers)
        got: list[str] = []
        t0, phase = time.time(), "waiting for an answer"

        def relay(t: str) -> None:
            got.append(t)
            if on_text:
                on_text(t)
        # v0.9: on 27 Nov 10:02 two calls in a row got nothing for 15 s, then "Server disconnected"; the third
        # answered in a second. Wait FIRST_BYTE_S for the answer to start, then try again; once it streams, be patient.
        # v1.1: a bigger request takes longer to start (Nov 27: 52 to 57 KB Haiku calls hit 9 s, then answered
        # at once on the retry): up to 14 s, below the 15 s at which his line drops idle connections.
        size_kb = len(json.dumps(body, default=str)) / 1024 if FIRST_BYTE_S else 0
        timeout = {"connect": 6.0, "read": min(14.0, FIRST_BYTE_S + size_kb / 25) if FIRST_BYTE_S else FIRST_BYTE_S,
                   "write": 60.0, "pool": 10.0}
        try:
            with self.http.stream("POST", API_URL, json={**body, "stream": True}, headers=headers,
                                  timeout=httpx.Timeout(**timeout)) as r:
                phase = "streaming"
                try:
                    r.request.extensions["timeout"]["read"] = STREAM_READ_S
                except Exception:
                    pass
                if r.status_code >= 400:
                    r.read()
                    return _Resp(r.status_code, r.text)
                if "text/event-stream" not in r.headers.get("content-type", ""):
                    r.read()                              # a server (or test) that answered in one piece
                    return _Resp(r.status_code, r.text)
                return _Resp(r.status_code, data=read_stream(r.iter_lines(), relay))
        except (httpx.TransportError, _StreamError) as e:
            try:
                kb = len(json.dumps(body)) // 1024
            except Exception:
                kb = -1
            logger.warning(f"CLAUDE: {type(e).__name__} after {time.time() - t0:.1f} s, {phase}"
                           f"{f', {len(got)} pieces of text in' if got else ''} (model {body.get('model')}, "
                           f"request {kb} KB)")
            if got:                                       # words were already spoken: can't quietly start over
                raise ClaudeCut(f"the line broke mid-answer ({e})", "".join(got)) from e
            raise

    def _retrying(self, body: dict, headers: dict, on_text=None):
        waits = list(RETRY_WAITS)
        while True:
            try:
                r = self._once(body, headers, on_text)
                if r.status_code not in TRANSIENT_STATUS or not waits:
                    return r
                why = f"HTTP {r.status_code}"
            except ClaudeCut:
                raise
            except (httpx.TransportError, _StreamError) as e:
                if not waits or (isinstance(e, _StreamError) and e.kind not in (
                        "overloaded_error", "api_error", "rate_limit_error", "timeout_error")):
                    raise
                why = f"{type(e).__name__}: {str(e)[:80]}"
            wait = waits.pop(0)
            logger.warning(f"CLAUDE: {why}; trying again in {wait:g} s")
            time.sleep(wait)

    def _send(self, body: dict, betas: Optional[list[str]] = None, on_text=None):
        toolsets = any(isinstance(t, dict) and str(t.get("type", "")).endswith("toolset_20260801")
                       for t in body.get("tools") or [])
        version = TOOLSET_API_VERSION if toolsets and self._toolset_version_ok else API_VERSION
        headers = {"x-api-key": self.api_key, "anthropic-version": version, "content-type": "application/json"}
        if betas:
            headers["anthropic-beta"] = ",".join(betas)
        r = self._retrying(body, headers, on_text)
        if toolsets and r.status_code == 400 and "anthropic-version" in r.text:
            # v0.7: the docs show the toolsets with a newer API version; if that one is refused (or required),
            # flip once and remember
            self._toolset_version_ok = not self._toolset_version_ok
            headers["anthropic-version"] = TOOLSET_API_VERSION if self._toolset_version_ok else API_VERSION
            logger.warning(f"Claude: anthropic-version {version} refused with the toolsets; trying "
                           f"{headers['anthropic-version']}")
            r = self._retrying(body, headers, on_text)
        return r

    def _post(self, body: dict, on_text=None) -> Optional[dict]:
        r = self._send(body, None, on_text)
        if r.status_code == 400 and "thinking" in body:
            logger.warning(f"Claude rejected extended thinking ({r.text[:160]}); retrying without it")
            return None
        if r.status_code >= 400:
            raise RuntimeError(f"Claude API error {r.status_code}: {r.text[:300]}")
        return r.json()


_client: ClaudeClient | None = None


def get_claude() -> ClaudeClient:
    global _client
    if _client is None:
        from core.settings import get_settings
        cloud = get_settings().get("inference", {}).get("cloud", {})
        http = None
        if cloud.get("http2"):                            # v0.9: try it if lines keep dropping (pip install h2)
            try:
                import h2  # noqa: F401
                http = httpx.Client(timeout=240.0, http2=True)
                logger.info("CLAUDE: HTTP/2 on")
            except ImportError:
                logger.warning("CLAUDE: inference.cloud.http2 is on but h2 isn't installed (pip install h2)")
        _client = ClaudeClient(model=cloud.get("model_heavy", "claude-sonnet-5"), http=http)
    return _client
