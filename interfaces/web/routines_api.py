"""
Routines panel API (v0.2.5 milestone 5): reminders, routines, moods, and the shopping list.

    GET    /api/routines                       everything the panel shows, plus a 7-day week view
    POST   /api/routines/reminders             {text, due: "YYYY-MM-DDTHH:MM"}
    PATCH  /api/routines/reminders/{id}        {text?, due?}
    DELETE /api/routines/reminders/{id}
    POST   /api/routines/routines              {text, days: [0..6], time: "HH:MM", action}
    PATCH  /api/routines/routines/{id}         {paused?, text?, days?, time?, action?}
    DELETE /api/routines/routines/{id}
    POST   /api/routines/moods                 {name, color?, white?, brightness?, playlist?} (create or edit)
    DELETE /api/routines/moods/{name}          custom moods; an edited built-in goes back to its default
    POST   /api/routines/shopping              {items: "milk, eggs"}
    POST   /api/routines/shopping/tick         {item, done}
    POST   /api/routines/shopping/remove       {item}
    POST   /api/routines/shopping/clear        {done_only}

These are your own clicks in the panel, so they don't ask "shall I go ahead?". Protection comes from
core/netsec.py: this PC or a paired device only, and no cross-site writes. Every change is audited.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from core import shopping
from core.oracle import describe_routine, get_oracle
from core.security.audit import audit

router = APIRouter(prefix="/api/routines")
ACTIONS = {"remind", "briefing", "agenda", "email"}
_DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def _dt(value: str) -> datetime:
    try:
        return datetime.fromisoformat(value.strip())
    except (ValueError, AttributeError):
        raise HTTPException(400, "due must look like 2026-09-27T15:00")


def _hm(value: str) -> tuple[int, int]:
    m = re.fullmatch(r"\s*([01]?\d|2[0-3]):([0-5]\d)\s*", value or "")
    if not m:
        raise HTTPException(400, "time must look like 08:30")
    return int(m.group(1)), int(m.group(2))


def _days(days: list[int]) -> list[int]:
    ds = sorted({int(d) for d in days})
    if not ds or any(d < 0 or d > 6 for d in ds):
        raise HTTPException(400, "days must be 0 (Monday) to 6 (Sunday)")
    return ds


def _reminder(r: dict) -> dict:
    return {"id": r["id"], "text": r["text"], "due": datetime.fromtimestamp(r["due"]).strftime("%Y-%m-%dT%H:%M")}


def _routine(r: dict) -> dict:
    return {"id": r["id"], "text": r.get("text", ""), "days": r["days"], "time": f"{r['hour']:02d}:{r['minute']:02d}",
            "action": r.get("action", "remind"), "paused": bool(r.get("paused")), "describe": describe_routine(r)}


def _moods() -> list[dict]:
    from core.lights import DEFAULT_MOODS, MOODS_PATH, load_moods
    import json
    try:
        custom = set(json.loads(MOODS_PATH.read_text(encoding="utf-8")))
    except Exception:
        custom = set()
    return [{"name": n, **m, "builtin": n in DEFAULT_MOODS, "edited": n in custom} for n, m in load_moods().items()]


def week_view(start: Optional[date] = None, days: int = 7) -> list[dict]:
    """Reminders and routines, day by day, from start (today by default)."""
    o = get_oracle()
    start = start or date.today()
    reminders = o.store.open()
    routines = [r for r in o.routines.all() if not r.get("paused")]
    out = []
    for i in range(days):
        d = start + timedelta(days=i)
        items = []
        for r in reminders:
            due = datetime.fromtimestamp(r["due"])
            if due.date() == d:
                items.append({"time": due.strftime("%H:%M"), "text": r["text"], "kind": "reminder", "id": r["id"]})
        for r in routines:
            if d.weekday() in r["days"]:
                items.append({"time": f"{r['hour']:02d}:{r['minute']:02d}", "text": describe_routine(r).split(" every")[0]
                              .split(" on ")[0], "kind": "routine", "id": r["id"]})
        label = "Today" if i == 0 else "Tomorrow" if i == 1 else _DAY_NAMES[d.weekday()]
        out.append({"date": d.isoformat(), "label": label, "items": sorted(items, key=lambda x: x["time"])})
    return out


@router.get("")
async def overview():
    o = get_oracle()
    return {"reminders": [_reminder(r) for r in o.store.open()],
            "routines": [_routine(r) for r in o.routines.all()],
            "moods": _moods(), "shopping": shopping.items(), "week": week_view()}


# ------------------------------------------------------------ reminders
class ReminderIn(BaseModel):
    text: str = Field(min_length=1, max_length=300)
    due: str


class ReminderPatch(BaseModel):
    text: Optional[str] = Field(default=None, max_length=300)
    due: Optional[str] = None


@router.post("/reminders")
async def add_reminder(body: ReminderIn):
    r = get_oracle().store.add(body.text, _dt(body.due))
    audit.log("ui_reminder_added", "routines_panel", {"id": r["id"], "text": r["text"]})
    return _reminder(r)


@router.patch("/reminders/{rid}")
async def edit_reminder(rid: str, body: ReminderPatch):
    r = get_oracle().store.update(rid, body.text, _dt(body.due) if body.due else None)
    if not r:
        raise HTTPException(404, "no open reminder with that id")
    audit.log("ui_reminder_edited", "routines_panel", {"id": rid})
    return _reminder(r)


@router.delete("/reminders/{rid}")
async def delete_reminder(rid: str):
    if not get_oracle().store.delete(rid):
        raise HTTPException(404, "no reminder with that id")
    audit.log("ui_reminder_deleted", "routines_panel", {"id": rid})
    return {"deleted": rid}


# ------------------------------------------------------------ routines
class RoutineIn(BaseModel):
    text: str = Field(default="", max_length=300)
    days: list[int]
    time: str
    action: str = "remind"


class RoutinePatch(BaseModel):
    text: Optional[str] = Field(default=None, max_length=300)
    days: Optional[list[int]] = None
    time: Optional[str] = None
    action: Optional[str] = None
    paused: Optional[bool] = None


def _action(a: str) -> str:
    if a not in ACTIONS:
        raise HTTPException(400, f"action must be one of {sorted(ACTIONS)}")
    return a


@router.post("/routines")
async def add_routine(body: RoutineIn):
    action = _action(body.action)
    if action == "remind" and not body.text.strip():
        raise HTTPException(400, "a reminder routine needs text")
    h, m = _hm(body.time)
    r = get_oracle().routines.add({"days": _days(body.days), "hour": h, "minute": m, "action": action,
                                   "text": body.text.strip() or action})
    audit.log("ui_routine_added", "routines_panel", {"id": r["id"]})
    return _routine(r)


@router.patch("/routines/{rid}")
async def edit_routine(rid: str, body: RoutinePatch):
    fields: dict = {"paused": body.paused, "text": body.text.strip() if body.text else None,
                    "days": _days(body.days) if body.days is not None else None,
                    "action": _action(body.action) if body.action else None}
    if body.time:
        fields["hour"], fields["minute"] = _hm(body.time)
    r = get_oracle().routines.update(rid, **fields)
    if not r:
        raise HTTPException(404, "no routine with that id")
    audit.log("ui_routine_edited", "routines_panel", {"id": rid, "paused": r.get("paused", False)})
    return _routine(r)


@router.delete("/routines/{rid}")
async def delete_routine(rid: str):
    if not get_oracle().routines.delete(rid):
        raise HTTPException(404, "no routine with that id")
    audit.log("ui_routine_deleted", "routines_panel", {"id": rid})
    return {"deleted": rid}


# ------------------------------------------------------------ moods
class MoodIn(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    color: Optional[str] = Field(default=None, max_length=40)
    white: Optional[int] = Field(default=None, ge=0, le=100)
    brightness: int = Field(default=60, ge=1, le=100)
    playlist: str = Field(default="", max_length=120)


@router.post("/moods")
async def save_mood_api(body: MoodIn):
    from core.lights import parse_color, save_mood
    name = body.name.strip().lower()
    mood: dict = {"brightness": body.brightness, "playlist": body.playlist.strip()}
    if body.color:
        c = parse_color(body.color)
        if not c:
            raise HTTPException(400, f"I don't know the colour {body.color!r}")
        if "white" in c and "rgb" not in c:
            mood["white"] = c["white"]                        # "warm white" is a white, not a colour
        else:
            mood["color"] = c.get("name") or body.color.strip().lower()
    else:
        mood["white"] = 50 if body.white is None else body.white
    save_mood(name, mood)
    audit.log("ui_mood_saved", "routines_panel", {"name": name})
    return {"name": name, **mood}


@router.delete("/moods/{name}")
async def delete_mood_api(name: str):
    from core.lights import delete_mood
    if not delete_mood(name.lower()):
        raise HTTPException(404, "not a custom or edited mood")
    audit.log("ui_mood_deleted", "routines_panel", {"name": name})
    return {"deleted": name}


# ------------------------------------------------------------ shopping list
class ItemsIn(BaseModel):
    items: str = Field(min_length=1, max_length=500)


class ItemIn(BaseModel):
    item: str = Field(min_length=1, max_length=80)
    done: bool = True


class ClearIn(BaseModel):
    done_only: bool = True


@router.post("/shopping")
async def shopping_add(body: ItemsIn):
    added, already = shopping.add(shopping.split_items(body.items))
    return {"added": added, "already": already, "items": shopping.items()}


@router.post("/shopping/tick")
async def shopping_tick(body: ItemIn):
    if not shopping.set_done(body.item, body.done):
        raise HTTPException(404, "not on the list")
    return {"items": shopping.items()}


@router.post("/shopping/remove")
async def shopping_remove(body: ItemIn):
    if not shopping.remove(body.item):
        raise HTTPException(404, "not on the list")
    return {"items": shopping.items()}


@router.post("/shopping/clear")
async def shopping_clear(body: ClearIn):
    n = shopping.clear(done_only=body.done_only)
    audit.log("ui_shopping_cleared", "routines_panel", {"removed": n, "done_only": body.done_only})
    return {"removed": n, "items": shopping.items()}
