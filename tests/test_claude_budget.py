import json

import httpx
import pytest

from core.budget import BudgetExceeded, BudgetTracker
from core.claude import ClaudeClient, ClaudeUnavailable


def budget(tmp_path, **kw):
    return BudgetTracker(track_file=str(tmp_path / "b.json"), **kw)


def test_local_models_are_free_and_claude_is_priced(tmp_path):
    b = budget(tmp_path)
    assert b.estimate_eur("qwen2.5:3b-instruct", 50_000, 50_000) == 0
    assert 0.1 < b.estimate_eur("claude-sonnet-5", 6000, 8000) < 0.2
    assert b.price_for("claude-haiku-4-5-20251001")["input"] == 0.001


def test_monthly_cap_blocks_even_when_today_is_fine(tmp_path):
    b = budget(tmp_path, daily_limit_euros=100, monthly_limit_euros=1.0)
    b._data["days"]["1999-01-01"] = {"total_euros": 5, "calls": []}          # other months don't count
    assert b.can_spend(0.5)
    b.record_usage("claude-sonnet-5", 100_000, 50_000)                        # about EUR 0.97
    assert not b.can_spend(0.1)


def mock(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_request_shape_usage_and_audit(tmp_path):
    seen = {}

    def h(req):
        seen["headers"], seen["body"] = req.headers, json.loads(req.content)
        return httpx.Response(200, json={"content": [{"type": "thinking", "thinking": "hmm"},
                                                     {"type": "text", "text": "Yes, sir."}],
                                         "usage": {"input_tokens": 1000, "output_tokens": 200}})
    b = budget(tmp_path)
    c = ClaudeClient(api_key="k", budget=b, http=mock(h))
    out = c.message(system="s", messages=[{"role": "user", "content": "q"}], thinking_budget=2000, purpose="t")
    assert seen["headers"]["x-api-key"] == "k" and seen["headers"]["anthropic-version"]
    assert seen["body"]["thinking"] == {"type": "adaptive"}            # Claude 5: adaptive only
    assert seen["body"]["output_config"] == {"effort": "medium"}
    assert seen["body"]["max_tokens"] >= 6000                            # thinking counts toward max_tokens
    assert out["text"] == "Yes, sir." and out["thinking"] == "hmm"
    assert b.today_summary()["num_calls"] == 1 and b.today_summary()["spent"] > 0


def test_thinking_rejection_retries_without_it(tmp_path):
    calls = []

    def h(req):
        body = json.loads(req.content)
        calls.append("thinking" in body)
        if "thinking" in body:
            return httpx.Response(400, json={"error": {"message": "thinking not supported"}})
        return httpx.Response(200, json={"content": [{"type": "text", "text": "ok"}], "usage": {}})
    c = ClaudeClient(api_key="k", budget=budget(tmp_path), http=mock(h))
    assert c.message(system="s", messages=[{"role": "user", "content": "q"}], thinking_budget=2000)["text"] == "ok"
    assert calls == [True, False]


def test_forced_tool_returns_structured_input(tmp_path):
    def h(req):
        assert json.loads(req.content)["tool_choice"] == {"type": "tool", "name": "w"}
        return httpx.Response(200, json={"content": [{"type": "tool_use", "name": "w", "input": {"a": 1}}], "usage": {}})
    c = ClaudeClient(api_key="k", budget=budget(tmp_path), http=mock(h))
    assert c.message(system="s", messages=[{"role": "user", "content": "q"}], tools=[{"name": "w"}],
                     tool_choice={"type": "tool", "name": "w"})["tool_input"] == {"a": 1}


def test_no_key_and_no_budget_refuse_before_sending(tmp_path):
    sent = []
    c = ClaudeClient(api_key="", budget=budget(tmp_path), http=mock(lambda r: sent.append(1)))
    with pytest.raises(ClaudeUnavailable):
        c.message(system="s", messages=[{"role": "user", "content": "q"}])
    broke = ClaudeClient(api_key="k", budget=budget(tmp_path, daily_limit_euros=0.0001), http=mock(lambda r: sent.append(1)))
    with pytest.raises(BudgetExceeded):
        broke.message(system="s", messages=[{"role": "user", "content": "q"}], max_tokens=8000)
    assert sent == []                                                     # nothing left the machine
