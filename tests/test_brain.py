"""Brain: cloud/local switching for FORGE and deep thinking."""

import json

import httpx
import pytest

import core.brain as br
from core.brain import Brain, LocalUnavailable


@pytest.fixture(autouse=True)
def state(tmp_path, monkeypatch):
    monkeypatch.setattr(br, "STATE", tmp_path / "brain_mode.json")


class Budget:
    def __init__(self, ok=True):
        self.ok = ok

    def can_spend(self, *_):
        return self.ok


class Claude:
    def __init__(self, key=True, budget_ok=True):
        self.key, self.budget, self.calls = key, Budget(budget_ok), []

    def available(self):
        return self.key

    def message(self, **kw):
        self.calls.append(kw)
        return {"text": "cloud answer", "tool_input": {"feasible": True, "name": "x"}, "cost_eur": 0.02}


def local(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_auto_prefers_cloud_and_falls_back_to_local():
    assert Brain(claude=Claude()).pick() == "cloud"
    assert Brain(claude=Claude(key=False)).pick() == "local"
    assert Brain(claude=Claude(budget_ok=False)).pick() == "local"             # out of budget: stay useful


def test_mode_switch_persists_and_overrides_auto():
    b = Brain(claude=Claude())
    assert b.set_mode("local") == "local" and Brain(claude=Claude()).pick() == "local"
    b.set_mode("nonsense")
    assert b.mode() == "auto"


def test_local_code_uses_constrained_json_with_the_schema():
    seen = {}

    def h(req):
        seen.update(json.loads(req.content))
        return httpx.Response(200, json={"message": {"content": json.dumps({"feasible": True, "name": "demo"})}})
    b = Brain(claude=Claude(key=False), http=local(h))
    data, cost, provider = b.code_json("sys", [{"role": "user", "content": "build x"}],
                                       {"name": "write_skill", "input_schema": {"type": "object"}})
    assert data == {"feasible": True, "name": "demo"} and cost == 0 and provider == "qwen2.5-coder:7b"
    assert seen["format"] == {"type": "object"} and seen["options"]["num_ctx"] >= 8192


def test_local_think_retries_without_think_flag_and_strips_tags():
    calls = []

    def h(req):
        body = json.loads(req.content)
        calls.append(body.get("think"))
        if body.get("think"):
            return httpx.Response(400, json={"error": "model does not support thinking"})
        return httpx.Response(200, json={"message": {"content": "<think>hmm</think>Keep it local, sir."}})
    text, cost, provider = Brain(claude=Claude(key=False), http=local(h)).think("sys", "q")
    assert text == "Keep it local, sir." and calls == [True, None] and provider == "qwen3:4b"


def test_missing_local_model_says_how_to_fix_it():
    b = Brain(claude=Claude(key=False), http=local(lambda r: httpx.Response(404, json={"error": "not found"})))
    with pytest.raises(LocalUnavailable, match="ollama pull qwen3:4b"):
        b.think("sys", "q")


def test_confirmation_text_says_where_and_what_it_costs():
    assert "Claude" in Brain(claude=Claude()).describe("code")
    assert "locally" in Brain(claude=Claude(key=False)).describe("code")


def test_voice_switch_fast_path():
    from core.orchestrator_hybrid import ToolBelt, fast_path
    from skills.registry import SkillRegistry
    belt = ToolBelt(SkillRegistry("skills").discover())
    assert fast_path("switch to local mode", belt) == ("set_brain_mode", {"mode": "local"})
    assert fast_path("go back to the cloud", belt) == ("set_brain_mode", {"mode": "cloud"})
    assert fast_path("use auto mode", belt) == ("set_brain_mode", {"mode": "auto"})
    assert fast_path("I think hard about moving to Tilburg", belt) == \
        ("think_deeply", {"question": "moving to Tilburg"})
