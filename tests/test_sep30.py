"""Sep 30 log: the lights crash, 'Alarm set for 00:00 tomorrow' (nothing was set), misheard alarms, and a planned
action announced in every open window."""

import asyncio
from datetime import datetime

import pytest

from core import device
from core.orchestrator_hybrid import _CLAIM, ToolBelt, fast_path


@pytest.mark.parametrize("text,args", [
    ("Can I turn the lights on?", {"on": True}),
    ("turn the kitchen lights off", {"on": False, "which": "kitchen"}),
    ("turn off the bedroom lights", {"on": False, "which": "bedroom"}),
    ("lights on", {"on": True}),
])
def test_lights_power_fast_path_never_crashes(text, args):
    b = ToolBelt(None, None)
    b.by_name.setdefault("lights_power", {})
    assert fast_path(text, b) == ("lights_power", args)


def test_an_invented_alarm_is_caught():
    assert _CLAIM.search("Alarm set for 00:00 tomorrow, sir.")
    assert _CLAIM.search("Timer started for 10 minutes, sir.")


@pytest.mark.parametrize("text,time", [
    ("Sit in Alarm for 6 hours from now", "6 hours from now"),
    ("No sets and alarm for 6 o'clock in the morning", "6 o'clock in the morning"),
    ("set an alarm for 7:30 am", "7:30 am"),
    ("wake me up at 7", "7"),
])
def test_alarms_despite_whisper(text, time):
    assert fast_path(text, ToolBelt(None, None)) == ("phone_alarm", {"time": time})


def test_not_every_alarm_sentence_sets_one():
    assert fast_path("cancel my alarm", ToolBelt(None, None)) != ("phone_alarm", {"time": ""})
    assert fast_path("the fire alarm is loud", ToolBelt(None, None)) is None or \
        fast_path("the fire alarm is loud", ToolBelt(None, None))[0] != "phone_alarm"


@pytest.mark.parametrize("text,hm", [
    ("6 hours from now", (6, 5)), ("in 2 hours", (2, 5)), ("in an hour", (1, 5)),
    ("6 o'clock in the morning", (6, 0)), ("7 in the evening", (19, 0)), ("8:15 at night", (20, 15)),
])
def test_alarm_time(text, hm):
    assert device.alarm_time(text, now=datetime(2026, 9, 30, 0, 5)) == hm


def test_a_planned_action_reports_in_the_window_that_asked():
    import core.later as later
    told = []

    async def execute(tool, args):
        return {"result": "{}", "say": "Lights off, sir."}

    async def announce(text, session=""):
        told.append((text, session))

    async def go():
        lt = later.Later()
        lt.start(asyncio.get_running_loop(), execute, announce)
        lt.add("lights_power", {"on": False}, 0.05, "turn off the lights", session="phone-123")
        await asyncio.sleep(0.3)
    asyncio.run(go())
    assert told == [("As planned: Lights off, sir.", "phone-123")]
