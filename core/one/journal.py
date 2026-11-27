"""What she did, and why (v1.0): every turn's decisions, so you can check them and undo them yourself.

One line per turn in data/journal/<date>.jsonl (this is her record of actions, not a log file: the raw log still
isn't written anywhere):

    {"id", "ts", "chat", "said", "answer", "brain": [{"backend", "model"}], "thinking", "escalated",
     "steps": [{"id", "tool", "args", "risk", "why", "decision", "ok", "result", "changes", "undo", "undone"}],
     "cost_eur", "seconds", "task", "project"}

decision: ran | asked (needs his yes) | approved (his yes ran it) | failed | refused
changes:  [{"what": "Standup", "before": "09:00", "after": "09:15"}]  what a tool changed, before and after
undo:     {"tool": "calendar_restore", "args": {...}, "what": "put Standup back at 09:00"}  how to put it back

The window shows them under "What she did" with an Undo button per step; "Eva, undo that" undoes the last one;
GET /api/journal, POST /api/journal/<step>/undo. Kept for one.journal_days (30).
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Optional

from loguru import logger

DIR = Path("data/journal")
_lock = threading.RLock()


def _short(v: Any, n: int = 400) -> str:
    s = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False, default=str)
    return s if len(s) <= n else s[:n] + "..."


def _plain(args: Any) -> dict:
    try:
        d = json.loads(json.dumps(args or {}, ensure_ascii=False, default=str))
    except (TypeError, ValueError):
        return {}
    if not isinstance(d, dict):
        return {}
    return {k: (v[:1500] if isinstance(v, str) else v) for k, v in d.items() if not str(k).startswith("_")}


class Turn:
    def __init__(self, said: str, chat: str = "", via: str = "voice"):
        self.data: dict = {"id": uuid.uuid4().hex[:10], "ts": time.time(), "chat": chat or "", "said": said[:2000],
                           "via": via, "answer": "", "brain": [], "thinking": "", "escalated": "", "steps": [],
                           "cost_eur": 0.0, "seconds": 0.0, "task": "", "project": "", "reflex": False}
        self._t0 = time.time()

    @property
    def id(self) -> str:
        return self.data["id"]

    def brain(self, backend: str, model: str) -> None:
        b = {"backend": backend, "model": model}
        if b not in self.data["brain"]:
            self.data["brain"].append(b)

    def think(self, text: str) -> None:
        if text:
            self.data["thinking"] = (self.data["thinking"] + ("\n\n" if self.data["thinking"] else "") + text)[-8000:]

    def step(self, tool: str, args: dict, risk: str, why: str, decision: str, out: Optional[dict] = None,
             inner: bool = False) -> dict:
        out = out or {}
        result = out.get("result") or ""
        ok = '"error"' not in result and decision in ("ran", "approved")
        s = {"id": f"{self.id}-{len(self.data['steps']) + 1}", "tool": tool, "args": _plain(args), "risk": risk, "why": why, "decision": decision,
             "ok": ok if decision in ("ran", "approved") else None, "result": _short(out.get("say") or result, 500),
             "changes": list(out.get("changes") or [])[:40], "undo": out.get("undo") if ok else None,
             "undone": False, "ts": time.time(), "inner": inner}
        self.data["steps"].append(s)
        if tool == "task_start" and ok:
            try:
                self.data["task"] = json.loads(result).get("task", "")
            except ValueError:
                pass
        if tool.startswith("project_") and ok:
            self.data["project"] = str(args.get("name") or args.get("project") or self.data["project"])[:80]
        return s

    def finish(self, answer: str, cost_eur: float = 0.0) -> dict:
        self.data["answer"] = answer[:4000]
        self.data["cost_eur"] = round(float(cost_eur or 0), 5)
        self.data["seconds"] = round(time.time() - self._t0, 2)
        save(self.data)
        return self.data


def _path(day: str) -> Path:
    return DIR / f"{day}.jsonl"


def save(rec: dict) -> None:
    day = datetime.fromtimestamp(rec["ts"]).date().isoformat()
    with _lock:
        try:
            DIR.mkdir(parents=True, exist_ok=True)
            with _path(day).open("a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
        except OSError as e:
            logger.warning(f"JOURNAL: couldn't write ({e})")
    _tidy()


_tidied = {"day": ""}


def _tidy() -> None:
    today = date.today().isoformat()
    if _tidied["day"] == today or not DIR.exists():
        return
    _tidied["day"] = today
    try:
        from core.one import cfg
        keep = int(cfg().get("journal_days", 30))
    except Exception:
        keep = 30
    cutoff = (date.today() - timedelta(days=keep)).isoformat()
    for p in DIR.glob("*.jsonl"):
        if p.stem < cutoff:
            try:
                p.unlink()
            except OSError:
                pass


def day(d: Optional[str] = None) -> list[dict]:
    d = d or date.today().isoformat()
    try:
        lines = _path(d).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def recent(limit: int = 50, days: int = 7, only_actions: bool = False) -> list[dict]:
    out: list[dict] = []
    d = date.today()
    for _ in range(days):
        recs = day(d.isoformat())
        if only_actions:
            recs = [r for r in recs if any(s.get("decision") in ("ran", "approved", "asked") and s.get("risk") != "free"
                                           for s in r.get("steps") or [])]
        out.extend(reversed(recs))
        if len(out) >= limit:
            break
        d -= timedelta(days=1)
    return out[:limit]


def _rewrite(d: str, recs: list[dict]) -> None:
    with _lock:
        tmp = _path(d).with_suffix(".tmp")
        tmp.write_text("".join(json.dumps(r, ensure_ascii=False, default=str) + "\n" for r in recs), encoding="utf-8")
        tmp.replace(_path(d))


def find_step(step_id: str) -> tuple[Optional[str], Optional[dict], Optional[dict]]:
    turn_id = step_id.split("-")[0]
    d = date.today()
    for _ in range(31):
        for r in day(d.isoformat()):
            if r.get("id") == turn_id:
                for s in r.get("steps") or []:
                    if s.get("id") == step_id:
                        return d.isoformat(), r, s
        d -= timedelta(days=1)
    return None, None, None


def last_undoable() -> Optional[dict]:
    for r in recent(limit=30, days=2):
        for s in reversed(r.get("steps") or []):
            if s.get("undo") and not s.get("undone"):
                return s
    return None


def undo(step_id: str, execute) -> dict:
    """Run a step's undo through `execute(tool, args) -> out`. -> {"ok", "say", "step"}."""
    d, rec, s = find_step(step_id)
    if not s:
        return {"ok": False, "say": "I can't find that step any more."}
    if s.get("undone"):
        return {"ok": False, "say": "That one is already undone."}
    spec = s.get("undo")
    if not spec:
        return {"ok": False, "say": "That step can't be undone automatically."}
    out = execute(spec["tool"], dict(spec.get("args") or {}))
    ok = '"error"' not in (out.get("result") or "")
    if ok:
        recs = day(d)
        for r in recs:
            if r.get("id") == rec["id"]:
                for x in r.get("steps") or []:
                    if x.get("id") == step_id:
                        x["undone"] = True
                        x["undone_at"] = time.time()
        _rewrite(d, recs)
        logger.info(f"JOURNAL: undid {s['tool']} ({step_id})")
    return {"ok": ok, "say": out.get("say") or ("Put back." if ok else "The undo didn't work."), "step": step_id,
            "what": spec.get("what", "")}


def as_text(recs: list[dict], limit: int = 12) -> str:
    """For her own tool (what_i_did) and the copy button: short lines."""
    lines = []
    for r in recs[:limit]:
        acts = [s for s in r.get("steps") or [] if s.get("risk") != "free" or s.get("decision") != "ran"]
        if not acts:
            continue
        when = datetime.fromtimestamp(r["ts"]).strftime("%a %H:%M")
        for s in acts:
            ch = "; ".join(f"{c.get('what')}: {c.get('before')} -> {c.get('after')}" for c in s.get("changes") or [])
            lines.append(f"{when} {s['tool']} [{s['decision']}{', undone' if s.get('undone') else ''}] "
                         f"{s.get('result', '')[:160]}" + (f" ({ch})" if ch else ""))
    return "\n".join(lines) or "Nothing changed in that time."
