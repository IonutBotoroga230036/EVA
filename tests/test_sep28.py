"""Sep 28 night log: FORGE yields the GPU, 'say that again', Spotify on a phone that isn't there."""

import json

import httpx
import pytest

from core import activity
from core.brain import Brain, Paused


def stream_handler(chunks_per_call):
    calls = {"n": 0}

    def handler(request):
        i = calls["n"]
        calls["n"] += 1
        lines = [json.dumps({"message": {"content": c}, "done": False}) for c in chunks_per_call[i]]
        lines.append(json.dumps({"message": {"content": ""}, "done": True}))
        return httpx.Response(200, text="\n".join(lines))
    return handler, calls


def test_the_local_coder_stops_when_you_talk_and_resumes_after(monkeypatch):
    b = Brain(claude=None)
    handler, calls = stream_handler([['{"a":', ' 1'], ['{"a": ', '2}']])
    b.http = httpx.Client(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(activity, "wait_quiet", lambda *a, **k: None)
    interrupted = {"done": False}

    def busy(t0):
        if not interrupted["done"]:
            interrupted["done"] = True                  # you start talking during the first generation
            return True
        return False
    monkeypatch.setattr(activity, "busy_since", busy)
    tool = {"name": "skill", "input_schema": {"type": "object"}}
    data, cost, provider = b.code_json("sys", [{"role": "user", "content": "x"}], tool)
    assert calls["n"] == 2 and data == {"a": 2} and cost == 0.0


def test_streamed_generation_raises_paused_on_abort():
    b = Brain(claude=None)
    handler, _ = stream_handler([["a", "b", "c"]])
    b.http = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(Paused):
        b._ollama_yielding("m", [], None, 1024, 10, abort=lambda: True)


def test_activity_tracks_turns():
    """Sep 29 on Windows: a clock-based check missed a turn 1 microsecond later. The counter can't."""
    activity.mark()
    assert activity.since() < 1.0
    tok = activity.token()
    assert not activity.busy_since(tok)
    activity.mark()                                  # immediately after: same clock tick on Windows
    assert activity.busy_since(tok)


@pytest.mark.parametrize("text", ["Can you say that again?", "say that again", "what did you say?",
                                  "repeat that please", "sorry, come again?", "pardon?"])
def test_say_that_again(text):
    from core.orchestrator_hybrid import _REPEAT
    assert _REPEAT.match(text)


def test_repeat_is_her_last_answer_word_for_word(tmp_path, monkeypatch):
    import core.orchestrator_hybrid as orch_mod
    from core.memory.cortex import Cortex
    from skills.registry import SkillRegistry
    from tests.test_m4_and_sep27b import fake_ollama, turn
    client, _ = fake_ollama()
    monkeypatch.setattr(orch_mod.httpx, "AsyncClient", client)
    o = orch_mod.HybridOrchestrator(session_id="r", cortex=Cortex(str(tmp_path / "c.db")),
                                    registry=SkillRegistry("skills").discover())
    o.history.append({"role": "assistant", "content": "It's 23:54 on Monday the 28th, sir."})
    assert turn(o, "Can you say that again?") == "It's 23:54 on Monday the 28th, sir."
    assert not o.last_tools                                           # no screenshot, no tool at all


def test_spotify_says_the_phone_is_missing_instead_of_playing_on_the_pc(monkeypatch):
    from core.spotify import Spotify
    s = Spotify.__new__(Spotify)
    monkeypatch.setattr(s, "devices", lambda: [{"id": "pc", "name": "DESKTOP-2OJUB6H", "type": "Computer",
                                                "is_active": True}], raising=False)
    with pytest.raises(LookupError, match="your phone isn't showing up in Spotify"):
        s.pick_device("phone")
    assert s.pick_device("")["id"] == "pc"
