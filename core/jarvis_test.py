"""The automatic JARVIS test (v1.0): she runs the scenarios in core/jarvis_scenarios.yaml in a fake world and
gets a score, so every version (and every night) shows whether she's getting closer to JARVIS.

    python -m core.jarvis_test                    # with the brain your settings use (local_first by default)
    python -m core.jarvis_test --backend local    # free
    python -m core.jarvis_test --only stock_price,undo

Each run is its own process with its own temp folder: a fake calendar, fake messages, fake tasks and fake web
results answer every tool, so nothing touches your accounts, files or lights, and your journal and memory stay
yours. The run is saved as data/jarvis_runs/<time>.json; the window shows it under "JARVIS test". With
jarvis_test.nightly she runs it once a night when you've been quiet (local brain: free).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
from datetime import date, datetime
from pathlib import Path
from typing import Callable, Optional

import yaml
from loguru import logger

SCENARIOS = Path(__file__).with_name("jarvis_scenarios.yaml")
RUNS = Path("data/jarvis_runs")
STOCK = [r"\bjust to confirm\b", r"^certainly[!,.]", r"\bas an ai\b", r"\bI'?m (?:just )?a language model\b",
         r"\bhow can I assist you\b"]
_lock = threading.Lock()
_running = {"since": 0.0, "backend": ""}


def cfg() -> dict:
    try:
        from core.settings import get_settings
        return {"nightly": True, "hour": 3, "backend": "local", **(get_settings().get("jarvis_test") or {})}
    except Exception:
        return {"nightly": True, "hour": 3, "backend": "local"}


def scenarios() -> list[dict]:
    try:
        return yaml.safe_load(SCENARIOS.read_text(encoding="utf-8")) or []
    except Exception as e:
        logger.warning(f"JARVIS TEST: couldn't read the scenarios ({e})")
        return []


# ---------------------------------------------------------------- the fake world
def _mins(t) -> int:
    t = str(t or "").strip().lower().replace(".", ":")
    if t in ("now", ""):
        n = datetime.now()
        return n.hour * 60 + n.minute
    m = re.match(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", t)
    if not m:
        return 0
    h, mm = int(m.group(1)), int(m.group(2) or 0)
    if m.group(3) == "pm" and h < 12:
        h += 12
    return h * 60 + mm


def _hm(m: int) -> str:
    return f"{m // 60:02d}:{m % 60:02d}"


class FakeWorld:
    """Her tool names, answered from the scenario. Anything not here: 'not in the test world'."""

    def __init__(self, spec: Optional[dict] = None):
        spec = spec or {}
        self.events = [dict(e) for e in (spec.get("events") or [
            {"id": "e1", "title": "Standup", "start": "09:00", "end": "09:15"},
            {"id": "e2", "title": "Review", "start": "09:30", "end": "10:00"},
            {"id": "e3", "title": "Lunch", "start": "12:00", "end": "13:00"}])]
        self.web = dict(spec.get("web") or {})
        self.pages = dict(spec.get("pages") or {})
        self.weather = dict(spec.get("weather") or {"place": "Breda", "temp_c": 8, "conditions": "cloudy",
                                                    "rain_chance_pct": 20, "high_c": 10, "low_c": 5})
        self.tasks_spec = list(spec.get("tasks") or [])
        self.calls: list[tuple[str, dict]] = []
        self.sent: list[dict] = []
        self.reminders: list[dict] = []
        self.shopping: list[str] = []
        self.settings: dict = {}

    def _r(self, data: dict, say: str = "", **extra) -> dict:
        out = {"result": json.dumps(data, default=str), **extra}
        if say:
            out["say"] = say
        return out

    def _find(self, title: str) -> Optional[dict]:
        t = (title or "").lower()
        return next((e for e in self.events if t and (t in e["title"].lower() or e["title"].lower() in t)), None)

    def execute(self, name: str, args: dict) -> dict:
        args = dict(args or {})
        self.calls.append((name, args))
        fn = getattr(self, f"t_{name}", None)
        if fn is None:
            return self._r({"error": f"{name} isn't available in the test world"})
        try:
            return fn(**args)
        except TypeError as e:
            return self._r({"error": f"bad arguments for {name}: {e}"})

    # calendar
    def t_calendar_agenda(self, day="today", **_):
        return self._r({"day": day, "events": self.events},
                       " ".join(f"{e['title']} at {e['start']}." for e in self.events))

    def t_calendar_next(self, **_):
        return self.t_calendar_agenda()

    def t_calendar_free(self, day="today", **_):
        return self._r({"free": ["10:00-12:00", "13:00-18:00"]}, "Free from 10 to 12 and from 1 to 6.")

    def t_calendar_shift(self, minutes=60, day="today", from_time="", until_time="", **_):
        lo, hi = _mins(from_time) if from_time else 0, _mins(until_time) if until_time else 24 * 60
        moved, undo = [], []
        for e in self.events:
            s = _mins(e["start"])
            if lo <= s < hi:
                undo.append({"id": e["id"], "title": e["title"], "start": e["start"], "end": e["end"]})
                moved.append({"what": e["title"], "before": e["start"], "after": _hm(s + int(minutes))})
                e["start"], e["end"] = _hm(s + int(minutes)), _hm(_mins(e["end"]) + int(minutes))
        return self._r({"moved": len(moved), "titles": [m["what"] for m in moved]},
                       f"Moved {len(moved)} events {abs(int(minutes))} minutes {'later' if int(minutes) > 0 else 'earlier'}.",
                       changes=moved, undo={"tool": "calendar_restore", "args": {"events": undo}, "what": "put back"})

    def t_calendar_move(self, title="", time="", day="", **_):
        e = self._find(title)
        if not e:
            return self._r({"error": "not found"}, f"I couldn't find {title}.")
        before = dict(e)
        length = _mins(e["end"]) - _mins(e["start"])
        e["start"], e["end"] = _hm(_mins(time)), _hm(_mins(time) + length)
        return self._r({"moved": e["title"], "start": e["start"]}, f"Moved {e['title']} to {e['start']}.",
                       changes=[{"what": e["title"], "before": before["start"], "after": e["start"]}],
                       undo={"tool": "calendar_restore", "args": {"events": [before]}, "what": "put back"})

    def t_calendar_restore(self, events=None, remove=None, recreate=None, **_):
        for b in events or []:
            for e in self.events:
                if e["id"] == b["id"]:
                    e["start"], e["end"] = b["start"], b["end"]
        self.events = [e for e in self.events if e["id"] not in set(remove or [])]
        for r in recreate or []:
            self.events.append({"id": f"r{len(self.events)}", **r})
        return self._r({"restored": True}, "Put back.")

    def t_calendar_add(self, title="", day="today", time="", duration_minutes=60, **_):
        eid = f"n{len(self.events) + 1}"
        self.events.append({"id": eid, "title": title, "start": _hm(_mins(time)), "end": _hm(_mins(time) + 60)})
        return self._r({"added": title}, f"Added {title} at {time}.", undo={"tool": "calendar_restore",
                                                                         "args": {"remove": [eid]}, "what": "remove"})

    def t_calendar_delete(self, title="", day="today", **_):
        e = self._find(title)
        if not e:
            return self._r({"error": "not found"})
        self.events.remove(e)
        return self._r({"deleted": e["title"]}, f"Removed {e['title']}.")

    # the web
    def t_web_search(self, query="", **_):
        q = query.lower()
        hits = [{"title": k, "snippet": v, "url": "https://example.org/" + re.sub(r"\W+", "-", k)}
                for k, v in self.web.items() if any(w in q for w in k.lower().split())]
        if not hits:
            return self._r({"query": query, "results": [], "error": "no results"})
        return self._r({"query": query, "results": hits})

    def t_web_read(self, url="", find="", **_):
        for k, v in {**self.pages, **self.web}.items():
            if re.sub(r"\W+", "-", k) in url or k.lower() in url.lower():
                return self._r({"url": url, "text": v})
        return self._r({"error": "the page answered 404", "url": url})

    def t_get_weather(self, city="", day="", **_):
        w = self.weather
        return self._r(w, f"{w['conditions'].capitalize()}, {w['temp_c']} degrees, {w['rain_chance_pct']}% chance "
                          f"of rain.")

    # actions
    def t_lights_power(self, on=False, **_):
        return self._r({"on": bool(on)}, f"Lights {'on' if on else 'off'}, sir.")

    def t_lights_set(self, **a):
        return self._r({"set": a}, "Done, sir.")

    def t_message_reply(self, **a):
        self.sent.append(a)
        return self._r({"sent": True}, "Sent.")

    def t_set_reminder(self, text="", when="", **_):
        self.reminders.append({"text": text, "when": when})
        return self._r({"id": "r1"}, f"I'll remind you to {text} {when}.",
                       undo={"tool": "cancel_reminder", "args": {"query": text}, "what": "cancel it"})

    def t_cancel_reminder(self, query="", **_):
        self.reminders = [r for r in self.reminders if query.lower() not in r["text"].lower()]
        return self._r({"cancelled": query}, "Cancelled.")

    def t_shopping_add(self, items="", **_):
        names = [x.strip() for x in re.split(r",| and ", items) if x.strip()]
        self.shopping += names
        return self._r({"added": names}, f"Added {', '.join(names)}.")

    def t_shopping_list(self, **_):
        return self._r({"items": self.shopping})

    def t_task_start(self, goal="", budget_euros=0, title="", **_):
        return self._r({"task": "t9", "title": title or "task"}, f"Started the {title or 'task'}.")

    def t_task_status(self, which="", **_):
        return self._r({"tasks": self.tasks_spec})

    def t_task_cancel(self, which="", **_):
        return self._r({"ok": True}, "Stopped the task.")

    def t_task_answer(self, answer="", task="", **_):
        return self._r({"task": "t1"}, "Got it.")

    def t_task_list(self, **_):
        return self._r({"tasks": self.tasks_spec})

    def t_change_setting(self, key="", value=None, **_):
        old = self.settings.get(key)
        self.settings[key] = value
        return self._r({"key": key, "was": old, "now": value}, f"Set {key} to {value}.",
                       undo={"tool": "change_setting", "args": {"key": key, "value": old}, "what": "set it back"})

    def t_open_website(self, site="", **_):
        return self._r({"opened": site}, f"Opening {site}.")

    def t_open_app(self, app="", **_):
        return self._r({"opened": app}, f"Opening {app}.")

    def t_remember_fact(self, fact="", **_):
        return self._r({"stored": fact}, "Noted.")

    def t_recall_memory(self, query="", **_):
        return self._r({"facts": []})

    def t_get_datetime(self, **_):
        n = datetime.now()
        return self._r({"date": n.date().isoformat(), "time": n.strftime("%H:%M"), "weekday": n.strftime("%A")})

    def t_calculate(self, expression="", **_):
        from core.tools_native import tool_calculate
        return tool_calculate(expression=expression)

    def t_my_files(self, query="", **_):
        files = [{"path": "C:/Users/you/Documents/E.V.A/face recognition camera/eva_face.py", "name": "eva_face.py",
                  "folder": "face recognition camera"},
                 {"path": "C:/Users/you/Documents/E.V.A/face recognition camera/who_is_here.py",
                  "name": "who_is_here.py", "folder": "face recognition camera"}]
        q = (query or "").lower()
        return self._r({"files": [f for f in files if not q or any(w in f["path"].lower() for w in q.split())]
                        or files})

    def t_read_my_file(self, path="", **_):
        return self._r({"path": path, "text": "who_is_here.py --camera N: prints JSON {is_ionut, confidence}"})

    def t_run_my_script(self, path="", args=None, **_):
        return self._r({"ok": True, "output": '{"is_ionut": true, "confidence": 0.98}',
                        "json": {"is_ionut": True, "confidence": 0.98}})

    def t_adopt_skill(self, name="", **_):
        return self._r({"ok": True, "skill": f"skill_{name}"})

    def t_task_resume(self, which="", **_):
        return self._r({"ok": True}, "Back on it.")

    def t_media_control(self, action="", **_):
        return self._r({"ok": True}, "Done.")

    def t_phone_ring(self, **_):
        return self._r({"ringing": True}, "Ringing your phone.")

    def t_where_am_i(self, **_):
        return self._r({"place": "home", "since": "21:37"}, "You're at home.")

    def t_memoir_start(self, topic="", **_):
        from core.memoir import tool_memoir_start
        return tool_memoir_start(topic)

    def t_memoir_stop(self, **_):
        from core.memoir import tool_memoir_stop
        return tool_memoir_stop()

    def t_what_i_did(self, day="today", **_):
        from core.one.tools import tool_what_i_did
        return tool_what_i_did(day)


class _NoMCP:
    def tool_schemas(self):
        return []

    def actions(self):
        return set()

    def has(self, name):
        return False


# ---------------------------------------------------------------- one scenario
def _matches(group: str, called: set[str]) -> bool:
    for alt in group.split("|"):
        alt = alt.strip()
        if alt in called or (alt.endswith("*") and any(c.startswith(alt[:-1]) for c in called)):
            return True
        if alt in ("run_skill", "make_skill") and any(c.startswith("skill_") for c in called):
            return True
    return False


def judge(sc: dict, answers: list[str], world: FakeWorld, called: set[str], asked: bool, seconds: float,
          gathered_text: str) -> list[str]:
    """What went wrong (empty = passed)."""
    exp = sc.get("expect") or {}
    last = answers[-1] if answers else ""
    bad = []
    for group in exp.get("tools") or []:
        if not _matches(group, called):
            bad.append(f"didn't use {group}")
    for t in exp.get("not_tools") or []:
        if _matches(t, called):
            bad.append(f"used {t}")
    if "asks" in exp and bool(exp["asks"]) != asked:
        bad.append("should have asked first" if exp["asks"] else "asked when it should just have happened")
    if "sent" in exp and len(world.sent) != int(exp["sent"]):
        bad.append(f"sent {len(world.sent)} messages, expected {exp['sent']}")
    for title, start in (exp.get("events") or {}).items():
        e = next((x for x in world.events if x["title"] == title), None)
        if not e or e["start"] != start:
            bad.append(f"{title} is at {e['start'] if e else 'nowhere'}, expected {start}")
    for rx in exp.get("say_has") or []:
        if not re.search(rx, last, re.I):
            bad.append(f"her answer lacks /{rx}/")
    for rx in list(exp.get("say_not") or []) + STOCK:
        if any(re.search(rx, a, re.I | re.M) for a in answers):
            bad.append(f"she said /{rx}/")
    if exp.get("grounded"):
        from core.orchestrator_hybrid import ungrounded_numbers
        off = ungrounded_numbers(last, gathered_text)
        if off:
            bad.append(f"numbers not from a tool: {', '.join(off[:3])}")
    if exp.get("max_s") and seconds > float(exp["max_s"]):
        bad.append(f"took {seconds:.1f} s (limit {exp['max_s']})")
    if not last.strip():
        bad.append("no answer")
    return bad


async def run_one(sc: dict, make_backend: Optional[Callable] = None, registry=None, workdir: Optional[Path] = None,
                  ) -> dict:
    import core.orchestrator_hybrid as orch_mod
    from core.agent import tasks as tasks_mod
    from core.memory.cortex import Cortex
    from core.one import journal
    workdir = workdir or Path(tempfile.mkdtemp(prefix="eva-jarvis-"))
    world = FakeWorld(sc.get("world"))
    store = tasks_mod.Store(workdir / f"tasks-{sc['id']}")
    for spec in world.tasks_spec:
        t = store.new(spec.get("goal") or spec.get("title", "a task"), 1.0, spec.get("title", ""), "", [])
        t.status = spec.get("status", "running")
        if spec.get("waiting"):
            t.waiting = {"kind": "question", "text": spec["waiting"]}
        store.save(t)
    old_store = tasks_mod.get_store
    tasks_mod.get_store = lambda: store
    o = orch_mod.HybridOrchestrator(registry=registry, mcp=_NoMCP(),
                                    cortex=Cortex(str(workdir / f"cortex-{sc['id']}.db")))
    o.history = []
    o.mem_cfg = {**o.mem_cfg, "extract_facts": False}
    o.belt.execute = world.execute
    if make_backend is not None:
        o.brain().backend_override = make_backend()
    answers, turn_ids, t0, err = [], [], time.time(), ""
    try:
        for text in sc.get("turns") or []:
            final = ""
            async for ev in o.process_stream(text):
                if ev["type"] == "final":
                    final = ev["text"]
                elif ev["type"] == "journal":
                    turn_ids.append(ev["turn"])
            answers.append(final)
    except Exception as e:                       # a crash is a failed scenario, not a failed run
        err = f"{type(e).__name__}: {e}"[:300]
        logger.exception(f"JARVIS TEST: {sc['id']} crashed")
    finally:
        tasks_mod.get_store = old_store
        try:
            from core import memoir
            memoir.cancel()                       # a story left recording mustn't swallow the next scenario
        except Exception:
            pass
    seconds = time.time() - t0
    called = {n for n, _ in world.calls}
    recs = [r for r in journal.recent(200, 1) if r["id"] in set(turn_ids)]
    asked = bool(o.pending) and bool(recs) and any(s.get("decision") == "asked" for s in recs[0].get("steps") or [])
    gathered = json.dumps(world.calls, default=str)        # what her tools were given
    results_text = " ".join(str(s.get("result", "")) for r in recs for s in r.get("steps") or []) + " " + \
        " ".join(json.dumps(v, default=str) for v in (world.web, world.weather, world.pages)) + " " + gathered
    bad = [f"crashed: {err}"] if err else judge(sc, answers, world, called, asked, seconds, results_text)
    brains = sorted({f"{b['backend']}:{b['model']}" for r in recs for b in r.get("brain") or []})
    return {"id": sc["id"], "title": sc.get("title", ""), "passed": not bad, "problems": bad, "answers": answers,
            "tools": sorted(called), "seconds": round(seconds, 1), "brain": brains,
            "cost_eur": round(sum(float(r.get("cost_eur") or 0) for r in recs), 4),
            "escalated": [r["escalated"] for r in recs if r.get("escalated")]}


async def run_all(only: Optional[list[str]] = None, make_backend: Optional[Callable] = None,
                  backend_name: str = "") -> dict:
    from skills.registry import SkillRegistry
    reg = SkillRegistry("skills")
    reg.discover()
    workdir = Path(tempfile.mkdtemp(prefix="eva-jarvis-"))
    picked = [s for s in scenarios() if not only or s["id"] in only]
    results = []
    t0 = time.time()
    for sc in picked:
        r = await run_one(sc, make_backend, reg, workdir)
        logger.info(f"JARVIS TEST: {sc['id']}: {'pass' if r['passed'] else 'FAIL ' + '; '.join(r['problems'])}")
        results.append(r)
    passed = sum(1 for r in results if r["passed"])
    return {"ts": time.time(), "date": date.today().isoformat(), "backend": backend_name or "custom",
            "passed": passed, "total": len(results), "score": round(100 * passed / max(1, len(results))),
            "seconds": round(time.time() - t0, 1), "cost_eur": round(sum(r["cost_eur"] for r in results), 4),
            "results": results}


# ---------------------------------------------------------------- runs, from the server
def listing(limit: int = 10) -> list[dict]:
    if not RUNS.exists():
        return []
    out = []
    for p in sorted(RUNS.glob("*.json"), reverse=True)[:limit]:
        try:
            out.append(json.loads(p.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    return out


def running() -> dict:
    return {"since": _running["since"], "backend": _running["backend"]} if _running["since"] else {}


def run_in_background(backend: Optional[str] = None, nightly: bool = False) -> bool:
    """A separate process (its own temp world), so a run never touches the live E.V.A."""
    with _lock:
        if _running["since"]:
            return False
        _running.update(since=time.time(), backend=backend or cfg().get("backend") or "local")
    out = RUNS / f"{datetime.now():%Y-%m-%d_%H%M%S}.json"

    def go():
        try:
            RUNS.mkdir(parents=True, exist_ok=True)
            cmd = [sys.executable, "-X", "utf8", "-m", "core.jarvis_test", "--backend", _running["backend"],
                   "--out", str(out)] + (["--nightly"] if nightly else [])
            r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", timeout=3600,
                               env={**os.environ, "EVA_JARVIS_RUN": "1"})
            if r.returncode != 0:
                logger.warning(f"JARVIS TEST: the run failed ({(r.stderr or '')[-300:]})")
            else:
                d = json.loads(out.read_text(encoding="utf-8"))
                logger.info(f"JARVIS TEST: {d['passed']}/{d['total']} ({d['score']}%) with {d['backend']}, "
                            f"EUR {d['cost_eur']}")
        except Exception as e:
            logger.warning(f"JARVIS TEST: couldn't run ({e})")
        finally:
            _running.update(since=0.0, backend="")
    threading.Thread(target=go, name="jarvis-test", daemon=True).start()
    return True


def due(now: Optional[datetime] = None, last_user_ts: float = 0.0) -> bool:
    c, now = cfg(), now or datetime.now()
    if not c.get("nightly", True) or now.hour != int(c.get("hour", 3)) or _running["since"]:
        return False
    if time.time() - last_user_ts < 30 * 60:
        return False
    return not any(r.get("date") == now.date().isoformat() and r.get("nightly") for r in listing(3))


def fake_writer(raw: str, topic: str) -> dict:
    """The JARVIS run checks her listening, not the writing: a plain chapter, no model."""
    return {"title": (topic or "A story")[:60], "text": raw, "period": "", "year": 0, "people": [], "places": [],
            "summary": raw[:120], "questions": ["What happened next?"], "_writer": "test"}


def _isolate(tmp: Path) -> None:
    """In the run's own process: her journal, skills, risk notes, biography and settings changes go to a temp
    folder."""
    from core import memoir, settings as settings_mod
    from core.one import files, journal, made, patterns, risk
    journal.DIR = tmp / "journal"
    made.DIR = tmp / "skills_made"
    risk.STORE = tmp / "risk.json"
    patterns.STORE = tmp / "patterns.json"
    files.APPROVALS = tmp / "script_approvals.json"
    memoir.STATE = tmp / "memoir_state.json"
    memoir.folder = lambda: tmp / "Biography"
    memoir._write_json = fake_writer
    settings_mod.UI_PATH = tmp / "settings_ui.json"


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="E.V.A.'s JARVIS test, in a fake world")
    ap.add_argument("--backend", choices=["local", "cloud", "local_first"], default=None)
    ap.add_argument("--only", default="")
    ap.add_argument("--out", default="")
    ap.add_argument("--nightly", action="store_true")
    a = ap.parse_args(argv)
    tmp = Path(tempfile.mkdtemp(prefix="eva-jarvis-"))
    _isolate(tmp)
    import core.one as one
    backend = a.backend or cfg().get("backend") or "local"
    one.backend_choice = lambda: backend
    one.mode = lambda: "one"
    d = asyncio.run(run_all([x for x in a.only.split(",") if x] or None, None, backend))
    d["nightly"] = bool(a.nightly or os.environ.get("EVA_JARVIS_NIGHTLY"))
    text = json.dumps(d, indent=1, ensure_ascii=False)
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(text, encoding="utf-8")
    print(f"JARVIS test: {d['passed']}/{d['total']} passed ({d['score']}%), {d['seconds']} s, EUR {d['cost_eur']}")
    for r in d["results"]:
        print(f"  {'PASS' if r['passed'] else 'FAIL'}  {r['id']:<18} {r['seconds']:>5} s  "
              + ("; ".join(r["problems"]) if r["problems"] else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
