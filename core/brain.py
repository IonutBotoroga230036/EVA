"""
Brain: where E.V.A.'s heavy thinking happens, in the cloud (Claude) or locally (Ollama).

Three modes, switchable by voice ("switch to local mode"), in the status panel, or in settings (cloud.mode):
    cloud  always Claude (needs ANTHROPIC_API_KEY and budget)
    local  always Ollama: private, free, slower, weaker  (default since v0.2.5: local by default, Claude by choice)
    auto   Claude when a key and budget exist, otherwise local

Per feature (v0.2.5 milestone 7): conversation, planning, thinking and forge can each follow the default or
have their own mode ("use Claude for FORGE", "keep thinking local"). Settings: inference.cloud.features.
    conversation  the conversation lane (milestone 4; stored now, used when that lane lands)
    planning      splitting multi-step commands
    thinking      think_deeply
    forge         writing skills (runs in the background, no time limit)

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
FEATURES = ("conversation", "planning", "thinking", "forge")
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
                "default_mode": s.get("inference", {}).get("cloud", {}).get("mode", "local"),
                "features": s.get("inference", {}).get("cloud", {}).get("features", {}) or {}}

    def _state(self) -> dict:
        try:
            st = json.loads(STATE.read_text(encoding="utf-8"))
            return st if isinstance(st, dict) else {}
        except Exception:
            return {}

    def _save(self, st: dict) -> None:
        STATE.parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text(json.dumps(st), encoding="utf-8")

    def mode(self) -> str:
        m = self._state().get("mode")
        if m in MODES:
            return m
        m = self._cfg()["default_mode"]
        return m if m in MODES else "local"

    def set_mode(self, mode: str) -> str:
        mode = mode if mode in MODES else "local"
        st = self._state()
        st["mode"] = mode
        self._save(st)
        return mode

    def feature_mode(self, feature: str) -> Optional[str]:
        """The feature's own mode, or None when it follows the default."""
        m = (self._state().get("features") or {}).get(feature)
        if m in MODES:
            return m
        if m == "default":
            return None
        m = self._cfg()["features"].get(feature)
        return m if m in MODES else None

    def set_feature(self, feature: str, mode: str) -> Optional[str]:
        if feature not in FEATURES:
            raise ValueError(f"unknown feature {feature!r}")
        st = self._state()
        st.setdefault("features", {})[feature] = mode if mode in MODES else "default"
        self._save(st)
        return self.feature_mode(feature)

    def overview(self) -> dict:
        ready = self.cloud_ready()
        feats = {}
        for f in FEATURES:
            own = self.feature_mode(f)
            m = own or self.mode()
            feats[f] = {"setting": own or "default", "effective": ("cloud" if ready else "local") if m == "auto" else m}
        return {"mode": self.mode(), "features": feats, "cloud_ready": ready}

    def cloud_ready(self, worst_eur: float = 0.15) -> bool:
        try:
            return self.claude.available() and self.claude.budget.can_spend(worst_eur)
        except Exception:
            return False

    def pick(self, feature: Optional[str] = None) -> str:
        m = (self.feature_mode(feature) if feature else None) or self.mode()
        if m == "auto":
            return "cloud" if self.cloud_ready() else "local"
        return m

    def describe(self, job: str) -> str:
        """For confirmations: where the work will happen and what it costs."""
        where = self.pick("forge" if job == "code" else "thinking")
        if where == "cloud":
            return ("in the background using Claude, roughly 10 to 20 cents" if job == "code"
                    else "using Claude, about a cent")
        model = self._cfg()["coder" if job == "code" else "thinker"]
        return (f"locally in the background with {model}, free, it can take a while" if job == "code"
                else f"locally with {model}, free")

    # ------------------------------------------------------------ local calls
    def _ollama(self, model: str, messages: list[dict], fmt: Optional[dict] = None,
                think: bool = False, num_ctx: int = 8192, max_tokens: int = 6000, unlimited: bool = False) -> dict:
        body = {"model": model, "messages": messages, "stream": False, "keep_alive": "5m",
                "options": {"temperature": 0.2, "num_ctx": num_ctx, "num_predict": max_tokens}}
        if fmt:
            body["format"] = fmt
        if think:
            body["think"] = True
        try:
            kw = {"timeout": httpx.Timeout(None, connect=10)} if unlimited else {}   # background FORGE: no time limit
            r = self.http.post(f"{self._cfg()['base_url']}/api/chat", json=body, **kw)
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
        if self.pick("forge") == "cloud":
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
        msg = self._ollama(model, chat, fmt=tool["input_schema"], num_ctx=12288, max_tokens=7000, unlimited=True)
        try:
            data = json.loads(msg.get("content", "") or "{}")
        except json.JSONDecodeError:
            m = re.search(r"\{.*\}", msg.get("content", ""), re.S)
            data = json.loads(m.group(0)) if m else {}
        logger.info(f"BRAIN local {model} [{purpose}] (free)")
        return data, 0.0, model

    def think(self, system: str, question: str, effort: str = "medium") -> tuple[str, float, str]:
        if self.pick("thinking") == "cloud":
            out = self.claude.message(system=system, messages=[{"role": "user", "content": question}],
                                      max_tokens=1500, effort=effort, purpose="think")
            return out["text"], out["cost_eur"], "Claude"
        model = self._cfg()["thinker"]
        msg = self._ollama(model, [{"role": "system", "content": system}, {"role": "user", "content": question}],
                           think=True, num_ctx=8192, max_tokens=3000)
        text = re.sub(r"<think>.*?</think>", "", msg.get("content", ""), flags=re.S).strip()
        logger.info(f"BRAIN local {model} [think] (free)")
        return text, 0.0, model


    def plan_json(self, system: str, text: str, schema: dict) -> Optional[dict]:
        """Planning on Claude when the planning feature says so; None means: plan locally as usual."""
        if self.pick("planning") != "cloud":
            return None
        tool = {"name": "plan", "description": "Return the plan.", "input_schema": schema}
        out = self.claude.message(system=system, messages=[{"role": "user", "content": text}], max_tokens=800,
                                  tools=[tool], tool_choice={"type": "tool", "name": "plan"}, purpose="planning")
        return out.get("tool_input") or {}


_brain: Brain | None = None


def get_brain() -> Brain:
    global _brain
    if _brain is None:
        _brain = Brain()
    return _brain
