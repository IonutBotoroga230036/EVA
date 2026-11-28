"""v1.0: the automatic JARVIS test. A rule-following stand-in brain plays the ideal assistant, so these tests check
the run itself: the fake world, the checks, the scoring, and that nothing real is touched."""

import asyncio
import json
import re

import pytest

from core import jarvis_test as jt
from core.one.backends import ScriptedBackend, Step

pytestmark = pytest.mark.one_brain


def _said(transcript):
    """His sentence (without the situation) and the tool results since then."""
    users = [i for i, m in enumerate(transcript) if m["role"] == "user" and not m["text"].startswith("(")]
    i = users[-1]
    text = transcript[i]["text"].split("</what he told you before>")[-1].split("</situation>")[-1].strip()
    tools = [m for m in transcript[i + 1:] if m["role"] == "tool"]
    situation = transcript[i]["text"]
    return text, tools, situation


def _c(_tool, **a):
    return Step(calls=[{"id": f"x_{_tool}", "name": _tool, "input": a}])


def ideal(transcript, tools):
    text, results, situation = _said(transcript)
    t = text.lower()
    done = {m["name"] for m in results}
    data = " ".join(m["text"] for m in results)
    if "NOT DONE YET" in data:
        who = re.search(r"Muaad|Mom", text)
        return Step(text=f"Shall I {'message ' + who.group(0) if who else 'go ahead with that'}?")
    if results:
        if "web_search" in done:
            n = re.search(r"\d+\.\d+", data)
            return Step(text=f"NVIDIA is at {n.group(0)} dollars, sir." if n else "The search found nothing, sir.")
        if "get_weather" in done:
            return Step(text="Light rain, 80% chance, so yes, take an umbrella.")
        if "calendar_agenda" in done:
            return Step(text="Standup at 9, Review at 9:30 and Lunch at 12.")
        if "what_i_did" in done:
            return Step(text="I moved Standup and Review fifteen minutes later.")
        if "calculate" in done or "get_datetime" in done:
            n = re.search(r"\d+", data)
            return Step(text=f"That's {n.group(0) if n else 'a few'}, sir.")
        if "my_files" in done:
            return Step(text="It's in the face recognition camera folder: eva_face.py.")
        if "memoir_add" in done:
            return Step(text="Added that to your bike chapter, sir.")
        if "memoir_start" in done:
            return Step(text="I'm listening, sir.")
        if "install_package" in done:
            return Step(text="OpenCV is in my own environment now.")
        if "place_save" in done and "place_action" not in done:
            return _c("place_action", place="shop", event="arrive", tool="shopping_list")
        if "place_action" in done:
            return Step(text="Saved the Albert Heijn as your shop; I'll read your list when you get there.")
        if "my_console" in done:
            return Step(text="It failed because the cv2 module wasn't installed.")
        if "code_find" in done:
            return Step(text="In voice/listen.py: max_pause_secs, the longest pause before I stop listening.")
        if "where_am_i" in done:
            return Step(text="Your phone says you're at home.")
        if "task_resume" in done:
            return Step(text="Back on the face recognition camera.")
        if "calendar_shift" in done:
            return Step(text="Done: Standup and Review are 15 minutes later.")
        return Step(text="Done, sir.")
    if "you had asked whether" in situation.lower() or "he said yes" in situation.lower():
        if "he said no" in situation.lower() or "never mind" in t:
            return Step(text="Okay, I'll leave it.")
        return Step(text="Sent, sir.")
    if "embarrassed" in t:
        return _c("memoir_add", text=text)
    if "install opencv" in t:
        return _c("install_package", packages=["opencv-python"])
    if "albert heijn" in t:
        return _c("place_save", name="shop", find="Albert Heijn")
    if "check your console" in t:
        return _c("my_console", level="warning")
    if "in your code" in t:
        return _c("code_find", query="max_pause")
    if "locate my phone" in t:
        return _c("where_am_i")
    if "resume the task" in t:
        return _c("task_resume", which="face recognition camera")
    if "where did you put" in t:
        return _c("my_files", query="face recognition")
    if "eva_face.py setup" in t:
        return _c("run_my_script", path="eva_face.py", args=["setup"])
    if "wire up" in t:
        return _c("adopt_skill", name="who_is_here", purpose="see who is at the PC", script="who_is_here.py")
    if "tell you a story" in t:
        return _c("memoir_start", topic="my first bike")
    if "how are you" in t:
        return Step(text="Running smoothly, sir. You?")
    if "stop the task" in t:
        return _c("task_cancel")
    if "daily limit" in t:
        return _c("change_setting", key="budget.daily_limit_euros", value=5)
    if "tell muaad" in t:
        return _c("message_reply", contact="Muaad", text="I'm running 10 minutes late")
    if "send mom" in t:
        return _c("message_reply", contact="Mom", text="I'll call you tonight")
    if "delete lunch" in t:
        return _c("calendar_delete", title="Lunch")
    if "nvidia" in t:
        return _c("web_search", query="NVIDIA stock price today")
    if "what's on my calendar" in t:
        return _c("calendar_agenda", day="today")
    if "remind me" in t:
        return _c("set_reminder", text="call mom", when="6pm")
    if "3d skull" in t:
        return _c("task_start", goal="design a 3D printable skull", title="3d skull")
    if "budget of 20" in t:
        return _c("task_start", goal="research the best 3D printers", budget_euros=20)
    if "umbrella" in t:
        return _c("get_weather", day="tomorrow")
    if "undo" in t:
        return _c("undo", step="last")
    if "what did you change" in t:
        return _c("what_i_did", day="today")
    if "between 9 and 9:45" in t:
        return _c("calendar_shift", minutes=15, from_time="9", until_time="9:45")
    if "trading 212" in t:
        return _c("open_website", site="Trading 212")
    if "15 percent of 240" in t:
        return Step(text="36, sir.")
    if "shopping list" in t:
        return Step(calls=[{"id": "a", "name": "shopping_add", "input": {"items": "milk and eggs"}},
                           {"id": "b", "name": "lights_power", "input": {"on": False}}])
    if "flight" in t:
        return Step(text="I can't book flights, sir, but I can find you options.")
    if "christmas" in t:
        return _c("get_datetime")
    return Step(text="Hmm?")


def test_every_scenario_passes_with_an_ideal_brain(monkeypatch):
    from core import memoir
    monkeypatch.setattr(memoir, "_write_json", jt.fake_writer)
    def make():
        b = ScriptedBackend(ideal, "ideal")
        b.kind = "cloud"
        return [b]
    d = asyncio.run(jt.run_all(None, make, "ideal"))
    failed = {r["id"]: r["problems"] for r in d["results"] if not r["passed"]}
    assert not failed, failed
    assert d["total"] == len(jt.scenarios()) >= 33 and d["score"] == 100


def test_a_lazy_brain_fails_the_right_checks():
    def make():
        b = ScriptedBackend(lambda tr, tools: Step(text="Just to confirm, sir: I'll do that. NVIDIA is at 125.00."),
                            "lazy")
        b.kind = "cloud"
        return [b]
    d = asyncio.run(jt.run_all(["stock_price", "calendar_window", "send_asks"], make, "lazy"))
    probs = {r["id"]: " | ".join(r["problems"]) for r in d["results"]}
    assert d["passed"] == 0
    assert "didn't use web_search|web_read" in probs["stock_price"] and "just to confirm" in probs["stock_price"]
    assert "Standup is at 09:00, expected 09:15" in probs["calendar_window"]
    assert "should have asked first" in probs["send_asks"]


def test_the_world_is_fake():
    w = jt.FakeWorld()
    out = w.execute("calendar_shift", {"minutes": 15, "from_time": "9", "until_time": "9:45"})
    assert json.loads(out["result"])["moved"] == 2 and w.events[2]["start"] == "12:00"
    assert "isn't available in the test world" in w.execute("email_send", {})["result"]


def test_runs_are_listed_and_due_once_a_night(tmp_path, monkeypatch):
    from datetime import datetime
    monkeypatch.setattr(jt, "RUNS", tmp_path / "runs")
    (tmp_path / "runs").mkdir()
    (tmp_path / "runs" / "2026-11-27_030000.json").write_text(json.dumps({"date": "2026-11-27", "nightly": True,
                                                                           "score": 80}))
    assert jt.listing()[0]["score"] == 80
    night = datetime(2026, 11, 28, 3, 5)
    assert jt.due(night, last_user_ts=0)
    assert not jt.due(datetime(2026, 11, 27, 3, 5), last_user_ts=0)       # already ran that night
    assert not jt.due(datetime(2026, 11, 28, 14, 0), last_user_ts=0)
