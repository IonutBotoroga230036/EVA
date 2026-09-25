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
"""

from __future__ import annotations

import os
from typing import Any, Optional

import httpx
from loguru import logger

from core.budget import BudgetExceeded, BudgetTracker, get_budget
from core.security.audit import audit

API_URL = "https://api.anthropic.com/v1/messages"
API_VERSION = "2023-06-01"


class ClaudeUnavailable(RuntimeError):
    pass


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
        self.api_key = api_key if api_key is not None else _load_key()
        self.budget = budget or get_budget()
        self.model = model
        self.http = http or httpx.Client(timeout=timeout)

    def available(self) -> bool:
        return bool(self.api_key)

    def message(self, *, system: str, messages: list[dict], model: Optional[str] = None,
                max_tokens: int = 1500, thinking_budget: Optional[int] = None, effort: Optional[str] = None,
                tools: Optional[list[dict]] = None, tool_choice: Optional[dict] = None,
                purpose: str = "") -> dict:
        if not self.available():
            raise ClaudeUnavailable("no ANTHROPIC_API_KEY (set it in config/secrets.env)")
        model = model or self.model
        worst_in = sum(len(str(m.get("content", ""))) for m in messages) // 3 + len(system) // 3 + 2000
        worst = self.budget.estimate_eur(model, worst_in, max_tokens)
        if not self.budget.can_spend(worst):
            raise BudgetExceeded(f"this call could cost up to EUR {worst:.3f}; the budget doesn't allow it "
                                 f"(EUR {self.budget.remaining_euros():.2f} left)")
        body: dict[str, Any] = {"model": model, "max_tokens": max_tokens, "system": system, "messages": messages}
        if tools:
            body["tools"] = tools
        if tool_choice:
            body["tool_choice"] = tool_choice
        if thinking_budget and not effort:              # legacy callers: map a budget to an effort level
            effort = "high" if thinking_budget >= 8000 else "medium"
        if effort and not tool_choice:                   # forced tool use and thinking don't mix
            body["thinking"] = {"type": "adaptive"}
            body["output_config"] = {"effort": effort}
            body["max_tokens"] = max(max_tokens, 6000)   # room for thinking AND the answer
        data = self._post(body)
        if data is None and "thinking" in body:
            body.pop("thinking")
            body.pop("output_config", None)
            body["max_tokens"] = max_tokens
            data = self._post(body)
        if data is None:
            raise RuntimeError("Claude API rejected the request")
        usage = data.get("usage", {})
        cost = self.budget.record_usage(model, int(usage.get("input_tokens", 0)),
                                        int(usage.get("output_tokens", 0)), purpose=purpose)
        audit.log("claude_call", "claude", {"model": model, "purpose": purpose, "cost_eur": round(cost, 5),
                                            "input_tokens": usage.get("input_tokens"),
                                            "output_tokens": usage.get("output_tokens")})
        out = {"text": "", "thinking": "", "tool_input": None, "cost_eur": cost, "usage": usage,
               "stop_reason": data.get("stop_reason")}
        for block in data.get("content", []):
            if block.get("type") == "text":
                out["text"] += block.get("text", "")
            elif block.get("type") == "thinking":
                out["thinking"] += block.get("thinking", "")
            elif block.get("type") == "tool_use" and out["tool_input"] is None:
                out["tool_input"] = block.get("input")
        logger.info(f"CLAUDE {model} [{purpose}] EUR {cost:.4f} "
                    f"({usage.get('input_tokens')} in / {usage.get('output_tokens')} out)")
        return out

    def _post(self, body: dict) -> Optional[dict]:
        r = self.http.post(API_URL, json=body, headers={
            "x-api-key": self.api_key, "anthropic-version": API_VERSION, "content-type": "application/json"})
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
        _client = ClaudeClient(model=cloud.get("model_heavy", "claude-sonnet-5"))
    return _client
