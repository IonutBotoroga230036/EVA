"""What she notices (v1.1): things you have her do again and again, offered once as something to automate.

Once a day she reads her journal of the last two weeks. The same change (the same tool with the same important
arguments) on three or more different days becomes a note in data/patterns.json, e.g.
    "You had me calendar shift (from 09:00, until 09:45, 15 minutes) on 4 different days lately."
The brain sees one such note in its situation, once, and may offer to make it automatic. A note is never offered
twice. The same daily pass tidies the skills she made (core/one/made.review).
"""

from __future__ import annotations

import json
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

from loguru import logger

STORE = Path("data/patterns.json")
MIN_DAYS = 3
IGNORE = {"undo", "make_skill", "run_skill", "task_answer", "task_cancel", "task_pause", "task_resume", "test_mode",
          "change_setting", "memoir_start", "memoir_stop", "open_file", "open_website", "open_app"}
VOLATILE = {"day", "date", "when", "text", "query", "goal", "note", "message", "content", "title", "items"}


def _load() -> dict:
    try:
        d = json.loads(STORE.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _save(d: dict) -> None:
    STORE.parent.mkdir(parents=True, exist_ok=True)
    STORE.write_text(json.dumps(d, indent=1, ensure_ascii=False), encoding="utf-8")


def _key(tool: str, args: dict) -> str:
    keep = {k: v for k, v in sorted((args or {}).items()) if k not in VOLATILE and isinstance(v, (str, int, float, bool))}
    return tool + "|" + json.dumps(keep, sort_keys=True)


def scan(days: int = 14) -> dict:
    from core.one import journal
    seen: dict[str, dict] = {}
    for i in range(days):
        d = (date.today() - timedelta(days=i)).isoformat()
        for rec in journal.day(d):
            for s in rec.get("steps") or []:
                if s.get("decision") not in ("ran", "approved") or s.get("risk") == "free" or s.get("tool") in IGNORE:
                    continue
                k = _key(s["tool"], s.get("args") or {})
                e = seen.setdefault(k, {"tool": s["tool"], "args": {kk: vv for kk, vv in (s.get("args") or {}).items()
                                                                    if kk not in VOLATILE}, "days": set()})
                e["days"].add(d)
    store = _load()
    for k, e in seen.items():
        if len(e["days"]) >= MIN_DAYS and k not in store:
            brief = ", ".join(f"{a} {v}" for a, v in list(e["args"].items())[:4])
            store[k] = {"text": f"You had me {e['tool'].replace('_', ' ')}{' (' + brief + ')' if brief else ''} on "
                                f"{len(e['days'])} different days lately: offer once to make it automatic.",
                        "found": time.time(), "offered": None, "days": len(e["days"])}
            logger.info(f"PATTERNS: {store[k]['text']}")
    store["_scanned"] = date.today().isoformat()
    _save(store)
    try:
        from core.one import made
        made.review()
    except Exception as e:
        logger.debug(f"PATTERNS: skill review skipped ({e})")
    return store


def offer_once() -> Optional[str]:
    """One note she hasn't offered yet (and it counts as offered from now on)."""
    store = _load()
    if store.get("_scanned") != date.today().isoformat():
        try:
            store = scan()
        except Exception as e:
            logger.debug(f"PATTERNS: scan failed ({e})")
            return None
    for k, v in store.items():
        if k.startswith("_") or not isinstance(v, dict) or v.get("offered"):
            continue
        v["offered"] = time.time()
        _save(store)
        return v["text"]
    return None
