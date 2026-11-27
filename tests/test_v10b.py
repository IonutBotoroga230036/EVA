"""v1.0: the pieces around the one brain: real calendar changes with their undo (fake Google), skill sandbox
limits, the journal and JARVIS endpoints, the window's brain settings, the page reader. No real accounts."""

import json
from datetime import datetime, timedelta

import httpx
import pytest

from core import one, tempo
from core.one import journal, made
from tests.fakes_google import FakeGoogle, ev


class MovingGoogle(FakeGoogle):
    def __init__(self, events):
        super().__init__(events)
        self.moves = []

    def move_event(self, eid, start, end):
        self.moves.append((eid, start, end))
        for e in self.events:
            if e["id"] == eid:
                e["start"], e["end"] = start, end


def _day(h, m=0):
    d = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
    return d.replace(hour=h, minute=m)


def test_shifting_a_window_keeps_before_after_and_an_undo():
    g = MovingGoogle([ev("a", "Standup", _day(9), _day(9, 15)), ev("b", "Review", _day(9, 30), _day(10)),
                      ev("c", "Lunch", _day(12), _day(13))])
    out = tempo.shift_events(g, 15, "tomorrow", from_time="9", until_time="9:45")
    assert out["moved"] == 2
    assert out["changes"][0] == {"what": "Standup", "before": "09:00", "after": "09:15",
                                 "day": _day(9).date().isoformat()}
    spec = out["undo"]
    assert spec["tool"] == "calendar_restore" and len(spec["args"]["events"]) == 2
    tempo.restore(g, **spec["args"])
    assert [e["start"] for e in g.events] == [_day(9), _day(9, 30), _day(12)]


def test_adding_and_deleting_events_can_be_undone():
    g = MovingGoogle([ev("a", "Lunch", _day(12), _day(13))])
    out = tempo.add_event(g, "Gym", "tomorrow", "18:00")
    assert out["undo"]["args"] == {"remove": ["new"]}
    out = tempo.delete_event(g, "Lunch", "tomorrow")
    assert out["undo"]["args"]["recreate"][0]["title"] == "Lunch"
    tempo.restore(g, **out["undo"]["args"])
    assert g.inserted[-1][0] == "Lunch"


def test_the_calendar_skill_passes_the_undo_through(monkeypatch):
    import skills.calendar.tools as cal
    g = MovingGoogle([ev("a", "Standup", _day(9), _day(9, 15))])
    monkeypatch.setattr(cal, "get_google", lambda: g)
    out = cal.calendar_shift(minutes=15, day="tomorrow", from_time="9", until_time="9:45")
    assert out["undo"]["tool"] == "calendar_restore" and out["changes"][0]["what"] == "Standup"
    assert "undo" not in json.loads(out["result"])
    back = cal.calendar_restore(**out["undo"]["args"])
    assert "Standup back at 09:00" in back["say"]


def test_a_skill_that_loops_forever_is_stopped(monkeypatch):
    monkeypatch.setattr(made, "TIME_LIMIT_S", 2)
    out = made.make("forever", "never ends", "def run(eva):\n    while True:\n        pass\n", None, {},
                    lambda t, a: {}, lambda t: False, lambda t: True)
    assert not out["ok"] and "longer than 2 s" in out["problems"][0]
    assert made.manifest("forever")["status"] == "draft"
    assert "skill_forever" not in [t["function"]["name"] for t in made.schemas()]


def test_a_skill_cannot_call_too_many_tools(monkeypatch):
    monkeypatch.setattr(made, "MAX_CALLS", 5)
    code = "def run(eva):\n    for i in range(50):\n        eva.call('get_datetime')\n    return 1\n"
    out = made.make("chatty", "calls a lot", code, None, {}, lambda t, a: {"result": "{}"}, lambda t: False,
                    lambda t: True)
    assert not out["ok"] and "more than 5 times" in out["problems"][0]


def test_a_skill_reads_for_real_in_its_dry_run():
    code = 'def run(eva):\n    d = eva.call("get_datetime")\n    return d.get("weekday")\n'
    out = made.make("weekday", "the weekday", code, None, {}, lambda t, a: {"result": json.dumps({"weekday": "Friday"})},
                    lambda t: False, lambda t: True)
    assert out["ok"] and out["value"] == "Friday" and out["risk"] == "free"


def test_journal_and_undo_endpoints(tmp_path):
    from fastapi.testclient import TestClient
    from interfaces.web import server_stream as srv
    rec = journal.Turn("move it")
    rec.step("change_setting", {"key": "budget.daily_limit_euros", "value": 3}, "tell", "a setting", "ran",
             {"result": json.dumps({"now": 3}), "undo": {"tool": "change_setting",
                                                         "args": {"key": "budget.daily_limit_euros", "value": 1.5}}})
    rec.finish("Done.")
    with TestClient(srv.app) as tc:
        turns = tc.get("/api/journal").json()["turns"]
        assert turns[0]["said"] == "move it" and turns[0]["steps"][0]["undo"]
        sid = turns[0]["steps"][0]["id"]
        assert tc.post(f"/api/journal/{sid}/undo").status_code == 403
        r = tc.post(f"/api/journal/{sid}/undo", headers={"X-EVA": "1"})
        assert r.status_code == 200 and r.json()["ok"]
        assert tc.post("/api/journal/../x/undo", headers={"X-EVA": "1"}).status_code == 404
        assert tc.get("/api/journal").json()["turns"][0]["steps"][0]["undone"] is True


def test_jarvis_endpoints(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from core import jarvis_test
    from interfaces.web import server_stream as srv
    monkeypatch.setattr(jarvis_test, "RUNS", tmp_path / "runs")
    started = []
    monkeypatch.setattr(jarvis_test, "run_in_background", lambda b=None, nightly=False: started.append(b) or True)
    with TestClient(srv.app) as tc:
        d = tc.get("/api/jarvis").json()
        assert d["runs"] == [] and d["scenarios"] >= 20
        assert tc.post("/api/jarvis/run", json={"backend": "local"}).status_code == 403
        assert tc.post("/api/jarvis/run", json={"backend": "x"}, headers={"X-EVA": "1"}).status_code == 400
        assert tc.post("/api/jarvis/run", json={"backend": "local"}, headers={"X-EVA": "1"}).json()["started"]
    assert started == ["local"]


def test_tasks_show_their_decision_trail(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from core.agent import tasks as tasks_mod
    from interfaces.web import server_stream as srv
    store = tasks_mod.Store(tmp_path / "tasks")
    monkeypatch.setattr(tasks_mod, "get_store", lambda: store)
    t = store.new("design a skull", 1.0, "skull")
    t.plan = [{"step": "find references", "done": True}]
    t.consents = [{"ts": 1.0, "kind": "install_program", "what": "install OpenSCAD", "decision": "yes"}]
    t.note("asked you: how detailed?")
    store.save(t)
    with TestClient(srv.app) as tc:
        got = tc.get("/api/tasks").json()["tasks"][0]
    assert got["plan"] == [{"step": "find references", "done": True}]
    assert got["consents"][0]["what"] == "install OpenSCAD"
    assert {"task created", "asked you: how detailed?"} <= {x["what"] for x in got["trail"]}


def test_the_window_picks_her_brain():
    from core import ui_settings
    assert ui_settings.value("@one.backend") == "local_first"
    ui_settings.change("@one.backend", "cloud")
    assert one.backend_choice() == "cloud"
    ui_settings.change("risk.ask_above_eur", 5)
    from core.one import risk
    assert risk.cfg()["ask_above_eur"] == 5
    with pytest.raises(ValueError):
        ui_settings.change("@one.backend", "mars")


def test_local_only_by_voice_moves_the_brain_local(monkeypatch):
    from core.tools_native import tool_set_brain_mode
    monkeypatch.setattr(one, "mode", lambda: "one")
    tool_set_brain_mode(mode="local")
    assert one.backend_choice() == "local"
    tool_set_brain_mode(mode="auto")
    assert one.backend_choice() == "local_first"


def test_web_read_reads_only_public_pages_and_finds_lines(monkeypatch):
    from core import tools_native as tn
    from core.agent import hands
    monkeypatch.setattr(hands, "_public_url", lambda u: "192.168" not in u)
    page = "<html><body><p>Markets</p><p>NVDA 177.82 +1.2%</p><p>AAPL 230.10</p><p>Weather</p></body></html>"
    monkeypatch.setattr(tn.httpx if hasattr(tn, "httpx") else httpx, "get",
                        lambda url, **k: httpx.Response(200, text=page, headers={"content-type": "text/html"},
                                                        request=httpx.Request("GET", url)))
    out = json.loads(tn.tool_web_read("https://quotes.example.org/nvda", find="NVDA")["result"])
    assert "NVDA 177.82" in out["text"]
    assert "error" in json.loads(tn.tool_web_read("http://192.168.1.1/admin")["result"])


def test_with_one_brain_a_task_question_is_not_primed(monkeypatch):
    from interfaces.web import server_stream as srv

    class O:
        pending = awaiting = None
    o = O()
    monkeypatch.setattr(one, "mode", lambda: "one")
    srv.prime_task(o, {"kind": "question", "task": "t1"})
    assert o.awaiting is None
    monkeypatch.setattr(one, "mode", lambda: "classic")
    srv.prime_task(o, {"kind": "question", "task": "t1"})
    assert o.awaiting["tool"] == "task_answer"
