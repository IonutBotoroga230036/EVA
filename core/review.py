"""Her nightly self-review (v0.9): she reads her own day and writes down her mistakes and what to fix.

What she reads is kept in memory only (a ring buffer of today's log lines: what you said, what she answered, tools,
warnings). No log file is written. Once a night (after `review.after_hour`, when you've been quiet for a while), or
when you ask ("Eva, how did you do today?"), Claude Haiku reads the buffer and writes a short review:

    {"date", "headline", "mistakes": [{"what", "why", "fix"}], "good": [...], "turns", "cost_eur", "read"}

Only that review is saved (data/reviews/<date>.json), never the raw lines. The window shows it ("Her notes"), and it
has a copy button: paste it to Claude instead of the whole log.
"""

from __future__ import annotations

import json
import re
import threading
import time
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Optional

from loguru import logger

DIR = Path("data/reviews")
KEEP = 4000                                   # lines in memory
_lines: deque = deque(maxlen=KEEP)
_lock = threading.Lock()
_sink_id: Optional[int] = None
_running = threading.Event()
last_user_ts = 0.0                            # when he last said something (from the log itself)

# what is worth reading: his words, her answers, decisions, tools, the mind, tasks, and every warning
_WORTH = re.compile(r"USER|EVA:|EVA \(proactive\)|UNDERSTAND|TOOL |MIND:|AGENT:|COMPUTER:|BROWSER|NEARBY|CHATS:|"
                    r"LATER:|ORCH:|claim guard|fact guard|ECHO heard|ECHO: '|holding an unfinished|CLAUDE: |BRAIN:|REFLEX:|"
                    r"JOURNAL:|SKILLS: made|JARVIS TEST:")
_SECRETS = re.compile(r"(sk-ant-[\w-]+|token=[\w-]+|Bearer [\w.-]+|\b\d{13,19}\b)")


def _sink(message) -> None:
    rec = message.record
    text = rec["message"]
    if rec["level"].no < 30 and not _WORTH.search(text):
        return
    global last_user_ts
    if text.startswith("USER"):
        last_user_ts = time.time()
    line = f"{rec['time']:%H:%M:%S} {rec['level'].name[:4]} {_SECRETS.sub('[hidden]', text)[:600]}"
    with _lock:
        if _lines and _lines[-1][0] != datetime.now().date().isoformat():
            _lines.clear()                    # a new day starts a new page
        _lines.append((datetime.now().date().isoformat(), line))


def start() -> None:
    """Listen to the log (memory only). Called once at start-up."""
    global _sink_id
    if _sink_id is None:
        _sink_id = logger.add(_sink, level="INFO", format="{message}", enqueue=False, catch=True)


def today_lines(day: Optional[str] = None) -> list[str]:
    day = day or datetime.now().date().isoformat()
    with _lock:
        return [l for d, l in _lines if d == day]


def _cfg() -> dict:
    try:
        from core.settings import get_settings
        return {"enabled": True, "after_hour": 23, "quiet_minutes": 20, "min_turns": 5,
                **(get_settings().get("review") or {})}
    except Exception:
        return {"enabled": True, "after_hour": 23, "quiet_minutes": 20, "min_turns": 5}


def _path(day: str) -> Path:
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day or ""):
        raise ValueError("not a day")
    return DIR / f"{day}.json"


def get(day: str) -> Optional[dict]:
    try:
        return json.loads(_path(day).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def listing(limit: int = 14) -> list[dict]:
    if not DIR.exists():
        return []
    out = [r for r in (get(p.stem) for p in sorted(DIR.glob("*.json"), reverse=True)[:limit]) if r]
    return out


def latest() -> Optional[dict]:
    items = listing(1)
    return items[0] if items else None


def mark_read(day: str) -> bool:
    r = get(day)
    if not r:
        return False
    r["read"] = True
    _path(day).write_text(json.dumps(r, indent=1, ensure_ascii=False), encoding="utf-8")
    return True


PROMPT = """You are E.V.A., Ionut's personal assistant, reviewing your own day from your log. Be honest and specific,
like an engineer's post-mortem: what went wrong from HIS side (misheard words, the wrong tool, a wrong or made-up
answer, an action that failed, being slow, asking when you should have acted, acting when you should have asked,
repeating yourself, nagging). For each mistake: what happened (quote his words and yours, with the time), why it
probably happened (which part: hearing, understanding, the mind, a tool, a guard, the network), and a concrete fix.
Skip things that went fine, but list up to 3 things that went well. Never invent anything that isn't in the log.
Answer with JSON only:
{"headline": "one sentence on the day", "mistakes": [{"time": "HH:MM", "what": "...", "why": "...", "fix": "..."}],
 "good": ["..."]}"""


def run(day: Optional[str] = None, force: bool = False) -> dict:
    """Write the review for a day from the lines in memory (Claude Haiku, a few cents at most)."""
    day = day or datetime.now().date().isoformat()
    lines = today_lines(day)
    turns = sum(1 for l in lines if "USER" in l)
    if not force and turns < int(_cfg()["min_turns"]):
        return {"error": "too quiet", "say": "There isn't enough from today to review yet, sir."}
    if not lines:
        return {"error": "nothing", "say": "I have nothing from today to review, sir: I only remember since I "
                                           "started."}
    from core.claude import get_claude
    text = "\n".join(lines)
    if len(text) > 60000:                       # the end of the day matters as much as the start
        text = text[:20000] + "\n[...]\n" + text[-40000:]
    out = get_claude().message(system=PROMPT, messages=[{"role": "user", "content": f"Your log for {day}:\n{text}"}],
                               model="claude-haiku-5-5", max_tokens=2500, purpose="review")
    raw = (out.get("text") or "").strip()
    m = re.search(r"\{.*\}", raw, re.S)
    try:
        data = json.loads(m.group(0)) if m else {}
    except ValueError:
        data = {}
    review = {"date": day, "headline": str(data.get("headline") or "")[:300],
              "mistakes": [{k: str(x.get(k) or "")[:600] for k in ("time", "what", "why", "fix")}
                           for x in (data.get("mistakes") or []) if isinstance(x, dict)][:15],
              "good": [str(g)[:300] for g in (data.get("good") or [])][:3],
              "turns": turns, "cost_eur": round(float(out.get("cost_eur") or 0), 4), "read": False,
              "written": time.time()}
    if not review["headline"] and not review["mistakes"]:
        return {"error": "no review", "say": "I couldn't write my review just now, sir."}
    DIR.mkdir(parents=True, exist_ok=True)
    _path(day).write_text(json.dumps(review, indent=1, ensure_ascii=False), encoding="utf-8")
    logger.info(f"REVIEW: {day}: {len(review['mistakes'])} mistakes, EUR {review['cost_eur']}")
    return review


def as_text(r: dict) -> str:
    """For the copy button: something to paste to Claude instead of the whole log."""
    out = [f"E.V.A. self-review, {r['date']} ({r.get('turns', 0)} turns): {r.get('headline', '')}", ""]
    for i, m in enumerate(r.get("mistakes") or [], 1):
        out.append(f"{i}. [{m.get('time', '')}] {m.get('what', '')}")
        if m.get("why"):
            out.append(f"   Why: {m['why']}")
        if m.get("fix"):
            out.append(f"   Fix: {m['fix']}")
    if r.get("good"):
        out += ["", "Went well: " + "; ".join(r["good"])]
    return "\n".join(out)


def due(now: Optional[datetime] = None, last_turn_ts: Optional[float] = None) -> bool:
    """Time for tonight's review: late enough, quiet for a while, enough happened, not written yet."""
    c, now = _cfg(), now or datetime.now()
    if not c.get("enabled", True) or now.hour < int(c["after_hour"]) or _running.is_set():
        return False
    if time.time() - (last_user_ts if last_turn_ts is None else last_turn_ts) < int(c["quiet_minutes"]) * 60:
        return False
    day = now.date().isoformat()
    return get(day) is None and sum(1 for l in today_lines(day) if "USER" in l) >= int(c["min_turns"])


def run_in_background() -> None:
    if _running.is_set():
        return
    _running.set()

    def go():
        try:
            run()
        except Exception as e:
            logger.warning(f"REVIEW: couldn't write tonight's review ({str(e)[:120]})")
        finally:
            _running.clear()
    threading.Thread(target=go, name="review", daemon=True).start()


# ---------------------------------------------------------------- her tool
def tool_review_day(**_):
    r = run(force=True)
    if r.get("error"):
        return {"result": json.dumps(r), "say": r["say"], "exact": True}
    n = len(r["mistakes"])
    say = (f"{r['headline']} I noted {n} mistake{'s' if n != 1 else ''}; they're under Her notes in the settings, "
           f"sir." if n else f"{r['headline']} No mistakes worth noting, sir.")
    return {"result": json.dumps(r, ensure_ascii=False), "say": say, "exact": True,
            "widget": {"kind": "note", "title": f"My notes on {r['date']}", "text": as_text(r)}}


SCHEMAS = [{"type": "function", "function": {
    "name": "review_day",
    "description": "Review your own day now: read today's conversation and list your mistakes and fixes ('how did "
                   "you do today?', 'review your day', 'what went wrong today?').",
    "parameters": {"type": "object", "properties": {}}}}]
FUNCTIONS = {"review_day": tool_review_day}
GUARDS = {"review_day": r"\b(review|how did you do|went wrong|mistakes?|your day)\b"}
