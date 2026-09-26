"""Routines panel API and the shopping list (v0.2.5 milestone 5). Temp vault, temp stores, temp moods."""

import json
from datetime import date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

import interfaces.web.server_stream as srv
from core import shopping
from core.oracle import get_oracle


@pytest.fixture
def tc(monkeypatch):
    monkeypatch.setattr(srv, "get_tts", lambda: None)
    with TestClient(srv.app) as client:
        yield client


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M")


# ------------------------------------------------------------ shopping list core + skill
def test_shopping_list_is_an_obsidian_note_with_checkboxes(tmp_path):
    added, already = shopping.add(shopping.split_items("milk, eggs and some bread"))
    assert added == ["milk", "eggs", "bread"] and already == []
    assert shopping.add(["Milk"]) == ([], ["milk"])
    assert shopping.set_done("egg") == "eggs"                          # near match
    text = shopping.path().read_text(encoding="utf-8")
    assert text.startswith("# Shopping list") and "- [ ] milk" in text and "- [x] eggs" in text
    assert shopping.path().parent == tmp_path / "vault"                # EVA_VAULT from conftest, never yours
    assert shopping.clear(done_only=True) == 1
    assert [r["text"] for r in shopping.items()] == ["milk", "bread"]
    assert shopping.spoken(shopping.items()) == "On your shopping list, sir: milk and bread."


def test_ticked_item_added_again_comes_back():
    shopping.add(["milk"])
    shopping.set_done("milk")
    assert shopping.add(["milk"]) == (["milk"], [])
    assert shopping.items() == [{"text": "milk", "done": False}]


def test_shopping_skill_lines_are_exact_and_use_your_words():
    from skills.shopping import tools as t
    out = t.shopping_add(items="whatever the model said")
    assert "Added whatever the model said" in out["say"]
    assert t._fill_add({"items": "wrong"}, "add oat milk and bananas to my shopping list") == \
        {"items": "oat milk and bananas"}
    out = t.shopping_add(items="oat milk and bananas")
    assert out["say"] == "Added oat milk and bananas to your shopping list, sir." and out["exact"]
    assert t.shopping_remove(item="bananas")["say"] == "Took bananas off your shopping list, sir."
    assert "isn't on your shopping list" in t.shopping_remove(item="caviar")["say"]
    assert t.CONFIRM == {"shopping_clear_all": "empty your whole shopping list"}


def test_add_to_shopping_list_takes_the_fast_path():
    from core.orchestrator_hybrid import ToolBelt, fast_path
    belt = ToolBelt(None, None)
    belt.by_name.setdefault("shopping_add", {})
    assert fast_path("add milk and eggs to my shopping list", belt) == ("shopping_add", {"items": "milk and eggs"})


# ------------------------------------------------------------ reminders
def test_reminders_create_edit_delete(tc):
    due = datetime.now().replace(second=0, microsecond=0) + timedelta(days=1)
    r = tc.post("/api/routines/reminders", json={"text": "call mom.", "due": iso(due)}).json()
    assert r["text"] == "call mom" and r["due"] == iso(due)
    later = due + timedelta(hours=2)
    r2 = tc.patch(f"/api/routines/reminders/{r['id']}", json={"text": "call mom back", "due": iso(later)}).json()
    assert r2 == {"id": r["id"], "text": "call mom back", "due": iso(later)}
    assert [x["text"] for x in tc.get("/api/routines").json()["reminders"]] == ["call mom back"]
    assert tc.delete(f"/api/routines/reminders/{r['id']}").status_code == 200
    assert tc.get("/api/routines").json()["reminders"] == []
    assert tc.delete(f"/api/routines/reminders/{r['id']}").status_code == 404


def test_bad_input_is_refused(tc):
    assert tc.post("/api/routines/reminders", json={"text": "x", "due": "tomorrow-ish"}).status_code == 400
    assert tc.post("/api/routines/routines", json={"text": "x", "days": [9], "time": "08:00"}).status_code == 400
    assert tc.post("/api/routines/routines", json={"text": "x", "days": [1], "time": "25:00"}).status_code == 400
    assert tc.post("/api/routines/routines", json={"days": [1], "time": "08:00", "action": "hack"}).status_code == 400
    assert tc.post("/api/routines/moods", json={"name": "odd", "color": "notacolour"}).status_code == 400


# ------------------------------------------------------------ routines
def test_routines_create_pause_resume_delete(tc):
    r = tc.post("/api/routines/routines", json={"text": "plan the week", "days": [6], "time": "19:00"}).json()
    assert r["describe"] == "a reminder to plan the week every Sunday at 19:00" and r["paused"] is False
    assert tc.patch(f"/api/routines/routines/{r['id']}", json={"paused": True}).json()["paused"] is True
    o = get_oracle()
    sunday = datetime(2026, 9, 27, 19, 5)
    assert o.routines.due(sunday) == []                                 # paused: never fires
    tc.patch(f"/api/routines/routines/{r['id']}", json={"paused": False, "time": "20:30", "days": [5, 6]})
    edited = tc.get("/api/routines").json()["routines"][0]
    assert edited["time"] == "20:30" and edited["days"] == [5, 6]
    assert [x["id"] for x in o.routines.due(datetime(2026, 9, 27, 20, 35))] == [r["id"]]
    assert tc.delete(f"/api/routines/routines/{r['id']}").status_code == 200
    assert tc.get("/api/routines").json()["routines"] == []


def test_week_view_lists_reminders_and_routines_by_day(tc):
    today = date.today()
    tomorrow_3pm = datetime.combine(today + timedelta(days=1), datetime.min.time()).replace(hour=15)
    tc.post("/api/routines/reminders", json={"text": "coffee with Tom", "due": iso(tomorrow_3pm)})
    tc.post("/api/routines/routines", json={"days": list(range(7)), "time": "08:00", "action": "briefing"})
    paused = tc.post("/api/routines/routines", json={"text": "stretch", "days": list(range(7)), "time": "09:00"}).json()
    tc.patch(f"/api/routines/routines/{paused['id']}", json={"paused": True})
    week = tc.get("/api/routines").json()["week"]
    assert len(week) == 7 and week[0]["label"] == "Today" and week[1]["label"] == "Tomorrow"
    assert [(i["time"], i["kind"]) for i in week[1]["items"]] == [("08:00", "routine"), ("15:00", "reminder")]
    assert all(i["text"] != "a reminder to stretch" for d in week for i in d["items"])   # paused is hidden


# ------------------------------------------------------------ moods
def test_moods_create_edit_and_reset(tc):
    moods = {m["name"]: m for m in tc.get("/api/routines").json()["moods"]}
    assert moods["relax"]["builtin"] and not moods["relax"]["edited"]
    assert tc.post("/api/routines/moods", json={"name": "Study", "color": "cyan", "brightness": 80,
                                                "playlist": "Deep Focus"}).status_code == 200
    tc.post("/api/routines/moods", json={"name": "relax", "color": "red", "brightness": 20})
    moods = {m["name"]: m for m in tc.get("/api/routines").json()["moods"]}
    assert moods["study"]["playlist"] == "Deep Focus" and not moods["study"]["builtin"]
    assert moods["relax"]["edited"] and moods["relax"]["brightness"] == 20
    assert tc.delete("/api/routines/moods/relax").status_code == 200        # back to the default
    assert {m["name"]: m for m in tc.get("/api/routines").json()["moods"]}["relax"]["brightness"] == 35
    assert tc.delete("/api/routines/moods/home").status_code == 404          # built-in, never edited


# ------------------------------------------------------------ shopping via the panel
def test_shopping_via_the_panel(tc):
    r = tc.post("/api/routines/shopping", json={"items": "milk and eggs"}).json()
    assert r["added"] == ["milk", "eggs"]
    tc.post("/api/routines/shopping/tick", json={"item": "milk", "done": True})
    assert tc.post("/api/routines/shopping/clear", json={"done_only": True}).json()["items"] == \
        [{"text": "eggs", "done": False}]
    assert tc.post("/api/routines/shopping/remove", json={"item": "eggs"}).json()["items"] == []
    assert tc.post("/api/routines/shopping/remove", json={"item": "eggs"}).status_code == 404


def test_panel_writes_are_audited(tc, tmp_path):
    due = datetime.now() + timedelta(days=1)
    tc.post("/api/routines/reminders", json={"text": "audit me", "due": iso(due)})
    logs = "".join(p.read_text(encoding="utf-8") for p in (tmp_path / "logs").glob("*"))
    assert "ui_reminder_added" in logs
