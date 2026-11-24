"""Ionut's log of 24-25 Nov, 23:57-00:10, and his NS and calendar screenshots:
- 00:00 "next train": she offered the 23:54 that had already left
- 00:03 "leave for uni tomorrow": it was past midnight (he meant Wednesday), she planned Thursday, and aimed at his
  own "Train To Nijmegen 8:45" event as if it were a lecture (the lecture was SOW-BKI141 at 10:30 on the Radboud
  calendar, with a bus to campus)
- "kill my music": Spotify said 'Restriction violated' (already paused), the media key then STARTED it
- "test mode on" at 23:57 ended by itself at midnight
- FORGE said "10 to 20 cents"; the build cost 40
- the phone's wake clip cut "when's the next..." and "record this..."; "Have you ever recorded this meeting?" was
  'Hey Eva, record this meeting'
"""

import time
from datetime import datetime, timedelta

import pytest

from core import commute, testmode
from core.budget import get_budget

from tests.test_commute import FakeGoogle, FakeNS, lecture, trip

WED = datetime(2026, 11, 25)
THU = datetime(2026, 11, 26)


def at(day, h, m=0):
    return day.replace(hour=h, minute=m)


def ev(title, start, minutes=60, calendar="Ionut-Alberto Botoroga", location=""):
    return {"id": f"{title}{start}", "title": title, "start": start, "end": start + timedelta(minutes=minutes),
            "all_day": False, "routine": False, "location": location, "calendar": calendar}


RADBOUD = "Radboud University - Personal"


# ---------------------------------------------------------------- trains already gone
def test_a_train_that_already_left_is_never_offered(monkeypatch):
    monkeypatch.setattr(commute, "_key", lambda: "k")
    now = datetime.now().replace(second=0, microsecond=0)
    fake = FakeNS([trip(now - timedelta(minutes=7), now + timedelta(minutes=70), late=1, track="5"),
                   trip(now + timedelta(minutes=20), now + timedelta(minutes=90))])
    monkeypatch.setattr(commute, "FETCH", fake)
    say = commute.tool_train_times()["say"]
    assert hm(now - timedelta(minutes=7)) not in say
    assert say.startswith(f"The next train to Nijmegen leaves at {hm(now + timedelta(minutes=20))}")


def test_after_the_last_train_she_says_so(monkeypatch):
    monkeypatch.setattr(commute, "_key", lambda: "k")
    now = datetime.now().replace(second=0, microsecond=0)
    first = now + timedelta(hours=5, minutes=30)
    monkeypatch.setattr(commute, "FETCH", FakeNS([trip(now - timedelta(minutes=7), now + timedelta(minutes=70)),
                                                  trip(first, first + timedelta(minutes=64), transfers=0)]))
    say = commute.tool_train_times()["say"]
    assert say.startswith(f"There are no more trains tonight, sir. The first one to Nijmegen leaves at {hm(first)}")


def hm(dt):
    return dt.strftime("%H:%M")


# ---------------------------------------------------------------- what counts as going to uni
def test_his_own_travel_events_are_not_lectures():
    for title in ("Train To Nijmegen", "Bus To RU", "Bike To Breda Centraal", "Go Back Home", "Train to Amsterdam"):
        assert commute.is_travel(ev(title, at(THU, 9)))
        assert not commute.is_uni(ev(title, at(THU, 9)))


def test_courses_are_recognised_by_code_or_by_the_radboud_calendar():
    assert commute.is_uni(ev("SOW-BKI141 PER2 - Research Design & Scientific Method", at(THU, 10, 30),
                             location="CC 4"))
    assert commute.is_uni(ev("Exam review", at(THU, 10), calendar=RADBOUD))
    assert not commute.is_uni(ev("Lunch Break", at(THU, 12)))
    assert commute.spoken_title("SOW-BKI141 PER2 - Research Design & Scientific Method") == \
        "Research Design & Scientific Method"
    assert commute.spoken_title("SOW-HPSS160 SEM1 - Honours") == "Honours"


def thursday():
    return [ev("Bike To Breda Centraal", at(THU, 8, 30), 15), ev("Train To Nijmegen", at(THU, 8, 45), 75),
            ev("Bus To RU", at(THU, 10), 15),
            ev("SOW-BKI141 PER2 - Research Design & Scientific Method", at(THU, 10, 30), 105, RADBOUD, "CC 4"),
            ev("Lunch Break", at(THU, 12, 15))]


def test_his_planned_train_is_the_plan(monkeypatch):
    monkeypatch.setattr(commute, "_key", lambda: "k")
    fake = FakeNS([trip(at(THU, 8, 19), at(THU, 9, 32)), trip(at(THU, 8, 49), at(THU, 10, 2), late=2)])
    monkeypatch.setattr(commute, "FETCH", fake)
    out = commute.plan(FakeGoogle(thursday()), "thursday", now=at(WED, 21))
    p = fake.calls[0][1]
    assert "searchForArrival" not in p and p["dateTime"].startswith("2026-11-26T08:40")
    assert out["say"] == ("Your calendar has the train to Nijmegen at 08:45 on Thursday: that's the 08:49 from Breda, "
                          "platform 3, 2 minutes late right now. Leave home at 08:34. It arrives in Nijmegen at 10:04, "
                          "in time for Research Design & Scientific Method at 10:30, sir.")


def test_without_a_planned_train_the_first_course_decides(monkeypatch):
    monkeypatch.setattr(commute, "_key", lambda: "k")
    fake = FakeNS([trip(at(THU, 7, 49), at(THU, 9, 2)), trip(at(THU, 8, 19), at(THU, 9, 32)),
                   trip(at(THU, 8, 49), at(THU, 10, 2))])
    monkeypatch.setattr(commute, "FETCH", fake)
    events = [e for e in thursday() if not commute.is_travel(e)]
    say = commute.plan(FakeGoogle(events), "thursday", now=at(WED, 21))["say"]
    assert fake.calls[0][1]["dateTime"].startswith("2026-11-26T10:00")        # 20 to campus + 10 to spare
    assert say.startswith("For Research Design & Scientific Method at 10:30 on Thursday, leave home at 08:04 "
                          "for the 08:19 from Breda")                         # the 08:49 arrives 10:02: too late


def test_going_elsewhere_that_day_is_said(monkeypatch):
    monkeypatch.setattr(commute, "_key", lambda: "k")
    monkeypatch.setattr(commute, "FETCH", FakeNS([trip(at(WED, 7, 19), at(WED, 8, 22))]))
    events = [ev("SOW-BKI323 SEM1 - Brain-Computer Interfacing", at(WED, 8, 45), 105, RADBOUD),
              ev("Train To Rotterdam", at(WED, 9, 15), 30)]
    say = commute.plan(FakeGoogle(events), "today", now=at(WED, 0, 3))["say"]
    assert say.startswith("Your calendar has you going to Rotterdam today, not Nijmegen, sir. If you go to "
                          "Brain-Computer Interfacing at 08:45")


def test_just_after_midnight_tomorrow_means_the_day_you_wake_up_to(monkeypatch):
    monkeypatch.setattr(commute, "_key", lambda: "k")
    fake = FakeNS([trip(at(WED, 7, 19), at(WED, 8, 10))])
    monkeypatch.setattr(commute, "FETCH", fake)
    events = [ev("SOW-BKI323 SEM1 - Brain-Computer Interfacing", at(WED, 8, 45), 105, RADBOUD)] + thursday()
    out = commute.plan(FakeGoogle(events), "tomorrow", now=at(WED, 0, 3))
    assert "Brain-Computer Interfacing at 08:45 on Wednesday" in out["say"]
    out = commute.plan(FakeGoogle(events), "tomorrow", now=at(WED, 9, 0))
    assert "on Thursday" in out["say"]


def test_the_leave_alert_follows_his_planned_train(monkeypatch):
    monkeypatch.setattr(commute, "_key", lambda: "k")
    monkeypatch.setattr(commute, "FETCH", FakeNS([trip(at(THU, 8, 49), at(THU, 10, 2), late=2)]))
    assert commute.leave_alerts(thursday(), at(THU, 8, 20)) == []
    lines = commute.leave_alerts(thursday(), at(THU, 8, 26))
    assert lines == ["Sir, leave in 8 minutes for the 08:49 to Nijmegen, platform 3. It's running 2 minutes late. "
                     "That gets you to Research Design & Scientific Method at 10:30."]


# ---------------------------------------------------------------- Spotify: already paused
def test_pausing_paused_music_never_presses_play(monkeypatch):
    import core.spotify as sp
    import core.tools_native as tn
    pressed = []

    class Fake:
        def control(self, what):
            raise RuntimeError('Spotify error 403: {"error": {"status": 403, "message": "Player command failed: '
                               'Restriction violated", "reason": "UNKNOWN"}}')
    monkeypatch.setattr(sp, "connected", lambda: True)
    monkeypatch.setattr(sp, "get_spotify", lambda: Fake())
    monkeypatch.setattr(tn, "PYAUTOGUI_OK", True)
    monkeypatch.setattr(tn, "pyautogui", type("P", (), {"press": staticmethod(pressed.append)}), raising=False)
    out = tn.tool_media_control(action="pause")
    assert pressed == [] and out["say"] == "It's already paused, sir."
    out = tn.tool_media_control(action="play")
    assert pressed == [] and out["say"] == "It's already playing, sir."


# ---------------------------------------------------------------- test mode across midnight
def test_test_mode_lasts_twelve_hours_not_until_midnight(monkeypatch):
    testmode.set_mode(True)
    st = __import__("json").loads(testmode.PATH.read_text(encoding="utf-8"))
    st["since"] -= 11 * 3600                          # started 11 hours ago, yesterday
    st["day"] = "2026-11-24"
    testmode.PATH.write_text(__import__("json").dumps(st), encoding="utf-8")
    assert testmode.is_on()
    st["since"] -= 2 * 3600
    testmode.PATH.write_text(__import__("json").dumps(st), encoding="utf-8")
    assert not testmode.is_on()
    assert testmode.tool_test_mode(on=False)["say"].startswith("Test mode had already ended by itself after 12 hours")


def test_test_mode_counts_spending_since_it_started():
    b = get_budget()
    b.record_usage("claude-haiku-5-5", 400000, 0, purpose="before")
    testmode.set_mode(True)
    b.record_usage("claude-sonnet-5", 10000, 1000, purpose="forge")
    eur, calls = testmode.spent()
    assert calls == 1 and 0.04 < eur < 0.06


# ---------------------------------------------------------------- FORGE: an honest price
def test_forge_quotes_what_builds_really_cost(monkeypatch):
    from core import forge_jobs
    monkeypatch.setattr(forge_jobs, "get_jobs", lambda: type("J", (), {"jobs": []})())
    assert forge_jobs.price_words() == "roughly 20 to 50 cents"
    jobs = [forge_jobs.Job(id=str(i), request="x", status="done", cost_eur=c) for i, c in enumerate((0.40, 0.30, 0.0))]
    monkeypatch.setattr(forge_jobs, "get_jobs", lambda: type("J", (), {"jobs": jobs})())
    assert forge_jobs.price_words() == "roughly 35 cents, what your last builds cost"


# ---------------------------------------------------------------- the phone's wake clip
def test_a_cut_off_command_waits_for_the_rest():
    from interfaces.web import server_stream as ss
    ss._WAKE_PARTIAL.clear()
    assert ss.wake_command("Hey, Eva, when's the next...") == ""
    assert ss.merge_partial("train to Nijmegen") == "when's the next train to Nijmegen"
    assert ss.merge_partial("train to Nijmegen") == "train to Nijmegen"                  # used once
    ss.wake_command("Hey Eva, record this...")
    assert ss.merge_partial("record this meeting") == "record this meeting"                # said it all again
    assert ss.wake_command("Hey Eva, stop the music.") == "stop the music"


def test_an_old_partial_is_forgotten():
    from interfaces.web import server_stream as ss
    ss.wake_command("Hey, Eva, when's the next...")
    ss._WAKE_PARTIAL["ts"] = time.time() - 60
    assert ss.merge_partial("what time is it") == "what time is it"


@pytest.mark.parametrize("heard", ["Have you ever recorded this meeting?", "Hey you ever, record this meeting"])
def test_hey_eva_misheard_as_have_you_ever(heard):
    from interfaces.web import server_stream as ss
    assert ss.heard_wake(heard) is not None


def test_have_you_ever_later_in_a_sentence_is_not_her_name():
    from interfaces.web import server_stream as ss
    assert ss.heard_wake("So I asked him, have you ever been to Paris?") is None
