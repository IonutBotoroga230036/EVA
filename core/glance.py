"""The glance (v0.9): what the idle screen shows when nothing is going on, EDITH style.

    GET /api/glance -> {"now", "weather", "next", "left_today", "train", "project", "tasks", "spend", "review"}

Everything here is cheap and cached: the weather every 15 minutes (Open-Meteo, free), the calendar from what ORACLE
already polls (no extra Google calls), the train plan every 5 minutes and only on a day with something in Nijmegen
(NS, free), projects and tasks from their files. No cloud model is called. A part that fails is left out.
"""

from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional

from loguru import logger

_cache: dict[str, tuple[float, Any]] = {}
TTL = {"weather": 900, "train": 300}


def _cached(key: str, make: Callable[[], Any]) -> Any:
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < TTL.get(key, 60):
        return hit[1]
    try:
        value = make()
    except Exception as e:
        logger.debug(f"GLANCE: {key} skipped ({e})")
        value = None
    _cache[key] = (time.time(), value)
    return value


def weather() -> Optional[dict]:
    def make():
        from core.tools_native import HOME_CITY
        from core.weather import get_weather_report
        r = get_weather_report(HOME_CITY)
        if r.get("error"):
            return None
        return {k: r.get(k) for k in ("place", "temp_c", "feels_like_c", "conditions", "high_c", "low_c",
                                      "rain_chance_pct") if r.get(k) is not None}
    return _cached("weather", make)


def _events(now: datetime) -> list[dict]:
    try:
        from core.oracle import get_oracle
        evs = list(getattr(get_oracle(), "_events", []) or [])
    except Exception:
        evs = []
    return [e for e in evs if e.get("start") and not e.get("all_day")]


def next_event(now: datetime) -> tuple[Optional[dict], int]:
    """The next thing on the calendar (routine items too: they're his day), and how many more are left today."""
    evs = sorted((e for e in _events(now) if (e.get("end") or e["start"]) > now), key=lambda e: e["start"])
    if not evs:
        return None, 0
    e = evs[0]
    mins = int((e["start"] - now).total_seconds() // 60)
    today = [x for x in evs if x["start"].date() == now.date()]
    return ({"time": e["start"].strftime("%H:%M"), "title": e["title"], "in_min": mins,
             "now": mins <= 0}, len(today))


def train(now: datetime) -> Optional[str]:
    def make():
        from core import commute
        evs = _events(now)
        if not any(commute.is_uni(e) for e in evs if e["start"].date() == now.date() and e["start"] > now):
            return None
        from core.google_api import get_google
        return commute.plan(get_google(), "today", now=now).get("say")
    return _cached("train", make)


def project() -> Optional[dict]:
    try:
        from core.projects import get_store
        p = get_store().active()
        if not p:
            return None
        return {"name": p.name, "open_questions": len(p.open_questions()), "parts": len(p.parts),
                "total_eur": p.total_eur()}
    except Exception:
        return None


def tasks() -> list[dict]:
    try:
        from core.agent import live
        out = []
        for t in live.recent():
            if t.get("status") in ("running", "waiting", "queued"):
                st = live.state(t["task"]) or {}
                out.append({"id": t["task"], "title": t.get("title", ""), "status": t["status"],
                            "now": str(st.get("now") or "")[:80]})
        return out[:4]
    except Exception:
        return []


def spend() -> Optional[dict]:
    try:
        from core.budget import get_budget
        s = get_budget().today_summary()
        return {"spent": round(s.get("spent", 0.0), 2), "limit": s.get("limit")}
    except Exception:
        return None


def review() -> Optional[dict]:
    try:
        from core import review as rv
        r = rv.latest()
        return {"date": r["date"], "headline": r.get("headline", ""), "unread": not r.get("read")} if r else None
    except Exception:
        return None


def lately(n: int = 4) -> list[dict]:
    """v1.1, the HUD: her latest decisions that changed something (from the journal)."""
    try:
        from core.one import journal
        out = []
        for r in journal.recent(30, 2):
            for st in reversed(r.get("steps") or []):
                if st.get("risk") != "free" and st.get("decision") in ("ran", "approved", "asked"):
                    out.append({"ts": st.get("ts") or r["ts"], "tool": st["tool"], "decision": st["decision"],
                                "result": str(st.get("result") or "")[:120], "undo": bool(st.get("undo")) and
                                not st.get("undone"), "id": st.get("id")})
            if len(out) >= n:
                break
        return out[:n]
    except Exception:
        return []


def latency() -> Optional[dict]:
    """v1.1, the latency meter: her first sound after you finished, today."""
    try:
        from voice.speech import FIRST_SOUND
        today = datetime.now().date()
        vals = [s for t, s in FIRST_SOUND if datetime.fromtimestamp(t).date() == today]
        if not vals:
            return None
        return {"last": vals[-1], "avg": round(sum(vals) / len(vals), 1), "slowest": max(vals), "turns": len(vals)}
    except Exception:
        return None


def snapshot(now: Optional[datetime] = None) -> dict:
    now = now or datetime.now()
    nxt, left = next_event(now)
    return {"now": now.isoformat(timespec="seconds"), "weather": weather(), "next": nxt, "left_today": left,
            "train": train(now), "project": project(), "tasks": tasks(), "spend": spend(), "review": review(),
            "lately": lately(), "latency": latency()}


# ---------------------------------------------------------------- the morning heads-up (v0.9)
GREETED = Path("data/greeted.txt")


def _greet_cfg() -> dict:
    try:
        from core.settings import get_settings
        return {"enabled": True, "from_hour": 5, "until_hour": 12, **(get_settings().get("greeting") or {})}
    except Exception:
        return {"enabled": True, "from_hour": 5, "until_hour": 12}


def greeting(now: Optional[datetime] = None) -> str:
    """'Good morning, sir. 7 degrees and cloudy. First up: Linear Algebra at 10:30; the 10:22 train...'"""
    now = now or datetime.now()
    out = ["Good morning, sir."]
    w = weather()
    if w and w.get("temp_c") is not None:
        out.append(f"It's {round(w['temp_c'])} degrees{' and ' + w['conditions'] if w.get('conditions') else ''}"
                   + (f", up to {round(w['high_c'])}" if w.get("high_c") is not None else "") + ".")
    nxt, left = next_event(now)
    if nxt:
        out.append(f"First up: {nxt['title']} at {nxt['time']}" + (f", and {left - 1} more after it." if left > 1
                                                                     else "."))
    t = train(now)
    if t:
        out.append(t if t.endswith(".") else t + ".")
    r = review()
    if r and r.get("unread"):
        out.append("I wrote my notes on yesterday; they're in the settings.")
    return " ".join(out)


def greeting_once(now: Optional[datetime] = None) -> str:
    """The first sentence of the morning gets the heads-up first; once a day, survives restarts."""
    now = now or datetime.now()
    c = _greet_cfg()
    if not c.get("enabled", True) or not (int(c["from_hour"]) <= now.hour < int(c["until_hour"])):
        return ""
    day = now.date().isoformat()
    try:
        if GREETED.exists() and GREETED.read_text(encoding="utf-8").strip() == day:
            return ""
        GREETED.parent.mkdir(parents=True, exist_ok=True)
        GREETED.write_text(day, encoding="utf-8")
    except OSError:
        return ""
    try:
        return greeting(now)
    except Exception as e:
        logger.debug(f"GLANCE: no greeting ({e})")
        return ""
