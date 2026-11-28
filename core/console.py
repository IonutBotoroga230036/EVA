"""Her console (v1.2): the last few thousand log lines, in memory only, so she can read her own errors.

Nov 28 00:50: "I want you to be able to see those errors and read them." The server's log already goes to the
terminal; this keeps a copy in memory (never a file), and my_console reads it: the newest lines, only warnings and
errors, or lines with a word in them. Lines that could hold a key are blanked.
"""

from __future__ import annotations

import re
import threading
from collections import deque
from typing import Optional

from loguru import logger

LINES: deque = deque(maxlen=4000)
_lock = threading.Lock()
_sink_id: Optional[int] = None
_SECRET = re.compile(r"(?i)(sk-ant-[\w-]+|api[_-]?key\s*[=:]\s*\S+|token\s*[=:]\s*\S+|bearer\s+\S+|password\s*[=:]\s*\S+)")
LEVELS = {"debug": 10, "info": 20, "success": 25, "warning": 30, "error": 40, "critical": 50}


def _sink(message) -> None:
    r = message.record
    text = _SECRET.sub("[hidden]", str(r["message"]))
    exc = r["exception"]
    if exc is not None and exc.type is not None:
        text += " | " + _SECRET.sub("[hidden]", f"{exc.type.__name__}: {exc.value}")
    with _lock:
        LINES.append({"t": r["time"].strftime("%H:%M:%S"), "level": r["level"].name.lower(),
                      "where": f"{r['module']}:{r['line']}", "text": text[:2000]})


def install() -> None:
    global _sink_id
    if _sink_id is None:
        _sink_id = logger.add(_sink, level="INFO", format="{message}", backtrace=False, diagnose=False)


def tail(last: int = 60, level: str = "", contains: str = "") -> list[dict]:
    least = LEVELS.get((level or "").lower(), 0)
    word = (contains or "").lower()
    with _lock:
        rows = list(LINES)
    rows = [r for r in rows if LEVELS.get(r["level"], 20) >= least and (not word or word in r["text"].lower()
                                                                          or word in r["where"].lower())]
    return rows[-max(1, min(int(last or 60), 400)):]


def text(rows: list[dict]) -> str:
    return "\n".join(f"{r['t']} {r['level'].upper():<7} {r['where']:<24} {r['text']}" for r in rows)


def tool_my_console(last: int = 60, level: str = "", contains: str = "", **_):
    import json
    rows = tail(last, level, contains)
    return {"result": json.dumps({"lines": text(rows)[-12000:] or "(nothing matches)", "count": len(rows)},
                                 ensure_ascii=False)}


SCHEMAS = [{"type": "function", "function": {
    "name": "my_console", "description": "Read your own console: the server's latest log lines (what you heard, "
                                         "which tools ran, warnings and errors with their reasons). level: 'warning' "
                                         "for problems only; contains: a word to look for (a tool name, 'error').",
    "parameters": {"type": "object", "properties": {"last": {"type": "integer"}, "level": {"type": "string"},
                                                    "contains": {"type": "string"}}}}}]
FUNCTIONS = {"my_console": tool_my_console}
