"""v0.2.5 milestone 4 (conversation lane, on/off) and the Sep 27 evening log fixes."""

import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

import core.conversation as conv
import core.orchestrator_hybrid as orch_mod
from core.memory.cortex import Cortex
from core.orchestrator_hybrid import _CLAIM, HybridOrchestrator, ToolBelt, fast_path, place_from, ungrounded_numbers
from skills.registry import SkillRegistry


def fake_ollama(decide=None, answer="I'm doing well, sir. What's on your mind tonight?"):
    """decide: {substring of the last user message: decision}. Records every system prompt it was sent."""
    seen = {"systems": [], "facts_calls": 0}

    def handler(request):
        body = json.loads(request.content)
        props = (body.get("format") or {}).get("properties", {})
        if "steps" in props:
            return httpx.Response(200, json={"message": {"content": json.dumps({"steps": []})}})
        if "facts" in props:
            seen["facts_calls"] += 1
            return httpx.Response(200, json={"message": {"content": json.dumps({"facts": []})}})
        if "tool" in props:
            last = body["messages"][-1]["content"].lower()
            d = next((v for k, v in (decide or {}).items() if k in last), {"tool": "none"})
            return httpx.Response(200, json={"message": {"content": json.dumps(d)}})
        if body.get("format"):
            return httpx.Response(200, json={"message": {"content": "{}"}})
        seen["systems"].append(body["messages"][0]["content"])
        lines = [json.dumps({"message": {"content": w + " "}, "done": False}) for w in answer.split()]
        lines.append(json.dumps({"message": {"content": ""}, "done": True}))
        return httpx.Response(200, text="\n".join(lines))
    real = httpx.AsyncClient
    return (lambda *a, **k: real(transport=httpx.MockTransport(handler))), seen


def turn(o, text):
    async def go():
        evs = [e async for e in o.process_stream(text)]
        if o._bg:
            await asyncio.gather(*o._bg, return_exceptions=True)
        return [e for e in evs if e["type"] == "final"][-1]["text"]
    return asyncio.run(go())


@pytest.fixture
def orch(tmp_path, monkeypatch):
    def make(**kw):
        client, seen = fake_ollama(**kw)
        monkeypatch.setattr(orch_mod.httpx, "AsyncClient", client)
        o = HybridOrchestrator(session_id="c", cortex=Cortex(str(tmp_path / "c.db")),
                               registry=SkillRegistry("skills").discover())
        return o, seen
    return make


# ------------------------------------------------------------ milestone 4: routing and prompt
@pytest.mark.parametrize("text,talk", [
    ("how are you doing?", True),
    ("what would make you more useful to me?", True),
    ("should I take the flat in Tilburg or stay in Breda?", True),
    ("what can you do?", True),
    ("turn the lights red", False),
    ("what time is it", False),
    ("I was wondering whether it makes sense to go back to university for a master after the pre-master or not?", True),
])
def test_what_counts_as_talking(text, talk):
    assert conv.wants(text) is talk


def test_she_knows_what_is_built_and_what_is_only_planned():
    k = conv.self_knowledge()
    assert "What you can do today" in k and "Planned, NOT built yet" in k
    assert "Private remote access" in k                     # a v0.3 milestone, listed as planned


def test_the_prompt_is_warm_honest_and_bounded():
    p = conv.system_prompt("You are E.V.A.", [{"text": "Studies at Radboud"}], voice=True)
    assert "at most 4 sentences" in p and "ONE short follow-up question" in p
    assert "Never say you did, set, sent, or changed anything" in p and "Studies at Radboud" in p


def test_switch_file_panel_and_default(tmp_path, monkeypatch):
    assert conv.enabled() is True                            # shipped default
    conv.set_enabled(False)
    assert conv.enabled() is False and json.loads(conv.STATE.read_text(encoding="utf-8"))["enabled"] is False
    conv.STATE.unlink()
    import core.settings as st
    local = tmp_path / "settings.local.yaml"
    local.write_text("conversation:\n  enabled: false\n", encoding="utf-8")
    monkeypatch.setattr(st, "LOCAL_PATH", local)
    st.get_settings.cache_clear()
    assert conv.enabled() is False                           # the file works on its own


def test_chat_goes_to_the_conversation_lane_and_learns_nothing_in_the_background(orch):
    o, seen = orch()
    text = turn(o, "how are you doing?")
    assert text == "I'm doing well, sir. What's on your mind tonight?"
    assert "You are in a conversation" in seen["systems"][-1] and o.last_lane == "conversation"
    assert seen["facts_calls"] == 0                          # no silent fact learning from chats


def test_follow_ups_stay_in_the_lane_and_a_task_ends_it(orch):
    o, seen = orch(decide={"time": {"tool": "get_datetime"}})
    turn(o, "what do you think about my week?")
    turn(o, "and why is that")                                # sticky
    assert o.last_lane == "conversation"
    turn(o, "what time is it")                                # a task
    assert o.last_lane == "task" and o._conv_left == 0
    turn(o, "ok thanks")
    assert o.last_lane == "task"


def test_switched_off_means_the_old_short_answers(orch):
    conv.set_enabled(False)
    o, seen = orch(answer="Fine, sir.")
    assert turn(o, "how are you doing?") == "Fine, sir."
    assert "You are in a conversation" not in seen["systems"][-1] and o.last_lane == "task"


def test_a_task_is_never_swallowed_by_the_lane(orch):
    o, _ = orch(decide={"what time": {"tool": "get_datetime"}})
    text = turn(o, "what do you think, what time is it?")
    assert o.last_lane == "task" and "sir" in text


def test_claude_for_conversation_when_chosen(orch, monkeypatch):
    import core.brain as brain_mod
    calls = []

    class FakeClaude:
        budget = SimpleNamespace(can_spend=lambda e: True)

        def available(self):
            return True

        def message(self, **kw):
            calls.append(kw)
            return {"text": "Honestly, sir, a longer memory of your projects would help most.", "cost_eur": 0.001}
    b = brain_mod.Brain(claude=FakeClaude())
    b.set_feature("conversation", "cloud")
    monkeypatch.setattr(brain_mod, "_brain", b)
    o, seen = orch()
    assert turn(o, "what would make you more useful to me?").startswith("Honestly, sir")
    msgs = calls[0]["messages"]
    assert msgs[0]["role"] == "user" and all(a["role"] != b["role"] for a, b in zip(msgs, msgs[1:]))


def test_a_chat_that_claims_an_action_is_caught(orch):
    o, _ = orch(answer="I've set the lights to purple for you, sir.")
    assert turn(o, "how are you doing?") == orch_mod.NOT_DONE


def test_voice_switch(orch):
    belt = ToolBelt(None, None)
    assert fast_path("turn conversation mode off", belt) == ("set_conversation_mode", {"on": False})
    assert fast_path("enable conversation mode", belt) == ("set_conversation_mode", {"on": True})
    from core.tools_native import tool_set_conversation_mode
    assert "off" in tool_set_conversation_mode(on=False)["say"] and conv.enabled() is False


def test_panel_api_and_longer_window_after_her_question(monkeypatch):
    import interfaces.web.server_stream as srv
    from tests.test_server import collect_until
    client, _ = fake_ollama()
    monkeypatch.setattr(orch_mod.httpx, "AsyncClient", client)
    monkeypatch.setattr(srv, "get_tts", lambda: None)
    with TestClient(srv.app) as tc:
        assert tc.post("/api/conversation", json={"enabled": False}).json() == {"enabled": False}
        assert tc.get("/api/status").json()["conversation"] == {"enabled": False}
        tc.post("/api/conversation", json={"enabled": True})
        with tc.websocket_connect("/ws") as ws:
            ws.receive_text(); ws.receive_text()
            ws.send_text(json.dumps({"type": "message", "text": "how are you doing?"}))
            meta = collect_until(ws, lambda e: e["type"] == "turn_meta")[-1]
    assert meta["listen_ms"] == 12000                        # she asked back, you get time to answer


# ------------------------------------------------------------ Sep 27 evening log
def test_whisper_hint_word_echo_from_music_is_dropped():
    from voice.stt import WhisperSTT, is_hint_echo
    hints = "Eva, Nijmegen, Breda, Tilburg, Deloitte, ZippZapp, Muaad, Radboud University"
    assert is_hint_echo("Nijmegen, Breda, Tilburg, Deloitte, SippZapp, Muaad,", hints)
    assert is_hint_echo("Nijmegen, Breda, Tilburg", hints)
    assert not is_hint_echo("what's the weather in Nijmegen", hints)
    assert not is_hint_echo("Radboud University", hints)                  # a real one-name answer

    class M:
        def transcribe(self, audio, **kw):
            return iter([SimpleNamespace(text=" Nijmegen, Breda, Tilburg,", no_speech_prob=0.1, avg_logprob=-0.3)]), None
    stt = WhisperSTT(device="cpu", vocab_source=lambda: "- Nijmegen\n- Breda\n- Tilburg\n",
                     model_factory=lambda *a, **k: M())
    import numpy as np
    assert stt.transcribe(np.zeros(16000, dtype=np.float32)) == ""


@pytest.mark.parametrize("noise", ["The", " you", "Um."])
def test_a_lone_filler_word_is_noise(noise):
    from voice.stt import clean_transcript
    assert clean_transcript([{"text": noise}]) == ""
    assert clean_transcript([{"text": " Tomorrow."}]) == "Tomorrow."            # a real one-word answer stays


def test_wake_mode_reacts_while_you_are_still_talking():
    from tests.test_listen import FakeSTT, make, run, silence, speech
    stt = FakeSTT("Eva, what", "Eva, what time is it")

    async def go():
        lst, log, _ = make(stt, early_wake_ms=300)
        lst.set_wake(True)
        await lst.feed(speech(12))                            # 384 ms in: the peek fires
        await asyncio.sleep(0.05)
        early = list(log["events"])
        await lst.feed(speech(10, 150) + silence(3))
        await lst.idle()
        return early, log
    early, log = run(go)
    assert {"type": "listen", "state": "speech"} in early         # the animation starts before you finish
    assert log["commands"] == ["what time is it"]
    assert ("listen", "end") in [(e["type"], e.get("state")) for e in log["events"]]


@pytest.mark.parametrize("text,claims", [
    ('Email sent to Muaad: "Hey buddy, how are you?"', True),
    ("I've set the lights to purple, playing The Weeknd, and Spotify volume to 75%. Tasks completed, sir.", True),
    ("Shall I set a reminder for that, sir?", False),
    ("The forecast for Nijmegen tomorrow is 12 degrees, sir.", False),
])
def test_claim_guard_catches_the_new_phrasings(text, claims):
    assert bool(_CLAIM.search(text)) is claims


def test_invented_numbers_are_caught():
    assert ungrounded_numbers("clear skies with a high of 12°C and a low of 3°C", "AccuWeather: tomorrow's weather") \
        == ["12"]
    assert ungrounded_numbers("It opened in 1923.", "founded in 1923 in Nijmegen") == []


def test_place_from_words():
    assert place_from("weather for Nijmegen tomorrow") == "Nijmegen"
    assert place_from("what's it like in the morning") == ""
    assert place_from("is it raining in Den Bosch today") == "Den Bosch"


def test_a_weather_web_search_becomes_the_weather_tool(orch, monkeypatch):
    import core.tools_native as tn
    calls = []
    fake = lambda **kw: calls.append(kw) or {"result": json.dumps({"city": kw.get("city")}), "exact": True,
                                            "say": f"Tomorrow in {kw.get('city')}: 14 to 19 degrees, sir."}
    for name, value in vars(tn).items():                      # never the real weather service in a test
        if isinstance(value, dict) and value.get("get_weather") is tn.tool_get_weather:
            monkeypatch.setitem(value, "get_weather", fake)
    monkeypatch.setattr(tn, "tool_get_weather", fake)
    monkeypatch.setattr(orch_mod, "fast_path", lambda *a, **k: None)          # force the model path
    o, _ = orch(decide={"nijmegen": {"tool": "web_search", "query": "weather for Nijmegen tomorrow"}})
    text = turn(o, "For Nijmegen tomorrow.")
    assert calls and calls[0].get("city") == "Nijmegen" and "14 to 19" in text


def test_email_recipient_from_your_words_and_who_before_what(tmp_path, monkeypatch):
    import core.scribe as scribe
    from skills.email import tools as et
    assert et.recipient_from("I want you to send an email to Muaad") == "Muaad"
    assert et.recipient_from("email Tom about the meeting") == "Tom"
    assert et.recipient_from("send an email") == ""
    assert et._ask_draft({}) == ("to", "Who should the email go to, sir?")
    assert et._ask_draft({"to": "Muaad"}) == ("instructions", "What should the email to {to} say, sir?")
    drafts = []
    monkeypatch.setattr(scribe, "draft", lambda g, instructions, to="", **k: drafts.append((to, instructions))
                        or {"say": f"Drafted to {to}, sir. Nothing was sent."})
    monkeypatch.setattr(orch_mod, "fast_path", lambda *a, **k: None)
    reg = SkillRegistry("skills").discover()
    mod = next(s for s in reg.enabled() if s.name == "email").functions["email_draft"].__globals__
    monkeypatch.setitem(mod, "_google", lambda: object())
    client, _ = fake_ollama(decide={"email": {"tool": "email_draft", "instructions": "Send an email."}})
    monkeypatch.setattr(orch_mod.httpx, "AsyncClient", client)
    o = HybridOrchestrator(session_id="e2", cortex=Cortex(str(tmp_path / "c.db")), registry=reg)
    assert turn(o, "I want you to send an email to Muaad") == "What should the email to Muaad say, sir?"
    o2 = HybridOrchestrator(session_id="e3", cortex=Cortex(str(tmp_path / "d.db")), registry=reg)
    assert turn(o2, "send an email please") == "Who should the email go to, sir?"
    assert turn(o2, "Muaad") == "What should the email to Muaad say, sir?"
    assert turn(o2, "Hey buddy, how are you?").startswith(
        'Just to confirm, sir: draft an email to Muaad saying: "Hey buddy, how are you?"')
    assert turn(o2, "yes") == "Drafted to Muaad, sir. Nothing was sent."
    assert drafts == [("Muaad", "Hey buddy, how are you?")]


@pytest.mark.parametrize("text", ["Can you turn down this? Can you?", "turn the music down", "lower the volume"])
def test_turn_down(text):
    assert fast_path(text, ToolBelt(None, None)) == ("media_control", {"action": "voldown"})


def test_no_stale_bus_handler_after_shutdown(monkeypatch):
    import interfaces.web.server_stream as srv
    from core.events.bus import get_bus
    monkeypatch.setattr(srv, "get_tts", lambda: None)
    with TestClient(srv.app):
        assert get_bus()._handlers.get("forge.done")
    assert not get_bus()._handlers.get("forge.done")


def test_forge_sandbox_reads_utf8_whatever_the_windows_default():
    """The sandbox child writes UTF-8; decoding it as cp1252 crashes on bytes like 0x81/0x8D/0x9D."""
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "core" / "forge_engine.py").read_text(encoding="utf-8")
    assert 'encoding="utf-8", errors="replace"' in src
    assert b"\xc5\x81".decode("utf-8") == "\u0141"             # e.g. a Polish L-stroke: UTF-8 C5 81
    with pytest.raises(UnicodeDecodeError):
        b"\xc5\x81".decode("cp1252")                          # 0x81 is undefined in cp1252: the old crash
