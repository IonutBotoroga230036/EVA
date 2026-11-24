"""Commute (Nov 23): NS trains, delays, and when to leave for Radboud.

- "When's the next train to Nijmegen?" / "next train home" / "is my train delayed?": the next trips from the NS
  Reisinformatie API (trips v3), with platform, platform changes, delay, arrival, changes and cancellations.
- "When should I leave for uni tomorrow?": your own "Train To Nijmegen" calendar event is the plan (she finds that
  NS train, its platform and delay); without one, your first course that day (a course code like SOW-BKI141, the
  Radboud calendar, or a uni word in title/place; commute.uni_words, commute.course_codes), and the latest train
  that gets you to the station commute.campus_min + commute.arrive_early_min before it starts. Travel events
  ("Bus To RU", "Train To Rotterdam") are never the lecture; a train elsewhere that day is mentioned. Before 04:00
  "tomorrow" means the day you wake up to.
- ORACLE: on such days, "Sir, leave in 10 minutes for the 08:22 to Nijmegen ..." about commute.alert_min before
  you have to leave, with the live delay. Not when your phone says you're away from home. Quiet hours don't stop
  it (you'd miss the train); do-not-disturb does.

Needs NS_API_KEY in config/secrets.env (free: apiportal.ns.nl, product "Ns-App", the Reisinformatie API key).
Field names follow the public trips v3 shape (trips[].legs[].origin.plannedDateTime/actualDateTime/
plannedTrack/actualTrack, destination, cancelled, status, transfers, crowdForecast). Stations are sent as NS
codes (BD, NM, ...); a station not in the list below goes as you said it; add its code in commute.stations.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta
from typing import Callable, Optional

from loguru import logger

BASE = "https://gateway.apiportal.ns.nl/reisinformatie-api/api"
TRIPS_URL = BASE + "/v3/trips"
DEFAULTS = {"home_station": "Breda", "uni_station": "Nijmegen", "walk_min": 15, "arrive_early_min": 10,
            "campus_min": 20, "night_ends_hour": 4, "course_codes": r"\b[A-Z]{2,4}-[A-Z]{2,4}\d{2,4}\b",
            "alert_min": 10, "alerts": True, "away_km": 2.0,
            "uni_words": ["Radboud", "Nijmegen", "Huygens", "Heyendaal", "Donders", "Grotius", "Lecture hall"],
            "stations": {}}
STATIONS = {"breda": "BD", "nijmegen": "NM", "nijmegen centraal": "NM", "nijmegen heyendaal": "NMH",
            "heyendaal": "NMH", "tilburg": "TB", "tilburg centraal": "TB", "tilburg universiteit": "TBU",
            "tilburg reeshof": "TBR", "s-hertogenbosch": "HT", "'s-hertogenbosch": "HT", "den bosch": "HT",
            "utrecht": "UT", "utrecht centraal": "UT", "amsterdam": "ASD", "amsterdam centraal": "ASD",
            "rotterdam": "RTD", "rotterdam centraal": "RTD", "eindhoven": "EHV", "eindhoven centraal": "EHV",
            "arnhem": "AH", "arnhem centraal": "AH", "den haag": "GVC", "den haag centraal": "GVC",
            "the hague": "GVC", "schiphol": "SHL", "schiphol airport": "SHL", "oss": "O", "etten-leur": "ETN",
            "breda prinsenbeek": "BDPB", "roosendaal": "RSD", "dordrecht": "DDR", "zwolle": "ZL",
            "leiden": "LEDN", "leiden centraal": "LEDN", "delft": "DT", "groningen": "GN", "maastricht": "MT"}

FETCH: Optional[Callable[[str, dict], dict]] = None          # tests answer here instead of NS
NOW: Optional[Callable[[], datetime]] = None                  # tests set the clock here


def _now() -> datetime:
    return NOW() if NOW else datetime.now()
_plans: dict = {}                                             # event -> {trip, fetched, said}


class NSError(RuntimeError):
    pass


def cfg() -> dict:
    try:
        from core.settings import get_settings
        return {**DEFAULTS, **(get_settings().get("commute") or {})}
    except Exception:
        return dict(DEFAULTS)


def _key() -> Optional[str]:
    if os.environ.get("NS_API_KEY"):
        return os.environ["NS_API_KEY"]
    try:
        from core.security.vault import Vault
        v = Vault()
        v.load()
        return v.get_optional("NS_API_KEY")
    except Exception:
        return None


NO_KEY = ("I need an NS API key for train times, sir. Get one free at apiportal.ns.nl and put it in "
          "config/secrets.env as NS_API_KEY=..., then restart me.")


def code(station: str) -> str:
    s = (station or "").strip()
    own = {k.lower(): v for k, v in (cfg().get("stations") or {}).items()}
    return own.get(s.lower()) or STATIONS.get(s.lower()) or s


def _get(url: str, params: dict) -> dict:
    if FETCH is not None:
        return FETCH(url, params)
    key = _key()
    if not key:
        raise NSError("no key")
    import httpx
    try:
        r = httpx.get(url, params=params, headers={"Ocp-Apim-Subscription-Key": key}, timeout=8.0)
    except httpx.HTTPError as e:
        raise NSError(f"no connection ({e.__class__.__name__})")
    if r.status_code != 200:
        raise NSError(f"NS answered {r.status_code}")
    return r.json()


def ns_time(text: str) -> Optional[datetime]:
    """'2026-11-24T07:22:00+0100' -> a local clock time (naive, like the calendar's)."""
    try:
        dt = datetime.fromisoformat((text or "").replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt.astimezone().replace(tzinfo=None) if dt.tzinfo else dt


def parse_trip(t: dict) -> Optional[dict]:
    legs = t.get("legs") or []
    if not legs:
        return None
    o, d = legs[0].get("origin") or {}, legs[-1].get("destination") or {}
    dep, arr = ns_time(o.get("plannedDateTime", "")), ns_time(d.get("plannedDateTime", ""))
    if not dep or not arr:
        return None
    dep_now = ns_time(o.get("actualDateTime", "")) or dep
    arr_now = ns_time(d.get("actualDateTime", "")) or arr
    status = str(t.get("status") or "NORMAL").upper()
    return {"dep": dep, "dep_now": dep_now, "arr": arr, "arr_now": arr_now,
            "late": max(0, round((dep_now - dep).total_seconds() / 60)),
            "track": o.get("actualTrack") or o.get("plannedTrack") or "", "planned_track": o.get("plannedTrack") or "",
            "from": o.get("name") or "", "to": d.get("name") or "",
            "changes": int(t.get("transfers", len(legs) - 1) or 0),
            "cancelled": status in ("CANCELLED", "NOT_POSSIBLE", "CHANGE_NOT_POSSIBLE") or any(
                leg.get("cancelled") for leg in legs),
            "status": status, "busy": str(t.get("crowdForecast") or "").upper() == "HIGH",
            "train": (legs[0].get("product") or {}).get("displayName") or legs[0].get("name") or ""}


def trips(frm: str, to: str, when: Optional[datetime] = None, arrive: bool = False) -> list[dict]:
    params = {"fromStation": code(frm), "toStation": code(to)}
    if when:
        params["dateTime"] = when.astimezone().isoformat(timespec="seconds")
    if arrive:
        params["searchForArrival"] = "true"
    data = _get(TRIPS_URL, params)
    out = [p for p in (parse_trip(t) for t in (data.get("trips") or [])) if p]
    logger.info(f"COMMUTE: {len(out)} trips {params['fromStation']} -> {params['toStation']}")
    return out


def hm(dt: datetime) -> str:
    return dt.strftime("%H:%M")


def late_words(n: int) -> str:
    return "on time" if n <= 0 else f"{n} minute{'s' if n != 1 else ''} late"


def changes_words(n: int) -> str:
    return "direct" if n == 0 else "one change" if n == 1 else f"{n} changes"


def platform(t: dict) -> str:
    if not t["track"]:
        return ""
    moved = t["planned_track"] and t["planned_track"] != t["track"]
    return f"platform {t['track']}" + (f" (changed from {t['planned_track']})" if moved else "")


def describe(t: dict) -> str:
    plat = platform(t)
    out = f"{hm(t['dep'])} from {t['from']}" + (f", {plat}" if plat else "") + f", {late_words(t['late'])}"
    out += f". It arrives in {t['to']} at {hm(t['arr_now'])}, {changes_words(t['changes'])}"
    if t["busy"]:
        out += ", and it's expected to be busy"
    if t["status"] not in ("NORMAL", "CANCELLED") and not t["cancelled"]:
        out += f" (NS reports: {t['status'].replace('_', ' ').lower()})"
    return out


# ------------------------------------------------------------ tools
def fill_trains(args: dict, text: str) -> dict:
    """'next train home' goes from uni to home; at a place you named uni/work, trains go home by default."""
    out = dict(args)
    c = cfg()
    if out.get("from_station") or out.get("to_station"):
        return out
    home_words = re.search(r"\b(home|back to breda|back home|go back)\b", text or "", re.I)
    at_uni = False
    try:
        from core.places import get_places
        at = get_places().where().get("at") or ""
        at_uni = at in ("uni", "university", "radboud", "campus", "school", "work")
    except Exception:
        pass
    if home_words or (at_uni and not re.search(r"\bto (?:nijmegen|uni|radboud)\b", text or "", re.I)):
        out["from_station"], out["to_station"] = c["uni_station"], c["home_station"]
    return out


def tool_train_times(from_station: str = "", to_station: str = "", when: str = "", arrive_by: bool = False, **_):
    c = cfg()
    frm, to = from_station or c["home_station"], to_station or c["uni_station"]
    at = None
    if when:
        from core.timeparse import parse_when
        at = parse_when(when)
    if not _key():
        return {"result": json.dumps({"error": "no NS key"}), "say": NO_KEY, "exact": True}
    try:
        found = trips(frm, to, at, bool(arrive_by and at))
    except NSError as e:
        return {"result": json.dumps({"error": str(e)}), "say": f"I couldn't reach NS, sir: {e}.", "exact": True}
    if at is None:                                       # Nov 24: never the 23:54 that left at 23:55
        now = _now()
        found = [t for t in found if t["dep_now"] >= now]
    if not found:
        return {"result": json.dumps({"trips": []}), "exact": True,
                "say": f"NS has no trains from {frm} to {to} for that time, sir."}
    gone = [t for t in found[:3] if t["cancelled"]]
    ok = [t for t in found if not t["cancelled"]]
    parts = [f"The {hm(t['dep'])} is cancelled, sir." for t in gone[:2]]
    if ok:
        first = ok[0]
        lead = "Then the next one leaves at" if parts else "The next train to " + (first["to"] or to) + " leaves at"
        if at is None and not parts and (first["dep"] - _now()).total_seconds() > 3 * 3600:
            lead = f"There are no more trains tonight, sir. The first one to {first['to'] or to} leaves at"
        parts.append(f"{lead} {describe(first)}, sir.")
        if len(ok) > 1:
            n = ok[1]
            parts.append(f"After that: {hm(n['dep'])}, {late_words(n['late'])}.")
    else:
        parts.append("NS shows no running trains right now, sir.")
    say = " ".join(parts)
    return {"result": json.dumps({"trips": [{"dep": hm(t["dep"]), "late": t["late"], "track": t["track"],
                                             "arr": hm(t["arr_now"]), "cancelled": t["cancelled"]} for t in found[:4]]}),
            "say": say, "exact": True,
            "widget": {"kind": "note", "title": f"{frm} to {to}",
                       "text": "; ".join(f"{hm(t['dep'])} {'cancelled' if t['cancelled'] else late_words(t['late'])}"
                                         for t in found[:4])}}


_TRAVEL = re.compile(r"^\s*(?:take (?:the )?|catch (?:the )?)?(?:train|bus|bike|cycle|ride|drive|walk|go|travel|tram|"
                     r"metro|taxi|uber|flight|fly)\b.*\b(?:to|back|home)\b", re.I)
_TRAIN = re.compile(r"^\s*(?:take (?:the )?|catch (?:the )?)?train\b.*\bto\s+(.+?)\s*$", re.I)
_COURSE = re.compile(r"^\s*[A-Z]{2,4}-[A-Z]{2,4}\d{2,4}\w*(?:\s+[A-Z]+\d+)?\s*-\s*")


def is_travel(e: dict) -> bool:
    """Your own travel plans ("Train To Nijmegen", "Bus To RU", "Bike To Breda Centraal"): never the lecture."""
    return bool(_TRAVEL.match(e.get("title") or ""))


def _uni_text(text: str, c: dict) -> bool:
    t = (text or "").lower()
    return any(w and w.lower() in t for w in (c.get("uni_words") or []))


def is_uni(e: dict, c: Optional[dict] = None) -> bool:
    """A lecture or meeting at uni: a course code (SOW-BKI141), the uni calendar, or a uni word in title/place."""
    c = c or cfg()
    if e.get("all_day") or e.get("routine") or not e.get("start") or is_travel(e):
        return False
    if re.search(c.get("course_codes") or r"$^", e.get("title") or ""):
        return True
    return _uni_text(f"{e.get('title', '')} {e.get('location', '')} {e.get('calendar', '')}", c)


def spoken_title(title: str) -> str:
    """'SOW-BKI141 PER2 - Research Design & Scientific Method' -> 'Research Design & Scientific Method'."""
    return _COURSE.sub("", title or "").strip() or (title or "your lecture")


def first_uni_event(events: list[dict], after: Optional[datetime] = None) -> Optional[dict]:
    c = cfg()
    for e in sorted((e for e in events if is_uni(e, c)), key=lambda e: e["start"]):
        if after is None or e["start"] > after:
            return e
    return None


def day_target(events: list[dict], after: Optional[datetime] = None) -> Optional[dict]:
    """How you get to uni that day. Your own "Train To Nijmegen" event wins (that's your plan); otherwise the first
    course, arriving in time. 'elsewhere': the places your train events go instead."""
    c = cfg()
    evs = sorted((e for e in events if e.get("start") and not e.get("all_day") and (after is None or e["start"] > after)),
                 key=lambda e: e["start"])
    planned, elsewhere = None, []
    for e in evs:
        m = _TRAIN.match(e.get("title") or "")
        if not m:
            continue
        dest = m.group(1)
        if _uni_text(dest, c) or dest.lower().startswith(c["uni_station"].lower().split()[0]):
            planned = planned or e
        elif dest.lower() not in ("home", c["home_station"].lower()) and not _uni_text(dest, {"uni_words": [
                c["home_station"]]}):
            elsewhere.append(dest.strip())
    if planned:
        return {"kind": "planned", "event": planned,
                "lecture": first_uni_event([e for e in evs if e["start"] >= planned["start"]]), "elsewhere": []}
    lecture = first_uni_event(evs)
    if lecture:
        return {"kind": "arrive", "event": lecture, "lecture": lecture, "elsewhere": elsewhere}
    return None


def best_trip(found: list[dict], arrive_by: datetime, now: datetime, walk: int) -> tuple[Optional[dict], Optional[dict]]:
    """(the latest train that gets you there in time and that you can still catch, the first catchable one)."""
    catchable = [t for t in found if not t["cancelled"] and t["dep"] - timedelta(minutes=walk) >= now - timedelta(minutes=1)]
    fits = [t for t in catchable if t["arr_now"] <= arrive_by]
    best = max(fits, key=lambda t: t["dep"]) if fits else None
    first = min(catchable, key=lambda t: t["dep"]) if catchable else None
    return best, first


def planned_trip(found: list[dict], planned: datetime) -> Optional[dict]:
    """The NS train that matches your calendar's 'Train To Nijmegen' time (leaving at it, or just after)."""
    near = [t for t in found if not t["cancelled"] and t["dep"] >= planned - timedelta(minutes=5)
            and t["dep"] <= planned + timedelta(minutes=30)]
    return min(near, key=lambda t: t["dep"]) if near else None


def _plat(t: dict) -> str:
    plat = platform(t)
    return f", {plat}" if plat else ""


def plan(google, day: str = "today", now: Optional[datetime] = None) -> dict:
    now = now or datetime.now()
    c = cfg()
    from core.timeparse import day_window
    base = now.date()
    phrase = (day or "today").strip().lower()
    if phrase == "tomorrow" and now.hour < int(c.get("night_ends_hour", 4)):
        base = base - timedelta(days=1)                  # 00:03 "tomorrow": the day you wake up to
    start, end, _ = day_window(phrase, base)
    label = "today" if start.date() == now.date() and phrase in ("today", "") else f"on {start:%A}"
    events = google.list_events(max(start, now), end)
    target = day_target(events)
    if not target:
        return {"say": f"I don't see anything in Nijmegen on your calendar {label}, sir."}
    walk, early, campus = int(c["walk_min"]), int(c["arrive_early_min"]), int(c.get("campus_min", 20))
    lec = target["lecture"]
    if target["kind"] == "planned":
        e = target["event"]
        found = trips(c["home_station"], c["uni_station"], e["start"] - timedelta(minutes=5))
        t = planned_trip(found, e["start"])
        head = f"Your calendar has the train to {c['uni_station']} at {hm(e['start'])} {label}"
        if not t:
            return {"say": f"{head}, but NS has no train around then, sir. Ask me for the next trains to see what runs."}
        leave = t["dep"] - timedelta(minutes=walk)
        say = (f"{head}: that's the {hm(t['dep'])} from {t['from']}{_plat(t)}, "
               f"{late_words(t['late']) + ' right now' if t['late'] else 'on time'}. Leave home at {hm(leave)}. "
               f"It arrives in {t['to']} at {hm(t['arr_now'])}")
        if lec:
            what = f"{spoken_title(lec['title'])} at {hm(lec['start'])}"
            ok = t["arr_now"] + timedelta(minutes=campus) <= lec["start"]
            say += f", in time for {what}" if ok else f", which is tight for {what}"
        return {"say": say + ", sir.", "leave": leave.isoformat(), "train": hm(t["dep"])}
    e = lec
    what = f"{spoken_title(e['title'])} at {hm(e['start'])}"
    lead = ""
    if target["elsewhere"]:
        lead = (f"Your calendar has you going to {target['elsewhere'][0]} {label}, not {c['uni_station']}, sir. "
                f"If you go to {what}: ")
    arrive_by = e["start"] - timedelta(minutes=early + campus)
    found = trips(c["home_station"], c["uni_station"], arrive_by, arrive=True)
    best, first = best_trip(found, arrive_by, now, walk)
    if best:
        leave = best["dep"] - timedelta(minutes=walk)
        say = (f"For {what} {label}, leave home at {hm(leave)} for the {hm(best['dep'])} from {best['from']}"
               f"{_plat(best)}. It arrives in {best['to']} at {hm(best['arr_now'])}")
        if best["late"]:
            say += f" ({late_words(best['late'])} right now)"
        return {"say": lead + say + ", sir.", "leave": leave.isoformat(), "train": hm(best["dep"])}
    if first and first["arr_now"] + timedelta(minutes=campus) <= e["start"]:
        leave = first["dep"] - timedelta(minutes=walk)
        mins = round((e["start"] - first["arr_now"]).total_seconds() / 60)
        return {"say": lead + (f"For {what} {label}, leave home at {hm(leave)} for the {hm(first['dep'])} from "
                               f"{first['from']}{_plat(first)}. It arrives in {first['to']} at {hm(first['arr_now'])}, "
                               f"which leaves {mins} minutes to get there: tight, sir."),
                "leave": leave.isoformat(), "train": hm(first["dep"])}
    if first:
        leave = first["dep"] - timedelta(minutes=walk)
        return {"say": lead + (f"You can't make it in time for {what}, sir. The first train you can still catch, the "
                               f"{hm(first['dep'])} from {first['from']}, arrives in {first['to']} at "
                               f"{hm(first['arr_now'])}: leave home at {hm(leave)}.")}
    return {"say": lead + f"NS has no train that still works for {what}, sir."}


def tool_commute_plan(day: str = "", **_):
    if not _key():
        return {"result": json.dumps({"error": "no NS key"}), "say": NO_KEY, "exact": True}
    try:
        from core.google_api import connected, get_google
        if not connected():
            return {"result": json.dumps({"error": "no calendar"}), "exact": True,
                    "say": "I need your Google Calendar connected to plan around your lectures, sir."}
        out = plan(get_google(), day or "today")
    except NSError as e:
        return {"result": json.dumps({"error": str(e)}), "say": f"I couldn't reach NS, sir: {e}.", "exact": True}
    return {"result": json.dumps({k: v for k, v in out.items() if k != "say"}), "say": out["say"], "exact": True}


# ------------------------------------------------------------ ORACLE: "leave in 10 minutes"
def away_from_home() -> bool:
    """True only when the phone recently said you're more than commute.away_km from the place called home."""
    try:
        from core.places import get_places, meters
        p = get_places()
        home = p.data["places"].get("home")
        fix = p.fresh()
        if not home or not fix:
            return False
        return meters((fix["lat"], fix["lon"]), (home["lat"], home["lon"])) > float(cfg()["away_km"]) * 1000
    except Exception:
        return False


def leave_alerts(events: list[dict], now: Optional[datetime] = None) -> list[str]:
    """Lines to say now: one per uni day, alert_min before you have to leave (your planned train, or the one that
    gets you to your first course in time)."""
    now = now or datetime.now()
    c = cfg()
    if not c.get("alerts", True) or not _key():
        return []
    target = day_target([x for x in events if x.get("start") and x["start"].date() == now.date()], after=now)
    if not target:
        return []
    e, lec = target["event"], target["lecture"]
    k = f"{e.get('id')}|{e['start'].isoformat()}"
    p = _plans.setdefault(k, {"trip": None, "fetched": 0.0, "said": False})
    if p["said"]:
        return []
    walk, early, alert = int(c["walk_min"]), int(c["arrive_early_min"]), int(c["alert_min"])
    campus = int(c.get("campus_min", 20))
    if (e["start"] - now).total_seconds() > (4 * 3600):
        return []
    if now.timestamp() - p["fetched"] > 600:
        try:
            if target["kind"] == "planned":
                found = trips(c["home_station"], c["uni_station"], e["start"] - timedelta(minutes=5))
                p["trip"] = planned_trip(found, e["start"])
            else:
                arrive_by = e["start"] - timedelta(minutes=early + campus)
                found = trips(c["home_station"], c["uni_station"], arrive_by, arrive=True)
                p["trip"], _ = best_trip(found, arrive_by, now - timedelta(minutes=alert), walk)
        except NSError as err:
            logger.warning(f"COMMUTE: NS check failed ({err})")
            return []
        p["fetched"] = now.timestamp()
    t = p["trip"]
    if not t:
        return []
    mins = round((t["dep"] - timedelta(minutes=walk) - now).total_seconds() / 60)
    if mins > alert or mins < -3:
        return []
    if away_from_home():
        p["said"] = True
        logger.info("COMMUTE: you're away from home; no leave alert")
        return []
    p["said"] = True
    lead = f"leave in {mins} minute{'s' if mins != 1 else ''}" if mins > 0 else "leave now"
    tail = f" That gets you to {spoken_title(lec['title'])} at {hm(lec['start'])}." if lec else ""
    return [f"Sir, {lead} for the {hm(t['dep'])} to {t['to'] or c['uni_station']}{_plat(t)}. It's "
            f"{'running ' + late_words(t['late']) if t['late'] else 'on time'}.{tail}"]


SCHEMAS = [
    {"type": "function", "function": {
        "name": "train_times",
        "description": ("NS train times in the Netherlands: the next trains, delays, platforms and cancellations "
                        "('next train to Nijmegen', 'trains home', 'is my train delayed?'). Default route: home "
                        "(Breda) to university (Nijmegen)."),
        "parameters": {"type": "object", "properties": {
            "from_station": {"type": "string", "description": "Only if the user names one."},
            "to_station": {"type": "string", "description": "Only if the user names one; 'home' means Breda."},
            "when": {"type": "string", "description": "Only if the user gives a time, e.g. 'tomorrow at 8'."},
            "arrive_by": {"type": "boolean", "description": "True when the time is when they want to arrive."}}}}},
    {"type": "function", "function": {
        "name": "commute_plan",
        "description": ("When the user should leave home to get to their first lecture or event in Nijmegen "
                        "(Radboud) on a day, from their calendar and live NS trains ('when should I leave for uni "
                        "tomorrow?')."),
        "parameters": {"type": "object", "properties": {
            "day": {"type": "string", "description": "'today', 'tomorrow' or a weekday. Empty means today."}}}}},
]
FUNCTIONS = {"train_times": tool_train_times, "commute_plan": tool_commute_plan}
GUARDS = {"train_times": r"\b(trains?|ns|platform|spoor|delayed|delay|intercity|sprinter)\b",
          "commute_plan": r"\b(leave|commute|get to (?:uni|radboud|nijmegen|class|my lecture))\b"}
EXACT = set(FUNCTIONS)
FILLERS = {"train_times": fill_trains}
