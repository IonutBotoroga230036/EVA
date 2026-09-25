"""Deep thinking, plus every bug from the Sep 25 midnight log."""

import json

import pytest

import core.orchestrator_hybrid as orch_mod
from core.memory.cortex import Cortex
from core.orchestrator_hybrid import HybridOrchestrator, ToolBelt, fast_path, is_small_talk
from skills.registry import SkillRegistry
from tests.test_orchestrator import fake_ollama, run_turn


@pytest.fixture
def belt():
    return ToolBelt(SkillRegistry("skills").discover())


def orch(tmp_path):
    return HybridOrchestrator(session_id="t", cortex=Cortex(str(tmp_path / "c.db")),
                              registry=SkillRegistry("skills").discover())


class FakeClaude:
    def __init__(self, text="Take the Tilburg flat, sir. It halves your commute.", key=True, boom=None):
        self.text, self.key, self.boom, self.calls = text, key, boom, []

    def available(self):
        return self.key

    def message(self, **kw):
        self.calls.append(kw)
        if self.boom:
            raise self.boom
        return {"text": self.text, "cost_eur": 0.01}


def think(monkeypatch, fake):
    """Deep thinking runs through the Brain; give it a Brain in cloud mode around the fake Claude."""
    from core.brain import Brain
    fn = SkillRegistry("skills").discover().functions()["think_deeply"]
    brain = Brain(claude=fake)
    brain.pick = lambda: "cloud"
    monkeypatch.setitem(fn.__globals__, "get_brain", lambda: brain)
    return fn


def test_think_fast_path_and_answer_is_spoken_as_is(belt, monkeypatch, tmp_path):
    assert fast_path("think hard about whether I should move to Tilburg", belt) == \
        ("think_deeply", {"question": "whether I should move to Tilburg"})
    import core.prompt_builder as pb
    (tmp_path / "EVA.local.md").write_text("## About me (private)\n- SECRET-MARKER-XYZ\n")
    monkeypatch.setattr(pb, "EVA_LOCAL_MD", tmp_path / "EVA.local.md")
    fake = FakeClaude()
    out = think(monkeypatch, fake)(question="Should I move?")
    assert out["say"].startswith("Take the Tilburg flat") and out["exact"]
    assert fake.calls[0]["effort"] == "medium"
    assert "SECRET-MARKER-XYZ" not in fake.calls[0]["system"]          # private context never leaves the machine


def test_think_over_budget_suggests_local_mode(monkeypatch):
    from core.budget import BudgetExceeded
    say = think(monkeypatch, FakeClaude(boom=BudgetExceeded("EUR 0.01 left")))(question="x")["say"]
    assert "budget" in say and "local mode" in say


# ---------------------------------------------------------------- Sep 25 log
def test_day_after_is_understood_from_your_words(tmp_path, monkeypatch):
    client, seen = fake_ollama(decisions=[{"tool": "get_weather", "city": "Breda", "day": "day after today"}],
                               answer="should not be used")
    monkeypatch.setattr(orch_mod.httpx, "AsyncClient", client)
    got = {}
    import core.tools_native as tn

    def fake_weather(city, day=None, hour=None):
        got.update(city=city, day=day)
        return {"city": city, "when": "Saturday 26 September", "conditions": "clear", "high_c": 22, "low_c": 12,
                "rain_chance_pct": 0, "date": "2026-09-27"}
    monkeypatch.setattr(tn, "get_weather_report", fake_weather)
    o = orch(tmp_path)
    o.last_tools = {"get_weather"}                                   # the previous turn was about weather
    events = run_turn(o, "what about the day after")
    assert got["day"] == "the day after"
    assert events[-1]["text"] == "Saturday 26 September in Breda: clear, 12 to 22 degrees, no rain expected, sir."
    assert seen["answers"] == 0                                      # exact line: no LLM, no drifting numbers


def test_weather_follow_up_passes_the_guard_only_after_weather(tmp_path, monkeypatch):
    client, _ = fake_ollama(decisions=[{"tool": "get_weather", "city": "what's"}], answer="Hello, sir.")
    monkeypatch.setattr(orch_mod.httpx, "AsyncClient", client)
    o = orch(tmp_path)
    run_turn(o, "what's the what's")                                 # garbled speech, no weather context
    assert "get_weather" not in [e["tool"] for e in o.bus.recent() if e["channel"] == "tool.executed"]


def test_one_plus_one_gets_calculated(belt, tmp_path, monkeypatch):
    assert fast_path("1 + 1", belt) == ("calculate", {"expression": "1 + 1"})
    assert fast_path("what's 15% of 80?", belt) == ("calculate", {"expression": "15% of 80"})
    client, seen = fake_ollama(decisions=[], answer="x")
    monkeypatch.setattr(orch_mod.httpx, "AsyncClient", client)
    assert run_turn(orch(tmp_path), "1 + 1")[-1]["text"] == "That's 2, sir." and seen["answers"] == 0


@pytest.mark.parametrize("text", ["what does that mean", "what does me being a person of interest mean",
                                  "can you search the Python Library number"])
def test_unavailable_is_not_believed_for_conversation(tmp_path, monkeypatch, text):
    client, _ = fake_ollama(decisions=[{"tool": "unavailable"}], answer="It means nothing sinister, sir.")
    monkeypatch.setattr(orch_mod.httpx, "AsyncClient", client)
    assert "don't have a skill" not in run_turn(orch(tmp_path), text)[-1]["text"]


def test_unavailable_still_works_for_real_missing_abilities(tmp_path, monkeypatch):
    client, _ = fake_ollama(decisions=[{"tool": "unavailable"}], answer="x")
    monkeypatch.setattr(orch_mod.httpx, "AsyncClient", client)
    o = orch(tmp_path)
    assert "don't have a skill" in run_turn(o, "can you check my calendar")[-1]["text"]
    assert o.last_missing == "can you check my calendar"            # "build it" would forge this


@pytest.mark.parametrize("text", ["would you like to know my name", "would you want to know my name",
                                  "it is cool to see that you can actually search for the weekend",
                                  "that's actually very cool"])
def test_more_small_talk(text):
    assert is_small_talk(text)


def test_she_knows_the_name_from_eva_md(monkeypatch, tmp_path):
    c = Cortex(str(tmp_path / "c.db"))
    monkeypatch.setattr("core.memory.cortex._cortex", c)
    fn = SkillRegistry("skills").discover().functions()["recall_memory"]
    facts = json.loads(fn(query="my name")["result"])["facts"]
    assert any("Ionuț" in f for f in facts)


def test_empty_memory_gets_an_exact_honest_answer(monkeypatch, tmp_path):
    import core.prompt_builder as pb
    monkeypatch.setattr(pb, "EVA_MD", tmp_path / "none.md")
    monkeypatch.setattr(pb, "EVA_LOCAL_MD", tmp_path / "none2.md")
    monkeypatch.setattr("core.memory.cortex._cortex", Cortex(str(tmp_path / "c.db")))
    out = SkillRegistry("skills").discover().functions()["recall_memory"](query="")
    assert out["say"] == "I don't have anything stored about that yet, sir." and out["exact"]


def test_vision_says_what_it_saw(monkeypatch):
    fn = SkillRegistry("skills").discover().functions()["see_screen"]
    assert fn.__globals__  # module loaded
    import types, sys
    fake_img = types.SimpleNamespace(thumbnail=lambda *a: None, save=lambda *a, **k: None)
    monkeypatch.setitem(sys.modules, "pyautogui", types.SimpleNamespace(screenshot=lambda: fake_img))

    class R:
        def raise_for_status(self): pass
        def json(self): return {"message": {"content": "The screenshot shows a YouTube page. A jazz video is playing."}}
    monkeypatch.setattr(fn.__globals__["httpx"], "post", lambda *a, **k: R())
    monkeypatch.setitem(fn.__globals__, "audit", types.SimpleNamespace(log=lambda *a, **k: None))
    out = fn(question="what do you see")
    assert out["say"] == "I can see a YouTube page. A jazz video is playing, sir." and out["exact"]


def test_budget_question_is_exact(belt):
    assert fast_path("how much have you spent today", belt) == ("budget_status", {})


# ---------------------------------------------------------------- web answers must use the sources' numbers
from core.orchestrator_hybrid import ungrounded_numbers

EURO_RESULT = json.dumps({"results": [{"title": "BTC to EUR | CoinGecko",
                                       "snippet": "the price of 1 Bitcoin (BTC) in Euro (EUR) is about €74,045.5"}]})


@pytest.mark.parametrize("answer,bad", [
    ("Bitcoin is €84,294.87 in euros, sir.", ["84,294.87"]),          # the Sep 24 bug: dollar figure, euro sign
    ("Bitcoin is about €74,045 in euros, sir.", []),                   # rounding is fine
    ("Bitcoin is €74.045,50, sir.", []),                               # European formatting is fine
    ("In 2026 it rose 3 percent, sir.", []),                           # years and small numbers ignored
])
def test_ungrounded_numbers(answer, bad):
    assert ungrounded_numbers(answer, EURO_RESULT) == bad


def test_invented_number_is_retried_then_replaced(tmp_path, monkeypatch):
    import core.tools_native as tn
    monkeypatch.setattr(tn, "_search", lambda q: json.loads(EURO_RESULT)["results"])
    monkeypatch.setattr(tn, "SEARCH_OK", True)
    client, seen = fake_ollama(decisions=[{"tool": "web_search", "query": "bitcoin price euro"}],
                               answer="The price is €84,294.87, sir.")          # the model keeps inventing it
    monkeypatch.setattr(orch_mod.httpx, "AsyncClient", client)
    final = run_turn(orch(tmp_path), "what's the price of bitcoin in euros")[-1]["text"]
    assert "84,294" not in final and "74,045.5" in final and seen["answers"] == 2
