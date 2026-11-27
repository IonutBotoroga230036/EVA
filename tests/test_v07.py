"""v0.7: her own browser (inside an answer and in tasks), using his computer (as a task he said yes to), Windows
settings pages, and the settings panel in the window. A scripted Claude plays her; the browser, screen and mouse
are fakes; nothing real is opened, clicked or typed."""

import json
import types

import pytest
from PIL import Image

from core import browser, computer, mind, ui_settings
from core import settings as settings_mod
from core.agent import live
from core.agent.tasks import get_store

from tests.test_agent import T, mgr  # noqa: F401  (fixture)
from tests.test_v05_mind import eva, say, think  # noqa: F401  (fixtures)


# ---------------------------------------------------------------- fakes
class FakeBrowser:
    """Her browser without Chromium: records what she did, answers like the real executor."""
    def __init__(self, fail_on=None):
        self.done, self.fail_on = [], fail_on
        self.last_title, self.last_url = "Prusa MINI+ | Prusa3D", "https://www.prusa3d.com/product/original-prusa-mini/"

    def execute(self, name, args):
        self.done.append((name, args))
        if name == self.fail_on:
            raise browser.BrowserError("ref_9 isn't on the page any more; use read_page again")
        browser.SHOT.parent.mkdir(parents=True, exist_ok=True)
        browser.SHOT.write_bytes(b"\x89PNG fake")
        if name == "read_page":
            return [{"type": "text", "text": '- heading "Original Prusa MINI+" [ref=ref_1]\n- text "459 EUR"'}]
        return [{"type": "text", "text": f"Page: {self.last_title} ({self.last_url})"}]


@pytest.fixture
def her_browser(monkeypatch):
    b = FakeBrowser()
    monkeypatch.setattr(browser, "installed", lambda: True)
    monkeypatch.setattr(browser, "get_browser", lambda: b)
    return b


class FakeGui:
    """pyautogui without a screen."""
    class FailSafeException(Exception):
        pass

    def __init__(self, failsafe_on=None):
        self.calls, self.failsafe_on = [], failsafe_on

    def __getattr__(self, name):
        def f(*a, **k):
            if name == self.failsafe_on:
                raise FakeGui.FailSafeException("mouse in a corner")
            self.calls.append((name, a, k))
            return (10, 20) if name == "position" else None
        return f


@pytest.fixture
def screen(monkeypatch):
    """A 2732 x 1600 screen (she sees it at half size) and a fake mouse and keyboard."""
    gui = FakeGui()
    monkeypatch.setattr(computer, "_gui", lambda: gui)
    import PIL.ImageGrab as grab
    monkeypatch.setattr(grab, "grab", lambda **k: Image.new("RGB", (2732, 1600), (30, 30, 60)))
    monkeypatch.setattr(computer.time, "sleep", lambda s: None)
    return gui


# ---------------------------------------------------------------- her browser, inside an answer
def test_she_checks_a_page_in_her_browser_and_shows_it(eva, think, her_browser):
    s = think([[T("browser:navigate", url="https://www.prusa3d.com/product/original-prusa-mini/"),
                T("browser:read_page")],
               ["The Prusa MINI+ is 459 euros on Prusa's own site, sir."]])
    reply, events = say(eva, "Can you check what the Prusa Mini costs on the Prusa website?")
    assert reply == "The Prusa MINI+ is 459 euros on Prusa's own site, sir."
    assert browser.ENTRY in s.calls[0]["tools"]
    assert [d[0] for d in her_browser.done] == ["navigate", "read_page"]
    results = s.calls[1]["messages"][-1]["content"]
    assert [r["toolset_name"] for r in results] == ["browser", "browser"]       # every result echoes its toolset
    assert "459 EUR" in json.dumps(results[1]["content"])
    shown = [e["data"] for e in events if e["type"] == "widget"]
    assert shown and shown[-1]["kind"] == "media" and shown[-1]["image"].startswith("/api/browser/shot.png?v=")
    assert shown[-1]["url"] == her_browser.last_url
    assert any(e["type"] == "ack" and e["text"] == "Checking prusa3d.com, sir." for e in events)


def test_after_a_failed_browser_step_the_rest_of_the_batch_waits(eva, think, her_browser):
    her_browser.fail_on = "left_click"
    s = think([[T("browser:left_click", ref="ref_9"), T("browser:read_page")],
               [T("browser:read_page")],
               ["It's 459 euros, sir."]])
    say(eva, "Can you check what the Prusa Mini costs on the Prusa website?")
    first = s.calls[1]["messages"][-1]["content"]
    assert first[0]["is_error"] and "ref_9" in first[0]["content"]
    assert first[1]["is_error"] and first[1]["content"] == "Not executed: an earlier action in this turn failed."
    assert [d[0] for d in her_browser.done] == ["left_click", "read_page"]     # the skipped one never ran
    assert not s.calls[2]["messages"][-1]["content"][0].get("is_error")       # a new turn tries again


def test_no_browser_tools_when_she_has_no_browser(eva, think, monkeypatch):
    monkeypatch.setattr(browser, "installed", lambda: True)
    settings_mod.UI_PATH.write_text(json.dumps({"browser": {"enabled": False}}))
    settings_mod.get_settings.cache_clear()
    s = think([["Here you go, sir."]])
    say(eva, "Can you check what the Prusa Mini costs on the Prusa website?")
    assert browser.ENTRY not in s.calls[0]["tools"]
    assert "Her own browser" in mind.system_prompt()                # instructions stay stable either way


def test_the_computer_toolset_is_never_run_inside_an_answer(eva, think, her_browser):
    s = think([[T("computer:screenshot")], ["I'll do that as a task, sir."]])
    say(eva, "Can you check what the Prusa Mini costs on the Prusa website?")
    res = s.calls[1]["messages"][-1]["content"][0]
    assert res["is_error"] and res["toolset_name"] == "computer" and "use_computer" in res["content"]


def test_the_browser_refuses_payments_and_his_network(monkeypatch):
    monkeypatch.setattr(browser.socket, "getaddrinfo", lambda host, port: [(2, 1, 6, "", (
        "192.168.1.1" if host == "router.example" else "23.10.5.4", 0))])          # no real DNS in tests
    assert not browser.public("https://router.example/")                          # a name that points home
    assert browser._RISKY.search("Place order and pay")
    assert browser._RISKY.search("Bestellen en betalen")
    assert not browser.public("http://192.168.1.10/admin")
    assert not browser.public("http://localhost:8000")
    assert browser.public("https://www.ah.nl/producten")
    assert browser.ENTRY["configs"]["javascript_exec"]["enabled"] is False


# ---------------------------------------------------------------- his computer, as a task
def test_a_computer_task_sees_the_screen_and_clicks_where_she_means(mgr, screen):  # noqa: F811
    m = mgr([[T("computer:screenshot")],
             [T("computer:left_click", coordinate=[100, 50]), T("computer:type", text="bluetooth"),
              T("computer:key", text="Return")],
             [T("finish", summary="Bluetooth is on, sir.")]])
    t = m.start("turn on Bluetooth", toolsets=["computer:act"])
    t = get_store().get(t.id)
    assert t.status == "done", (t.status, t.error, t.waiting)
    first = m.script.calls[0]
    assert computer.ENTRY in first["tools"]
    assert "HIS COMPUTER" in first["system"]
    shot = m.script.calls[1]["messages"][-1]["content"][0]
    assert shot["toolset_name"] == "computer" and shot["content"][0]["type"] == "image"
    png = Image.open(__import__("io").BytesIO(__import__("base64").b64decode(shot["content"][0]["source"]["data"])))
    assert png.size == (1366, 800)                              # scaled down for her eyes
    names = [(c[0], c[1]) for c in screen.calls]
    assert ("moveTo", (200, 100)) in names                      # her 100,50 is 200,100 on the real screen
    assert ("write", ("bluetooth",)) in names and ("press", ("enter",)) in names
    assert (get_store().workspace(t.id) / "_eva_shots" / "latest.png").exists()
    st = live.state(t.id)
    assert st["screen"]["png"] == "_eva_shots/latest.png" and st["screen"]["where"] == "your screen"


def test_watch_only_looks_but_never_clicks(mgr, screen):  # noqa: F811
    m = mgr([[T("computer:screenshot"), T("computer:left_click", coordinate=[5, 5])],
             [T("finish", summary="I can see the Bluetooth page, sir.")]])
    t = m.start("look at the Bluetooth settings", toolsets=["computer:watch"])
    res = m.script.calls[1]["messages"][-1]["content"]
    assert not res[0].get("is_error") and res[1]["is_error"] and "Watch only" in res[1]["content"]
    assert not any(c[0] in ("click", "moveTo") for c in screen.calls)
    assert "Watch only" in m.script.calls[0]["system"]
    assert get_store().get(t.id).status == "done"


def test_pushing_the_mouse_into_a_corner_stops_the_task(mgr, screen):  # noqa: F811
    screen.failsafe_on = "click"
    m = mgr([[T("computer:left_click", coordinate=[10, 10]), T("computer:type", text="hello")]])
    t = m.start("click the Bluetooth switch", toolsets=["computer:act"])
    t = get_store().get(t.id)
    assert t.status == "cancelled" and "corner" in t.error
    assert m.told[-1][1].startswith("Stopped, sir. You took over the mouse")
    assert not any(c[0] == "write" for c in screen.calls)          # nothing typed after the fail-safe


def test_she_never_types_a_card_number(screen):
    c = computer.Computer()
    res = computer.result({"id": "toolu_1", "name": "type", "input": {"text": "4111 1111 1111 1111"}}, c)
    assert res["is_error"] and "card" in res["content"]
    assert not any(x[0] == "write" for x in screen.calls)


def test_use_computer_asks_first_and_starts_a_task(monkeypatch):
    from core import tools_native
    assert "use_computer" in tools_native.CONFIRM_TOOLS
    assert "mouse and keyboard to turn on Bluetooth" in computer.describe_use({"goal": "turn on Bluetooth"})
    started = []
    import core.agent.runner as runner
    monkeypatch.setattr(runner, "get_manager", lambda: types.SimpleNamespace(
        start=lambda *a, **k: started.append((a, k)) or types.SimpleNamespace(id="abcd1234", title="Bluetooth")))
    out = computer.tool_use_computer(goal="Turn on Bluetooth")
    assert started[0][1]["toolsets"] == ["computer:act"]
    assert out["say"].startswith("On it, sir: I'll use your mouse and keyboard to turn on Bluetooth.")


def test_computer_use_switched_off_refuses(monkeypatch):
    ui_settings.change("computer.mode", "off")
    out = computer.tool_use_computer(goal="turn on Bluetooth")
    assert out["say"] == "Using your computer is switched off in the settings, sir."
    ui_settings.change("computer.mode", "watch")
    assert "look at your screen" in computer.describe_use({"goal": "check the display"})


def test_open_settings_opens_the_right_page(monkeypatch):
    import os
    opened = []
    monkeypatch.setattr(os, "startfile", lambda uri: opened.append(uri), raising=False)
    assert computer.tool_open_settings("turn on bluetooth")["say"] == "Opening bluetooth settings on your PC, sir."
    computer.tool_open_settings("default apps")
    computer.tool_open_settings("something odd")
    assert opened == ["ms-settings:bluetooth", "ms-settings:defaultapps", "ms-settings:"]


# ---------------------------------------------------------------- the settings panel
def test_settings_show_groups_values_and_spend():
    snap = ui_settings.snapshot()
    titles = [g["title"] for g in snap["groups"]]
    assert titles == ["Budget", "Brain", "Mind", "Tasks", "Her browser", "Using your computer", "Focus"]
    budget = {f["key"]: f for f in snap["groups"][0]["fields"]}
    assert budget["budget.daily_limit_euros"]["kind"] == "number" and budget["@testmode"]["value"] is False
    assert "spend" in snap


def test_changing_a_setting_writes_the_window_file_and_applies_now():
    from core.budget import get_budget
    ui_settings.change("budget.daily_limit_euros", 3)
    assert json.loads(settings_mod.UI_PATH.read_text())["budget"]["daily_limit_euros"] == 3
    assert settings_mod.get_settings()["budget"]["daily_limit_euros"] == 3
    assert get_budget().daily_limit == 3.0
    ui_settings.reset("budget.daily_limit_euros")
    assert "budget" not in json.loads(settings_mod.UI_PATH.read_text())          # tidy: empty sections go
    ui_settings.change("focus.skip_words", "lunch, gym ,  ")
    assert ui_settings.value("focus.skip_words") == ["lunch", "gym"]


def test_bad_settings_are_refused():
    for key, v in [("budget.daily_limit_euros", 900), ("mind.effort", "max"), ("browser.enabled", "yes"),
                   ("claude.api_key", "sk-123")]:
        with pytest.raises(ValueError):
            ui_settings.change(key, v)
    assert not settings_mod.UI_PATH.exists()


def test_the_brain_and_test_mode_switches_are_the_spoken_ones():
    from core import testmode
    from core.brain import get_brain
    ui_settings.change("@brain.mind", "local")
    assert get_brain()._state()["features"]["mind"] == "local"
    ui_settings.change("@testmode", True)
    assert testmode.is_on()
    ui_settings.change("@testmode", False)


def test_the_settings_api_needs_the_window_header():
    from fastapi.testclient import TestClient
    from interfaces.web import server_stream as srv
    with TestClient(srv.app) as tc:
        assert tc.get("/api/settings").json()["groups"][0]["title"] == "Budget"
        assert tc.post("/api/settings", json={"key": "mind.max_steps", "value": 4}).status_code == 403
        r = tc.post("/api/settings", json={"key": "mind.max_steps", "value": 4}, headers={"X-EVA": "1"})
        assert r.status_code == 200 and r.json()["value"] == 4
        r = tc.post("/api/settings", json={"key": "mind.max_steps", "value": 99}, headers={"X-EVA": "1"})
        assert r.status_code == 400
        r = tc.post("/api/settings/reset", json={"key": "mind.max_steps"}, headers={"X-EVA": "1"})
        assert r.status_code == 200
        assert tc.get("/api/browser/shot.png").status_code == 404           # nothing seen yet


def test_the_page_has_the_settings_and_the_screen_tab():
    from pathlib import Path
    page = Path("interfaces/web/eva.html").read_text(encoding="utf-8")
    assert 'id="evaSettings"' in page and "/api/settings" in page and "'X-EVA': '1'" in page
    assert "['screen', 'Screen', !!t.screen]" in page


# ---------------------------------------------------------------- chats (v0.7)
def test_a_chat_keeps_what_was_said_and_gets_a_title(monkeypatch):
    from core import chats
    import core.claude as claude_mod
    monkeypatch.setattr("core.understand.brains.cloud_allowed", lambda: True)
    monkeypatch.setattr(claude_mod, "get_claude", lambda: types.SimpleNamespace(
        message=lambda **k: {"text": "Guitar stand print shops."}))
    c = chats.new()
    assert chats.listing() == []                                 # an empty chat never shows up
    chats.add(c["id"], "you", "Hey Eva, can you find a print shop for the guitar stand?")
    assert chats.get(c["id"])["title"] == "Find a print shop for the"   # a stand-in at once
    chats.add(c["id"], "eva", "MakerSpace Breda prints it from 5 euros, sir.")
    named = []
    chats.make_title(c["id"], lambda cid, t: named.append(t))
    assert chats.get(c["id"])["title"] == "Guitar stand print shops" and named == ["Guitar stand print shops"]
    assert chats.history(chats.get(c["id"])) == [
        {"role": "user", "content": "Hey Eva, can you find a print shop for the guitar stand?"},
        {"role": "assistant", "content": "MakerSpace Breda prints it from 5 euros, sir."}]


def test_without_the_cloud_the_first_words_stay_the_title(monkeypatch):
    from core import chats
    monkeypatch.setattr("core.understand.brains.cloud_allowed", lambda: False)
    c = chats.new()
    chats.add(c["id"], "you", "What's the weather tomorrow?")
    chats.add(c["id"], "eva", "Sunny, sir.")
    chats.make_title(c["id"])
    got = chats.get(c["id"])
    assert got["title"] == "What's the weather tomorrow" and got["titled"]


def test_search_rename_delete_and_find_in_chats():
    from core import chats
    a, b = chats.new(), chats.new()
    chats.add(a["id"], "you", "Let's design the desk display")
    chats.add(a["id"], "eva", "A transparent OLED would work, sir.")
    chats._write({**chats.get(a["id"]), "updated": chats.get(a["id"])["updated"] - 60})
    chats.add(b["id"], "you", "Play some jazz")
    assert [c["id"] for c in chats.listing()] == [b["id"], a["id"]]          # newest first
    hits = chats.search("transparent oled")
    assert [h["id"] for h in hits] == [a["id"]] and "transparent OLED" in hits[0]["match"]
    chats.rename(a["id"], "Desk display")
    assert chats.search("desk")[0]["title"] == "Desk display"
    found = json.loads(chats.tool_find_in_chats("oled")["result"])
    assert found["chats"][0]["chat"] == "Desk display"
    assert found["chats"][0]["lines"] == ["You: A transparent OLED would work, sir."]
    assert chats.delete(b["id"]) and chats.get(b["id"]) is None
    assert chats.get("../../config/secrets") is None                   # never a path outside data/chats
    with pytest.raises(ValueError):
        chats.rename("../../config/secrets", "x")


def test_a_window_writes_its_chat_and_can_switch(monkeypatch):
    from fastapi.testclient import TestClient
    from core import chats
    from interfaces.web import server_stream as srv
    monkeypatch.setattr("core.understand.brains.cloud_allowed", lambda: False)

    async def fake_stream(self, text):
        self.history += [{"role": "user", "content": text}, {"role": "assistant", "content": f"Echo: {text}"}]
        yield {"type": "final", "text": f"Echo: {text}"}
    monkeypatch.setattr(srv.HybridOrchestrator, "process_stream", fake_stream)
    old = chats.new()
    chats.add(old["id"], "you", "Earlier we picked PLA for the stand")
    chats.add(old["id"], "eva", "PLA it is, sir.")
    chats._write({**chats.get(old["id"]), "updated": 1.0})           # long ago: a new window starts fresh

    def until(ws, kind):
        while True:
            m = json.loads(ws.receive_text())
            if m["type"] == kind:
                return m
    with TestClient(srv.app) as tc, tc.websocket_connect("/ws") as ws:
        ws.send_text(json.dumps({"type": "chat_current"}))
        assert until(ws, "chat")["chat"] == {"id": None, "title": "New chat", "turns": []}
        ws.send_text(json.dumps({"type": "message", "text": "Hello Eva"}))
        meta = until(ws, "chat_meta")
        until(ws, "turn_meta")
        new_id = meta["id"]
        ws.send_text(json.dumps({"type": "chat_open", "id": old["id"]}))
        opened = until(ws, "chat")["chat"]
        assert opened["id"] == old["id"] and len(opened["turns"]) == 2
        ws.send_text(json.dumps({"type": "message", "text": "Which plastic did we pick?"}))
        until(ws, "turn_meta")
        listed = tc.get("/api/chats").json()["chats"]
        assert {c["id"] for c in listed} == {old["id"], new_id}
        assert tc.post(f"/api/chats/{new_id}/rename", json={"title": "Hello"}).status_code == 403
        assert tc.post(f"/api/chats/{new_id}/rename", json={"title": "Hello"}, headers={"X-EVA": "1"}).json()["title"] == "Hello"
        assert tc.get("/api/chats?q=plastic").json()["chats"][0]["id"] == old["id"]
    turns = chats.get(old["id"])["turns"]
    assert [t["text"] for t in turns[-2:]] == ["Which plastic did we pick?", "Echo: Which plastic did we pick?"]
    with TestClient(srv.app) as tc, tc.websocket_connect("/ws") as ws:   # a window opened now carries on
        ws.send_text(json.dumps({"type": "chat_current"}))
        assert until(ws, "chat")["chat"]["id"] == old["id"]
        assert tc.post(f"/api/chats/{old['id']}/delete", headers={"X-EVA": "1"}).json() == {"deleted": True}
