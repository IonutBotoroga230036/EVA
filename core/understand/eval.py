"""Score the ways she can understand you, on the same sentences, before switching anything.

    python -m core.understand.eval                       every mode that can run here
    python -m core.understand.eval --modes legacy,auto   just these
    python -m core.understand.eval --no-fast             without the word shortcuts (the fast path)

Nothing is executed: no music starts, no light changes, no email is read. Each sentence only goes through the
decision (which tool, which details), exactly as in a real turn, then gets compared to what you marked as right.
Cloud modes send the sentence and the two turns before it to Jev or Claude; a full run costs a few cents at most.
The report lands in data/understand/eval_<time>.md with every miss listed.
"""

from __future__ import annotations

import argparse
import asyncio
import statistics
import tempfile
import time
from datetime import datetime
from pathlib import Path

import httpx

from core import later as later_mod
from core.understand import brains, cases as cases_mod

MODES = ("legacy", "local", "jev", "claude", "auto")


def args_ok(expected: dict, got: dict) -> bool:
    """Every detail you marked must be in what she filled in ('tomorrow' in 'tomorrow afternoon' counts)."""
    for k, v in (expected or {}).items():
        have = got.get(k)
        if have is None:
            return False
        if str(v).lower() not in str(have).lower():
            return False
    return True


def outcome(decision: dict, belt, text: str, recent: set, ability_hint) -> str:
    """What the turn would do with this decision: a tool name, 'none', 'unavailable', or 'blocked:<tool>' when a
    word guard stops it (she'd then ask, or answer without it)."""
    tool = decision.get("tool", "none")
    trusted = decision.get("_trusted")
    if tool == "unavailable" and not (trusted or ability_hint.search(text)):
        return "none"
    if tool in ("none", "unavailable"):
        return tool
    if not belt.has(tool):
        return "none"
    if not (trusted or belt.guard_ok(tool, text) or tool in recent):
        return f"blocked:{tool}"
    return tool


async def decide_one(o, client, case: dict, mode: str, fast: bool) -> dict:
    from core.orchestrator_hybrid import _ABILITY_HINT, args_for, fast_path, missing_required
    hist = [{"role": r, "content": t} for r, t in case.get("history", [])]
    text, _delay = later_mod.split_delay(case["say"])
    o.history = [*hist, {"role": "user", "content": text}]
    o.understand_mode = mode
    t0 = time.perf_counter()
    fp = fast_path(text, o.belt) if fast else None
    if fp:
        tool, args, by = fp[0], o.belt.fill(fp[0], fp[1], text), "fast"
    else:
        ctx: dict = {}
        o._context(text, ctx)
        d = await o._decide(client, ctx["skills"], [])
        by = d.get("_by", mode)
        tool = outcome(d, o.belt, text, set(), _ABILITY_HINT)
        args = {}
        if o.belt.has(tool):
            schema = o.belt.by_name[tool]
            args = o.belt.fill(tool, args_for(schema, d, o.belt.names()), text)
            if missing_required(schema, args):
                args = o.belt.fill(tool, {**args, **args_for(schema, await o._repair_args(client, tool),
                                                             o.belt.names())}, text)
    ms = (time.perf_counter() - t0) * 1000
    right = tool in case["expect"]
    return {"say": case["say"], "expect": case["expect"], "got": tool, "args": args, "by": by, "ms": ms,
            "tool_ok": right, "args_ok": right and args_ok(case.get("args", {}), args)}


def available_modes() -> list[str]:
    out = ["legacy"]
    if brains.cfg().get("local_model"):
        out.append("local")
    jev_ok = brains.get_jev().available()
    claude_ok = brains.get_claude_light().available()
    if jev_ok:
        out.append("jev")
    if claude_ok:
        out.append("claude")
    if jev_ok or claude_ok:
        out.append("auto")
    return out


def report(results: dict[str, list[dict]], spent: dict[str, float]) -> str:
    lines = [f"# Understanding eval, {datetime.now():%Y-%m-%d %H:%M}", "",
             "| mode | right tool | right details too | median ms | slowest 10% ms | cost EUR |",
             "|---|---|---|---|---|---|"]
    for mode, rs in results.items():
        n = len(rs) or 1
        ms = sorted(r["ms"] for r in rs) or [0]
        p90 = ms[min(len(ms) - 1, int(len(ms) * 0.9))]
        lines.append(f"| {mode} | {sum(r['tool_ok'] for r in rs)}/{len(rs)} ({100 * sum(r['tool_ok'] for r in rs) / n:.0f}%)"
                     f" | {sum(r['args_ok'] for r in rs)}/{len(rs)} | {statistics.median(ms):.0f} | {p90:.0f}"
                     f" | {spent.get(mode, 0):.4f} |")
    for mode, rs in results.items():
        miss = [r for r in rs if not r["args_ok"]]
        lines += ["", f"## {mode}: {len(miss)} not fully right", ""]
        for r in miss:
            why = "wrong details" if r["tool_ok"] else f"did {r['got']}"
            lines.append(f"- \"{r['say']}\": expected {' or '.join(r['expect'])}, {why} {r['args'] or ''} "
                         f"(by {r['by']}, {r['ms']:.0f} ms)")
    return "\n".join(lines) + "\n"


async def run(modes: list[str], fast: bool, limit: int = 0, case_list=None, orchestrator=None, say=print) -> str:
    from core.budget import get_budget
    from core.memory.cortex import Cortex
    from core.orchestrator_hybrid import HybridOrchestrator
    cs = case_list if case_list is not None else cases_mod.load()
    if limit:
        cs = cs[:limit]
    tmp = tempfile.mkdtemp(prefix="eva_eval_")
    o = orchestrator or HybridOrchestrator(session_id="eval", cortex=Cortex(str(Path(tmp) / "eval.db")))
    o.history = []
    results, spent = {}, {}
    async with httpx.AsyncClient(timeout=120) as client:
        for mode in modes:
            say(f"\n{mode}: {len(cs)} sentences")
            before = get_budget().today_summary().get("spent", 0.0)
            rs = []
            for i, c in enumerate(cs, 1):
                r = await decide_one(o, client, c, mode, fast)
                rs.append(r)
                mark = "ok " if r["args_ok"] else ("~  " if r["tool_ok"] else "X  ")
                say(f"  {mark}{i:>3}. {c['say'][:60]:<60} -> {r['got']} ({r['ms']:.0f} ms)")
            results[mode] = rs
            spent[mode] = max(0.0, get_budget().today_summary().get("spent", 0.0) - before)
    o.understand_mode = None
    return report(results, spent)


def main() -> None:
    ap = argparse.ArgumentParser(description="Compare the ways E.V.A. can understand you.")
    ap.add_argument("--modes", default="", help=f"comma list from {', '.join(MODES)}; default: all that can run")
    ap.add_argument("--no-fast", action="store_true", help="skip the word shortcuts, judge the brains alone")
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()
    modes = [m.strip() for m in a.modes.split(",") if m.strip()] or available_modes()
    bad = [m for m in modes if m not in MODES]
    if bad:
        raise SystemExit(f"unknown mode(s): {', '.join(bad)}")
    print(f"Modes: {', '.join(modes)}. Nothing gets executed; only the decisions are scored.")
    text = asyncio.run(run(modes, fast=not a.no_fast, limit=a.limit))
    out = Path("data/understand") / f"eval_{datetime.now():%Y%m%d_%H%M}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    print("\n" + text.split("\n## ")[0])
    print(f"Every miss: {out}")


if __name__ == "__main__":
    main()
