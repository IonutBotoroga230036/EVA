"""
Brain: where E.V.A.'s heavy thinking happens, in the cloud (Claude) or locally (Ollama).

Used by FORGE (writing code) and deep thinking. Three modes, switchable by voice
("switch to local mode") or in settings (cloud.mode):

    cloud  always Claude (needs ANTHROPIC_API_KEY and budget)
    local  always Ollama: private, free, slower, weaker
    auto   Claude when a key and budget exist, otherwise local  (default)

Local models (settings inference.local):
    coder_model  qwen2.5-coder:7b   ~4.7 GB. FORGE code. Ollama swaps it in for the job,
                                    which unloads the 3B for a minute; that's fine for a rare task.
    think_model  qwen3:4b           ~2.6 GB, has a thinking mode. Fits next to the 3B.
Pull them once:  ollama pull qwen2.5-coder:7b   and   ollama pull qwen3:4b
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional

import httpx
from loguru import logger

MODES = ("auto", "cloud", "local")
STATE = Path("data/brain_mode.json")


class LocalUnavailable(RuntimeError):
    pass


class Brain:
    def __init__(self, claude=None, http: Optional[httpx.Client] = None):
        self._claude = claude
        self.http = http or httpx.Client(timeout=900)

    # ------------------------------------------------------------ settings
    @property
    def claude(self):
        if self._claude is None:
            from core.claude import get_claude
            self._claude = get_claude()
        return self._claude

    def _cfg(self) -> dict:
        from core.settings import get_settings, local_cfg
        loc = local_cfg()
        s = get_settings()
        return {"base_url": loc["base_url"], "coder": loc.get("coder_model", "qwen2.5-coder:7b"),
                "thinker": loc.get("think_model", "qwen3:4b"),
                "default_mode": s.get("inference", {}).get("cloud", {}).get("mode", "auto")}

    def mode(self) -> str:
        try:
            m = json.loads(STATE.read_text(encoding="utf-8"))["mode"]
            if m in MODES:
                return m
        except Exception:
            pass
        m = self._cfg()["default_mode"]
        return m if m in MODES else "auto"

    def set_mode(self, mode: str) -> str:
        mode = mode if mode in MODES else "auto"
        STATE.parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text(json.dumps({"mode": mode}), encoding="utf-8")
        return mode

    def cloud_ready(self, worst_eur: float = 0.15) -> bool:
        try:
            return self.claude.available() and self.claude.budget.can_spend(worst_eur)
        except Exception:
            return False

    def pick(self) -> str:
        m = self.mode()
        if m == "auto":
            return "cloud" if self.cloud_ready() else "local"
        return m

    def describe(self, job: str) -> str:
        """For confirmations: where the work will happen and what it costs."""
        where = self.pick()
        if where == "cloud":
            return "using Claude, roughly 10 to 20 cents" if job == "code" else "using Claude, about a cent"
        model = self._cfg()["coder" if job == "code" else "thinker"]
        return f"locally with {model}, free but slower, a few minutes" if job == "code" else f"locally with {model}, free"

    # ------------------------------------------------------------ local calls
    def _ollama(self, model: str, messages: list[dict], fmt: Optional[dict] = None,
                think: bool = False, num_ctx: int = 8192, max_tokens: int = 6000) -> dict:
        body = {"model": model, "messages": messages, "stream": False, "keep_alive": "5m",
                "options": {"temperature": 0.2, "num_ctx": num_ctx, "num_predict": max_tokens}}
        if fmt:
            body["format"] = fmt
        if think:
            body["think"] = True
        try:
            r = self.http.post(f"{self._cfg()['base_url']}/api/chat", json=body)
            if r.status_code == 400 and think:          # model or Ollama without thinking support
                body.pop("think")
                r = self.http.post(f"{self._cfg()['base_url']}/api/chat", json=body)
            if r.status_code == 404:
                raise LocalUnavailable(f"the local model {model} isn't installed. Run: ollama pull {model}")
            r.raise_for_status()
            return r.json().get("message", {})
        except LocalUnavailable:
            raise
        except Exception as e:
            raise LocalUnavailable(f"local model {model} failed: {e}")

    # ------------------------------------------------------------ jobs
    def code_json(self, system: str, messages: list[dict], tool: dict, purpose: str = "forge") -> tuple[dict, float, str]:
        """A structured answer following tool['input_schema']. Returns (data, cost_eur, provider label)."""
        if self.pick() == "cloud":
            out = self.claude.message(system=system, messages=messages, max_tokens=8000, tools=[tool],
                                      tool_choice={"type": "tool", "name": tool["name"]}, purpose=purpose)
            return out.get("tool_input") or {}, out["cost_eur"], "Claude"
        model = self._cfg()["coder"]
        chat = [{"role": "system", "content": system + "\n\nReply ONLY with the JSON object."}]
        for m in messages:
            content = m["content"]
            if isinstance(content, list):
                content = " ".join(b.get("text", "") for b in content if isinstance(b, dict))
            chat.append({"role": m["role"], "content": content})
        msg = self._ollama(model, chat, fmt=tool["input_schema"], num_ctx=12288, max_tokens=7000)
        try:
            data = json.loads(msg.get("content", "") or "{}")
        except json.JSONDecodeError:
            m = re.search(r"\{.*\}", msg.get("content", ""), re.S)
            data = json.loads(m.group(0)) if m else {}
        logger.info(f"BRAIN local {model} [{purpose}] (free)")
        return data, 0.0, model

    def think(self, system: str, question: str, effort: str = "medium") -> tuple[str, float, str]:
        if self.pick() == "cloud":
            out = self.claude.message(system=system, messages=[{"role": "user", "content": question}],
                                      max_tokens=1500, effort=effort, purpose="think")
            return out["text"], out["cost_eur"], "Claude"
        model = self._cfg()["thinker"]
        msg = self._ollama(model, [{"role": "system", "content": system}, {"role": "user", "content": question}],
                           think=True, num_ctx=8192, max_tokens=3000)
        text = re.sub(r"<think>.*?</think>", "", msg.get("content", ""), flags=re.S).strip()
        logger.info(f"BRAIN local {model} [think] (free)")
        return text, 0.0, model


_brain: Brain | None = None


def get_brain() -> Brain:
    global _brain
    if _brain is None:
        _brain = Brain()
    return _brain
