"""One mind (v0.5): sentences that need thinking get a reasoning loop with all of E.V.A.'s tools.

Reflexes stay reflexes: Jev or Haiku picks a tool for a clear command ("lights off", "next train", "remind me at
5"), it runs, she says its line. Same speed, same cost as before. Everything that needs thinking comes here:

    "how can I see the files?"  -> check_installed(file_type="stl") -> "Nothing on your PC opens STL files yet.
                                   I can show the stand here, or install PrusaSlicer to open them. Which?"
    "what would you do?", "compare these", vague requests, several steps, "can you ...?" questions,
    or when understanding says work_it_out / no tool fits.

The mind (Claude Haiku 5.5, `mind.model`; `think_harder` switches to Sonnet for the rest of the turn) sees the
conversation, the situation (time, place, open tasks, what the last task made), what you've told her, and every
tool. It looks things up instead of guessing, offers options when there's a real choice, and phrases the answer
itself. Anything that changes something still goes through the usual "Just to confirm, sir" (the turn ends
there; your yes runs it). Big jobs become background tasks (task_start). Local only, no key, or no budget: the old
way, unchanged.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

DEFAULTS = {"enabled": True, "model": "claude-haiku-5-5", "hard_model": "claude-sonnet-5-5", "max_steps": 6,
            "max_tokens": 3000, "effort": "medium", "hard_effort": "medium", "history_turns": 10}
# effort: Haiku 5.5's default. The docs warn that at "low" it skips checks and searches more often, and checking
# instead of guessing is the point of the mind.
SKIP_TOOLS = {"work_it_out"}
MIND_ONLY = {"look_at_shown"}          # needs eyes: a reflex can't look at a picture, so understanding hands it over

THINK_HARDER = {"name": "think_harder",
                "description": "Switch to a stronger model for the rest of this answer, for genuinely hard "
                               "questions: designing, tricky planning, debugging, careful comparisons. Not for "
                               "simple things.",
                "input_schema": {"type": "object", "properties": {"why": {"type": "string"}}}}

WORK_IT_OUT = {"type": "function", "function": {
    "name": "work_it_out",
    "description": "The request needs thinking, not one command: advice or how-to, explaining, comparing, "
                   "questions about her own work or files ('how can I see the files?'), 'can you...' questions, "
                   "vague or several-step requests, or when no single tool does exactly what is asked.",
    "parameters": {"type": "object", "properties": {}}}}


# personal chat stays with the conversation lane (feelings, herself, "let's talk"); advice and questions come here
_CHAT = re.compile(r"\b(how do you feel|how are you|how'?s it going|how have you been|do you (?:like|enjoy|feel|ever|love)|"
                   r"are you (?:happy|ok|okay|alive|real|conscious)|who are you|tell me about (?:yourself|you)|"
                   r"let'?s (?:talk|chat)|can we (?:talk|chat)|i feel\b|i'?m feeling|i'?ve been thinking|be honest|"
                   r"why do you|what do you (?:think|know) (?:of|about) me|would you rather|what'?s next for you|"
                   r"improve yourself|more useful)\b", re.I)


def is_chat(text: str) -> bool:
    return bool(_CHAT.search(text or ""))


def cfg() -> dict:
    try:
        from core.settings import get_settings
        return {**DEFAULTS, **(get_settings().get("mind") or {})}
    except Exception:
        return dict(DEFAULTS)


_why_off = {"said": None}


def why_off() -> str:
    """'' when the mind may think, otherwise the reason (logged once, so a quiet mind is never a mystery)."""
    if not cfg().get("enabled", True):
        return "mind.enabled is false in the settings"
    try:
        from core.brain import get_brain
        from core.claude import get_claude
        b = get_brain()
        said = (b._state().get("features") or {}).get("mind")
        if said in ("local",):
            return "you set the mind to local"
        if said in ("auto", "cloud"):
            pass                                   # you switched it on by voice
        elif (b._cfg()["features"].get("mind") or "") == "local":
            return "inference.features.mind is local in the settings"
        else:
            # Nov 25: it follows understanding. His saved state was "local" with understanding on Claude, so the
            # mind (a new feature he had never chosen for) stayed off all evening
            from core.understand.brains import cloud_allowed
            if not cloud_allowed():
                return "understanding is local only, so the mind is too"
        if not get_claude().available():
            return "no Claude key (ANTHROPIC_API_KEY)"
        return ""
    except Exception as e:
        return f"couldn't check ({e})"


def allowed() -> bool:
    """On, Claude reachable, and not local only: the mind may use the cloud whenever understanding may, unless you
    set the mind itself to local ("Eva, use local for the mind")."""
    reason = why_off()
    if reason and _why_off["said"] != reason:
        from loguru import logger
        logger.info(f"MIND: off ({reason}); open questions go the old way")
    _why_off["said"] = reason
    return not reason


def tools_for(schemas: list[dict]) -> list[dict]:
    """E.V.A.'s tools (OpenAI style) as Claude tools, same order every time (so the prompt cache holds)."""
    out = []
    for t in schemas:
        f = t.get("function") or {}
        if not f.get("name") or f["name"] in SKIP_TOOLS:
            continue
        params = f.get("parameters") or {"type": "object", "properties": {}}
        out.append({"name": f["name"], "description": (f.get("description") or "")[:900], "input_schema": params})
    return out + [THINK_HARDER]


def _about_him() -> str:
    parts = []
    for name in ("EVA.md", "EVA.local.md"):
        p = Path(name)
        if p.exists():
            parts.append(p.read_text(encoding="utf-8", errors="replace")[:3000])
    return "\n\n".join(parts)[:5000] or "(nothing written yet)"


def system_prompt() -> str:
    """Written once per session and resent unchanged: the cache and her thinking both depend on it."""
    return f"""You are E.V.A., Ionut's personal assistant (like J.A.R.V.I.S. or EDITH): warm, sharp, a step ahead.
You run on his PC in Breda and talk to him by voice. Address him as "sir".

You are the part of E.V.A. that thinks things through. Clear commands never reach you; you get what needs
reasoning: questions, advice, how-to, vague or several-step requests, things no single tool does.

How you work:
- Find out instead of guessing: use tools for anything live (time, weather, files, what's installed, his
  calendar, the web). Never state a live fact no tool gave you.
- Think like a real assistant: what does he actually want, what would help most, what could you do for him
  right now? When there's a real choice, offer two or three concrete options with your pick ("I can show it
  here, or install PrusaSlicer so you can open it on the PC. I'd show it here first."), then ask ONE question.
- Act when he clearly asked you to. Tools that change something (install, send, add to the calendar, start a
  task) ask him first by themselves: just call them; the turn ends at his "shall I?".
- Big jobs (designing something, research that takes minutes, writing files, anything with many steps) go to
  task_start: a background task with its own plan and a live view in the window.
- Projects last weeks (project_start). With an active project, keep it current as you talk: decisions he makes
  (project_note kind decision), open questions, parts with prices and links (project_add_part); tasks for it get
  project=<its name>. Answer from what's decided before; say when something contradicts it.
- Not sure what he means by a thing (a "transparent screen", a style, a part)? Show examples with show_images
  and ask "something like one of these?"; look_at_shown lets you see one yourself. What's on his screen is in
  the situation, so "the second one" means number 2 there. find_videos for how-tos; open_shown plays or opens one.
- Showing things: task_show puts what a task made in the window (models you can turn in 3D, pictures,
  write-ups); open_file opens a task's file or folder on the PC, in its default app or a named program;
  check_installed tells you what's installed and what opens a file type; install_app installs a program
  (winget, exact id) after his yes.
- Something you can't do yet that a small skill could do (a new API, a calculation): offer forge_build.
  Controlling the mouse, keyboard or screen isn't a skill; say it's coming as a built-in.
- Hard problem: call think_harder first.
- Never claim you did something no tool did. If a tool failed, say so plainly and offer the next best thing.

How you speak (he hears you, he doesn't read you):
- Short: one to three sentences, unless he asks for detail. No lists, no markdown, no emojis, no headings.
- Plain words, numbers as he'd say them. One question at most, at the end.

About him (his own notes):
{_about_him()}"""


def situation(orch=None) -> str:
    """What's true right now, sent with his sentence (not in the system prompt, so the cache holds)."""
    lines = [f"Now: {datetime.now():%A %d %B %Y, %H:%M}."]
    try:
        from core.agent.tasks import get_store
        tasks = get_store().all()
        live = [t for t in tasks if t.status in ("queued", "running", "waiting", "paused")]
        for t in live[-3:]:
            lines.append(f"Task '{t.title}' is {t.status}" + (f", waiting for: {t.waiting.get('text', '')[:160]}"
                                                              if t.status == "waiting" else "") + ".")
        done = [t for t in tasks if t.status == "done"]
        if done:
            t = max(done, key=lambda x: x.updated)
            files = ", ".join(Path(f).name for f in t.files[:6]) or "no files delivered"
            lines.append(f"Last finished task: '{t.title}' ({datetime.fromtimestamp(t.updated):%d %b %H:%M}); "
                         f"files: {files}. Summary: {t.summary[:300]}")
    except Exception:
        pass
    try:
        from core import focus
        f = focus.current(focus._events()) if focus.enabled() else None
        if f:
            lines.append(f"He's in '{f.get('title') or f.get('summary') or 'an event'}' (focus mode: announcements "
                         f"wait).")
    except Exception:
        pass
    try:
        from core.projects import get_store as projects
        pr = projects().active()
        if pr:
            lines.append("Active project (build on it):\n" + pr.brief(900))
    except Exception:
        pass
    try:
        from core import show
        on_screen = show.shown_text()
        if on_screen:
            lines.append(on_screen)
    except Exception:
        pass
    if orch is not None and getattr(orch, "locked", False):
        lines.append("His phone is locked: private things (messages, email, calendar details) wait for an unlock.")
    return "\n".join(lines)


def facts_text(facts) -> str:
    out = []
    for f in facts or []:
        t = f.get("text") if isinstance(f, dict) else (getattr(f, "text", None) or str(f))
        if t:
            out.append(f"- {t}")
    return "\n".join(out[:8])


def build_messages(history: list[dict], user_input: str, situation_text: str, facts: str, turns: int) -> list[dict]:
    """Earlier turns as plain text (their thinking isn't kept, which the API allows), then this sentence."""
    prior: list[dict] = []
    for m in history:
        role, content = m.get("role"), m.get("content")
        if role not in ("user", "assistant") or not isinstance(content, str) or not content.strip():
            continue
        if prior and prior[-1]["role"] == role:
            prior[-1]["content"] += "\n" + content
        else:
            prior.append({"role": role, "content": content})
    prior = prior[-turns * 2:]
    while prior and prior[0]["role"] != "user":
        prior.pop(0)
    if prior and prior[-1]["role"] == "user":
        prior.pop()                                   # this sentence comes below, with the situation
    now = f"<situation>\n{situation_text}\n</situation>\n"
    if facts:
        now += f"<what he told you before>\n{facts}\n</what he told you before>\n"
    return prior + [{"role": "user", "content": now + user_input}]


def tool_result_content(out: dict, widget_shown: bool):
    """Text, or text and a picture when the tool let her look at one (look_at_shown)."""
    text = tool_result_text(out, widget_shown)
    img = out.get("_image")
    if not img:
        return text
    return [{"type": "text", "text": text},
            {"type": "image", "source": {"type": "base64", "media_type": img["media_type"], "data": img["b64"]}}]


def tool_result_text(out: dict, widget_shown: bool) -> str:
    text = (out.get("result") or "")[:6000]
    if out.get("say"):
        text += f"\nThe tool's own line for him: \"{out['say']}\""
    if widget_shown:
        text += "\n(It's shown in his window as a card.)"
    return text or "(no result)"


def spoken(text: str) -> str:
    """No markdown in speech, whatever the model wrote."""
    t = re.sub(r"```.*?```", " ", text or "", flags=re.S)
    t = re.sub(r"[*_#`>|]+", "", t)
    t = re.sub(r"^\s*(?:[-•]|\d+[.)])\s*", "", t, flags=re.M)
    return " ".join(t.split()).strip()


def json_args(u: dict) -> dict:
    a = u.get("input") or {}
    return a if isinstance(a, dict) else {}


def describe_steps(steps: list[str]) -> Optional[str]:
    return "; ".join(steps) if steps else None


def dumps(d) -> str:
    return json.dumps(d, ensure_ascii=False, default=str)
