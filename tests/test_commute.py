"""Nov 23: NS trains Breda to Nijmegen, delays, and when to leave (from your calendar and where you are).
NS is never reached: a fake answers in the shape of the Reisinformatie API (trips v3)."""

from datetime import datetime, timedelta

import pytest

from core import commute
from core.orchestrator_hybrid import ToolBelt, fast_path

T0 = datetime(2026, 11, 24, 7, 22)


def iso(dt):
    return dt.astimezone().strftime("%Y-%m-%dT%H:%M:%S%z")       # NS style ("+0100"), in this PC's time zone


def trip(dep, arr, late=0, track="3", status="NORMAL", cancelled=False, transfers=1, crowd="MEDIUM",
         new_track=None):
    return {"plannedDurationInMinutes": int((arr - dep).total_seconds() // 60), "transfers": transfers,
            "status": status, "crowdForecast": crowd,
            "legs": [{"name": "IC 3600", "direction": "Zwolle", "cancelled": cancelled,
                      "product": {"displayName": "NS Intercity", "type": "TRAIN", "number": "3600"},
                      "origin": {"name": "Breda", "plannedDateTime": iso(dep),
                                 "actualDateTime": iso(dep + timedelta(minutes=late)),
                                 "plannedTrack": track, "actualTrack": new_track or track},
                      "destination": {"name": "'s-Hertogenbosch", "plannedDateTime": iso(dep + timedelta(minutes=30))}},
                     {"name": "SPR 4400", "cancelled": False, "product": {"displayName": "NS Sprinter"},
                      "origin": {"name": "'s-Hertogenbosch", "plannedDateTime": iso(dep + timedelta(minutes=40))},
                      "destination": {"name": "Nijmegen", "plannedDateTime": iso(arr),
                                      "actualDateTime": iso(arr + timedelta(minutes=late))}}]}


class FakeNS:
    def __init__(self, trips):
        self.trips, self.calls = trips, []

    def __call__(self, url, params):
        self.calls.append((url, dict(params)))
        return {"trips": self.trips}


@pytest.fixture
def ns(monkeypatch):
    monkeypatch.setattr(commute, "_key", lambda: "test-key")
    monkeypatch.setattr(commute, "NOW", lambda: T0 - timedelta(minutes=10))
    fake = FakeNS([trip(T0, T0 + timedelta(minutes=112), late=4),
                   trip(T0 + timedelta(minutes=30), T0 + timedelta(minutes=142))])
    monkeypatch.setattr(commute, "FETCH", fake)
    return fake


def test_ns_times_with_an_offset_become_local_clock_times():
    from datetime import timezone
    want = datetime(2026, 11, 24, 6, 22, tzinfo=timezone.utc).astimezone().replace(tzinfo=None)
    assert commute.ns_time("2026-11-24T07:22:00+0100") == want                # 07:22 on a PC in Amsterdam
    assert commute.ns_time("") is None and commute.ns_time("nonsense") is None


def test_next_trains_say_platform_delay_arrival_and_changes(ns):
    out = commute.tool_train_times()
    url, params = ns.calls[0]
    assert url == "https://gateway.apiportal.ns.nl/reisinformatie-api/api/v3/trips"
    assert params["fromStation"] == "BD" and params["toStation"] == "NM" and "searchForArrival" not in params
    say = out["say"]
    assert "07:22 from Breda, platform 3, 4 minutes late" in say
    assert "arrives in Nijmegen at 09:18" in say and "one change" in say
    assert "After that: 07:52, on time." in say


def test_a_cancelled_train_and_a_changed_platform_are_said_first(monkeypatch):
    monkeypatch.setattr(commute, "_key", lambda: "k")
    monkeypatch.setattr(commute, "NOW", lambda: T0 - timedelta(minutes=10))
    monkeypatch.setattr(commute, "FETCH", FakeNS([
        trip(T0, T0 + timedelta(minutes=112), status="CANCELLED", cancelled=True),
        trip(T0 + timedelta(minutes=30), T0 + timedelta(minutes=142), track="3", new_track="5")]))
    say = commute.tool_train_times()["say"]
    assert say.startswith("The 07:22 is cancelled, sir.")
    assert "07:52 from Breda, platform 5 (changed from 3)" in say


def test_trains_home_go_the_other_way(ns):
    args = commute.fill_trains({}, "when's the next train home?")
    commute.tool_train_times(**args)
    assert ns.calls[0][1]["fromStation"] == "NM" and ns.calls[0][1]["toStation"] == "BD"


def test_named_stations_and_a_time(ns):
    commute.tool_train_times(from_station="Tilburg", to_station="Utrecht Centraal", when="tomorrow at 8")
    p = ns.calls[0][1]
    assert p["fromStation"] == "TB" and p["toStation"] == "UT" and p["dateTime"].startswith(
        (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%dT08:00"))


def test_no_key_says_how_to_add_one_and_never_calls_ns(monkeypatch):
    calls = []
    monkeypatch.setattr(commute, "FETCH", lambda url, params: calls.append(url) or {})
    out = commute.tool_train_times()
    assert "NS_API_KEY" in out["say"] and "secrets.env" in out["say"] and calls == []


def test_ns_errors_are_honest(monkeypatch):
    monkeypatch.setattr(commute, "_key", lambda: "k")

    def boom(url, params):
        raise commute.NSError("NS answered 401")
    monkeypatch.setattr(commute, "FETCH", boom)
    assert commute.tool_train_times()["say"] == "I couldn't reach NS, sir: NS answered 401."


# ---------------------------------------------------------------- when to leave
class FakeGoogle:
    def __init__(self, events):
        self.events = events

    def list_events(self, start, end, max_results=100):
        return [e for e in self.events if e["start"] and start <= e["end"] and e["start"] <= end]


def lecture(start, title="Neural Networks", location="Radboud University, Huygensgebouw", minutes=105, **kw):
    return {"id": title, "title": title, "start": start, "end": start + timedelta(minutes=minutes), "all_day": False,
            "location": location, "routine": False, **kw}


def test_when_to_leave_for_the_first_lecture(monkeypatch):
    monkeypatch.setattr(commute, "_key", lambda: "k")
    start = datetime(2026, 11, 24, 10, 45)
    fake = FakeNS([trip(datetime(2026, 11, 24, 7, 52), datetime(2026, 11, 24, 9, 44)),
                   trip(datetime(2026, 11, 24, 8, 22), datetime(2026, 11, 24, 10, 14)),
                   trip(datetime(2026, 11, 24, 8, 52), datetime(2026, 11, 24, 10, 44))])     # too late for 10:35
    monkeypatch.setattr(commute, "FETCH", fake)
    g = FakeGoogle([lecture(datetime(2026, 11, 24, 8, 0), "Creatine", location="", minutes=5),
                    lecture(start), lecture(datetime(2026, 11, 24, 13, 30), "Lab")])
    out = commute.plan(g, "tomorrow", now=datetime(2026, 11, 23, 21, 0))
    p = fake.calls[0][1]
    # at the station 30 minutes early: 20 to get to campus (commute.campus_min) + 10 to spare
    assert p["searchForArrival"] == "true" and p["dateTime"].startswith("2026-11-24T10:15")
    assert out["say"] == ("For Neural Networks at 10:45 on Tuesday, leave home at 08:07 for the 08:22 from Breda, "
                          "platform 3. It arrives in Nijmegen at 10:14, sir.")


def test_no_uni_event_is_said_plainly(monkeypatch):
    monkeypatch.setattr(commute, "_key", lambda: "k")
    monkeypatch.setattr(commute, "FETCH", FakeNS([]))
    g = FakeGoogle([lecture(datetime(2026, 11, 24, 11, 0), "Dentist", location="Breda")])
    assert commute.plan(g, "tomorrow", now=datetime(2026, 11, 23, 21, 0))["say"] == \
        "I don't see anything in Nijmegen on your calendar on Tuesday, sir."


def test_too_late_says_the_next_option(monkeypatch):
    monkeypatch.setattr(commute, "_key", lambda: "k")
    now = datetime(2026, 11, 24, 8, 30)
    fake = FakeNS([trip(datetime(2026, 11, 24, 8, 22), datetime(2026, 11, 24, 10, 14)),     # already gone
                   trip(datetime(2026, 11, 24, 8, 58), datetime(2026, 11, 24, 10, 50))])
    monkeypatch.setattr(commute, "FETCH", fake)
    g = FakeGoogle([lecture(datetime(2026, 11, 24, 10, 45))])
    say = commute.plan(g, "today", now=now)["say"]
    assert say == ("You can't make it in time for Neural Networks at 10:45, sir. The first train you can still "
                   "catch, the 08:58 from Breda, arrives in Nijmegen at 10:50: leave home at 08:43.")


def test_cutting_it_close_is_said(monkeypatch):
    monkeypatch.setattr(commute, "_key", lambda: "k")
    fake = FakeNS([trip(datetime(2026, 11, 24, 8, 30), datetime(2026, 11, 24, 10, 20))])
    monkeypatch.setattr(commute, "FETCH", fake)
    g = FakeGoogle([lecture(datetime(2026, 11, 24, 10, 45))])
    say = commute.plan(g, "today", now=datetime(2026, 11, 24, 8, 0))["say"]
    assert say == ("For Neural Networks at 10:45 today, leave home at 08:15 for the 08:30 from Breda, platform 3. "
                   "It arrives in Nijmegen at 10:20, which leaves 25 minutes to get there: tight, sir.")


def test_leave_alert_ten_minutes_before(monkeypatch):
    monkeypatch.setattr(commute, "_key", lambda: "k")
    monkeypatch.setattr(commute, "FETCH", FakeNS([trip(datetime(2026, 11, 24, 8, 22), datetime(2026, 11, 24, 10, 5),
                                                       late=3)]))
    events = [lecture(datetime(2026, 11, 24, 10, 45))]
    assert commute.leave_alerts(events, datetime(2026, 11, 24, 7, 30)) == []          # too early
    lines = commute.leave_alerts(events, datetime(2026, 11, 24, 7, 58))
    assert lines == ["Sir, leave in 9 minutes for the 08:22 to Nijmegen, platform 3. It's running 3 minutes late. "
                     "That gets you to Neural Networks at 10:45."]
    assert commute.leave_alerts(events, datetime(2026, 11, 24, 8, 0)) == []           # said once


def test_no_leave_alert_when_you_are_not_home(monkeypatch):
    from core import places
    monkeypatch.setattr(commute, "_key", lambda: "k")
    monkeypatch.setattr(commute, "FETCH", FakeNS([trip(datetime(2026, 11, 24, 8, 22), datetime(2026, 11, 24, 10, 14))]))
    p = places.get_places()
    p.update(51.589, 4.776, 20)
    p.remember("home")
    p.update(51.84, 5.87, 20)                                  # in Nijmegen already
    assert commute.leave_alerts([lecture(datetime(2026, 11, 24, 10, 45))], datetime(2026, 11, 24, 7, 58)) == []


def test_the_oracle_says_the_leave_alert(monkeypatch):
    import asyncio
    from core import oracle as oracle_mod
    monkeypatch.setattr(commute, "_key", lambda: "k")
    monkeypatch.setattr(commute, "FETCH", FakeNS([trip(datetime(2026, 11, 24, 8, 22), datetime(2026, 11, 24, 10, 14))]))
    said = []

    async def deliver(text, widget=None):
        said.append(text)
        return True
    g = FakeGoogle([lecture(datetime(2026, 11, 24, 10, 45))])
    o = oracle_mod.Oracle(store=oracle_mod.get_oracle().store, deliver=deliver, google_getter=lambda: g,
                          announce_email=False)            # 07:58 is in the default quiet hours: still said
    asyncio.run(o.tick(datetime(2026, 11, 24, 7, 58)))
    assert any(s.startswith("Sir, leave in 9 minutes for the 08:22") for s in said)


@pytest.mark.parametrize("text,tool", [
    ("when's the next train to Nijmegen?", "train_times"), ("next train home", "train_times"),
    ("is my train delayed?", "train_times"), ("trains to Utrecht", "train_times"),
    ("when should I leave for uni tomorrow?", "commute_plan"), ("when do I need to leave?", "commute_plan"),
    ("what time do I have to leave for Radboud", "commute_plan")])
def test_commute_by_voice(text, tool):
    got = fast_path(text, ToolBelt())
    assert got and got[0] == tool
