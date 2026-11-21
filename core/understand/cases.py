"""Test sentences for the understanding step: the seed set (cases_seed.jsonl, in the repo) plus YOUR sentences,
harvested from her conversation history and marked by you (data/understand/cases.jsonl, private, never committed).

    python -m core.understand.cases            harvest the last 60 days and review them one by one
    python -m core.understand.cases --days 7   only the last week

For each sentence you see what she did. Press Enter if that was right, or type what she should have done:
a tool name (the first letters are enough when they're unique), n = no tool (just answer), u = she can't do that
yet, s = skip, q = save and stop. Only the sentence, the two turns before it and your verdict are saved.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable, Optional

SEED = Path(__file__).with_name("cases_seed.jsonl")
MINE = Path("data/understand/cases.jsonl")
SKIP_WORDS = {"yes", "no", "yeah", "yep", "nope", "ok", "okay", "do it", "go ahead", "cancel", "stop", "sure",
              "thanks", "thank you"}


def load(paths: Iterable[Path] = (SEED, MINE)) -> list[dict]:
    out, seen = [], set()
    for p in paths:
        p = Path(p)
        if not p.exists():
            continue
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("//"):
                continue
            c = json.loads(line)
            key = (c["say"].strip().lower(), json.dumps(c.get("history", [])))
            if key in seen:
                continue
            seen.add(key)
            c.setdefault("expect", [])
            c["expect"] = [c["expect"]] if isinstance(c["expect"], str) else list(c["expect"])
            c["source"] = p.name
            out.append(c)
    return out


def _tool_calls(log_dir: Path, since: datetime) -> list[tuple[float, str]]:
    calls = []
    for f in sorted(Path(log_dir).glob("audit_*.jsonl")):
        try:
            day = datetime.strptime(f.stem.replace("audit_", ""), "%Y-%m-%d")
        except ValueError:
            continue
        if day < since - timedelta(days=1):
            continue
        for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            if e.get("action") in ("tool_call", "confirm_requested"):
                tool = (e.get("details") or {}).get("tool")
                if tool:
                    calls.append((datetime.fromisoformat(e["timestamp"]).timestamp(), tool))
    return calls


def harvest(db_path: str = "data/cortex.db", log_dir: str = "data/logs", days: int = 60) -> list[dict]:
    """[{say, she_did: [tools], history: [[role, text], ...], ts}] for what you said to her, newest last."""
    since = datetime.now() - timedelta(days=days)
    if not Path(db_path).exists():
        return []
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)     # read-only: her memory is never touched
    try:
        rows = con.execute("SELECT session_id, role, content, ts FROM episodes WHERE ts >= ? ORDER BY ts, id",
                           (since.timestamp(),)).fetchall()
    finally:
        con.close()
    calls = _tool_calls(Path(log_dir), since)
    out, by_session = [], {}
    for sess, role, content, ts in rows:
        hist = by_session.setdefault(sess, [])
        if role == "user":
            # both lines of a turn are logged when it ends, so its tools ran between the previous turn and this line
            prev = hist[-1][2] if hist else ts - 300
            did = sorted({t for when, t in calls if prev < when <= ts + 1})
            if content.strip().lower().strip(" .!?,") not in SKIP_WORDS:
                out.append({"say": content.strip(), "she_did": did or ["none"],
                            "history": [[r, c] for r, c, _ in hist[-2:]], "ts": ts})
        hist.append((role, content, ts))
    return out


def resolve(answer: str, names: list[str]) -> Optional[list[str]]:
    """'cal_f' or 'calendar_free' or 'n' -> ['calendar_free'] / ['none']; None when it isn't clear."""
    answer = answer.strip().lower()
    if answer in ("n", "none"):
        return ["none"]
    if answer in ("u", "unavailable"):
        return ["unavailable"]
    picked = []
    for part in [a.strip() for a in answer.split(",") if a.strip()]:
        exact = [n for n in names if n == part]
        hits = exact or [n for n in names if n.startswith(part)] or [n for n in names if part in n]
        if len(hits) != 1:
            return None
        picked.append(hits[0])
    return picked or None


def review(items: list[dict], names: list[str], out: Path = MINE, ask=input, say=print) -> int:
    done = {json.loads(l)["say"].strip().lower() for l in out.read_text(encoding="utf-8").splitlines() if l.strip()} \
        if out.exists() else set()
    todo = [c for c in items if c["say"].strip().lower() not in done]
    say(f"{len(todo)} sentences to review ({len(done)} already marked). Enter = right, or type what she should "
        f"have done (tool / n / u), s = skip, q = stop.")
    out.parent.mkdir(parents=True, exist_ok=True)
    saved = 0
    with out.open("a", encoding="utf-8") as f:
        for i, c in enumerate(todo, 1):
            say("")
            for role, text in c.get("history", []):
                say(f"    {'you' if role == 'user' else 'she'}: {text[:110]}")
            say(f"[{i}/{len(todo)}] you: {c['say']}")
            say(f"        she did: {', '.join(c['she_did'])}")
            while True:
                a = ask("        right? ").strip()
                if a.lower() == "q":
                    return saved
                if a.lower() == "s":
                    break
                expect = c["she_did"] if a == "" else resolve(a, names)
                if expect is None:
                    say("        not sure which tool that is; type more of its name")
                    continue
                f.write(json.dumps({"say": c["say"], "expect": expect, "history": c.get("history", []),
                                    "why": "harvested"}, ensure_ascii=False) + "\n")
                f.flush()
                saved += 1
                break
    return saved


def tool_names() -> list[str]:
    from core.mcp_client import MCPManager
    from core.orchestrator_hybrid import ToolBelt
    from skills.registry import SkillRegistry
    return list(ToolBelt(SkillRegistry("skills").discover(), MCPManager()).by_name)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Harvest and mark your real sentences for the understanding test.")
    ap.add_argument("--days", type=int, default=60)
    a = ap.parse_args()
    found = harvest(days=a.days)
    if not found:
        print("No conversation history found in data/cortex.db for that period.")
    else:
        n = review(found, tool_names())
        print(f"\nSaved {n} marked sentences to {MINE}. Next: python -m core.understand.eval")
