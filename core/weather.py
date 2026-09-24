"""
Weather v2 (Milestone D): current conditions AND forecasts from Open-Meteo.

    get_weather_report("Tilburg", day="tomorrow", hour="18:00")
    get_weather_report("Portugal", day="friday")       # countries -> capital, said openly
    get_weather_report("")                              # home city, right now

Model choice: Dutch, Belgian and Luxembourg locations use KNMI HARMONIE
(knmi_seamless: 2 km, updated hourly for 2.5 days, then blended with ECMWF),
which is what Dutch weather apps are built on. Everywhere else uses Open-Meteo's
best_match. Every report names its source so answers can be checked.

Ambiguous hours: "at 6" with no am/pm means 18:00 for hours 1 to 6 (people rarely
ask about 3 in the morning); the report carries "assumed" so she says the time
out loud and a wrong guess is obvious immediately.

No API key. The parsing is pure (resolve_day, resolve_hour) so it is unit tested
without network. "Now" is computed in the CITY's timezone, so "tomorrow in
Tokyo" means Tokyo's tomorrow. Forecasts reach 16 days ahead; beyond that the
tool says so instead of guessing.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from typing import Optional

import httpx

GEO_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
MAX_DAYS = 15                  # knmi_seamless reaches ~15 days; keep one limit everywhere
KNMI_COUNTRIES = {"NL", "BE", "LU"}

CODES = {
    0: "clear", 1: "mainly clear", 2: "partly cloudy", 3: "overcast", 45: "fog", 48: "rime fog",
    51: "light drizzle", 53: "drizzle", 55: "heavy drizzle", 56: "freezing drizzle", 57: "freezing drizzle",
    61: "light rain", 63: "rain", 65: "heavy rain", 66: "freezing rain", 67: "freezing rain",
    71: "light snow", 73: "snow", 75: "heavy snow", 77: "snow grains", 80: "rain showers",
    81: "rain showers", 82: "violent rain showers", 85: "snow showers", 86: "heavy snow showers",
    95: "thunderstorm", 96: "thunderstorm with hail", 99: "severe thunderstorm",
}

# Asking for a country's weather means its main city. We say which city we used.
COUNTRY_CAPITALS = {
    "portugal": "Lisbon", "spain": "Madrid", "france": "Paris", "germany": "Berlin",
    "italy": "Rome", "netherlands": "Amsterdam", "the netherlands": "Amsterdam", "holland": "Amsterdam",
    "belgium": "Brussels", "romania": "Bucharest", "moldova": "Chisinau", "switzerland": "Bern",
    "austria": "Vienna", "poland": "Warsaw", "greece": "Athens", "turkey": "Istanbul",
    "uk": "London", "united kingdom": "London", "england": "London", "ireland": "Dublin",
    "denmark": "Copenhagen", "sweden": "Stockholm", "norway": "Oslo", "finland": "Helsinki",
    "czechia": "Prague", "czech republic": "Prague", "hungary": "Budapest", "bulgaria": "Sofia",
    "croatia": "Zagreb", "serbia": "Belgrade", "usa": "New York", "united states": "New York",
    "america": "New York", "canada": "Toronto", "japan": "Tokyo", "china": "Beijing",
    "india": "New Delhi", "morocco": "Marrakesh", "egypt": "Cairo", "brazil": "Sao Paulo",
    "australia": "Sydney", "mexico": "Mexico City", "thailand": "Bangkok", "iceland": "Reykjavik",
}

WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
PARTS_OF_DAY = {"morning": 9, "noon": 12, "midday": 12, "lunch": 12, "afternoon": 15,
                "evening": 19, "tonight": 21, "night": 22}


# ----------------------------------------------------------------- parsing
def resolve_day(day: Optional[str], today: date) -> Optional[date]:
    """'today', 'tomorrow', 'friday', 'next friday', 'in 3 days', '2026-09-30' -> date."""
    d = (day or "today").strip().lower()
    if d in ("", "today", "now", "right now", "currently", "tonight", "this evening",
             "this morning", "this afternoon", "later", "later today"):
        return today
    if d in ("tomorrow", "tomorrow morning", "tomorrow evening", "tomorrow night", "tomorrow afternoon"):
        return today + timedelta(days=1)
    if d in ("day after tomorrow", "the day after tomorrow", "overmorrow"):
        return today + timedelta(days=2)
    m = re.fullmatch(r"(?:in\s+)?\+?(\d{1,2})\s*(?:days?)?(?:\s+from now)?", d)
    if m:
        return today + timedelta(days=int(m.group(1)))
    if d in ("this weekend", "weekend", "the weekend"):
        return today + timedelta(days=(5 - today.weekday()) % 7)
    m = re.fullmatch(r"(next\s+|this\s+|on\s+)?(" + "|".join(WEEKDAYS) + r")(?:\s+\w+)?", d)
    if m:
        target = WEEKDAYS.index(m.group(2))
        delta = (target - today.weekday()) % 7
        if m.group(1) and m.group(1).strip() == "next" and delta == 0:
            delta = 7
        return today + timedelta(days=delta)
    try:
        return date.fromisoformat(d)
    except ValueError:
        return None


def resolve_hour(hour: Optional[str], day_text: Optional[str] = None) -> Optional[int]:
    """'18:00', '18', '6pm', '6 pm', '18h', 'evening' -> 18 (24h clock). None means 'no specific time'."""
    return resolve_hour_ex(hour, day_text)[0]


def resolve_hour_ex(hour: Optional[str], day_text: Optional[str] = None) -> tuple[Optional[int], bool]:
    """Like resolve_hour, plus a flag saying the am/pm half was ASSUMED."""
    for raw in ((hour or ""), (day_text or "")):
        h = raw.strip().lower()
        if not h:
            continue
        m = re.fullmatch(r"(?:at\s+|around\s+)?(\d{1,2})(?::(\d{2}))?\s*(am|pm|a\.m\.|p\.m\.|h|u|uur|o'?clock)?", h)
        if m:
            val, suffix, minutes = int(m.group(1)), (m.group(3) or "").replace(".", ""), m.group(2)
            assumed = False
            if suffix == "pm" and val < 12:
                val += 12
            elif suffix == "am" and val == 12:
                val = 0
            elif suffix in ("", "oclock", "o'clock") and 1 <= val <= 6:
                val, assumed = val + 12, True        # "at 6", "6:00", even a model-written "06:00": evening
            if int(minutes or 0) >= 30 and val < 23:
                val += 1                              # 17:45 -> the 18:00 slot
            return (val, assumed) if 0 <= val <= 23 else (None, False)
        for word, val in PARTS_OF_DAY.items():
            if re.search(rf"\b{word}\b", h):
                return val, False
    return None, False


# ----------------------------------------------------------------- network
def geocode(client: httpx.Client, place: str) -> dict:
    wanted = place.strip()
    capital = COUNTRY_CAPITALS.get(wanted.lower())
    query = capital or wanted
    res = client.get(GEO_URL, params={"name": query, "count": 5, "language": "en"}).json().get("results") or []
    if not res:
        raise LookupError(f"I couldn't find a place called {wanted}")
    populated = [r for r in res if str(r.get("feature_code", "")).startswith("PPL")]
    g = (populated or res)[0]
    return {"name": g["name"], "country": g.get("country", ""), "country_code": g.get("country_code", ""),
            "lat": g["latitude"], "lon": g["longitude"],
            "note": f"using {g['name']} for {wanted}" if capital else ""}


def pick_model(geo: dict, setting: str = "auto") -> str:
    if setting and setting != "auto":
        return setting
    return "knmi_seamless" if geo.get("country_code", "").upper() in KNMI_COUNTRIES else "best_match"


SOURCES = {"knmi_seamless": "Open-Meteo, KNMI HARMONIE", "best_match": "Open-Meteo, best match"}


def fetch_forecast(client: httpx.Client, lat: float, lon: float, model: str = "best_match") -> dict:
    return client.get(FORECAST_URL, params={
        "latitude": lat, "longitude": lon, "timezone": "auto", "forecast_days": MAX_DAYS, "models": model,
        "current": "temperature_2m,apparent_temperature,precipitation,weather_code,wind_speed_10m",
        "hourly": "temperature_2m,apparent_temperature,precipitation_probability,precipitation,"
                  "weather_code,wind_speed_10m",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,"
                 "precipitation_sum,wind_speed_10m_max",
    }).json()


def _r(v):
    """Round, but keep missing values missing (models leave gaps, e.g. no rain probability)."""
    return None if v is None else round(v)


def _clean(d: dict) -> dict:
    return {k: v for k, v in d.items() if v is not None}


def _at(series: dict, key: str, i: int):
    vals = series.get(key)
    return vals[i] if vals and i < len(vals) else None


# ----------------------------------------------------------------- report
def _label(target: date, today: date, hour: Optional[int]) -> str:
    if target == today:
        base = "Today"
    elif target == today + timedelta(days=1):
        base = "Tomorrow"
    else:
        base = target.strftime("%A %d %B").replace(" 0", " ")
    return f"{base} {hour:02d}:00" if hour is not None else base


def build_report(geo: dict, fc: dict, day: Optional[str], hour: Optional[str],
                 now_utc: Optional[datetime] = None, source: str = "") -> dict:
    offset = timedelta(seconds=int(fc.get("utc_offset_seconds", 0)))
    now_utc = now_utc or datetime.now(timezone.utc)
    local_now = (now_utc + offset).replace(tzinfo=None)
    today = local_now.date()

    target = resolve_day(day, today)
    if target is None:
        return {"error": f"I didn't understand the day '{day}'"}
    if (target - today).days < 0:
        return {"error": "I can only look forward, not at past weather"}
    if (target - today).days >= MAX_DAYS:
        return {"error": f"forecasts only reach {MAX_DAYS} days ahead"}
    hr, assumed = resolve_hour_ex(hour, day)
    base = {"city": geo["name"], "country": geo["country"], "date": target.isoformat()}
    if source:
        base["source"] = source
    if geo.get("note"):
        base["note"] = geo["note"]

    if target == today and hr is None:                     # right now
        cur, d0 = fc["current"], fc["daily"]
        if cur.get("temperature_2m") is None:
            return {"error": "the weather service returned no current data"}
        return _clean({**base, "when": "Now", "temp_c": _r(cur["temperature_2m"]),
                       "feels_like_c": _r(cur.get("apparent_temperature")),
                       "conditions": CODES.get(cur.get("weather_code"), "unknown"),
                       "wind_kmh": _r(cur.get("wind_speed_10m")),
                       "high_c": _r(_at(d0, "temperature_2m_max", 0)), "low_c": _r(_at(d0, "temperature_2m_min", 0)),
                       "rain_chance_pct": _at(d0, "precipitation_probability_max", 0),
                       "rain_mm_today": _at(d0, "precipitation_sum", 0)})

    if hr is not None:                                     # a specific hour
        stamp = f"{target.isoformat()}T{hr:02d}:00"
        h = fc["hourly"]
        if stamp not in h["time"]:
            return {"error": f"no hourly forecast for {stamp}"}
        i = h["time"].index(stamp)
        if _at(h, "temperature_2m", i) is None:
            return {"error": f"the weather service has no value for {stamp}"}
        out = {**base, "when": _label(target, today, hr), "temp_c": _r(_at(h, "temperature_2m", i)),
               "feels_like_c": _r(_at(h, "apparent_temperature", i)),
               "conditions": CODES.get(_at(h, "weather_code", i), "unknown"),
               "rain_chance_pct": _at(h, "precipitation_probability", i),
               "rain_mm": _at(h, "precipitation", i), "wind_kmh": _r(_at(h, "wind_speed_10m", i))}
        if assumed:
            out["assumed"] = f"took the time as {hr:02d}:00; say it so the user can correct it"
        return _clean(out)

    d = fc["daily"]                                        # a whole day
    i = d["time"].index(target.isoformat())
    if _at(d, "temperature_2m_max", i) is None:
        return {"error": f"the weather service has no forecast for {target.isoformat()}"}
    return _clean({**base, "when": _label(target, today, None),
                   "conditions": CODES.get(_at(d, "weather_code", i), "unknown"),
                   "high_c": _r(_at(d, "temperature_2m_max", i)), "low_c": _r(_at(d, "temperature_2m_min", i)),
                   "rain_chance_pct": _at(d, "precipitation_probability_max", i),
                   "rain_mm": _at(d, "precipitation_sum", i), "wind_kmh": _r(_at(d, "wind_speed_10m_max", i))})


def get_weather_report(city: str, day: Optional[str] = None, hour: Optional[str] = None,
                       client: Optional[httpx.Client] = None, now_utc: Optional[datetime] = None) -> dict:
    own = client is None
    client = client or httpx.Client(timeout=15)
    try:
        geo = geocode(client, city)
        try:
            from core.settings import get_settings
            setting = get_settings().get("weather", {}).get("model", "auto")
        except Exception:
            setting = "auto"
        model = pick_model(geo, setting)
        return build_report(geo, fetch_forecast(client, geo["lat"], geo["lon"], model), day, hour,
                            now_utc, source=SOURCES.get(model, f"Open-Meteo, {model}"))
    except LookupError as e:
        return {"error": str(e)}
    finally:
        if own:
            client.close()
