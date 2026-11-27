"""Places near you, with opening hours (v0.6): "is there a pharmacy still open?", "how long until the Jumbo
closes?", "a café near me".

OpenStreetMap through the Overpass API: free, no key. The main server asks apps to identify themselves (a
User-Agent and Referer) and to send the query in a POST body (since April 2026 it answers 406 otherwise), to cache
and not to hammer it; a second free server is the fallback. Where you are: the phone's fresh fix, else your saved
home, else the middle of your city.

The opening hours are OSM's own format ("Mo-Sa 08:00-22:00; Su 10:00-20:00"). The common forms are read here into
open now / closes at / opens at; anything unusual is passed on as written, for the mind to read.
"""

from __future__ import annotations

import json
import math
import re
import time
from datetime import datetime, timedelta
from typing import Optional

SERVERS = ("https://overpass-api.de/api/interpreter", "https://overpass.private.coffee/api/interpreter")
HEADERS = {"User-Agent": "EVA-personal-assistant/0.6 (single user, github.com/IonutBotoroga230036/EVA)",
           "Referer": "https://github.com/IonutBotoroga230036/EVA", "Accept": "application/json",
           "Content-Type": "application/x-www-form-urlencoded"}
CITY_CENTRES = {"breda": (51.5890, 4.7760), "tilburg": (51.5555, 5.0913), "nijmegen": (51.8426, 5.8528)}
_cache: dict = {}
_last_call = {"ts": 0.0}

# words -> OSM tags (key, value). Anything else is looked up by name (brands: "Jumbo", "Albert Heijn", "Action")
KINDS = {
    "supermarket": ("shop", "supermarket"), "grocery": ("shop", "supermarket"), "groceries": ("shop", "supermarket"),
    "pharmacy": ("amenity", "pharmacy"), "drugstore": ("shop", "chemist"), "chemist": ("shop", "chemist"),
    "cafe": ("amenity", "cafe"), "café": ("amenity", "cafe"), "coffee": ("amenity", "cafe"),
    "restaurant": ("amenity", "restaurant"), "fast food": ("amenity", "fast_food"), "pizza": ("cuisine", "pizza"),
    "bar": ("amenity", "bar"), "pub": ("amenity", "pub"), "bakery": ("shop", "bakery"), "bank": ("amenity", "bank"),
    "atm": ("amenity", "atm"), "cash machine": ("amenity", "atm"), "library": ("amenity", "library"),
    "gym": ("leisure", "fitness_centre"), "fuel": ("amenity", "fuel"), "petrol": ("amenity", "fuel"),
    "gas station": ("amenity", "fuel"), "charging": ("amenity", "charging_station"),
    "hardware": ("shop", "doityourself"), "diy": ("shop", "doityourself"), "electronics": ("shop", "electronics"),
    "post office": ("amenity", "post_office"), "parcel": ("amenity", "parcel_locker"), "doctor": ("amenity", "doctors"),
    "dentist": ("amenity", "dentist"), "hospital": ("amenity", "hospital"), "hairdresser": ("shop", "hairdresser"),
    "barber": ("shop", "hairdresser"), "bike shop": ("shop", "bicycle"), "bicycle": ("shop", "bicycle"),
    "clothes": ("shop", "clothes"), "books": ("shop", "books"), "bookshop": ("shop", "books"),
    "print": ("shop", "copyshop"), "copy shop": ("shop", "copyshop"), "makerspace": ("leisure", "hackerspace"),
    "hackerspace": ("leisure", "hackerspace"), "3d print": ("leisure", "hackerspace"), "cinema": ("amenity", "cinema"),
    "park": ("leisure", "park"), "station": ("railway", "station"), "train station": ("railway", "station"),
}


# Nov 25: Whisper heard "Albert Hain". Shop names in the Netherlands, as he says them, to how OSM spells them
BRANDS = {r"albert h\w*[nm]\b|albert hijn|albert heyn|albert hein|\bah\b|appie": "Albert Heijn", r"jumbo": "Jumbo", r"lidl": "Lidl",
          r"aldi": "Aldi", r"plus\b": "PLUS", r"dirk": "Dirk", r"action\b": "Action", r"kruidvat": "Kruidvat",
          r"etos": "Etos", r"hema\b": "HEMA", r"gamma\b": "GAMMA", r"praxis": "Praxis", r"karwei": "Karwei",
          r"media ?markt": "MediaMarkt", r"coolblue": "Coolblue", r"blokker": "Blokker", r"primark": "Primark",
          r"ikea": "IKEA", r"spar\b": "Spar", r"coop\b": "Coop", r"mcdonald'?s?|mac ?donalds": "McDonald's",
          r"burger ?king": "Burger King", r"starbucks": "Starbucks", r"bol\.?com": "bol"}


def brand(what: str) -> str:
    w = (what or "").lower()
    for rx, name in BRANDS.items():
        if re.search(rx, w):
            return name
    return ""


def _kind(what: str) -> Optional[tuple[str, str]]:
    w = (what or "").lower().strip()
    for word in sorted(KINDS, key=len, reverse=True):
        if re.search(rf"\b{re.escape(word)}s?\b", w):
            return KINDS[word]
    return None


def meters(a: tuple[float, float], b: tuple[float, float]) -> float:
    r = 6371000.0
    p1, p2 = math.radians(a[0]), math.radians(b[0])
    dp, dl = p2 - p1, math.radians(b[1] - a[1])
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def where() -> tuple[Optional[tuple[float, float]], str]:
    """(lat, lon), and how it's known: 'here' (the phone), 'home', 'the city centre'."""
    try:
        from core.places import get_places
        pl = get_places()
        fix = pl.fresh()
        if fix:
            return (fix["lat"], fix["lon"]), "here"
        home = pl.data.get("places", {}).get("home")
        if home:
            return (home["lat"], home["lon"]), "home"
    except Exception:
        pass
    import os
    city = os.environ.get("EVA_HOME_CITY", "Breda").lower()
    return CITY_CENTRES.get(city, CITY_CENTRES["breda"]), "the city centre"


# ---------------------------------------------------------------- opening hours (the common forms)
DAYS = ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"]


def _days(spec: str) -> Optional[set[int]]:
    out: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        m = re.fullmatch(r"(Mo|Tu|We|Th|Fr|Sa|Su)(?:-(Mo|Tu|We|Th|Fr|Sa|Su))?", part)
        if not m:
            return None
        a = DAYS.index(m.group(1))
        b = DAYS.index(m.group(2)) if m.group(2) else a
        i = a
        while True:
            out.add(i)
            if i == b:
                break
            i = (i + 1) % 7
    return out


def _spans(spec: str) -> Optional[list[tuple[int, int]]]:
    out = []
    for part in spec.split(","):
        m = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})\s*", part)
        if not m:
            return None
        a = int(m.group(1)) * 60 + int(m.group(2))
        b = int(m.group(3)) * 60 + int(m.group(4))
        out.append((a, b if b > a else b + 24 * 60))
    return out


def schedule(hours: str) -> Optional[dict[int, list[tuple[int, int]]]]:
    """'Mo-Fr 08:00-20:00; Sa 09:00-17:00; Su off' -> {weekday: [(from_min, to_min)]}, or None if unusual."""
    h = (hours or "").strip()
    if not h:
        return None
    if h == "24/7":
        return {d: [(0, 24 * 60)] for d in range(7)}
    week: dict[int, list[tuple[int, int]]] = {d: [] for d in range(7)}
    for rule in [r.strip() for r in h.split(";") if r.strip()]:
        rule = re.sub(r"\s*,?\s*PH\s+(?:off|closed)\s*$", "", rule)
        if re.fullmatch(r"PH.*", rule):
            continue
        m = re.fullmatch(r"((?:(?:Mo|Tu|We|Th|Fr|Sa|Su)(?:-(?:Mo|Tu|We|Th|Fr|Sa|Su))?,?\s*)+)\s+(.+)", rule)
        if m:
            days = _days(m.group(1).replace(" ", "").rstrip(","))
            times = m.group(2).strip()
        else:
            days, times = set(range(7)), rule
        if days is None:
            return None
        if times in ("off", "closed"):
            for d in days:
                week[d] = []
            continue
        spans = _spans(times)
        if spans is None:
            return None
        for d in days:
            week[d] = spans                       # a later rule for a day replaces an earlier one, as in OSM
    return week


def status(hours: str, now: Optional[datetime] = None) -> Optional[dict]:
    """{'open': bool, 'closes': 'HH:MM', 'minutes_left': n} or {'open': False, 'opens': 'Mon 08:00'}; None if unknown."""
    week = schedule(hours)
    if week is None:
        return None
    now = now or datetime.now()
    t = now.hour * 60 + now.minute
    today, yesterday = now.weekday(), (now.weekday() - 1) % 7
    for a, b in week[yesterday]:                  # still open from last night (22:00-02:00)
        if b > 24 * 60 and t < b - 24 * 60:
            return {"open": True, "closes": f"{(b - 1440) // 60:02d}:{(b - 1440) % 60:02d}", "minutes_left": b - 1440 - t}
    for a, b in week[today]:
        if a <= t < b:
            end = b % (24 * 60)
            return {"open": True, "closes": f"{end // 60:02d}:{end % 60:02d}", "minutes_left": b - t}
    for k in range(0, 8):
        d = (today + k) % 7
        for a, b in sorted(week[d]):
            if k > 0 or a > t:
                day = "today" if k == 0 else "tomorrow" if k == 1 else (now + timedelta(days=k)).strftime("%A")
                return {"open": False, "opens": f"{day} {a // 60:02d}:{a % 60:02d}"}
    return {"open": False, "opens": None}


# ---------------------------------------------------------------- the search
def query(what: str, centre: tuple[float, float], radius: int) -> str:
    lat, lon = centre
    kind = _kind(what)
    b = brand(what)
    if b:
        safe = re.sub(r"([.^$*+?()\[\]{}|\\])", r"\\\1", b)
        return (f"[out:json][timeout:20];(nwr[\"brand\"~\"^{safe}\",i](around:{radius},{lat},{lon});"
                f"nwr[\"name\"~\"^{safe}\",i](around:{radius},{lat},{lon}););out center tags 40;")
    elif kind:
        sel = f'["{kind[0]}"="{kind[1]}"]'
    else:
        name = re.sub(r"[^\w\s'&.-]", "", what).strip()[:60] or "shop"
        sel = f'["name"~"{name}",i]'
    return (f"[out:json][timeout:20];(node{sel}(around:{radius},{lat},{lon});way{sel}(around:{radius},{lat},{lon}););"
            f"out center tags 40;")


NOMINATIM = "https://nominatim.openstreetmap.org/search"
OVERPASS_TIMEOUT_S = 7.0          # v0.8: per server, all asked at once
OVERPASS_HEAD_START_S = 2.5       # Nominatim's answer waits this long for Overpass


def nominatim(what: str, centre: tuple[float, float], radius: int) -> list[dict]:
    """The second route (Nov 25: both Overpass servers timed out after 50 s): OSM's own search, bounded to a box
    around you, with extratags for the opening hours. Same element shape as Overpass."""
    import httpx
    lat, lon = centre
    d_lat = radius / 111000.0
    d_lon = radius / (111000.0 * max(0.2, math.cos(math.radians(lat))))
    kind = _kind(what)
    q = brand(what) or (f"[{kind[1].replace('_', ' ')}]" if kind and not brand(what) else what)
    r = httpx.get(NOMINATIM, params={"q": q, "format": "jsonv2", "extratags": 1, "limit": 20, "bounded": 1,
                                     "viewbox": f"{lon - d_lon},{lat + d_lat},{lon + d_lon},{lat - d_lat}"},
                  headers={k: v for k, v in HEADERS.items() if k != "Content-Type"}, timeout=10)
    r.raise_for_status()
    out = []
    for x in r.json():
        tags = dict(x.get("extratags") or {})
        tags.setdefault("name", x.get("name") or (x.get("display_name") or "").split(",")[0])
        out.append({"lat": float(x["lat"]), "lon": float(x["lon"]), "tags": tags})
    return out


def search(what: str, centre: tuple[float, float], radius: int) -> list[dict]:
    import httpx
    from loguru import logger
    q = query(what, centre, radius)
    key = (q, int(time.time() // 600))                 # ten minutes of cache
    if key in _cache:
        return _cache[key]
    wait = 2.0 - (time.time() - _last_call["ts"])      # never two calls in quick succession
    if wait > 0:
        time.sleep(wait)
    problems: list[str] = []
    _last_call["ts"] = time.time()

    def overpass(url):
        r = httpx.post(url, data={"data": q}, headers=HEADERS, timeout=httpx.Timeout(OVERPASS_TIMEOUT_S, connect=4.0))
        if r.status_code != 200:
            raise RuntimeError(f"answered {r.status_code}")
        return r.json().get("elements", [])
    # v0.8: all three at once (27 Nov: two Overpass timeouts in a row took 20 s); Overpass has the better data, so
    # it gets a head start; Nominatim answers when Overpass is slow or down
    from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait as fwait
    pool = ThreadPoolExecutor(max_workers=3)
    try:
        jobs = {pool.submit(overpass, url): url.split("/")[2] for url in SERVERS}
        nomi = pool.submit(nominatim, what, centre, radius)
        jobs[nomi] = "nominatim"
        deadline = time.time() + OVERPASS_TIMEOUT_S + 1
        pending = set(jobs)
        while pending and time.time() < deadline:
            done, pending = fwait(pending, timeout=max(0.05, deadline - time.time()), return_when=FIRST_COMPLETED)
            for f in done:
                try:
                    els = f.result()
                except Exception as e:
                    problems.append(f"{jobs[f]}: {e.__class__.__name__}")
                    continue
                if f is nomi and any(j not in (nomi,) and not j.done() for j in jobs) and \
                        time.time() < _last_call["ts"] + OVERPASS_HEAD_START_S:
                    pending.add(f)                     # hold Nominatim's answer a moment for Overpass
                    time.sleep(0.1)
                    continue
                if f is nomi:
                    logger.info(f"NEARBY: Nominatim answered first ({'; '.join(problems) or 'Overpass slow'})")
                _cache[key] = els
                return els
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    raise RuntimeError("; ".join(problems) or "no map server answered in time")


def _clean_what(what: str) -> str:
    """'Is the Albert Heim still open?' -> 'Albert Heijn' (27 Nov: the whole question went in as the name)."""
    w = " ".join((what or "").split()).strip(" ?.!")
    w = re.sub(r"^(?:is|are|was)\s+(?:the|a|an|there\s+(?:a|an|any))?\s*", "", w, flags=re.I)
    w = re.sub(r"\s+(?:still\s+)?open(?:\s+(?:now|today|tonight|right now))?$", "", w, flags=re.I)
    w = re.sub(r"\s+(?:near(?:by| me)?|around (?:here|me)|close by)$", "", w, flags=re.I)
    return brand(w) or w.strip() or what


def tool_nearby(what: str = "", radius_m: int = 1500, open_now: bool = False, **_):
    what = _clean_what(what)
    if not what:
        return {"result": json.dumps({"error": "what are you looking for?"})}
    centre, how = where()
    radius = max(200, min(int(radius_m or 1500), 10000))
    try:
        els = search(what, centre, radius)
    except Exception as e:
        return {"result": json.dumps({"error": f"the map search failed: {e}"}), "exact": True,
                "say": "The map search isn't answering just now, sir, so I can't tell you what's open."}
    now = datetime.now()
    places = []
    for el in els:
        tags = el.get("tags") or {}
        lat = el.get("lat") or (el.get("center") or {}).get("lat")
        lon = el.get("lon") or (el.get("center") or {}).get("lon")
        if not tags.get("name") or lat is None:
            continue
        hours = tags.get("opening_hours", "")
        st = status(hours, now) if hours else None
        addr = " ".join(x for x in (tags.get("addr:street", ""), tags.get("addr:housenumber", "")) if x)
        places.append({"name": tags["name"], "distance_m": round(meters(centre, (lat, lon))),
                       "hours": hours or None, "open_now": st.get("open") if st else None,
                       "closes": st.get("closes") if st else None, "minutes_left": st.get("minutes_left") if st else None,
                       "opens": st.get("opens") if st else None, "address": addr or None,
                       "map": f"https://www.openstreetmap.org/?mlat={lat}&mlon={lon}#map=18/{lat}/{lon}"})
    places.sort(key=lambda p: p["distance_m"])
    closed = [p for p in places if p["open_now"] is False]
    if open_now:
        places = [p for p in places if p["open_now"] is not False]
    places = places[:8]
    if not places and closed:                         # v0.8: found, but all closed: say so, with when they open
        p0 = closed[0]
        d = f"{p0['distance_m']} metres" if p0["distance_m"] < 1000 else f"{p0['distance_m'] / 1000:.1f} kilometres"
        when = f"; it opens {p0['opens']}" if p0.get("opens") else ""
        return {"result": json.dumps({"what": what, "near": how, "open_now": [], "closed": closed[:5]},
                                     ensure_ascii=False), "exact": True,
                "say": f"None is open right now, sir. The nearest {what}, {d} away, is closed{when}."}
    if not places:
        return {"result": json.dumps({"what": what, "near": how, "places": []}), "exact": True,
                "say": f"I couldn't find {what} within {radius / 1000:g} kilometres of {how}, sir, so I can't say "
                       f"whether one is open."}

    def line(p):
        d = f"{p['distance_m']} metres" if p["distance_m"] < 1000 else f"{p['distance_m'] / 1000:.1f} kilometres"
        if p["open_now"]:
            h = f", open until {p['closes']}"
        elif p["open_now"] is False:
            h = f", closed, opens {p['opens']}" if p["opens"] else ", closed"
        else:
            h = ""
        return f"{p['name']}, {d}{h}"
    say = f"The nearest is {line(places[0])}, sir." + (f" Then {line(places[1])}." if len(places) > 1 else "")
    rows = "\n".join(f"{i}. {line(p)}" + (f" ({p['address']})" if p["address"] else "") for i, p in enumerate(places, 1))
    return {"result": json.dumps({"what": what, "near": how, "now": now.strftime("%a %H:%M"), "places": places},
                                 ensure_ascii=False),
            "say": say, "exact": True, "widget": {"kind": "note", "title": f"{what.capitalize()} near {how}", "text": rows}}


SCHEMAS = [{"type": "function", "function": {
    "name": "nearby",
    "description": "Places near him from the map, with distance and opening hours (open now, closes at, opens at): "
                   "shops, supermarkets, pharmacies, cafés, restaurants, ATMs, makerspaces, or a brand or name "
                   "('Jumbo', 'Action'). 'Is the Jumbo still open?', 'a pharmacy open now', 'how long until X closes'.",
    "parameters": {"type": "object", "properties": {
        "what": {"type": "string"}, "radius_m": {"type": "integer", "description": "Default 1500"},
        "open_now": {"type": "boolean", "description": "Only places open now"}}, "required": ["what"]}}}]
FUNCTIONS = {"nearby": tool_nearby}
GUARDS = {"nearby": r"\b(near|nearby|close|closest|nearest|around|open|closes?|closing|where)\w*\b"}
ACKS = {"nearby": "Checking the map, sir."}
