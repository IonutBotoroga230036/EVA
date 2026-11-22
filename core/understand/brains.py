"""Understanding: which of her skills does this sentence want, and with what details?

Modes (settings: understanding.mode)
  legacy  the original small local model choosing among every tool at once, behind word guards
  local   the same, with understanding.local_model (e.g. qwen3.5:4b)
  jev     Jev picks the skill (fast, with a real confidence), the local model fills in its details
  claude  Claude Haiku picks the skill and fills in the details in one call
  auto    Claude first (picks and fills in details); if Claude fails, Jev + local details; then local

Every decision comes back in the shape the orchestrator already uses: {"tool": name | "none" | "unavailable",
...args}, plus "_by", "_conf" and "_trusted". A trusted decision (Jev sure, or Claude) skips the word guards, so
"check my calendar for any empty slots" reaches calendar_free without "free" being said. Nothing else is skipped:
anything that sends, deletes or spends still asks for your yes.

New skills FORGE builds become choices automatically (their name and description), so nobody has to write word
patterns for them, and "nothing fits" is a real answer that leads to the offer to build one.
"""

from __future__ import annotations

import asyncio
import re
import time
from typing import Optional

from loguru import logger

from core.settings import get_settings
from core.understand.jev import Jev, JevError

NONE_LABEL, MISSING_LABEL = "none", "unavailable"
NONE_TEXT = ("No tool: the person is chatting, asking something E.V.A. can answer from general knowledge or the "
             "conversation itself, thanking her, or reacting to what she said. Also when they ask for a tool but leave "
             "out what it needs and there is no sensible default ('search the web' with no topic, 'set an alarm' "
             "with no time): she asks first.")
MISSING_TEXT = ("None of the other options can do it: the person wants an action done or specific live information "
                "that no listed tool provides (for example booking, ordering, controlling a device she has no tool "
                "for).")
JEV_INSTRUCTIONS = ("E.V.A. is a voice assistant. Each option is one of her tools. Which ONE should she use for the "
                    "user's latest message? Use the recent conversation to understand follow-ups like 'and "
                    "tomorrow?' or 'do it again'. Speech-to-text may mishear words; go by the meaning. If the request lacks what "
                    "the tool needs and has no sensible default, choose none so she can ask.")
CLAUDE_SYSTEM = (
    "You are the decision step of E.V.A., a voice assistant on the user's PC and phone. Read the latest message (it "
    "comes from speech-to-text, so words may be misheard) and call exactly ONE tool that does what the user means, "
    "with its arguments taken from their words. Go by meaning, not keywords: 'queue my music' or 'put something "
    "on' means music; 'any empty slots tomorrow' means free time in the calendar. Use the conversation for "
    "follow-ups. Leave out optional arguments the user didn't give; never invent names, times or text. Act when "
    "there is a sensible default ('play some music' = their liked songs, weather = their city now); when the "
    "request lacks what a tool needs and has no default ('search the web' with no topic, 'set an alarm' with no "
    "time), call just_answer so she can ask. Call just_answer when no tool is needed. Call cannot_do_yet when the user wants something done that none of the "
    "tools can do.")
DEFAULTS = {"mode": "legacy", "local_model": "", "claude_model": "claude-haiku-5-5", "timeout_s": 6.0,
            "jev": {"provider": "typesafe", "model": "", "sure": 0.8}}


def cfg() -> dict:
    u = dict(get_settings().get("understanding") or {})
    out = {**DEFAULTS, **u}
    out["jev"] = {**DEFAULTS["jev"], **(u.get("jev") or {})}
    return out


def _clip(text: str, n: int = 220) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    return text if len(text) <= n else text[:n - 1].rstrip() + "…"


def catalog(schemas: list[dict]) -> dict[str, str]:
    """{tool name: what it does} for every tool she has right now, plus 'none' and 'unavailable'."""
    out = {t["function"]["name"]: _clip(t["function"].get("description", "")) or t["function"]["name"]
           for t in schemas}
    out[NONE_LABEL] = NONE_TEXT
    out[MISSING_LABEL] = MISSING_TEXT
    return out


def transcript(messages: list[dict], turns: int = 4) -> tuple[str, str]:
    """(recent conversation, latest user message), from chat-style history."""
    msgs = [m for m in messages if m.get("role") in ("user", "assistant") and m.get("content")]
    latest = next((m["content"] for m in reversed(msgs) if m["role"] == "user"), "")
    if msgs and msgs[-1]["role"] == "user":
        msgs = msgs[:-1]
    lines = [f"{'User' if m['role'] == 'user' else 'E.V.A.'}: {_clip(m['content'], 300)}" for m in msgs[-turns:]]
    return "\n".join(lines), latest


def jev_state(messages: list[dict]) -> str:
    convo, latest = transcript(messages)
    return (f"Recent conversation:\n{convo}\n\n" if convo else "") + f"Latest message from the user: {latest}"


# ---------------------------------------------------------------- Jev
_jev: Optional[Jev] = None


def get_jev() -> Jev:
    global _jev
    c = cfg()["jev"]
    if _jev is None or _jev.provider != c["provider"]:
        _jev = Jev(provider=c["provider"], model=c.get("model", ""))
    return _jev


async def jev_pick(messages: list[dict], schemas: list[dict], jev: Optional[Jev] = None) -> dict:
    jev = jev or get_jev()
    t0 = time.perf_counter()
    choice, conf, probs = await jev.choose(jev_state(messages), JEV_INSTRUCTIONS, catalog(schemas))
    ms = (time.perf_counter() - t0) * 1000
    top = sorted(probs.items(), key=lambda kv: -kv[1])[:3]
    logger.info(f"UNDERSTAND jev -> {choice} ({conf:.2f}) in {ms:.0f} ms; runners-up {top[1:]}")
    return {"tool": choice or NONE_LABEL, "_by": "jev", "_conf": conf, "_ms": ms, "_top": top}


# ---------------------------------------------------------------- Claude
_SAFE = re.compile(r"[^a-zA-Z0-9_-]")


def claude_tools(schemas: list[dict]) -> tuple[list[dict], dict[str, str]]:
    """Anthropic tool definitions (+ just_answer / cannot_do_yet) and a map back to E.V.A.'s names."""
    tools, back = [], {}
    for t in schemas:
        fn = t["function"]
        safe = _SAFE.sub("_", fn["name"])[:64]
        back[safe] = fn["name"]
        params = dict(fn.get("parameters") or {})
        params.setdefault("type", "object")
        params.setdefault("properties", {})
        tools.append({"name": safe, "description": fn.get("description", "") or fn["name"], "input_schema": params})
    tools.append({"name": "just_answer", "description": NONE_TEXT,
                  "input_schema": {"type": "object", "properties": {}}})
    tools.append({"name": "cannot_do_yet", "description": MISSING_TEXT,
                  "input_schema": {"type": "object", "properties": {}}})
    back.update(just_answer=NONE_LABEL, cannot_do_yet=MISSING_LABEL)
    tools[-1]["cache_control"] = {"type": "ephemeral"}      # the tool list rarely changes: cached, faster and cheaper
    return tools, back


_claude = None


def get_claude_light():
    global _claude
    if _claude is None:
        from core.claude import _load_key
        if not _load_key():                    # no key: say so at once (an HTTPS client costs ~0.5 s on Windows)
            raise RuntimeError("no Claude key")
        from core.claude import ClaudeClient
        _claude = ClaudeClient(model=cfg()["claude_model"], timeout=float(cfg()["timeout_s"]) + 4)
    return _claude


async def claude_pick(messages: list[dict], schemas: list[dict], client=None) -> dict:
    client = client or get_claude_light()
    if not client.available():
        raise RuntimeError("no Claude key")
    tools, back = claude_tools(schemas)
    convo, latest = transcript(messages, turns=6)
    content = (f"Conversation so far:\n{convo}\n\n" if convo else "") + f"Latest message: {latest}"
    t0 = time.perf_counter()
    # a forced tool call answers straight away, without thinking first: that is what keeps this under a second or two
    out = await asyncio.to_thread(client.message, system=CLAUDE_SYSTEM, model=cfg()["claude_model"],
                                  messages=[{"role": "user", "content": content}], tools=tools,
                                  tool_choice={"type": "any"}, max_tokens=400, purpose="understand")
    ms = (time.perf_counter() - t0) * 1000
    name = out.get("tool_name") or ""
    tool = back.get(name, NONE_LABEL)
    args = {k: v for k, v in (out.get("tool_input") or {}).items() if v not in (None, "", [], {})}
    logger.info(f"UNDERSTAND claude -> {tool} {args} in {ms:.0f} ms (EUR {out.get('cost_eur', 0):.5f})")
    return {"tool": tool, **args, "_by": "claude", "_conf": 1.0, "_ms": ms, "_args_done": True}


# ---------------------------------------------------------------- the policy
def cloud_allowed() -> bool:
    """'Local only, nothing leaves this machine' (said by voice or in settings) also covers understanding."""
    try:
        from core.brain import get_brain
        b = get_brain()
        st = b._state()
        said = (st.get("features") or {}).get("understanding")
        if st.get("mode") == "local" and said in (None, "default"):
            return False            # "Eva, local only" by voice beats a settings-file default for understanding
        return ((b.feature_mode("understanding") or b.mode()) in ("auto", "cloud"))
    except Exception:
        return False


async def understand(messages: list[dict], schemas: list[dict], legacy, fill, mode: Optional[str] = None,
                     jev: Optional[Jev] = None, claude=None, private: bool = True) -> dict:
    """legacy(): the original local decision (a coroutine). fill(tool): the local model filling ONE tool's
    arguments. Falls back step by step, so a missing key, no internet or an empty budget never leaves her deaf."""
    c = cfg()
    mode = mode or c["mode"]
    if private and mode in ("jev", "claude", "auto") and not cloud_allowed():
        logger.info(f"UNDERSTAND {mode}: local only was said by voice, so nothing leaves this PC "
                    f"(say 'Eva, use auto for understanding' to allow Jev and Claude again)")
        mode = "local"
    names = {t["function"]["name"]: t for t in schemas}
    if mode in ("legacy", "local"):
        return {**await legacy(), "_by": mode}

    # auto: Claude first. Nov 22 on your phone: Jev chose well but the small local model filled in the details
    # badly ('empty slots for tomorrow' became day='morning'); Claude picks AND fills in one call, ~0.7 s.
    # Jev (+ local details) is the fallback when Claude is slow, down or out of budget.
    if mode in ("claude", "auto"):
        try:
            d = await asyncio.wait_for(claude_pick(messages, schemas, claude), timeout=float(c["timeout_s"]))
            d["_trusted"] = True
            return d
        except Exception as e:                          # no key, budget, offline, timeout: never stay silent
            logger.warning(f"UNDERSTAND claude failed ({e.__class__.__name__}: {str(e)[:120]}); "
                           f"{'trying Jev' if mode == 'auto' else 'using the local model'}")

    if mode in ("jev", "auto"):
        j = jev or get_jev()
        if j.available():
            try:
                d = await asyncio.wait_for(jev_pick(messages, schemas, j), timeout=float(c["timeout_s"]))
                sure = d["_conf"] >= float(c["jev"]["sure"])
                d["_trusted"] = sure
                tool = d["tool"]
                if tool in names and names[tool]["function"].get("parameters", {}).get("properties"):
                    d.update(await fill(tool))
                return d                                  # unsure: the word guards still check it
            except (JevError, asyncio.TimeoutError) as e:
                logger.warning(f"UNDERSTAND jev failed ({e.__class__.__name__}: {e}); falling back")
        elif mode == "jev":
            logger.warning(f"UNDERSTAND mode is jev but there is no {j.key_name}; using the local model")

    return {**await legacy(), "_by": "local-fallback"}
