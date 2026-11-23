"""Places (Nov 23): reminders and actions when you arrive or leave, "where did I park", and a history only if
you ask for one.

The phone (menu > Location: on, with Hands-free on) reports where it is every few minutes or after ~75 m. Here:
- your places: "remember this place as home", "this is the gym", "remember where I parked" (the place "car");
- rules: "remind me to buy milk when I leave work", "when I get home turn on the lights" (an action, decided now
  and done then, like 'in 10 minutes'), one-off unless you say "every time";
- visits: when you arrived at and left your named places (small, useful: "when did I get home?");
- the full trail only with "track my location" (off by default, kept 30 days, never leaves this PC).

Geofences have a margin so a jumpy fix doesn't make you "leave" home: you arrive inside the radius and leave
beyond radius + max(60 m, the fix's accuracy). Fixes worse than 250 m never trigger anything.
"""

from __future__ import annotations

import json
import math
import re
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from loguru import logger

PATH = Path("data/places.json")
HISTORY = Path("data/location_history.jsonl")
KEEP_DAYS = 30
FRESH_S = 15 * 60
DEFAULT_RADIUS = {"home": 120, "work": 150, "car": 40}
_ALIASES = {"my car": "car", "the car": "car", "parking": "car", "where i parked": "car", "the gym": "gym",
            "uni": "university", "my house": "home", "the office": "work", "office": "work", "my work": "work"}


def meters(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 2 * 6371000 * math.asin(math.sqrt(min(1.0, h)))


def place_name(text: str) -> str:
    t = re.sub(r"[^\w\s'-]", "", (text or "").lower()).strip()
    t = _ALIASES.get(t, t)
    t = re.sub(r"^(?:the|my|our)\s+", "", t)
    return _ALIASES.get(t, t)


def distance_words(m: float) -> str:
    if m < 1000:
        return f"{int(round(m / 10.0) * 10)} meters"
    km = m / 1000
    return f"{km:.1f} km" if km < 10 else f"{km:.0f} km"


# "when I get home", "when I leave work", "once I'm at the gym", "next time I'm at the supermarket"
_END = r"(?=[,.!?]|\s+(?:remind|turn|switch|play|stop|tell|send|text|set|start|pause|and|please|then)\b|$)"
_TRIGGER = re.compile(
    r"\b(?:when(?:ever)?|once|as soon as|the (?:next|moment) (?:time )?|next time|every time)\s+i(?:'m| am)?\s*"
    r"(?:(?P<home>(?:get|arrive|come|am|'m)?\s*(?:back\s+)?home)" + _END + r"|"
    r"(?P<verb>get (?:to |back (?:to )?)|arrive (?:at |in )|come (?:back )?to |reach|am at|'m at|at |leave|left|"
    r"go out of|exit|head out of|walk out of)\s*(?P<place>[\w' -]{2,40}?)" + _END + ")", re.I)


def split_trigger(text: str) -> tuple[str, Optional[str], Optional[str], bool]:
    """'remind me to buy milk when I leave work' -> ('remind me to buy milk', 'work', 'leave', once)."""
    m = _TRIGGER.search(text or "")
    if not m:
        return text, None, None, True
    verb = (m.group("verb") or "").lower()
    event = "leave" if re.match(r"leave|left|go out|exit|head out|walk out", verb) else "arrive"
    place = "home" if m.group("home") else place_name(m.group("place"))
    every = bool(re.search(r"\b(?:every time|whenever)\b", text, re.I))
    clean = (text[:m.start()] + " " + text[m.end():]).strip(" ,.?!")
    clean = re.sub(r"\s{2,}", " ", re.sub(r"^\s*(?:and|then)\s+", "", clean))
    return clean, place or None, event, not every


class Places:
    def __init__(self, path: Optional[Path] = None, history: Optional[Path] = None,
                 now: Callable[[], float] = time.time):
        self.path, self.history, self.now = Path(path or PATH), Path(history or HISTORY), now
        self._lock = threading.Lock()
        self.data = self._load()
        self.last: Optional[dict] = None            # the latest fix: memory only

    def _load(self) -> dict:
        try:
            d = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            d = {}
        return {"places": {}, "rules": [], "visits": [], "inside": {}, "tracking": False, **d}

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, indent=1, ensure_ascii=False), encoding="utf-8")

    # ------------------------------------------------------------ the phone reports
    def update(self, lat: float, lon: float, acc: float = 50.0, ts: Optional[float] = None) -> list[dict]:
        """A new fix. The rules that fired, each {"rule", "place", "event"}."""
        ts = float(ts or self.now())
        fired: list[dict] = []
        with self._lock:
            first = self.last is None and not self.data["inside"]
            self.last = {"lat": lat, "lon": lon, "acc": acc, "ts": ts}
            if self.data.get("tracking"):
                self._append_history(self.last)
            if acc > 250:
                return []
            for name, p in self.data["places"].items():
                d = meters((lat, lon), (p["lat"], p["lon"]))
                was = name in self.data["inside"]
                if not was and d <= p["radius"]:
                    self.data["inside"][name] = ts
                    if not first:
                        fired += self._event(name, "arrive", ts)
                elif was and d > p["radius"] + max(60.0, acc):
                    since = self.data["inside"].pop(name)
                    self.data["visits"] = (self.data["visits"] + [{"place": name, "from": since, "to": ts}])[-500:]
                    fired += self._event(name, "leave", ts)
            self._save()
        return fired

    def _event(self, place: str, event: str, ts: float) -> list[dict]:
        out, keep = [], []
        for r in self.data["rules"]:
            if r["place"] == place and r["event"] == event:
                out.append({"rule": dict(r), "place": place, "event": event})
                if not r.get("once", True):
                    keep.append(r)
            else:
                keep.append(r)
        self.data["rules"] = keep
        if out:
            logger.info(f"PLACES: {event} {place}: {len(out)} rule(s)")
        return out

    def _append_history(self, fix: dict) -> None:
        self.history.parent.mkdir(parents=True, exist_ok=True)
        with self.history.open("a", encoding="utf-8") as f:
            f.write(json.dumps({k: round(v, 6) if isinstance(v, float) else v for k, v in fix.items()}) + "\n")

    def prune_history(self) -> None:
        if not self.history.exists():
            return
        cut = self.now() - KEEP_DAYS * 86400
        lines = [ln for ln in self.history.read_text(encoding="utf-8").splitlines()
                 if ln.strip() and json.loads(ln).get("ts", 0) >= cut]
        self.history.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")

    # ------------------------------------------------------------ what you ask
    def fresh(self) -> Optional[dict]:
        return self.last if self.last and self.now() - self.last["ts"] <= FRESH_S else None

    def remember(self, name: str, radius: Optional[float] = None) -> Optional[dict]:
        fix = self.fresh()
        if not fix:
            return None
        name = place_name(name)
        r = float(radius or DEFAULT_RADIUS.get(name, 100))
        with self._lock:
            self.data["places"][name] = {"lat": fix["lat"], "lon": fix["lon"], "radius": r, "saved": fix["ts"]}
            self.data["inside"][name] = fix["ts"]          # you're there now: no "arrived" for it
            self._save()
        return self.data["places"][name]

    def forget(self, name: str) -> bool:
        name = place_name(name)
        with self._lock:
            gone = self.data["places"].pop(name, None)
            self.data["inside"].pop(name, None)
            self.data["rules"] = [r for r in self.data["rules"] if r["place"] != name]
            self._save()
        return gone is not None

    def add_rule(self, place: str, event: str, text: str, tool: str = "", args: Optional[dict] = None,
                 once: bool = True) -> dict:
        rule = {"id": uuid.uuid4().hex[:8], "place": place_name(place), "event": event, "text": text.strip(),
                "tool": tool, "args": args or {}, "once": once, "created": self.now()}
        with self._lock:
            self.data["rules"].append(rule)
            self._save()
        return rule

    def rules(self) -> list[dict]:
        return list(self.data["rules"])

    def where(self) -> dict:
        """{"at": place or None, "near": (place, meters) or None, "fix": fix or None}."""
        fix = self.fresh()
        if not fix:
            return {"at": None, "near": None, "fix": None}
        best = None
        for name, p in self.data["places"].items():
            d = meters((fix["lat"], fix["lon"]), (p["lat"], p["lon"]))
            if d <= p["radius"]:
                return {"at": name, "near": (name, d), "fix": fix}
            if name != "car" and (best is None or d < best[1]):
                best = (name, d)
        return {"at": None, "near": best, "fix": fix}

    def set_tracking(self, on: bool) -> None:
        with self._lock:
            self.data["tracking"] = bool(on)
            self._save()
        if not on and self.history.exists():
            self.history.unlink()                           # "stop tracking" also deletes the trail

    def at_time(self, when: datetime) -> Optional[dict]:
        if not self.history.exists():
            return None
        t, best = when.timestamp(), None
        for ln in self.history.read_text(encoding="utf-8").splitlines():
            if ln.strip():
                f = json.loads(ln)
                if best is None or abs(f["ts"] - t) < abs(best["ts"] - t):
                    best = f
        return best if best and abs(best["ts"] - t) <= 3600 else None

    def last_visit(self, name: str, event: str = "arrive") -> Optional[float]:
        name = place_name(name)
        if event == "arrive" and name in self.data["inside"]:
            return self.data["inside"][name]
        vs = [v for v in self.data["visits"] if v["place"] == name]
        return (vs[-1]["from"] if event == "arrive" else vs[-1]["to"]) if vs else None

    def name_at(self, fix: dict) -> Optional[str]:
        for name, p in self.data["places"].items():
            if meters((fix["lat"], fix["lon"]), (p["lat"], p["lon"])) <= p["radius"]:
                return name
        return None


_places: Optional[Places] = None


def get_places() -> Places:
    global _places
    if _places is None:
        _places = Places()
    return _places


def phrase(event: str, place: str) -> str:
    """'get home', 'leave home', 'get to work', 'leave the gym'."""
    if place == "home":
        return "leave home" if event == "leave" else "get home"
    return f"leave {place}" if event == "leave" else f"get to {place}"


def maps_link(lat: float, lon: float) -> str:
    return f"https://www.google.com/maps/search/?api=1&query={lat:.6f},{lon:.6f}"


def when_from_words(text: str, now: Optional[datetime] = None) -> Optional[datetime]:
    """'yesterday at 3', 'at 9 this morning', '2 hours ago', 'Monday at 18:30' -> a past datetime, or None."""
    from datetime import timedelta
    from core.device import parse_clock
    now = now or datetime.now()
    t = (text or "").lower()
    m = re.search(r"(\d+|an?|half an?)\s*(hours?|minutes?|mins?)\s+ago", t)
    if m:
        n = {"a": 1, "an": 1}.get(m.group(1), 0.5 if m.group(1).startswith("half") else None)
        n = n if n is not None else int(m.group(1))
        return now - timedelta(hours=n) if m.group(2).startswith("h") else now - timedelta(minutes=n)
    day = now
    if "yesterday" in t:
        day = now - timedelta(days=1)
    else:
        for i, wd in enumerate(("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")):
            if wd in t:
                day = now - timedelta(days=(now.weekday() - i) % 7 or 7)
    hm = parse_clock(re.sub(r"\b(yesterday|today|this|last|on|monday|tuesday|wednesday|thursday|friday|saturday|"
                            r"sunday)\b", " ", t))
    if not hm:
        return None
    h, mi = hm
    if h < 12 and (re.search(r"\b(afternoon|evening|night|pm)\b", t) or
                   (h < 8 and not re.search(r"\b(morning|am)\b", t))):
        h += 12                                          # "at 3" means 15:00 unless you said morning
    out = day.replace(hour=h, minute=mi, second=0, microsecond=0)
    return out - timedelta(days=1) if out > now and day is now else out


# ------------------------------------------------------------ tools
NO_FIX = ("I don't know where your phone is right now, sir. Turn on Location in the E.V.A. app menu, with "
          "Hands-free on, and give it a minute.")


def tool_place_remember(name: str = "", **_):
    if not (name or "").strip():
        return {"result": json.dumps({"error": "no name"}), "say": "What should I call this place, sir?"}
    p = get_places().remember(name)
    if not p:
        return {"result": json.dumps({"error": "no fix"}), "say": NO_FIX, "exact": True}
    n = place_name(name)
    say = ("Got it, sir: I'll remember where you parked." if n == "car" else f"Got it, sir: this is {n} now.")
    return {"result": json.dumps({"place": n}), "say": say, "exact": True}


def tool_place_forget(name: str = "", **_):
    ok = get_places().forget(name)
    return {"result": json.dumps({"forgot": ok}), "exact": True,
            "say": f"Forgotten, sir: {place_name(name)}." if ok else f"I don't have a place called {name}, sir."}


def tool_where_am_i(place: str = "", **_):
    pl = get_places()
    target = place_name(place) if place else ""
    if target:
        p = pl.data["places"].get(target)
        if not p:
            return {"result": json.dumps({"error": "unknown place"}), "exact": True,
                    "say": (f"I don't have {target} saved, sir. When you're there, say 'remember this place as "
                            f"{target}'." if target != "car" else
                            "I don't know where you parked, sir. Next time, say 'remember where I parked'.")}
        link = maps_link(p["lat"], p["lon"])
        fix = pl.fresh()
        dist = f", {distance_words(meters((fix['lat'], fix['lon']), (p['lat'], p['lon'])))} from you" if fix else ""
        saved = datetime.fromtimestamp(p.get("saved", 0)).strftime("%H:%M on %A") if p.get("saved") else ""
        say = (f"You parked there at {saved}{dist}, sir. The map link is on your screen." if target == "car" else
               f"{target.capitalize()} is{dist or ' saved'}, sir.")
        return {"result": json.dumps({"place": target, "link": link}), "say": say, "exact": True,
                "widget": {"kind": "note", "title": target.capitalize(), "text": link}}
    w = pl.where()
    if not w["fix"]:
        return {"result": json.dumps({"error": "no fix"}), "say": NO_FIX, "exact": True}
    if w["at"]:
        since = pl.data["inside"].get(w["at"])
        when = f", since {datetime.fromtimestamp(since):%H:%M}" if since else ""
        return {"result": json.dumps({"at": w["at"]}), "say": f"You're at {w['at']}{when}, sir.", "exact": True}
    near = f" You're {distance_words(w['near'][1])} from {w['near'][0]}." if w["near"] else ""
    link = maps_link(w["fix"]["lat"], w["fix"]["lon"])
    return {"result": json.dumps({"at": None, "link": link}), "exact": True,
            "say": f"You're not at one of your saved places, sir.{near} The map link is on your screen.",
            "widget": {"kind": "note", "title": "Where you are", "text": link}}


def tool_place_reminder(place: str = "", event: str = "arrive", text: str = "", every_time: bool = False, **_):
    """For decisions that come without the sentence ("remind me when I get home to call Mom")."""
    if not place or not text:
        return {"result": json.dumps({"error": "missing"}), "say": "Which place, and what should I remind you of, sir?"}
    name = place_name(place)
    if name not in get_places().data["places"] and name != "car":
        return {"result": json.dumps({"error": "unknown place"}), "exact": True,
                "say": f"I don't know where {name} is yet, sir. When you're there, say 'remember this place as {name}'."}
    rule = get_places().add_rule(name, "leave" if str(event).startswith("leav") else "arrive",
                                 re.sub(r"^(?:to\s+)", "", text.strip()), once=not every_time)
    return {"result": json.dumps({"rule": rule["id"]}), "exact": True,
            "say": f"Okay, sir: when you {phrase(rule['event'], name)}, I'll remind you to {rule['text']}."}


def tool_location_tracking(on: bool = True, **_):
    get_places().set_tracking(bool(on))
    say = ("Tracking on, sir. I'll keep where you've been on this PC for 30 days, so you can ask 'where was I "
           "yesterday at 3'. Say 'stop tracking my location' to stop and delete it." if on else
           "Tracking off, sir, and the trail is deleted. Your saved places and reminders still work.")
    return {"result": json.dumps({"tracking": bool(on)}), "say": say, "exact": True}


def tool_location_history(when: str = "", place: str = "", **_):
    pl = get_places()
    if place:
        t = pl.last_visit(place, "arrive")
        if not t:
            return {"result": json.dumps({"error": "no visit"}), "exact": True,
                    "say": f"I haven't seen you at {place_name(place)} yet, sir."}
        return {"result": json.dumps({"place": place_name(place), "arrived": t}), "exact": True,
                "say": f"You last got to {place_name(place)} at {datetime.fromtimestamp(t):%H:%M on %A}, sir."}
    if not pl.data.get("tracking"):
        return {"result": json.dumps({"error": "tracking off"}), "exact": True,
                "say": "I don't keep a trail unless you say 'track my location', sir. I only know your saved places."}
    dt = when_from_words(when)
    fix = pl.at_time(dt) if dt else None
    if not fix:
        return {"result": json.dumps({"error": "nothing then"}), "exact": True,
                "say": "I don't have where you were around then, sir."}
    name = pl.name_at(fix)
    link = maps_link(fix["lat"], fix["lon"])
    say = (f"Around {dt:%H:%M} you were at {name}, sir." if name else
           f"Around {dt:%H:%M} you were somewhere I don't have a name for, sir. The map link is on your screen.")
    return {"result": json.dumps({"link": link}), "say": say, "exact": True,
            "widget": {"kind": "note", "title": f"{dt:%A %H:%M}", "text": link}}


SCHEMAS = [
    {"type": "function", "function": {"name": "place_remember",
     "description": "Save where the phone is now as a named place ('remember this place as home', 'this is the gym', "
                    "'remember where I parked' = car).",
     "parameters": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}}},
    {"type": "function", "function": {"name": "place_forget", "description": "Forget a saved place.",
     "parameters": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}}},
    {"type": "function", "function": {"name": "where_am_i",
     "description": "Where the user is now, or where a saved place is ('where am I', 'where did I park', 'how far "
                    "is home').",
     "parameters": {"type": "object", "properties": {"place": {"type": "string"}}}}},
    {"type": "function", "function": {"name": "place_reminder",
     "description": "A reminder for when the user arrives at or leaves a saved place ('remind me to buy milk when I "
                    "leave work').",
     "parameters": {"type": "object", "properties": {
         "place": {"type": "string"}, "event": {"type": "string", "enum": ["arrive", "leave"]},
         "text": {"type": "string", "description": "what to remind, e.g. 'buy milk'"},
         "every_time": {"type": "boolean"}}, "required": ["place", "event", "text"]}}},
    {"type": "function", "function": {"name": "location_tracking",
     "description": "Keep (on) or stop keeping and delete (off) a history of where the user has been.",
     "parameters": {"type": "object", "properties": {"on": {"type": "boolean"}}, "required": ["on"]}}},
    {"type": "function", "function": {"name": "location_history",
     "description": "Where the user was at a past time, or when they last got to a place ('where was I yesterday "
                    "at 3', 'when did I get home').",
     "parameters": {"type": "object", "properties": {"when": {"type": "string"}, "place": {"type": "string"}}}}},
]
FUNCTIONS = {"place_remember": tool_place_remember, "place_forget": tool_place_forget, "where_am_i": tool_where_am_i,
             "place_reminder": tool_place_reminder, "location_tracking": tool_location_tracking,
             "location_history": tool_location_history}
GUARDS = {"place_remember": r"\b(remember|save|this is|call this|parked)\b",
          "place_forget": r"\b(forget|delete|remove)\b",
          "where_am_i": r"\b(where|how far|park(?:ed)?|distance)\b",
          "place_reminder": r"\b(when|once|next time|every time|whenever)\b",
          "location_tracking": r"\b(track|tracking|location history|trail)\b",
          "location_history": r"\b(where was i|when did i|last time i|yesterday|ago)\b"}
ACTIONS = {"place_remember", "place_forget", "place_reminder", "location_tracking"}
EXACT = set(FUNCTIONS)
