"""Nov 23 (afternoon): testing her should cost nothing twice. The same sentence in the same situation reuses the
cloud decision; test mode caps cloud spending at a few cents and logs what each turn cost."""

import asyncio
import json

import pytest

import core.understand.brains as U
from core import testmode
from core.budget import get_budget
from core.understand import cache

SCHEMAS = [
    {"type": "function", "function": {"name": "calendar_free", "description": "Find free time in the calendar.",
                                      "parameters": {"type": "object", "properties": {"day": {"type": "string"}}}}},
    {"type": "function", "function": {"name": "message_reply", "description": "Reply to a message.",
                                      "parameters": {"type": "object", "properties": {
                                          "to": {"type": "string"}, "text": {"type": "string"}}}}},
    {"type": "function", "function": {"name": "lights_power", "description": "Lights on or off.",
                                      "parameters": {"type": "object", "properties": {"on": {"type": "boolean"}}}}},
]


class FakeClaude:
    def __init__(self, name="calendar_free", args=None):
        self.name, self.args, self.calls = name, args or {}, []

    def available(self):
        return True

    def message(self, **kw):
        self.calls.append(kw)
        return {"tool_input": self.args, "tool_name": self.name, "cost_eur": 0.0001}


def decide(messages, claude, monkeypatch, schemas=SCHEMAS):
    monkeypatch.setattr(U, "cloud_allowed", lambda: True)

    async def legacy():
        return {"tool": "none"}

    async def fill(tool):
        return {}
    return asyncio.run(U.understand(messages, schemas, legacy, fill, mode="auto", claude=claude))


def user(text):
    return {"role": "user", "content": text}


def bot(text):
    return {"role": "assistant", "content": text}


# ---------------------------------------------------------------- the cache
def test_the_same_sentence_costs_one_cloud_call(monkeypatch):
    claude = FakeClaude("calendar_free", {"day": "tomorrow"})
    first = decide([user("Any empty slots tomorrow?")], claude, monkeypatch)
    again = decide([bot("Lights off, sir."), user("any empty slots tomorrow")], claude, monkeypatch)
    assert len(claude.calls) == 1
    assert first["_by"] == "claude" and again["_by"] == "cache"
    assert again["tool"] == "calendar_free" and again["day"] == "tomorrow" and again["_trusted"] is True


def test_wake_words_and_punctuation_dont_matter(monkeypatch):
    claude = FakeClaude("lights_power", {"on": False})
    decide([user("Turn off the lights.")], claude, monkeypatch)
    decide([user("Hey Eva, turn off the lights")], claude, monkeypatch)
    assert len(claude.calls) == 1


def test_a_follow_up_depends_on_what_came_before(monkeypatch):
    claude = FakeClaude("calendar_free", {"day": "tomorrow"})
    decide([user("weather in Tilburg"), bot("It's 8 degrees, sir."), user("and tomorrow?")], claude, monkeypatch)
    decide([user("my calendar today"), bot("Two events, sir."), user("and tomorrow?")], claude, monkeypatch)
    assert len(claude.calls) == 2                      # "and tomorrow?" means different things after each


def test_new_or_changed_tools_start_fresh(monkeypatch):
    claude = FakeClaude("calendar_free", {"day": "tomorrow"})
    decide([user("any empty slots tomorrow")], claude, monkeypatch)
    more = SCHEMAS + [{"type": "function", "function": {"name": "train_times", "description": "NS trains.",
                                                         "parameters": {"type": "object", "properties": {}}}}]
    decide([user("any empty slots tomorrow")], claude, monkeypatch, schemas=more)
    assert len(claude.calls) == 2


def test_personal_text_is_never_written_to_disk(monkeypatch):
    claude = FakeClaude("message_reply", {"to": "Mom", "text": "I'm on my way"})
    decide([user("tell Mom I'm on my way")], claude, monkeypatch)
    decide([user("tell Mom I'm on my way")], claude, monkeypatch)
    assert len(claude.calls) == 1                      # remembered while she runs
    assert "on my way" not in (cache.PATH.read_text(encoding="utf-8") if cache.PATH.exists() else "")


def test_the_disk_cache_survives_a_restart_and_expires(monkeypatch):
    claude = FakeClaude("calendar_free", {"day": "tomorrow"})
    decide([user("any empty slots tomorrow")], claude, monkeypatch)
    assert cache.PATH.exists()
    monkeypatch.setattr(cache, "_cache", None)          # a restart
    decide([user("any empty slots tomorrow")], claude, monkeypatch)
    assert len(claude.calls) == 1
    monkeypatch.setattr(cache, "_cache", None)
    data = json.loads(cache.PATH.read_text(encoding="utf-8"))
    for v in data.values():
        v["t"] -= 8 * 86400                             # older than the 7 days it keeps
    cache.PATH.write_text(json.dumps(data), encoding="utf-8")
    decide([user("any empty slots tomorrow")], claude, monkeypatch)
    assert len(claude.calls) == 2


def test_dates_written_out_are_not_reused(monkeypatch):
    claude = FakeClaude("calendar_free", {"day": "2026-11-24"})
    decide([user("any empty slots tomorrow")], claude, monkeypatch)
    decide([user("any empty slots tomorrow")], claude, monkeypatch)
    assert len(claude.calls) == 2                      # 'tomorrow' as a date is only true today


def test_the_cache_can_be_switched_off(monkeypatch):
    real = U.cfg
    monkeypatch.setattr(U, "cfg", lambda: {**real(), "cache": {"enabled": False}})
    claude = FakeClaude("lights_power", {"on": True})
    decide([user("lights on")], claude, monkeypatch)
    decide([user("lights on")], claude, monkeypatch)
    assert len(claude.calls) == 2


def test_local_only_never_reads_the_cache(monkeypatch):
    claude = FakeClaude("lights_power", {"on": True})
    decide([user("lights on")], claude, monkeypatch)
    monkeypatch.setattr(U, "cloud_allowed", lambda: False)

    async def legacy():
        return {"tool": "lights_power", "on": True}

    async def fill(tool):
        return {}
    d = asyncio.run(U.understand([user("lights on")], SCHEMAS, legacy, fill, mode="auto", claude=claude))
    assert d["_by"] == "local"


# ---------------------------------------------------------------- test mode
def test_test_mode_caps_cloud_spending_and_reports_it():
    b = get_budget()
    b.record_usage("claude-haiku-5-5", 400000, 0, purpose="before")       # spent before test mode: not counted
    assert b.can_spend()
    say = testmode.tool_test_mode(on=True)["say"]
    assert "Test mode on" in say and "10 cents" in say
    assert b.can_spend()
    b.record_usage("claude-sonnet-5", 20000, 2000, purpose="understand")  # about 9 cents
    assert b.can_spend(0.0) and not b.can_spend(0.02)
    b.record_usage("claude-sonnet-5", 10000, 1000, purpose="understand")
    assert not b.can_spend()                                              # cloud pauses, local carries on
    off = testmode.tool_test_mode(on=False)["say"]
    assert "Test mode off" in off and "2 cloud calls" in off and b.can_spend()


def test_test_mode_cap_can_be_set_by_voice():
    out = testmode.tool_test_mode(on=True, cap_euros=0.5)
    assert "50 cents" in out["say"] and testmode.state()["cap"] == 0.5


@pytest.mark.parametrize("text,on", [("test mode on", True), ("Eva, start test mode", True),
                                     ("turn off test mode", False), ("stop testing mode", False),
                                     ("end test mode", False)])
def test_test_mode_by_voice(text, on):
    from core.orchestrator_hybrid import ToolBelt, fast_path
    assert fast_path(text, ToolBelt()) == ("test_mode", {"on": on})


def test_each_turn_logs_its_cost():
    from core.orchestrator_hybrid import cost_snapshot, turn_cost
    b = get_budget()
    before = cost_snapshot(b)
    b.record_usage("claude-haiku-5-5", 3000, 50, purpose="understand")
    line = turn_cost(b, before)
    assert line.startswith("COST turn: EUR 0.000") and "1 cloud call (understand)" in line
    assert turn_cost(b, cost_snapshot(b)) == ""                           # nothing spent, nothing said
    testmode.set_mode(True)
    assert turn_cost(b, cost_snapshot(b)) == "COST turn: free"            # in test mode, free turns say so too
