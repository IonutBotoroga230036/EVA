"""Sep 29 log: the crew's fixer was blind (bare 'AssertionError: '), a shopping list became six reminders, and
'When should I remind you?' saved her own question as the reminder."""

import json

import pytest

from core import forge_crew as fc
from core.forge_engine import Forge, run_sandbox
from tests.test_forge_crew import MOON, PLAN, FakeBrain


def test_the_fixer_now_sees_the_failing_line_and_what_the_tool_returned(tmp_path):
    d = tmp_path / "s"
    d.mkdir()
    plan = fc.normalize_plan(PLAN)
    wrong = MOON.replace('NAMES[int((age / SYN) * 8 + 0.5) % 8]', '"full moon"')
    (d / "tools.py").write_text(fc.assemble(plan, wrong), encoding="utf-8")
    (d / "test_skill.py").write_text('import json\nimport tools\n\ndef test_epoch():\n'
                                     '    data = json.loads(tools.moon_phase(date="2000-01-06T18:14:00")["result"])\n'
                                     '    assert data["phase"] == "new moon"\n', encoding="utf-8")
    msg = run_sandbox(d)["failed"]["test_epoch"]
    assert msg.startswith("AssertionError")
    assert 'failing line: assert data["phase"] == "new moon"' in msg
    assert "last call: moon_phase(date='2000-01-06T18:14:00') returned" in msg and "full moon" in msg


def test_a_missing_name_becomes_the_first_tools_name():
    assert fc.normalize_plan({**PLAN, "name": ""})["name"] == "moon_phase"


def test_prompts_ask_for_facts_from_the_method_and_messages_on_every_assert():
    assert "never from memory" in fc.PLANNER and "Give EVERY assert a message" in fc.TESTER
    assert "the TEST is wrong" in fc.FIXER


def test_a_failed_build_says_which_tests_failed_and_offers_claude(monkeypatch):
    from types import SimpleNamespace
    import core.brain as brain_mod
    import core.forge_jobs as fj
    monkeypatch.setattr(brain_mod, "get_brain", lambda: SimpleNamespace(cloud_ready=lambda: True))
    p = SimpleNamespace(status="rejected", name="moon_phase", cost_eur=0.0, provider="qwen2.5-coder:14b",
                        reason="test_full_moon: AssertionError | ...; test_bad_input: AssertionError")
    say = fj.result_message(p)["say"]
    assert "The tests about full moon and bad input kept failing." in say
    assert 'Say "use Claude for FORGE"' in say


def test_no_claude_offer_when_claude_isnt_available(monkeypatch):
    from types import SimpleNamespace
    import core.brain as brain_mod
    import core.forge_jobs as fj
    monkeypatch.setattr(brain_mod, "get_brain", lambda: SimpleNamespace(cloud_ready=lambda: False))
    p = SimpleNamespace(status="rejected", name="moon_phase", cost_eur=0.0, provider="qwen2.5-coder:14b", reason="x")
    assert "Claude" not in fj.result_message(p)["say"]


def test_a_shopping_list_is_one_reminder_not_six():
    from core.multistep import parse_plan
    said = "I want you to remind me to buy bread, chicken, potatoes, avocado, eggs and something to make sandwiches in 30 mins"
    raw = {"steps": [{"command": f"set reminder to buy {x}", "uses_previous": False}
                     for x in ("bread", "chicken", "potatoes", "avocado", "eggs")]}
    assert parse_plan(raw, said) is None
    two = {"steps": [{"command": "turn the lights red", "uses_previous": False},
                     {"command": "play The Weeknd", "uses_previous": False}]}
    assert parse_plan(two, "turn the lights red and play The Weeknd")          # real multi-step still works


def test_when_should_i_remind_you_keeps_your_words(tmp_path, monkeypatch):
    import core.orchestrator_hybrid as orch_mod
    from core.memory.cortex import Cortex
    from core.oracle import get_oracle
    from skills.registry import SkillRegistry
    from tests.test_m4_and_sep27b import fake_ollama, turn
    monkeypatch.setattr(orch_mod, "fast_path", lambda *a, **k: None)
    client, _ = fake_ollama(decide={"buy bread": {"tool": "set_reminder", "text": "buy bread and eggs"}})
    monkeypatch.setattr(orch_mod.httpx, "AsyncClient", client)
    o = orch_mod.HybridOrchestrator(session_id="w", cortex=Cortex(str(tmp_path / "c.db")),
                                    registry=SkillRegistry("skills").discover())
    assert turn(o, "remind me to buy bread and eggs") == "When should I remind you to buy bread and eggs, sir?"
    answer = turn(o, "in 30 minutes")
    texts = [r["text"] for r in get_oracle().store.open()]
    assert texts == ["buy bread and eggs"], texts                      # not "I'll remind you to ..." again
    assert "30 minutes" in answer


@pytest.mark.parametrize("text", ["how is the skilled building going?", "how's the build going", "how is the skill"])
def test_build_status_despite_whisper(text):
    from core.orchestrator_hybrid import ToolBelt, fast_path
    b = ToolBelt(None, None)
    b.by_name.setdefault("forge_status", {})
    assert fast_path(text, b) == ("forge_status", {})
