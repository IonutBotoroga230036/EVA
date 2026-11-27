"""v0.6: the mind's gate, FORGE after a cancel, task names, pictures and videos, projects, places with hours."""

import json
from datetime import datetime
from pathlib import Path

import pytest

from core import mind, nearby, projects, show
from core.agent.tasks import get_store

from tests.test_agent import T, mgr  # noqa: F401
from tests.test_v05_mind import eva, say, think  # noqa: F401


# ---------------------------------------------------------------- Nov 25 22:00 log: the mind never ran
class _Key:
    def available(self):
        return True


def test_the_mind_follows_understanding(monkeypatch):
    """His saved state: mode local, understanding on Claude. Understanding worked, the mind stayed silent."""
    import core.claude as claude_mod
    from core.brain import get_brain
    monkeypatch.setattr(claude_mod, "get_claude", lambda: _Key())
    b = get_brain()
    b.set_mode("local")
    b.set_feature("understanding", "auto")
    assert mind.why_off() == "" and mind.allowed()
    b.set_feature("mind", "local")                  # "Eva, use local for the mind"
    assert mind.why_off() == "you set the mind to local" and not mind.allowed()
    b.set_feature("mind", "auto")
    b.set_feature("understanding", "local")
    assert mind.allowed()                           # you switched the mind on yourself


def test_a_quiet_mind_says_why_once(monkeypatch):
    from loguru import logger
    seen = []
    sink = logger.add(lambda m: seen.append(str(m)), level="INFO")
    try:
        mind._why_off["said"] = None
        mind.allowed()
        mind.allowed()
    finally:
        logger.remove(sink)
    assert sum("MIND: off (" in s for s in seen) == 1


def test_forge_says_it_stopped_after_a_cancel(monkeypatch):
    """22:00: 'Your skill is ready, sir: tell bedtime story' after he cancelled it; installing then failed."""
    from core import forge_jobs
    from skills.forge import tools as ft

    class Job:
        status, request, result_name, result_status, say = "cancelled", "a bedtime story skill", "tell_story", \
            "ready", "Your skill is ready, sir: tell story."

    class Jobs:
        def running(self):
            return None

        def waiting(self):
            return []

        def latest(self):
            return Job()
    monkeypatch.setattr(forge_jobs, "get_jobs", lambda: Jobs())
    out = ft.forge_status()
    assert out["say"].startswith('I stopped the build of "a bedtime story skill", sir')
    assert json.loads(out["result"])["request"] == "a bedtime story skill"


def test_tasks_get_a_real_name(monkeypatch):
    from core.agent import tasks as tmod
    import core.claude as claude_mod

    class K:
        def available(self):
            return True

        def message(self, **kw):
            assert kw["purpose"] == "task name" and kw["max_tokens"] == 20
            return {"text": "3D printer prices."}
    monkeypatch.setattr(mind, "allowed", lambda: True)
    monkeypatch.setattr(claude_mod, "get_claude", lambda: K())
    goal = "Find prices online for a small 3D printer, sorted by price range."
    assert tmod.name_task(goal) == "3d printer prices"
    monkeypatch.setattr(mind, "allowed", lambda: False)
    assert tmod.name_task(goal) == tmod.short_title(goal)


# ---------------------------------------------------------------- pictures and videos
class FakeDDGS:
    def __init__(self, *a, **k):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def images(self, query, **kw):
        assert kw["safesearch"] == "moderate"
        return [{"title": f"{query} {i}", "image": f"https://img.example/{i}.jpg",
                 "thumbnail": f"https://tse.example/{i}.jpg", "url": f"https://site.example/{i}",
                 "source": "Bing", "width": 800, "height": 600} for i in range(1, 5)]

    def videos(self, query, **kw):
        return [{"title": "How to build a Pepper's ghost display", "content": "https://www.youtube.com/watch?v=abc",
                 "embed_url": "https://www.youtube.com/embed/abc", "images": {"medium": "https://i.ytimg.com/a.jpg"},
                 "publisher": "YouTube", "uploader": "Maker", "duration": "12:04", "published": "2026-03-01T00:00"}]


@pytest.fixture
def ddgs(monkeypatch):
    import core.tools_native as tn
    monkeypatch.setattr(tn, "DDGS", FakeDDGS)
    monkeypatch.setattr(tn, "SEARCH_OK", True)


def test_show_me_pictures(ddgs):
    out = show.tool_show_images("transparent desk display")
    w = out["widget"]
    assert w["kind"] == "gallery" and w["media"] == "images" and len(w["items"]) == 4
    assert w["items"][0]["thumbnail"] == "https://tse.example/1.jpg"
    assert "On his screen: pictures for 'transparent desk display': 1. transparent desk display 1 (Bing)" in \
        show.shown_text()


def test_the_second_one_and_videos(ddgs):
    show.tool_show_images("transparent desk display")
    out = show.tool_open_shown(2)
    assert out["widget"]["kind"] == "media" and out["widget"]["image"] == "https://img.example/2.jpg"
    assert "no number 9" in show.tool_open_shown(9)["result"]
    v = show.tool_find_videos("peppers ghost display")
    assert v["widget"]["items"][0]["embed"] == "https://www.youtube.com/embed/abc"
    assert show.tool_open_shown(1, on="pc")["say"] == "Opening it on your PC, sir."


def test_she_can_look_at_a_picture(ddgs, monkeypatch):
    show.tool_show_images("transparent desk display")
    monkeypatch.setattr(show, "fetch_image", lambda url: (b"\x89PNG fake", "image/png"))
    out = show.tool_look_at_shown(1)
    assert out["_image"]["media_type"] == "image/png"
    content = mind.tool_result_content(out, False)
    assert content[1]["type"] == "image" and content[1]["source"]["data"]


def test_pictures_only_from_public_addresses(monkeypatch):
    from core.agent import hands
    monkeypatch.setattr(hands, "_public_url", lambda u: "192.168" not in u)
    with pytest.raises(ValueError, match="not a public address"):
        show.fetch_image("http://192.168.1.10/cam.jpg")


def test_a_reflex_cant_look_so_the_mind_does(eva, think, ddgs, monkeypatch):  # noqa: F811
    show.tool_show_images("transparent desk display")
    monkeypatch.setattr(show, "fetch_image", lambda url: (b"\x89PNG", "image/png"))

    async def decide(client, skills, gathered, messages=None):
        return {"tool": "look_at_shown", "number": 2, "_trusted": True}
    monkeypatch.setattr(eva, "_decide", decide)
    s = think([["That one is a glass panel at 45 degrees over a monitor, sir."]])
    reply, _ = say(eva, "What is the second one?")
    assert reply.startswith("That one is a glass panel") and len(s.calls) == 1     # 23:33: she answered from the
    first = s.calls[0]["messages"][-1]["content"]                                   # caption; now she gets the picture
    assert first[1]["type"] == "image" and "On his screen: pictures" in first[0]["text"]
    assert "Understanding's guess for this sentence: look_at_shown" in first[0]["text"]


# ---------------------------------------------------------------- projects
def test_a_project_keeps_what_we_decide(tmp_path):
    out = projects.tool_project_start("a transparent screen for my desk", "Show E.V.A. on the desk like EDITH")
    assert out["say"].startswith("Started the transparent screen for my desk project")
    projects.tool_project_note("Go with the Pepper's ghost version first: a monitor under angled glass", "decision")
    projects.tool_project_note("How big should the glass be?", "question")
    projects.tool_project_add_part("Raspberry Pi 5", 1, 80, "https://shop.example/pi5", "runs the display", "chosen")
    projects.tool_project_add_part("acrylic sheet 3 mm", 2, 12.5)
    projects.tool_project_add_part("Raspberry Pi 5", price_eur=79.95)          # same part: updated, not added
    p = projects.get_store().active()
    assert [x["name"] for x in p.parts] == ["Raspberry Pi 5", "acrylic sheet 3 mm"] and p.total_eur() == 104.95
    w = projects.tool_project_show()["widget"]
    assert w["kind"] == "project" and w["questions"] == [{"n": 2, "text": "How big should the glass be?"}]
    assert "Decided: Go with the Pepper's ghost version" in p.brief()
    out = projects.tool_project_answer(2, "about 30 by 20 cm")
    assert "0 open questions left" in out["say"]


def test_the_mind_and_tasks_know_the_project(mgr, monkeypatch):  # noqa: F811
    projects.tool_project_start("desk screen", "a transparent screen")
    projects.tool_project_note("Pepper's ghost first", "decision")
    assert "Active project (build on it):" in mind.situation() and "Pepper's ghost first" in mind.situation()
    from core.agent import tools as atools
    m = mgr([[T("finish", summary="The 10 inch screen works best, sir.")]])
    import core.agent.runner as runner                 # v0.9: the test's manager, not a threaded global one
    monkeypatch.setattr(runner, "get_manager", lambda: m)  # (they raced: calls[0] sometimes didn't exist yet)
    out = atools.tool_task_start("research which screen to put under the glass", project="desk screen")
    t = get_store().get(json.loads(out["result"])["task"])
    assert t.project == projects.get_store().active().id
    assert "This task is part of a project" in m.script.calls[0]["system"] and "Pepper's ghost first" in \
        m.script.calls[0]["system"]
    p = projects.get_store().active()
    assert t.id in p.tasks and p.notes[-1]["kind"] == "research" and "10 inch screen" in p.notes[-1]["text"]


def test_a_project_goes_to_a_spreadsheet(tmp_path, monkeypatch):
    from core.agent import pc
    monkeypatch.setattr(pc, "_cfg", lambda: {"output_dir": str(tmp_path / "out")})
    projects.tool_project_start("desk screen")
    projects.tool_project_add_part("Raspberry Pi 5", 1, 80)
    out = json.loads(projects.tool_project_export()["result"])
    rows = (Path(out["folder"]) / "parts.csv").read_text(encoding="utf-8-sig").splitlines()
    assert rows[0].startswith("Part,Qty") and rows[1].startswith("Raspberry Pi 5,1,80") and rows[-1].startswith("Total")


def test_tasks_may_add_to_projects():
    from core.agent.hands import FREE_EVA_TOOLS
    assert {"project_note", "project_add_part", "nearby"} <= FREE_EVA_TOOLS


# ---------------------------------------------------------------- places with opening hours
def test_opening_hours():
    sat_noon = datetime(2026, 11, 28, 12, 0)          # a Saturday
    assert nearby.status("Mo-Sa 08:00-22:00; Su 10:00-20:00", sat_noon) == \
        {"open": True, "closes": "22:00", "minutes_left": 600}
    assert nearby.status("Mo-Fr 09:00-17:00; Sa,Su off", sat_noon) == {"open": False, "opens": "Monday 09:00"}
    assert nearby.status("24/7", sat_noon)["open"] is True
    late = datetime(2026, 11, 29, 1, 0)                # Sunday 01:00, after a Saturday night
    assert nearby.status("Fr-Sa 18:00-02:00", late) == {"open": True, "closes": "02:00", "minutes_left": 60}
    assert nearby.status("Mo-Fr 08:00-12:00,13:00-17:00", datetime(2026, 11, 30, 12, 30)) == \
        {"open": False, "opens": "today 13:00"}
    assert nearby.status("sunrise-sunset") is None    # unusual: the mind reads it as written


def test_the_query():
    q = nearby.query("supermarket", (51.589, 4.776), 1500)
    assert '["shop"="supermarket"](around:1500,51.589,4.776)' in q and q.startswith("[out:json]")
    q = nearby.query("Albert Hain", (51.589, 4.776), 1500)                # 23:38: how Whisper heard it
    assert 'nwr["brand"~"^Albert Heijn",i]' in q and 'nwr["name"~"^Albert Heijn",i]' in q
    assert '["name"~"Bakkerij Bart",i]' in nearby.query("Bakkerij Bart", (51.589, 4.776), 1500)


def test_nearby_answers_with_hours(monkeypatch):
    monkeypatch.setattr(nearby, "where", lambda: ((51.589, 4.776), "here"))
    monkeypatch.setattr(nearby, "search", lambda what, c, r: [
        {"lat": 51.590, "lon": 4.776, "tags": {"name": "Jumbo Ginnekenweg", "opening_hours": "Mo-Su 08:00-22:00"}},
        {"lat": 51.600, "lon": 4.790, "tags": {"name": "Albert Heijn", "opening_hours": "Mo-Sa 08:00-21:00"}},
        {"lat": 51.591, "lon": 4.777, "tags": {}}])
    out = nearby.tool_nearby("supermarket")
    data = json.loads(out["result"])
    assert [p["name"] for p in data["places"]] == ["Jumbo Ginnekenweg", "Albert Heijn"]
    assert out["say"].startswith("The nearest is Jumbo Ginnekenweg, 111 metres")
    assert out["widget"]["kind"] == "note"


def test_the_map_server_is_asked_politely(monkeypatch):
    import httpx
    sent = []

    def post(url, data=None, headers=None, timeout=None):
        sent.append((url, data, headers))
        return httpx.Response(406 if "overpass-api.de" in url else 200, json={"elements": []},
                              request=httpx.Request("POST", url))
    monkeypatch.setattr(httpx, "post", post)
    monkeypatch.setattr(nearby.time, "sleep", lambda s: None)
    nearby._cache.clear()
    assert nearby.search("cafe", (51.589, 4.776), 900) == []
    assert [u for u, _, _ in sent] == list(nearby.SERVERS)                       # the fallback answered
    assert sent[0][2]["User-Agent"].startswith("EVA-personal-assistant") and "data" in sent[0][1]


# ---------------------------------------------------------------- Nov 25 23:14 log: a task stuck on budget
def test_a_budget_pause_is_clear_and_carry_on_resumes_it(mgr):  # noqa: F811
    from core.budget import BudgetExceeded
    m = mgr([BudgetExceeded("this call could cost up to EUR 0.170"), [T("finish", summary="Done, sir.")]])
    t = m.start("research transparent oled desk displays")
    t = get_store().get(t.id)
    assert t.status == "waiting" and t.waiting["consent_kind"] == "budget"
    assert "cents of today's" in t.waiting["text"] and "EUR 0.170" not in t.waiting["text"]
    st = m.status_text(t)
    assert "paused" in st and "say 'carry on'" in st and "I asked" not in st
    m.resume(t)                                  # "you can carry on" -> task_resume used to say "it's waiting"
    assert get_store().get(t.id).status == "done"


def test_with_budget_again_it_goes_on_by_itself(mgr, monkeypatch):  # noqa: F811
    from core.agent import runner
    from core.budget import BudgetExceeded
    m = mgr([BudgetExceeded("x"), [T("finish", summary="Done, sir.")]])
    t = m.start("research transparent oled desk displays")
    monkeypatch.setattr(runner, "_budget_ok", lambda need=0.25: False)
    m.resume_after_restart()
    assert get_store().get(t.id).status == "waiting"
    monkeypatch.setattr(runner, "_budget_ok", lambda need=0.25: True)
    m.resume_after_restart()
    assert get_store().get(t.id).status == "done"
    assert any("there's budget again" in said for _, said in m.told)


@pytest.mark.parametrize("answer,kind", [
    ("Today I've spent 1.33 of your 3.00 euro daily cloud budget. Local work is free.", None),
    ("The shop is open until 10, sir.", None),
    ("Your afternoon is free, sir.", "calendar"),
    ("Friday looks open.", "calendar"),
])
def test_free_and_open_are_only_calendar_claims_with_a_day(answer, kind):
    import core.orchestrator_hybrid as orch_mod
    assert orch_mod.unchecked_fact(answer, []) == kind



# ---------------------------------------------------------------- Nov 25 23:32 log
def test_a_new_project_offers_the_first_real_work():
    out = projects.tool_project_start("transparent desk screen", "laptop size, a Raspberry Pi drives it")
    assert out["exact"] and out["say"].endswith("Shall I research how to build it and draft a first parts list?")
    nxt = out["confirm_next"]
    assert nxt["tool"] == "task_start" and nxt["args"]["project"] == json.loads(out["result"])["project"]
    assert "parts list with prices" in nxt["args"]["goal"]


def test_yes_starts_the_research(eva, monkeypatch):  # noqa: F811
    async def decide(client, skills, gathered, messages=None):
        return {"tool": "project_start", "name": "desk screen", "_trusted": True}
    monkeypatch.setattr(eva, "_decide", decide)
    reply, _ = say(eva, "Start a project for the transparent screen on my desk")
    assert reply.endswith("Shall I research how to build it and draft a first parts list?")
    assert eva.pending["tool"] == "task_start"
    started = []
    import core.tools_native as tn
    monkeypatch.setitem(tn.REGISTRY, "task_start", lambda **a: started.append(a) or
                        {"result": json.dumps({"task": "x"}), "say": "On it, sir."})
    reply, _ = say(eva, "yes")
    assert started and started[0]["project"]


def test_projects_by_number_and_delete():
    for n in ("Desk Display", "Test Display Project", "Transparent desk screen", "Transparent Desk Screen Project"):
        projects.get_store().new(n)
    out = projects.tool_project_list()
    assert out["say"].startswith("Your projects, sir: 1, Desk Display; 2, Test Display Project;")
    assert projects.describe_delete({"projects": ["1", "2", "3"]}) == \
        "delete these projects: Desk Display, Test Display Project, Transparent desk screen"
    out = projects.tool_project_delete(projects=["first", "second", "third"])
    assert out["say"] == "Deleted, sir: Desk Display, Test Display Project, Transparent desk screen."
    assert [p.name for p in projects.get_store().all()] == ["Transparent Desk Screen Project"]
    p = projects.get_store().all()[0]
    assert projects._pick(p.id) is not None                 # 23:37: project_answer(project='7189a95a') -> no project


def test_deleting_projects_asks_first():
    from core.orchestrator_hybrid import ToolBelt
    b = ToolBelt(None, None)
    assert b.needs_confirm("project_delete") and {"project_start", "project_note"} <= mind.MIND_ONLY


def test_play_the_video_not_the_music(ddgs):
    """23:39: 'Can you play it on my computer?' moved the Spotify music; 'play the first one on my PC and stop the
    music' searched Spotify for that whole sentence."""
    from core.orchestrator_hybrid import ToolBelt, fast_path
    b = ToolBelt(None, None)
    assert fast_path("Can you play it on my computer?", b)[0] == "spotify_play"          # nothing on screen yet
    show.tool_find_videos("peppers ghost display")
    assert fast_path("Can you play it on my computer?", b) == \
        ("open_shown", {"number": 1, "on": "pc", "pause_music": False})
    assert fast_path("Play the first one on my PC and stop the music", b) == \
        ("open_shown", {"number": 1, "on": "pc", "pause_music": True})
    assert fast_path("play the second video", b) == ("open_shown", {"number": 2, "on": "window", "pause_music": False})
    assert fast_path("play my chill playlist", b)[0] == "spotify_play"


def test_open_shown_can_stop_the_music_too(ddgs, monkeypatch):
    import core.tools_native as tn
    paused = []
    monkeypatch.setitem(tn.REGISTRY, "media_control", lambda **a: paused.append(a))
    show.tool_find_videos("peppers ghost display")
    show.tool_open_shown(1, on="pc", pause_music=True)
    assert paused == [{"action": "pause"}]


def test_a_skill_for_it_means_what_he_said_before(eva):  # noqa: F811
    eva.history = [{"role": "user", "content": "can you turn on my Bluetooth on my computer?"},
                   {"role": "assistant", "content": "I can't switch Bluetooth on from here, sir."},
                   {"role": "user", "content": "Can you develop a skill to open it?"}]
    assert eva._resolve_vague("open it?") == "can you turn on my Bluetooth on my computer (open it)"
    assert eva._resolve_vague("tell me the moon phase for any date") == "tell me the moon phase for any date"


def test_when_overpass_is_down_nominatim_answers(monkeypatch):
    import httpx
    monkeypatch.setattr(nearby.time, "sleep", lambda s: None)
    nearby._cache.clear()

    def post(url, data=None, headers=None, timeout=None):
        raise httpx.ReadTimeout("The read operation timed out")

    def get(url, params=None, headers=None, timeout=None):
        assert params["q"] == "Albert Heijn" and params["extratags"] == 1 and params["bounded"] == 1
        return httpx.Response(200, json=[{"lat": "51.59", "lon": "4.776", "name": "Albert Heijn",
                                          "extratags": {"opening_hours": "Mo-Su 08:00-22:00"}}],
                              request=httpx.Request("GET", url))
    monkeypatch.setattr(httpx, "post", post)
    monkeypatch.setattr(httpx, "get", get)
    els = nearby.search("Albert Hain", (51.589, 4.776), 1500)
    assert els[0]["tags"]["name"] == "Albert Heijn" and els[0]["tags"]["opening_hours"]
