from datetime import date, datetime, timedelta, timezone

import httpx
import pytest

from core.weather import build_report, geocode, get_weather_report, resolve_day, resolve_hour

THU = date(2026, 9, 24)          # a Thursday


@pytest.mark.parametrize("text,expected", [
    (None, THU), ("", THU), ("today", THU), ("now", THU), ("tonight", THU),
    ("tomorrow", THU + timedelta(1)), ("tomorrow evening", THU + timedelta(1)),
    ("day after tomorrow", THU + timedelta(2)), ("in 3 days", THU + timedelta(3)), ("+2", THU + timedelta(2)),
    ("friday", THU + timedelta(1)), ("on friday", THU + timedelta(1)), ("thursday", THU),
    ("next thursday", THU + timedelta(7)), ("monday", THU + timedelta(4)),
    ("this weekend", THU + timedelta(2)), ("2026-09-30", date(2026, 9, 30)),
])
def test_resolve_day(text, expected):
    assert resolve_day(text, THU) == expected


def test_resolve_day_rejects_nonsense():
    assert resolve_day("whenever the moon is full", THU) is None


@pytest.mark.parametrize("hour,day,expected", [
    ("18:00", None, 18), ("18", None, 18), ("6pm", None, 18), ("6 pm", None, 18), ("12am", None, 0),
    ("6", None, 18), ("at 6", None, 18), ("06:00", None, 18), ("6 am", None, 6), ("7", None, 7),
    ("6 a.m.", None, 6), ("3 o'clock", None, 15),
    ("17:45", None, 18), ("9h", None, 9), ("evening", None, 19), ("", "tomorrow morning", 9),
    (None, "tonight", 21), (None, "tomorrow", None), ("25:00", None, None),
])
def test_resolve_hour(hour, day, expected):
    assert resolve_hour(hour, day) == expected


# ---------------------------------------------------------------- fake Open-Meteo
def forecast(start: date, offset_s: int = 7200, rain_prob=True):
    days = [(start + timedelta(i)).isoformat() for i in range(16)]
    hours = [f"{d}T{h:02d}:00" for d in days for h in range(24)]
    n = len(hours)
    return {
        "utc_offset_seconds": offset_s,
        "current": {"temperature_2m": 17.4, "apparent_temperature": 15.2, "precipitation": 0,
                    "weather_code": 3, "wind_speed_10m": 9.4},
        "daily": {"time": days, "weather_code": [61] * 16, "temperature_2m_max": [19.6] * 16,
                  "temperature_2m_min": [11.2] * 16, "precipitation_probability_max": [70] * 16,
                  "precipitation_sum": [2.1] * 16,
                  "wind_speed_10m_max": [22.0] * 16},
        "hourly": {"time": hours, "temperature_2m": [float(i % 24) for i in range(n)],
                   "apparent_temperature": [float(i % 24) - 2 for i in range(n)],
                   "precipitation_probability": ([40] * n) if rain_prob else [None] * n,
                   "precipitation": [0.4] * n, "weather_code": [80] * n,
                   "wind_speed_10m": [12.0] * n},
    }


GEO = {"name": "Tilburg", "country": "Netherlands", "lat": 51.56, "lon": 5.09, "note": ""}
NOW = datetime(2026, 9, 24, 12, 0, tzinfo=timezone.utc)


def test_now_report_includes_feels_like():
    r = build_report(GEO, forecast(THU), None, None, NOW)
    assert r["when"] == "Now" and r["temp_c"] == 17 and r["feels_like_c"] == 15
    assert r["conditions"] == "overcast" and r["high_c"] == 20 and r["rain_chance_pct"] == 70


def test_tomorrow_at_18_uses_hourly_slot():
    r = build_report(GEO, forecast(THU), "tomorrow", "18:00", NOW)
    assert r["when"] == "Tomorrow 18:00" and r["temp_c"] == 18 and r["conditions"] == "rain showers"


def test_friday_uses_daily_summary():
    r = build_report(GEO, forecast(THU), "friday", None, NOW)
    assert r["when"] == "Tomorrow" and r["high_c"] == 20 and r["low_c"] == 11 and "temp_c" not in r


def test_too_far_ahead_is_refused_honestly():
    assert "15 days" in build_report(GEO, forecast(THU), "in 20 days", None, NOW)["error"]


def test_today_is_the_citys_today():
    # 23:30 UTC is already the 25th in Tokyo (+9h), so "tomorrow" there is the 26th
    late = datetime(2026, 9, 24, 23, 30, tzinfo=timezone.utc)
    r = build_report(GEO, forecast(date(2026, 9, 25), offset_s=9 * 3600), "tomorrow", None, late)
    assert r["when"] == "Tomorrow" and r["date"] == "2026-09-26"


def mock_client(geo_results):
    def handler(req: httpx.Request):
        if "geocoding" in req.url.host:
            return httpx.Response(200, json={"results": geo_results})
        return httpx.Response(200, json=forecast(THU))
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_country_resolves_to_capital_and_says_so():
    c = mock_client([{"name": "Lisbon", "country": "Portugal", "latitude": 38.7, "longitude": -9.1,
                      "feature_code": "PPLC"}])
    g = geocode(c, "Portugal")
    assert g["name"] == "Lisbon" and g["note"] == "using Lisbon for Portugal"


def test_geocode_prefers_populated_places():
    c = mock_client([{"name": "Breda Region", "latitude": 1, "longitude": 1, "feature_code": "ADM2"},
                     {"name": "Breda", "country": "Netherlands", "latitude": 51.6, "longitude": 4.8,
                      "feature_code": "PPL"}])
    assert geocode(c, "Breda")["name"] == "Breda"


def test_full_report_round_trip_and_unknown_place():
    ok = get_weather_report("Tilburg", "tomorrow", "6pm", client=mock_client(
        [{"name": "Tilburg", "country": "Netherlands", "latitude": 51.56, "longitude": 5.09, "feature_code": "PPL"}]),
        now_utc=NOW)
    assert ok["city"] == "Tilburg" and ok["when"] == "Tomorrow 18:00"
    missing = get_weather_report("Atlantis", client=mock_client([]))
    assert "couldn't find" in missing["error"]


def test_ambiguous_hour_is_assumed_evening_and_flagged():
    r = build_report(GEO, forecast(THU), "tomorrow", "6", NOW)
    assert r["when"] == "Tomorrow 18:00" and "18:00" in r["assumed"]
    assert "assumed" not in build_report(GEO, forecast(THU), "tomorrow", "6 am", NOW)


def test_missing_rain_probability_falls_back_to_millimetres():
    r = build_report(GEO, forecast(THU, rain_prob=False), "tomorrow", "18:00", NOW)
    assert "rain_chance_pct" not in r and r["rain_mm"] == 0.4


def test_dutch_cities_use_knmi_everywhere_else_best_match():
    from core.weather import pick_model
    assert pick_model({"country_code": "NL"}) == "knmi_seamless"
    assert pick_model({"country_code": "be"}) == "knmi_seamless"
    assert pick_model({"country_code": "PT"}) == "best_match"


def test_report_names_its_source_and_model_is_requested():
    seen = {}

    def handler(req):
        if "geocoding" in req.url.host:
            return httpx.Response(200, json={"results": [{"name": "Tilburg", "country": "Netherlands",
                                  "country_code": "NL", "latitude": 51.5, "longitude": 5.1, "feature_code": "PPL"}]})
        seen["models"] = req.url.params.get("models")
        return httpx.Response(200, json=forecast(THU))
    r = get_weather_report("Tilburg", client=httpx.Client(transport=httpx.MockTransport(handler)), now_utc=NOW)
    assert seen["models"] == "knmi_seamless" and r["source"] == "Open-Meteo, KNMI HARMONIE"


def test_null_values_become_an_honest_error():
    fc = forecast(THU)
    fc["hourly"]["temperature_2m"] = [None] * len(fc["hourly"]["temperature_2m"])
    assert "no value" in build_report(GEO, fc, "tomorrow", "18:00", NOW)["error"]
