"""Ionut's log of 25 Nov, 00:33-00:43:
- "build a skill that converts currency": FORGE's tests passed (offline, with a fake API), it was installed, and
  the first real call failed: "no exchange rate (Expecting value: line 1 column 1)". The real service was never
  called before she offered it. api.frankfurter.app now lives at api.frankfurter.dev/v1.
- "can you do it locally" to "shall I go ahead?" was read as yes ("do it") and built with Claude (24 cents)
- "kill my music": "no active Spotify device", then the play/pause media key
"""

import json

import pytest

from core import forge_crew as fc
from core import forge_engine

from tests.test_forge_crew import MOON, PLAN, TESTS, FakeBrain, build, forge  # noqa: F401

NET_PLAN = {**PLAN, "name": "fx_rate", "method": "GET https://api.example.app/latest?from=EUR&to=RON",
            "network_hosts": ["api.example.app"],
            "tools": [{**PLAN["tools"][0], "name": "moon_phase"}],
            "test_cases": [{"tool": "moon_phase", "args": {"date": "2000-01-06T18:14:00"}, "expect": "new moon"},
                           {"tool": "moon_phase", "args": {"date": "x"}, "expect": "an error"}]}


# ---------------------------------------------------------------- FORGE calls the real service before offering
def test_a_network_skill_is_tried_live_before_it_is_offered(forge, monkeypatch):  # noqa: F811
    seen = []

    def live(skill_dir, plan):
        seen.append((plan["name"], [c["args"] for c in plan["test_cases"]]))
        return None                                    # the real call worked
    monkeypatch.setattr(forge_engine, "live_check", live)
    stages = []
    p = build(forge, FakeBrain(plan=NET_PLAN), stages=stages)
    assert p.status == "ready" and seen == [("fx_rate", [{"date": "2000-01-06T18:14:00"}, {"date": "x"}])]
    assert "trying it for real" in stages


def test_a_failed_live_call_goes_back_to_the_fixer(forge, monkeypatch):  # noqa: F811
    answers = ["LIVE: moon_phase({'date': '2026-11-25'}) against the real service returned: no exchange rate. "
               "GET https://api.example.app/latest answered 301, Location: https://api.example.dev/v1/latest", None]
    monkeypatch.setattr(forge_engine, "live_check", lambda d, plan: answers.pop(0))
    brain = FakeBrain(plan=NET_PLAN, fixer=[f"```python\n{MOON}```"])
    p = build(forge, brain)
    assert p.status == "ready" and brain.calls.count("fixer") == 1
    assert any("live check failed once" in f["msg"] for f in p.review)


def test_a_service_that_never_answers_is_not_offered(forge, monkeypatch):  # noqa: F811
    monkeypatch.setattr(forge_engine, "live_check", lambda d, plan: "LIVE: the real service answered 503")
    p = build(forge, FakeBrain(plan=NET_PLAN, fixer=[f"```python\n{MOON}```"] * 4))
    assert p.status == "rejected" and "real service" in p.reason


def test_offline_skills_need_no_live_call(forge, monkeypatch):  # noqa: F811
    monkeypatch.setattr(forge_engine, "live_check", lambda d, plan: pytest.fail("no network for an offline skill"))
    assert build(forge, FakeBrain()).status == "ready"


def test_a_redirect_to_a_sister_host_is_allowed_and_named():
    plan = {"network_hosts": ["api.frankfurter.app"]}
    assert forge_engine.follow_host(plan, "https://api.frankfurter.dev/v1/latest") == "api.frankfurter.dev"
    assert plan["network_hosts"] == ["api.frankfurter.app", "api.frankfurter.dev"]
    assert forge_engine.follow_host(plan, "https://evil.example.com/x") is None
    assert plan["network_hosts"] == ["api.frankfurter.app", "api.frankfurter.dev"]


def test_the_live_call_runs_the_skill_in_its_own_process(tmp_path):
    d = tmp_path / "s"
    d.mkdir()
    (d / "tools.py").write_text("import json\ndef moon_phase(date='', **_):\n"
                                "    return {'result': json.dumps({'error': 'no rate'}), 'say': 'no'}\n", encoding="utf-8")
    out = forge_engine.run_live(d, "moon_phase", {"date": "x"})
    assert json.loads(out["result"]) == {"error": "no rate"}


# ---------------------------------------------------------------- "can you do it locally"
def test_doing_it_locally_is_an_answer_not_a_yes_for_claude(monkeypatch):
    from core.orchestrator_hybrid import local_answer
    assert local_answer("can you do it locally") and local_answer("do it on my GPU instead")
    assert local_answer("yes, but offline") and not local_answer("yes")


def test_a_local_build_really_runs_locally(monkeypatch):
    from core import forge_jobs
    from core.brain import get_brain
    seen = []

    class FakeForge:
        def build(self, request, progress=None):
            seen.append(get_brain().pick("forge"))
            raise RuntimeError("stop here")
    monkeypatch.setattr(forge_jobs, "JOBS_PATH", __import__("pathlib").Path(str(forge_jobs.JOBS_PATH) + ".t"))
    jm = forge_jobs.JobManager(forge_factory=lambda: FakeForge(), threaded=False)
    jm.submit("currency converter", where="local")
    assert seen == ["local"] and jm.jobs[-1].where == "local"
    assert get_brain()._override == {}                        # only that build


def test_the_skill_says_it_builds_locally(monkeypatch):
    import skills.forge.tools as ft
    from core import forge_jobs
    got = []

    class J:
        status, id = "running", "x"

    class M:
        def submit(self, request, where=""):
            got.append(where)
            return J()

        def queued_before(self, job):
            return 0
    monkeypatch.setattr(forge_jobs, "get_jobs", lambda: M())
    out = ft.forge_build(request="currency", where="local")
    assert got == ["local"] and "locally" in out["say"]


# ---------------------------------------------------------------- Spotify with nothing playing
def test_pause_with_nothing_playing_on_spotify_presses_nothing(monkeypatch):
    import core.spotify as sp
    import core.tools_native as tn
    pressed = []

    class Fake:
        def control(self, what):
            raise RuntimeError("no active Spotify device")
    monkeypatch.setattr(sp, "connected", lambda: True)
    monkeypatch.setattr(sp, "get_spotify", lambda: Fake())
    monkeypatch.setattr(tn, "PYAUTOGUI_OK", True)
    monkeypatch.setattr(tn, "pyautogui", type("P", (), {"press": staticmethod(pressed.append)}), raising=False)
    out = tn.tool_media_control(action="pause")
    assert pressed == [] and out["say"] == "Nothing is playing on Spotify, sir."
    tn.tool_media_control(action="next")
    assert pressed == ["nexttrack"]                   # other keys still work for other players


# ---------------------------------------------------------------- the currency skill, rewritten
@pytest.fixture
def fx(monkeypatch):
    from skills.currency_converter import tools
    calls = []

    def fake_get(url, params):
        calls.append((url, dict(params)))
        rates = {"RON": 4.9770, "USD": 1.0412, "GBP": 0.8321}
        want = params.get("symbols", "").split(",") if params.get("symbols") else list(rates)
        return {"amount": 1.0, "base": params.get("base", "EUR"), "date": "2026-11-24",
                "rates": {k: rates[k] for k in want if k in rates}}
    monkeypatch.setattr(tools, "FETCH", fake_get)
    return tools, calls


def test_currency_words_become_codes(fx):
    tools, _ = fx
    for word, code in [("euro", "EUR"), ("Euros", "EUR"), ("€", "EUR"), ("lei", "RON"), ("Ron", "RON"),
                       ("dollars", "USD"), ("$", "USD"), ("pounds", "GBP"), ("usd", "USD")]:
        assert tools.code(word) == code, word


def test_100_euro_in_ron(fx):
    tools, calls = fx
    out = tools.convert_currency(amount=100, from_currency="euro", to_currency="Ron")
    assert calls[0] == ("https://api.frankfurter.dev/v1/latest", {"base": "EUR", "symbols": "RON"})
    assert out["say"] == "100 euros is 497.70 lei, sir, at the European Central Bank rate of 24 November."
    assert json.loads(out["result"])["converted"] == 497.7


def test_an_unknown_currency_is_said_plainly(fx):
    tools, _ = fx
    out = tools.convert_currency(amount=5, from_currency="EUR", to_currency="bitcoin")
    assert "error" in json.loads(out["result"]) and "bitcoin" in out["say"]


def test_a_failing_service_says_what_happened(monkeypatch):
    from skills.currency_converter import tools

    def boom(url, params):
        raise tools.RateError("the rate service answered 503")
    monkeypatch.setattr(tools, "FETCH", boom)
    out = tools.convert_currency(amount=1, from_currency="EUR", to_currency="RON")
    assert out["say"] == "I couldn't get the exchange rate, sir: the rate service answered 503."
