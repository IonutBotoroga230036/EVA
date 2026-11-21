"""Jev (TypeSafe): a fast decision model. It doesn't write text; it picks one of labelled choices and says how sure
it is, with real probabilities. E.V.A. uses it for the first question of every turn: which of her skills fits?

Two ways in, same request and answer shape (checked Nov 21, 2026):
  typesafe    POST https://api.typesafe.ai/v1/systemone      model "jev-latest"        key TYPESAFE_API_KEY
  openrouter  POST https://openrouter.ai/api/alpha/decisions  model "typesafe/jev-1.13" key OPENROUTER_API_KEY
Keys live in config/secrets.env (or the environment), never in settings files, logs or chat.
"""

from __future__ import annotations

import os
import time
from typing import Optional

import httpx
from loguru import logger

PROVIDERS = {
    "typesafe": {"url": "https://api.typesafe.ai/v1/systemone", "model": "jev-latest", "key": "TYPESAFE_API_KEY"},
    "openrouter": {"url": "https://openrouter.ai/api/alpha/decisions", "model": "typesafe/jev-1.13",
                   "key": "OPENROUTER_API_KEY"},
}
MAX_CHOICES = 255                      # the API's limit per choice question


def _secret(name: str) -> Optional[str]:
    if os.environ.get(name):
        return os.environ[name]
    try:
        from core.security.vault import Vault
        v = Vault()
        v.load()
        return v.get_optional(name)
    except Exception:
        return None


class JevError(RuntimeError):
    pass


class Jev:
    def __init__(self, provider: str = "typesafe", model: str = "", api_key: Optional[str] = None,
                 timeout: float = 4.0, http: Optional[httpx.AsyncClient] = None):
        if provider not in PROVIDERS:
            raise ValueError(f"unknown Jev provider {provider!r}: use {', '.join(PROVIDERS)}")
        p = PROVIDERS[provider]
        self.provider, self.url = provider, p["url"]
        self.model = model or p["model"]
        self.api_key = api_key if api_key is not None else _secret(p["key"])
        self.key_name = p["key"]
        self.timeout = timeout
        self._http = http

    def available(self) -> bool:
        return bool(self.api_key)

    async def ask(self, state, questions: dict) -> dict:
        """One request, several independent questions about the same state. Returns {name: answer}."""
        if not self.api_key:
            raise JevError(f"no {self.key_name} in config/secrets.env")
        body = {"model": self.model, "state": state, "questions": questions}
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        t0 = time.perf_counter()
        http = self._http or httpx.AsyncClient(timeout=self.timeout)
        try:
            r = await http.post(self.url, json=body, headers=headers)
        except httpx.HTTPError as e:
            raise JevError(f"Jev didn't answer ({e.__class__.__name__})") from e
        finally:
            if self._http is None:
                await http.aclose()
        if r.status_code >= 400:
            raise JevError(f"Jev error {r.status_code}: {r.text[:200]}")
        data = r.json()
        logger.debug(f"JEV {self.provider} {(time.perf_counter() - t0) * 1000:.0f} ms")
        return data.get("answers", {})

    async def choose(self, state, instructions: str, criteria: dict[str, str]) -> tuple[Optional[str], float, dict]:
        """('calendar_free', 0.93, {label: probability}). (None, 0, {}) when Jev refuses."""
        if len(criteria) > MAX_CHOICES:
            raise JevError(f"{len(criteria)} choices; Jev takes at most {MAX_CHOICES}")
        ans = (await self.ask(state, {"action": {"type": "choice", "instructions": instructions,
                                                 "criteria": criteria}})).get("action") or {}
        if ans.get("type") != "choice" or not ans.get("choice"):
            return None, 0.0, {}
        probs = {k: float(v) for k, v in (ans.get("probabilities") or {}).items()}
        conf = float(ans.get("confidence", probs.get(ans["choice"], 0.0)))
        return ans["choice"], conf, probs
