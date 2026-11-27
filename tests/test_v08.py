"""v0.8: every Claude call streams and survives a dropped line; the mind speaks while it thinks; long answers are
voiced sentence by sentence; acks only when something takes a while; computer use without asking; and the fixes
from his 27 Nov log (places, calendar, the screen, links, "yes" to her question, vague sites). The internet, the
screen and the API are fakes."""

import asyncio
import json
import types
from datetime import date, datetime

import httpx
import pytest

import core.claude as claude_mod
import core.orchestrator_hybrid as orch_mod
from core import computer, mind, nearby, tempo, timeparse
from core.agent.tasks import get_store
from core.budget import BudgetTracker
from core.claude import ClaudeClient, ClaudeCut, read_stream

from tests.test_agent import T, mgr  # noqa: F401  (fixture)
from tests.test_v05_mind import eva, say, think  # noqa: F401  (fixtures)


# ---------------------------------------------------------------- streaming
def sse(*events) -> str:
    return "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events)


START = {"type": "message_start", "message": {"id": "msg_1", "type": "message", "role": "assistant", "content": [],
                                               "model": "claude-haiku-5-5", "usage": {"input_tokens": 25,
                                                                                      "output_tokens": 1}}}
STOP = {"type": "message_stop"}


def text_stream(*pieces, stop="end_turn"):
    return sse(START, {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
               *[{"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": p}} for p in pieces],
               {"type": "content_block_stop", "index": 0},
               {"type": "message_delta", "delta": {"stop_reason": stop}, "usage": {"output_tokens": 12}}, STOP)


def client(tmp_path, handler):
    return ClaudeClient(api_key="k", budget=BudgetTracker(track_file=str(tmp_path / "b.json")),
                        http=httpx.Client(transport=httpx.MockTransport(handler)))


def test_a_stream_becomes_the_same_message():
    got = []
    raw = sse(START,
              {"type": "content_block_start", "index": 0, "content_block": {"type": "thinking", "thinking": "",
                                                                            "signature": ""}},
              {"type": "content_block_delta", "index": 0, "delta": {"type": "thinking_delta", "thinking": "Hmm."}},
              {"type": "content_block_delta", "index": 0, "delta": {"type": "signature_delta", "signature": "EqQB"}},
              {"type": "content_block_stop", "index": 0},
              {"type": "ping"},
              {"type": "content_block_start", "index": 1, "content_block": {"type": "text", "text": ""}},
              {"type": "content_block_delta", "index": 1, "delta": {"type": "text_delta", "text": "Checking "}},
              {"type": "content_block_delta", "index": 1, "delta": {"type": "text_delta", "text": "it, sir."}},
              {"type": "content_block_stop", "index": 1},
              {"type": "content_block_start", "index": 2, "content_block": {
                  "type": "tool_use", "id": "toolu_1", "name": "navigate", "input": {}, "toolset_name": "browser"}},
              {"type": "content_block_delta", "index": 2, "delta": {"type": "input_json_delta",
                                                                     "partial_json": "{\"url\": \"https://pru"}},
              {"type": "content_block_delta", "index": 2, "delta": {"type": "input_json_delta",
                                                                     "partial_json": "sa3d.com\"}"}},
              {"type": "content_block_stop", "index": 2},
              {"type": "some_new_event", "x": 1},                               # unknown events are ignored
              {"type": "message_delta", "delta": {"stop_reason": "tool_use"}, "usage": {"output_tokens": 89}},
              STOP)
    msg = read_stream(raw.splitlines(), got.append)
    assert got == ["Checking ", "it, sir."]
    assert msg["content"][0] == {"type": "thinking", "thinking": "Hmm.", "signature": "EqQB"}
    assert msg["content"][1] == {"type": "text", "text": "Checking it, sir."}
    assert msg["content"][2]["input"] == {"url": "https://prusa3d.com"} and msg["content"][2]["toolset_name"] == "browser"
    assert msg["stop_reason"] == "tool_use" and msg["usage"] == {"input_tokens": 25, "output_tokens": 89}


def test_the_client_streams_and_hands_over_the_words(tmp_path):
    seen = []

    def handler(req):
        seen.append(json.loads(req.content))
        return httpx.Response(200, text=text_stream("Hello ", "sir."), headers={"content-type": "text/event-stream"})
    words = []
    out = client(tmp_path, handler).message(system="s", messages=[{"role": "user", "content": "hi"}], on_text=words.append)
    assert seen[0]["stream"] is True and out["text"] == "Hello sir." and words == ["Hello ", "sir."]
    assert out["usage"]["output_tokens"] == 12 and out["cost_eur"] > 0


def test_a_dropped_line_is_sent_again(tmp_path, monkeypatch):
    """27 Nov: 'Server disconnected without sending a response', about 15 s into a call; three tasks crashed."""
    monkeypatch.setattr(claude_mod, "RETRY_WAITS", (0, 0))
    calls = []

    def handler(req):
        calls.append(1)
        if len(calls) == 1:
            raise httpx.RemoteProtocolError("Server disconnected without sending a response.")
        if len(calls) == 2:
            return httpx.Response(529, json={"type": "error", "error": {"type": "overloaded_error"}})
        return httpx.Response(200, text=text_stream("Back, sir."), headers={"content-type": "text/event-stream"})
    out = client(tmp_path, handler).message(system="s", messages=[{"role": "user", "content": "hi"}])
    assert out["text"] == "Back, sir." and len(calls) == 3


def test_an_overloaded_stream_is_sent_again_but_a_cut_answer_is_not(tmp_path, monkeypatch):
    monkeypatch.setattr(claude_mod, "RETRY_WAITS", (0,))
    n = []

    def handler(req):
        n.append(1)
        if len(n) == 1:
            return httpx.Response(200, text=sse(START, {"type": "error", "error": {"type": "overloaded_error",
                                                                                   "message": "Overloaded"}}),
                                  headers={"content-type": "text/event-stream"})
        body = text_stream("Half a sent", "ence.")
        return httpx.Response(200, text=body[:body.index("ence.") - 40], headers={"content-type": "text/event-stream"})
    words = []
    with pytest.raises(ClaudeCut) as cut:
        client(tmp_path, handler).message(system="s", messages=[{"role": "user", "content": "hi"}], on_text=words.append)
    assert len(n) == 2 and cut.value.partial == "Half a sent" == "".join(words)


def test_plain_json_answers_still_work(tmp_path):
    def handler(req):
        return httpx.Response(200, json={"content": [{"type": "text", "text": "ok"}], "usage": {"input_tokens": 1,
                                                                                               "output_tokens": 1}})
    assert client(tmp_path, handler).message(system="s", messages=[{"role": "user", "content": "x"}])["text"] == "ok"


# ---------------------------------------------------------------- tasks survive it
def test_a_task_waits_out_a_dropped_line(mgr, monkeypatch):  # noqa: F811
    import core.agent.runner as runner
    monkeypatch.setattr(runner, "LINE_WAITS", (0,))
    m = mgr([httpx.RemoteProtocolError("Server disconnected without sending a response."),
             [T("finish", summary="Bluetooth is on, sir.")]])
    t = m.start("turn on Bluetooth")
    assert get_store().get(t.id).status == "done"


def test_a_task_that_keeps_losing_the_line_pauses_and_carries_on(mgr):  # noqa: F811
    m = mgr([httpx.RemoteProtocolError("gone"), [T("finish", summary="Done, sir.")]])
    t = m.start("make a study log")
    t = get_store().get(t.id)
    assert t.status == "waiting" and t.waiting["consent_kind"] == "network"       # not "failed"
    assert "lost the connection to Claude" in m.told[-1][1]
    m.answer("carry on", t.id)
    assert get_store().get(t.id).status == "done"
    said = m.script.calls[-1]["messages"][-1]["content"]
    assert "The connection to you dropped" in json.dumps(said)


# ---------------------------------------------------------------- the mind speaks while it thinks
def test_the_mind_streams_checked_sentences(eva, think):
    think([["The cheapest version is a mini projector and film, sir. That's about 100 euros. Shall I add it?"]])
    _, events = say(eva, "What would be the cheapest way to build the screen?")
    tokens = [e["text"] for e in events if e["type"] == "token"]
    assert tokens == ["The cheapest version is a mini projector and film, sir. ", "That's about 100 euros. ",
                      "Shall I add it? "]
    final = [e for e in events if e["type"] == "final"][-1]
    assert events.index(final) > max(i for i, e in enumerate(events) if e["type"] == "token")
    assert not final.get("replaced")


def test_a_claim_is_never_spoken_before_the_guard(eva, think):
    think([["Good choice, sir. I've ordered the projector for you."]])
    reply, events = say(eva, "What would be the cheapest way to build the screen?")
    spoken = " ".join(e["text"] for e in events if e["type"] == "token")
    assert "ordered" not in spoken and spoken.startswith("Good choice, sir.")
    final = [e for e in events if e["type"] == "final"][-1]
    assert final["replaced"] and final["text"] == orch_mod.NOT_DONE


def test_a_dropped_line_in_the_mind_says_so(eva, think):
    """27 Nov 01:04: the line dropped, the old way answered 'I'll start a task to research and purchase it'."""
    think([httpx.RemoteProtocolError("Server disconnected without sending a response.")])
    reply, _ = say(eva, "Yeah, I have curtains.")
    assert reply == "I lost the line to Claude for a moment, sir. Could you say that again?"


def test_talk_thinks_briefly_and_working_thinks_properly(eva, think, monkeypatch):
    s = think([["The library, sir, unless you print weekly."], ["Here's how I'd do it, sir."]])
    say(eva, "Should I print it at the library or buy a printer?")

    async def decide(client, skills, gathered, messages=None):
        return {"tool": "work_it_out", "_trusted": True}
    monkeypatch.setattr(eva, "_decide", decide)
    say(eva, "How would you build a presence sensor for the desk?")
    assert [c["effort"] for c in s.calls] == ["low", "medium"]


def test_one_moment_only_when_working_and_still_silent(eva, monkeypatch):
    monkeypatch.setattr(mind, "allowed", lambda: True)
    monkeypatch.setattr(mind, "cfg", lambda: {**mind.DEFAULTS, "ack_after_s": 0.05})

    class Slow:
        def message(self, **kw):
            import time
            time.sleep(0.3)
            return {"content": [{"type": "text", "text": "Here it is, sir."}], "tool_uses": [],
                    "text": "Here it is, sir.", "cost_eur": 0.0}
    monkeypatch.setattr(claude_mod, "get_claude", lambda: Slow())

    async def decide(client, skills, gathered, messages=None):
        return {"tool": "work_it_out", "_trusted": True}
    monkeypatch.setattr(eva, "_decide", decide)
    _, events = say(eva, "How would you build a presence sensor for the desk?")
    acks = [e["text"] for e in events if e["type"] == "ack"]
    assert len(acks) == 1 and acks[0] in orch_mod.MIND_ACKS and "Let me check, sir." not in orch_mod.GENERIC_ACKS
    monkeypatch.setattr(eva, "_decide", lambda *a, **k: asyncio.sleep(0, {"tool": "none"}))
    _, events = say(eva, "Should I print it at the library or buy a printer?")
    assert not [e for e in events if e["type"] == "ack"]                     # talk never says "one moment"


# ---------------------------------------------------------------- the voice
def test_a_long_answer_is_voiced_sentence_by_sentence():
    from voice.speech import sentences
    assert sentences("First one, sir. Then the second! And a third") == ["First one, sir.", "Then the second!",
                                                                         "And a third"]


def test_the_window_hears_that_a_sentence_is_on_its_way():
    from voice.speech import TurnSpeaker
    sent = []

    async def go():
        async def send(m):
            sent.append(m["type"])
        sp = TurnSpeaker(types.SimpleNamespace(synth=lambda text: b"RIFF"), send, 1)
        await sp.say("Hello, sir.")
        await sp.finish()
    asyncio.run(go())
    assert sent == ["audio_pending", "audio", "audio_end"]


def test_kokoro_uses_the_gpu_when_there_is_one(monkeypatch):
    from voice import speech
    import sys
    monkeypatch.setitem(sys.modules, "torch", types.SimpleNamespace(cuda=types.SimpleNamespace(is_available=lambda: True)))
    assert speech._device("auto") == "cuda" and speech._device("cpu") == "cpu"
    monkeypatch.setitem(sys.modules, "torch", types.SimpleNamespace(cuda=types.SimpleNamespace(is_available=lambda: False)))
    assert speech._device("auto") == "cpu"


def test_eva_stop_over_her_voice_only_stops_her(monkeypatch):
    from interfaces.web import server_stream as srv
    assert srv._SHUSH.match("Eva, stop.") and srv._SHUSH.match("okay stop talking") and srv._SHUSH.match("that's enough")
    assert not srv._SHUSH.match("stop the music") and not srv._SHUSH.match("Eva, stop the timer")
    conn = srv.Connection.__new__(srv.Connection)
    sent, submitted = [], []

    async def send(m):
        sent.append(m)

    async def submit(*a, **k):
        submitted.append(a)
    conn.send, conn.submit, conn.orch = send, submit, types.SimpleNamespace()
    monkeypatch.setattr(srv, "_romanian_hint", lambda o: None)
    conn._barged_at = __import__("time").time()
    asyncio.run(conn.voice_command("Eva, stop."))
    assert submitted == [] and sent[-1] == {"type": "quiet"}
    conn._barged_at = 0
    asyncio.run(conn.voice_command("Eva, stop."))
    assert submitted                                         # not over her voice: a normal turn (the music, a timer)


# ---------------------------------------------------------------- computer use without asking
def test_act_without_asking(monkeypatch):
    from core import ui_settings
    import os
    belt = orch_mod.HybridOrchestrator.__new__(orch_mod.HybridOrchestrator)
    from skills.registry import SkillRegistry
    b = orch_mod.ToolBelt(SkillRegistry("skills"))
    assert b.needs_confirm("use_computer")
    ui_settings.change("computer.mode", "auto")
    assert not b.needs_confirm("use_computer") and b.acts_alone("use_computer")
    opened, started = [], []
    monkeypatch.setattr(os, "startfile", lambda uri: opened.append(uri), raising=False)
    import core.agent.runner as runner
    monkeypatch.setattr(runner, "get_manager", lambda: types.SimpleNamespace(
        start=lambda goal, *a, **k: started.append((goal, k)) or types.SimpleNamespace(id="abcd1234", title="bt")))
    computer.tool_use_computer(goal="Turn on Bluetooth on this PC")
    assert opened == ["ms-settings:bluetooth"]                # the page first: a few steps fewer
    assert "settings page was just opened" in started[0][0] and started[0][1]["toolsets"] == ["computer:act"]
    snap = {f["key"]: f for g in ui_settings.snapshot()["groups"] for f in g["fields"]}
    assert "auto" in [c["value"] for c in snap["computer.mode"]["choices"]]
    del belt


# ---------------------------------------------------------------- places
def test_the_question_is_not_the_shop_name():
    assert nearby._clean_what("Is the Albert Heim still open?") == "Albert Heijn"
    assert nearby._clean_what("a pharmacy open now") == "a pharmacy"
    assert nearby.brand("albert hain") == "Albert Heijn"


def test_all_closed_says_closed_not_nothing(monkeypatch):
    """27 Nov 00:37: nothing open at night, so the list was empty and she said 'Albert Heim is closed' from nothing."""
    monkeypatch.setattr(nearby, "where", lambda: ((51.589, 4.776), "the city centre"))
    monkeypatch.setattr(nearby, "search", lambda what, c, r: [
        {"lat": 51.590, "lon": 4.777, "tags": {"name": "Albert Heijn", "opening_hours": "Mo-Su 08:00-22:00"}}])
    monkeypatch.setattr(nearby, "datetime", types.SimpleNamespace(now=lambda: datetime(2026, 11, 27, 0, 37)))
    out = nearby.tool_nearby("Is the Albert Heim still open?", open_now=True)
    assert out["exact"] and out["say"].startswith("None is open right now, sir. The nearest Albert Heijn,")
    assert "opens" in out["say"]


def test_map_servers_are_asked_at_once(monkeypatch):
    import time as _t
    monkeypatch.setattr(nearby, "OVERPASS_TIMEOUT_S", 1.0)
    monkeypatch.setattr(nearby, "OVERPASS_HEAD_START_S", 0.1)
    nearby._cache.clear()
    monkeypatch.setattr(nearby, "_last_call", {"ts": 0.0})

    def slow_post(url, **k):
        _t.sleep(3)
        raise httpx.ReadTimeout("slow")
    monkeypatch.setattr(httpx, "post", slow_post)
    monkeypatch.setattr(nearby, "nominatim", lambda what, c, r: [{"lat": 1, "lon": 2, "tags": {"name": "AH"}}])
    t0 = _t.time()
    els = nearby.search("Albert Heijn", (51.5, 4.7), 1500)
    assert els[0]["tags"]["name"] == "AH" and _t.time() - t0 < 2.0


# ---------------------------------------------------------------- the calendar
def test_yesterday_and_the_26th():
    s, e, label = timeparse.day_window("yesterday", date(2026, 11, 27))
    assert (s.date(), e.date(), label) == (date(2026, 11, 26), date(2026, 11, 27), "yesterday")
    s, _, label = timeparse.day_window("on the 26th", date(2026, 11, 27))
    assert s.date() == date(2026, 11, 26) and label == "yesterday"
    s, _, label = timeparse.day_window("the 3rd", date(2026, 11, 27))
    assert s.date() == date(2026, 11, 3) and label == "on Tuesday the 3rd"


class Cal:
    def __init__(self, events):
        self.events = events

    def list_events(self, start, end, **k):
        return [e for e in self.events if start <= e["start"] < end]


def ev(h, title, d=27):
    return {"start": datetime(2026, 11, d, h), "end": datetime(2026, 11, d, h, 45), "title": title, "all_day": False}


def test_yesterday_is_past_and_after_midnight_today_is_named():
    cal = Cal([ev(9, "Lecture", 26), ev(14, "Gym", 26), ev(9, "Wake up", 27), ev(10, "Exam prep", 27)])
    out = tempo.agenda(cal, "yesterday", now=datetime(2026, 11, 27, 0, 48))
    assert out["say"] == "You had 2 things yesterday, sir: 09:00 Lecture, and 14:00 Gym."
    out = tempo.agenda(cal, "today", now=datetime(2026, 11, 27, 0, 48))
    assert out["say"].startswith("You have 2 things today, Friday the 27th, sir:")
    out = tempo.free_slots(cal, "today", now=datetime(2026, 11, 27, 0, 39))
    assert out["say"].startswith("You're free today, Friday the 27th from 08:00 to 09:00")


def test_more_than_the_calendar_goes_to_the_mind():
    b = types.SimpleNamespace(has=lambda name: True)          # every tool there, like on his PC
    fp = orch_mod.fast_path
    assert fp("what's on my calendar tomorrow and do I have time to go to a makerspace?", b) == ("work_it_out", {})
    assert fp("It was a busy day, you can see my schedule for yesterday and actually talk about it with me", b) == \
        ("work_it_out", {})
    assert fp("what's on my calendar tomorrow", b)[0] == "calendar_agenda"
    assert fp("what's on my calendar yesterday", b) == ("calendar_agenda", {"day": "yesterday"})


# ---------------------------------------------------------------- the screen, links, her questions, vague sites
def test_she_looks_at_the_screen_herself(eva, think, monkeypatch):
    import skills.vision.tools as vision
    monkeypatch.setattr(vision, "grab_screen", lambda: {"b64": "AAAA", "media_type": "image/jpeg", "path": "x"})
    s = think([["It's an eBay listing for the TMY V08 at 5 dollars from swann_34, sir; that price is too low "
                "to trust."]])

    async def decide(*a, **k):
        return {"tool": "see_screen", "question": "is it good?", "_trusted": True}
    monkeypatch.setattr(eva, "_decide", decide)
    reply, _ = say(eva, "Can you check my screen and tell me if it's good?")
    first = s.calls[0]["messages"][-1]["content"]
    assert first[1] == {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": "AAAA"}}
    assert "Here is his screen right now" in first[0]["text"] and reply.startswith("It's an eBay listing")


def test_show_me_the_links(mgr):  # noqa: F811
    from core.agent import tools as atools
    t = get_store().new("used mini projector search")
    t.status = "done"
    t.details = ("TMY V08 on Marktplaats: https://www.marktplaats.nl/q/mini+beamer/\n"
                 "Rear film: https://www.foliencenter24.com/rear-projection-film.")
    get_store().save(t)
    out = atools.tool_task_show("used mini projector")
    assert out["say"] == "Here's what the used mini projector search found, sir: 2 links, in the window."
    assert [l["url"] for l in out["widget"]["links"]] == ["https://www.marktplaats.nl/q/mini+beamer/",
                                                           "https://www.foliencenter24.com/rear-projection-film"]
    assert out["widget"]["links"][1]["title"] == "Rear film"


def test_yes_to_her_question_goes_back_to_her(eva, think, monkeypatch):
    """27 Nov 00:08: 'Shall I open it in Notepad?' 'Yes.' became open_file('""')."""
    s = think([["Nothing opens .md files on your PC, sir. I can open it in Notepad. Shall I do that?"],
               ["Right, sir: Notepad shows it as plain text."]])
    say(eva, "How can I see the files you made?")
    called = []

    async def decide(*a, **k):
        called.append(1)
        return {"tool": "open_file", "file": '""', "_trusted": True}
    monkeypatch.setattr(eva, "_decide", decide)
    reply, _ = say(eva, "Yes.")
    assert called == [] and len(s.calls) == 2 and reply == "Right, sir: Notepad shows it as plain text."
    assert "Shall I do that?" in json.dumps(s.calls[1]["messages"])


def test_yes_but_is_not_cut_into_commands(eva, think, monkeypatch):
    think([["A study log in Obsidian, one note per course. Shall I draft it as a skill?"],
           ["I'll make the courses a list you can change, sir. Shall I start?"]])
    say(eva, "How would the study log work?")
    monkeypatch.setattr(orch_mod.multistep, "looks_multi", lambda t: (_ for _ in ()).throw(AssertionError("split")))
    reply, _ = say(eva, "Yes, but you can change the names of the courses because now I have other courses. Make it "
                        "flexible so you can add and remove any courses.")
    assert reply.startswith("I'll make the courses a list")


def test_a_site_named_by_what_was_said_goes_to_the_mind(eva, think, monkeypatch):
    s = think([["Opening the TMY V08 listing, sir."]])

    async def decide(*a, **k):
        return {"tool": "open_website", "site": "the projector's shop page", "_trusted": True}
    monkeypatch.setattr(eva, "_decide", decide)
    reply, _ = say(eva, "Open the web page")
    assert len(s.calls) == 1 and "open_website" in s.calls[0]["messages"][-1]["content"]
    assert orch_mod._vague_site("open_website", {"site": "thuisbezorgd.nl"}) is False


# ---------------------------------------------------------------- the page
def test_the_page_waits_for_long_answers_and_stops_quietly():
    from pathlib import Path
    page = Path("interfaces/web/eva.html").read_text(encoding="utf-8")
    assert "case 'audio_pending': if (ev.turn === P.accept) kick();" in page
    assert "case 'quiet': stopAudio();" in page and "card__links" in page
