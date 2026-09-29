"""Sep 29, second build: the planner claimed 2023-10-15 was a full moon (14 Oct 2023 was a NEW moon, the eclipse),
the tester compared the exact spoken sentence, and the fixer changed nothing for 4 rounds (26 minutes)."""

import re

from core import forge_crew as fc
from core.forge_engine import Forge
from tests.test_forge_crew import MOON, PLAN, FakeBrain

WRONG_FACT_TESTS = '''import json
import tools


def test_new_moon_at_the_epoch():
    out = tools.moon_phase(date="2000-01-06T18:14:00")
    assert json.loads(out["result"])["phase"] == "new moon", f"got {out!r}"


def test_full_moon_on_2023_10_15():
    out = tools.moon_phase(date="2023-10-15")
    assert json.loads(out["result"])["phase"] == "full moon", f"got {out!r}"
    assert out["say"] == "The moon phase on 2023-10-15 is a Full Moon.", f"got {out['say']!r}"
'''


class JudgeBrain(FakeBrain):
    def __init__(self, verdict, **kw):
        super().__init__(**kw)
        self.verdict = verdict

    def code_json(self, system, messages, tool):
        role = re.match(r"ROLE: (\w+)", system).group(1)
        if role == "judge":
            self.calls.append("judge")
            return self.verdict, 0.0, "fake"
        return super().code_json(system, messages, tool)


def forge(tmp_path):
    f = Forge(skills_dir=tmp_path / "skills", brain=None)
    f._state = tmp_path / "p.json"
    return f


def test_the_judge_drops_the_test_with_the_wrong_fact_and_the_skill_is_ready(tmp_path):
    brain = JudgeBrain({"wrong_tests": ["test_full_moon_on_2023_10_15"], "explain": "the formula gives new moon"},
                       tester=f"```python\n{WRONG_FACT_TESTS}```", fixer=[f"```python\n{MOON}```"] * 4)
    stages = []
    p = fc.Crew(brain, forge(tmp_path), progress=stages.append).build("tell me the moon phase")
    assert p.status == "ready", p.reason
    assert "judging: code or test?" in stages and brain.calls.count("fixer") == 1    # one useless round, not 4
    assert any("judge dropped tests" in f["msg"] for f in p.review)


def test_no_progress_stops_early_when_the_judge_blames_the_code(tmp_path):
    brain = JudgeBrain({"wrong_tests": []}, tester=f"```python\n{WRONG_FACT_TESTS}```",
                       fixer=[f"```python\n{MOON}```"] * 4)
    p = fc.Crew(brain, forge(tmp_path)).build("tell me the moon phase")
    assert p.status == "rejected" and brain.calls.count("fixer") == 2 and brain.calls.count("judge") == 1


def test_the_judge_can_never_drop_every_test(tmp_path):
    only_wrong = WRONG_FACT_TESTS.split("def test_new_moon_at_the_epoch")[0] + \
        "def test_full_moon_on_2023_10_15" + WRONG_FACT_TESTS.split("def test_full_moon_on_2023_10_15")[1]
    brain = JudgeBrain({"wrong_tests": ["test_full_moon_on_2023_10_15"]}, tester=f"```python\n{only_wrong}```",
                       fixer=[f"```python\n{MOON}```"] * 4)
    p = fc.Crew(brain, forge(tmp_path)).build("tell me the moon phase")
    assert p.status == "rejected"                               # a skill with no passing test is never ready


def test_exact_sentence_checks_are_rewritten():
    src = '    assert out["say"] == "The moon phase on 2023-10-15 is a Full Moon.", f"got {out[\'say\']!r}"\n' \
          "    assert 'It is a full moon, sir.' == res['say']\n"
    out = fc.lint_tests(src)
    assert out == '    assert out["say"]\n    assert res[\'say\']\n'


def test_drop_tests_removes_whole_functions():
    out = fc.drop_tests(WRONG_FACT_TESTS, ["test_full_moon_on_2023_10_15"])
    assert "test_full_moon_on_2023_10_15" not in out and "def test_new_moon_at_the_epoch" in out


def test_a_skill_whose_tests_were_dropped_is_announced_as_not_fully_verified():
    """Sep 29 19:32: 'ready, passed 2 tests' for a moon-phase skill that called 15 Oct 2023 (a new moon) full."""
    from types import SimpleNamespace
    import core.forge_jobs as fj
    p = SimpleNamespace(status="ready", name="calculate_moon_phase", summary="", network_hosts=[], tools=["x"],
                        tests_passed=2, cost_eur=0.0, provider="qwen2.5-coder:14b",
                        review=[{"level": "warn", "msg": "judge dropped tests with wrong expectations: test_a, test_b"}])
    say = fj.result_message(p)["say"]
    assert "I dropped 2 of its tests that looked wrong, so its answers aren't fully verified" in say
    clean = SimpleNamespace(**{**p.__dict__, "review": []})
    assert "not fully verified" not in fj.result_message(clean)["say"]
