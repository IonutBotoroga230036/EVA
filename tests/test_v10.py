"""v1.0: one brain decides every turn. Scripted backends stand in for the local model and Claude; her tools that
would touch the world (calendar, messages, tasks) are fakes. Nothing here reaches Ollama, Claude or Google."""

import asyncio
import json

import httpx
import pytest

import core.orchestrator_hybrid as orch_mod
from core import one
from core.one import journal, made, risk
from core.one.backends import BackendError, CloudBackend, LocalBackend, ScriptedBackend, Step
from core.one.loop import OneBrain, _promises

pytestmark = pytest.mark.one_brain


def call(_tool, **args):
    return {"id": f"c_{_tool}_{len(args)}", "name": _tool, "input": args}


def say(o, text):
    async def go():
        out, events = "", []
        async for ev in o.process_stream(text):
            events.append(ev)
            if ev["type"] == "final":
                out = ev["text"]
        return out, events
    return asyncio.run(go())


class World:
    """Fake calendar, messages and tasks behind her real tool names."""

    def __init__(self):
        self.events = [{"id": "e1", "title": "Standup", "start": "09:00", "end": "09:15"},
                       {"id": "e2", "title": "Review", "start": "09:30", "end": "10:00"},
                       {"id": "e3", "title": "Lunch", "start": "12:00", "end": "13:00"}]
        self.calls = []
        self.sent = []

    def execute(self, name, args):
        self.calls.append((name, dict(args)))
        if name == "calendar_agenda":
            return {"result": json.dumps({"events": self.events}), "say": "Three things today."}
        if name == "calendar_shift":
            def mins(t):
                h, _, mm = str(t).partition(":")
                return int(h) * 60 + int(mm or 0)
            lo, hi, m = args.get("from_time", "0"), args.get("until_time", "23:59"), int(args["minutes"])
            moved, undo = [], []
            for e in self.events:
                if mins(lo) <= mins(e["start"]) < mins(hi):
                    h, mi = map(int, e["start"].split(":"))
                    t = h * 60 + mi + m
                    undo.append({"id": e["id"], "title": e["title"], "start": e["start"], "end": e["end"]})
                    moved.append({"what": e["title"], "before": e["start"], "after": f"{t // 60:02d}:{t % 60:02d}"})
                    e["start"] = f"{t // 60:02d}:{t % 60:02d}"
            return {"result": json.dumps({"moved": len(moved)}), "say": f"Moved {len(moved)} events.",
                    "changes": moved, "undo": {"tool": "calendar_restore", "args": {"events": undo},
                                               "what": "put them back"}}
        if name == "calendar_restore":
            for back in args.get("events") or []:
                for e in self.events:
                    if e["id"] == back["id"]:
                        e["start"] = back["start"]
            return {"result": json.dumps({"restored": len(args.get("events") or [])}), "say": "Put back."}
        if name == "message_reply":
            self.sent.append(args)
            return {"result": json.dumps({"sent": True}), "say": "Sent."}
        if name == "web_search":
            return {"result": json.dumps({"results": [{"title": "NVDA", "snippet": "NVIDIA 177.82 USD", "url": "x"}]})}
        if name == "task_cancel":
            return {"result": json.dumps({"ok": True}), "say": "Stopped the skull task."}
        if name == "lights_power":
            return {"result": json.dumps({"on": args.get("on")}), "say": "Lights off, sir."}
        return {"result": json.dumps({"error": f"{name} isn't in the test world"})}


@pytest.fixture
def eva(tmp_path, monkeypatch):
    from core.memory.cortex import Cortex
    from skills.registry import SkillRegistry
    monkeypatch.setattr(orch_mod, "ack_after", lambda: -1)
    reg = SkillRegistry("skills")
    reg.discover()
    o = orch_mod.HybridOrchestrator(registry=reg)
    o.cortex = Cortex(str(tmp_path / "c.db"))
    o.history = []
    w = World()
    o.belt.execute = w.execute
    o.world = w
    return o


def script(o, steps, kind="cloud"):
    b = ScriptedBackend(steps)
    b.kind = kind
    o.brain().backend_override = [b]
    return b


def test_she_answers_in_her_own_words_and_keeps_a_journal(eva):
    script(eva, [Step(text="Morning, sir. Nothing needs you right now.")])
    reply, events = say(eva, "anything I should know?")
    assert reply == "Morning, sir. Nothing needs you right now."
    rec = journal.recent(1)[0]
    assert rec["said"] == "anything I should know?" and rec["answer"] == reply
    assert rec["brain"] == [{"backend": "cloud", "model": "scripted"}]
    assert any(e["type"] == "journal" for e in events)


def test_a_reversible_change_just_happens_and_can_be_undone(eva):
    """The example from Nov 27: no 'just to confirm', no 'yes go ahead'."""
    script(eva, [Step(calls=[call("calendar_shift", minutes=15, from_time="9", until_time="9:45")]),
                 Step(text="Done, sir: Standup and Review are 15 minutes later now.")])
    reply, events = say(eva, "Look into my calendar and move all the tasks between 9 and 9:45 15 minutes later")
    assert reply.startswith("Done, sir") and "confirm" not in reply.lower()
    assert [e["start"] for e in eva.world.events] == ["09:15", "09:45", "12:00"]
    step = [s for s in journal.recent(1)[0]["steps"] if s["tool"] == "calendar_shift"][0]
    assert step["risk"] == "tell" and step["decision"] == "ran" and step["undo"]
    assert step["changes"][0] == {"what": "Standup", "before": "09:00", "after": "09:15"}
    assert any(e["type"] == "step" and e["data"]["tool"] == "calendar_shift" for e in events)
    out = journal.undo(step["id"], eva.belt.execute)
    assert out["ok"] and [e["start"] for e in eva.world.events] == ["09:00", "09:30", "12:00"]
    assert journal.find_step(step["id"])[2]["undone"] is True


def test_sending_a_message_asks_in_her_words_and_his_yes_sends_it(eva):
    b = script(eva, [Step(calls=[call("message_reply", contact="Muaad", text="Running 10 minutes late")]),
                     Step(text="Shall I tell Muaad you're running ten minutes late?")])
    reply, _ = say(eva, "tell Muaad I'm running 10 minutes late")
    assert reply == "Shall I tell Muaad you're running ten minutes late?" and not eva.world.sent
    assert eva.pending["tool"] == "message_reply" and eva.pending["one"]
    tool_msg = [m for m in b.calls_seen[1]["transcript"] if m["role"] == "tool"][0]
    assert "NOT DONE YET" in tool_msg["text"] and "sends something as you" in tool_msg["text"]
    assert b.calls_seen[1]["tools"] == []              # after asking she only talks
    script(eva, [Step(text="Sent, sir. Muaad knows.")])
    reply, _ = say(eva, "yes")
    assert reply == "Sent, sir. Muaad knows." and eva.world.sent[0]["contact"] == "Muaad"
    rec = journal.recent(1)[0]
    assert rec["steps"][0]["decision"] == "approved"


def test_a_no_leaves_it_undone(eva):
    script(eva, [Step(calls=[call("message_reply", contact="Mom", text="hi")]), Step(text="Send Mom a hi?")])
    say(eva, "say hi to mom")
    b = script(eva, [Step(text="Okay, I won't.")])
    reply, _ = say(eva, "no")
    assert reply == "Okay, I won't." and not eva.world.sent
    assert "he said no" in b.calls_seen[0]["transcript"][-1]["text"]


def test_a_yes_with_a_change_is_approved_for_this_turn(eva):
    script(eva, [Step(calls=[call("message_reply", contact="Mom", text="hi")]), Step(text="Send Mom a hi?")])
    say(eva, "say hi to mom")
    script(eva, [Step(calls=[call("message_reply", contact="Mom", text="hi, love you")]), Step(text="Sent, sir.")])
    reply, _ = say(eva, "yes, but add love you")
    assert reply == "Sent, sir." and eva.world.sent[0]["text"] == "hi, love you"


def test_reflexes_are_instant_and_journaled(eva):
    b = script(eva, [])
    reply, _ = say(eva, "turn off the lights")
    assert reply == "Lights off, sir." and not b.calls_seen
    rec = journal.recent(1)[0]
    assert rec["reflex"] and rec["steps"][0]["tool"] == "lights_power"


def test_a_promise_without_a_tool_is_sent_back_to_fix(eva):
    """Nov 27 19:55: 'Performing the web search for NVIDIA's stock price today, sir.' and nothing ran."""
    b = script(eva, [Step(text="Performing the web search for NVIDIA's stock price today, sir."),
                     Step(calls=[call("web_search", query="NVIDIA stock price")]),
                     Step(text="NVIDIA is at 177.82 dollars, sir.")])
    reply, events = say(eva, "what's the NVIDIA stock price?")
    assert reply == "NVIDIA is at 177.82 dollars, sir."
    assert "you said you'd do something" in b.calls_seen[1]["transcript"][-1]["text"]
    assert not any(e["type"] == "token" and "Performing" in e["text"] for e in events)


def test_a_made_up_number_is_sent_back(eva):
    b = script(eva, [Step(calls=[call("web_search", query="NVIDIA stock price")]),
                     Step(text="NVIDIA is at 125.00 dollars, sir."),
                     Step(text="The search shows 177.82 dollars, sir.")])
    reply, _ = say(eva, "NVIDIA price?")
    assert reply == "The search shows 177.82 dollars, sir."
    assert "125.00" in b.calls_seen[2]["transcript"][-1]["text"]


def test_local_first_hands_a_failing_turn_to_claude(eva):
    local = ScriptedBackend([Step(text="I'll proceed with the task."), Step(text="I'll proceed with it now.")],
                            "qwen3.5:4b")
    local.kind = "local"
    cloud = ScriptedBackend([Step(text="I haven't started anything, sir. Want me to design the skull?")],
                            "claude-haiku-5-5")
    cloud.kind = "cloud"
    eva.brain().backend_override = [local, cloud]
    reply, events = say(eva, "can you 3D print me a skull?")
    assert reply.startswith("I haven't started anything")
    rec = journal.recent(1)[0]
    assert rec["escalated"].startswith("qwen3.5:4b -> cloud")
    assert [b["backend"] for b in rec["brain"]] == ["local", "cloud"]
    assert any(e["type"] == "thinking" and "handing this to Claude" in e["text"] for e in events)


def test_a_broken_local_model_hands_over_before_speaking(eva):
    local = ScriptedBackend([BackendError("Ollama isn't answering")], "qwen3.5:4b")
    local.kind = "local"
    cloud = ScriptedBackend([Step(text="Here, sir.")], "claude-haiku-5-5")
    cloud.kind = "cloud"
    eva.brain().backend_override = [local, cloud]
    reply, _ = say(eva, "hello?")
    assert reply == "Here, sir."


def test_only_local_says_whats_wrong(eva):
    local = ScriptedBackend([BackendError("no local model: run 'ollama pull qwen3.5:4b'")], "qwen3.5:4b")
    local.kind = "local"
    eva.brain().backend_override = [local]
    reply, _ = say(eva, "hello?")
    assert "ollama pull qwen3.5:4b" in reply and "local brain" in reply


def test_actions_already_done_are_not_repeated_after_a_handover(eva):
    local = ScriptedBackend([Step(calls=[call("calendar_shift", minutes=15, from_time="9", until_time="9:45")]),
                             Step(calls=[call("hand_to_cloud", why="he asked something hard too")])], "qwen3.5:4b")
    local.kind = "local"
    cloud = ScriptedBackend([Step(text="Moved, and here's the rest, sir.")], "claude-haiku-5-5")
    cloud.kind = "cloud"
    eva.brain().backend_override = [local, cloud]
    reply, _ = say(eva, "move 9 to 9:45 later by 15 and then explain quantum tunneling")
    assert reply == "Moved, and here's the rest, sir."
    assert [c for c in eva.world.calls if c[0] == "calendar_shift"].__len__() == 1
    first = cloud.calls_seen[0]["transcript"][-1]["text"]
    assert "Already done this turn" in first and "calendar_shift" in first


def test_a_waiting_task_does_not_swallow_stop_the_task(eva, monkeypatch, tmp_path):
    """Nov 27 19:53: four sentences in a row went to the waiting skull task."""
    from core.agent import tasks as tasks_mod
    store = tasks_mod.Store(tmp_path / "tasks")
    monkeypatch.setattr(tasks_mod, "get_store", lambda: store)
    t = store.new("design a 3D skull", 1.0, "skull", "", [])
    t.status = "waiting"
    t.waiting = {"kind": "question", "text": "How detailed should it be?"}
    store.save(t)
    b = script(eva, [])
    reply, _ = say(eva, "Stop the task.")
    assert reply == "Stopped the skull task." and ("task_cancel", {"which": ""}) in eva.world.calls
    assert not b.calls_seen


def test_risk_levels():
    assert risk.judge("calendar_shift", {"minutes": 15})[0] == "tell"
    assert risk.judge("message_reply", {})[0] == "ask"
    assert risk.judge("calendar_delete", {})[1] == "it deletes something"
    assert risk.judge("task_start", {"goal": "x", "budget_euros": 500})[0] == "ask"
    assert risk.judge("task_start", {"goal": "x"})[0] == "tell"
    assert risk.judge("task_start", {"goal": "x", "budget_euros": 2})[0] == "tell"
    assert risk.judge("install_app", {})[0] == "ask"
    assert risk.judge("message_reply", {}, approved={"message_reply"})[0] == "tell"


def test_risk_limit_and_lists_come_from_the_settings(monkeypatch):
    monkeypatch.setattr(risk, "cfg", lambda: {**risk.DEFAULTS, "ask_above_eur": 5.0, "always_ask": ["lights_power"]})
    assert risk.judge("task_start", {"budget_euros": 4})[0] == "tell"
    assert risk.judge("lights_power", {})[0] == "ask"


def test_an_unknown_tool_is_judged_once_and_remembered(monkeypatch):
    seen = []

    def judge(tool, desc, code):
        seen.append(tool)
        return {"level": "free", "why": "reads", "by": "test"}
    monkeypatch.setattr(risk, "_ask_judge", judge)
    assert risk.unknown("weather_station", "reads my weather station") == ("free", "reads")
    assert risk.unknown("weather_station", "reads my weather station") == ("free", "reads")
    assert seen == ["weather_station"]
    assert risk.unknown("post_tweet", "posts")[0] == "ask"       # sending never needs a judge


def test_make_skill_dry_runs_then_runs_for_real(eva):
    code = ('def run(eva, start="09:00", end="09:45", minutes=15):\n'
            '    out = eva.call("calendar_shift", minutes=minutes, from_time=start, until_time=end)\n'
            '    return out.get("say")\n')
    script(eva, [Step(calls=[call("make_skill", name="shift_window", purpose="move events in a window",
                                  code=code, example_args={"start": "09:00", "end": "09:45", "minutes": 15})]),
                 Step(calls=[call("run_skill", name="shift_window", args={"start": "09:00", "end": "09:45"})]),
                 Step(text="Done, sir.")])
    reply, events = say(eva, "push my 9 to 9:45 window back fifteen minutes")
    assert reply == "Done, sir."
    shifts = [c for c in eva.world.calls if c[0] == "calendar_shift"]
    assert len(shifts) == 1                             # the dry run only planned it
    m = made.manifest("shift_window")
    assert m["status"] == "ready" and m["risk"] == "tell" and m["uses"] == 1
    rec = journal.recent(1)[0]
    inner = [s for s in rec["steps"] if s.get("inner")]
    assert inner and inner[0]["tool"] == "calendar_shift" and inner[0]["undo"]
    assert "skill_shift_window" in [t["function"]["name"] for t in made.schemas()]


def test_a_made_skill_that_plans_a_message_asks(eva):
    code = 'def run(eva, who="Mom"):\n    return eva.call("message_reply", contact=who, text="hi")\n'
    b = script(eva, [Step(calls=[call("make_skill", name="greet", purpose="greet someone", code=code,
                                      example_args={"who": "Mom"})]),
                     Step(calls=[call("run_skill", name="greet", args={"who": "Mom"})]),
                     Step(text="Shall I send Mom a hi?")])
    reply, _ = say(eva, "greet mom")
    assert not eva.world.sent and eva.pending["tool"] == "run_skill"
    assert made.manifest("greet")["risk"] == "ask"


def test_bad_skill_code_is_refused():
    assert any("import os" in p for p in made.check("import os\ndef run(eva):\n    return 1\n"))
    assert any("open" in p for p in made.check("def run(eva):\n    return open('x')\n"))
    assert any("_" in p for p in made.check("def run(eva):\n    return ().__class__\n"))
    assert made.check("def run(eva, n=2):\n    return n * 2\n") == []
    assert "there must be a top-level def run(eva, ...)" in made.check("x = 1\n")


def test_undo_by_voice(eva):
    script(eva, [Step(calls=[call("calendar_shift", minutes=15, from_time="9", until_time="9:45")]),
                 Step(text="Moved them.")])
    say(eva, "move 9 to 9:45 fifteen later")
    script(eva, [Step(calls=[call("undo", step="last")]), Step(text="Put them back, sir.")])
    reply, _ = say(eva, "undo that")
    assert reply == "Put them back, sir." and eva.world.events[0]["start"] == "09:00"


def test_what_i_did_reads_the_journal(eva):
    script(eva, [Step(calls=[call("calendar_shift", minutes=15, from_time="9", until_time="9:45")]),
                 Step(text="Moved them.")])
    say(eva, "move 9 to 9:45 fifteen later")
    from core.one.tools import tool_what_i_did
    text = json.loads(tool_what_i_did("today")["result"])["changes"]
    assert "calendar_shift" in text and "Standup: 09:00 -> 09:15" in text


def test_change_setting_raises_the_daily_limit_with_an_undo():
    from core.budget import get_budget
    from core.one.tools import tool_change_setting
    out = tool_change_setting("budget.daily_limit_euros", "5")
    assert json.loads(out["result"])["now"] == 5 and get_budget().daily_limit == 5.0
    assert out["undo"]["args"] == {"key": "budget.daily_limit_euros", "value": 1.5}


def test_find_tools_adds_tools_for_the_local_model(eva):
    local = ScriptedBackend([Step(calls=[call("find_tools", need="shopping list items")]), Step(text="Okay.")],
                            "qwen3.5:4b")
    local.kind = "local"
    eva.brain().backend_override = [local]
    say(eva, "hmm")
    assert "shopping_add" in local.calls_seen[1]["tools"]


def test_the_local_model_sees_a_small_menu_and_claude_all(eva):
    o = eva.brain()
    small = o.tools_for("local", "move my calendar events")
    big = o.tools_for("cloud", "move my calendar events")
    names = [t["function"]["name"] for t in small if "function" in t]
    assert len(small) < 45 and "make_skill" in names and "web_search" in names
    assert len(big) > 100 and not any(t.get("function", {}).get("name") in one.HIDDEN for t in big)


def test_promises():
    assert _promises("Performing the web search for NVIDIA's stock price today, sir.")
    assert _promises("Understood, sir. I'll proceed with the task within your budget.")
    assert not _promises("Shall I search for it?")
    assert not _promises("NVIDIA is at 177 dollars.")


# ---------------------------------------------------------------- backends
def test_local_backend_streams_thinking_text_and_tool_calls():
    lines = [{"message": {"thinking": "He wants the price. "}}, {"message": {"thinking": "Search."}},
             {"message": {"content": "", "tool_calls": [{"function": {"name": "web_search",
                                                                      "arguments": {"query": "NVDA"}}}]}},
             {"message": {"content": ""}, "done": True}]
    seen = {}

    def handler(req):
        if req.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "qwen3.5:4b"}]})
        seen.update(json.loads(req.content))
        return httpx.Response(200, text="\n".join(json.dumps(x) for x in lines))
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    b = LocalBackend("http://x", "qwen3.5:4b", "qwen2.5:3b-instruct", http=http)
    thoughts = []
    st = asyncio.run(b.step("sys", [{"role": "user", "text": "price?"}], [{"type": "function", "function": {
        "name": "web_search", "parameters": {}}}], on_thinking=thoughts.append))
    assert st.thinking == "He wants the price. Search." and thoughts == ["He wants the price. ", "Search."]
    assert st.calls[0]["name"] == "web_search" and st.calls[0]["input"] == {"query": "NVDA"}
    assert seen["think"] is True and seen["tools"][0]["function"]["name"] == "web_search"
    assert seen["messages"][0] == {"role": "system", "content": "sys"}


def test_local_backend_falls_back_until_the_model_is_pulled():
    def handler(req):
        if req.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "qwen2.5:3b-instruct"}]})
        body = json.loads(req.content)
        return httpx.Response(200, text=json.dumps({"message": {"content": body["model"]}, "done": True}))
    b = LocalBackend("http://x", "qwen3.5:4b", "qwen2.5:3b-instruct",
                     http=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    st = asyncio.run(b.step("s", [{"role": "user", "text": "hi"}], []))
    assert st.text == "qwen2.5:3b-instruct" and b.think is False


def test_local_messages_replay_tool_calls_and_results():
    msgs = LocalBackend.messages("s", [
        {"role": "user", "text": "hi"},
        {"role": "assistant", "text": "", "thinking": "t", "calls": [{"id": "a", "name": "web_search",
                                                                       "input": {"query": "q"}}]},
        {"role": "tool", "id": "a", "name": "web_search", "text": "{}"}])
    assert msgs[2]["tool_calls"][0]["function"] == {"index": 0, "name": "web_search", "arguments": {"query": "q"}}
    assert msgs[2]["thinking"] == "t" and msgs[3] == {"role": "tool", "tool_name": "web_search", "content": "{}"}


def test_cloud_messages_group_tool_results():
    msgs = CloudBackend.messages([
        {"role": "user", "text": "hi"},
        {"role": "assistant", "text": "Checking.", "calls": [{"id": "a", "name": "x", "input": {}},
                                                             {"id": "b", "name": "y", "input": {}}]},
        {"role": "tool", "id": "a", "name": "x", "text": "1"},
        {"role": "tool", "id": "b", "name": "y", "text": "2", "is_error": True}])
    assert [m["role"] for m in msgs] == ["user", "assistant", "user"]
    assert [b["type"] for b in msgs[1]["content"]] == ["text", "tool_use", "tool_use"]
    assert msgs[2]["content"][1] == {"type": "tool_result", "tool_use_id": "b", "content": "2", "is_error": True}


# ---------------------------------------------------------------- the fixes around it
def test_web_search_tries_other_engines(monkeypatch):
    from core import tools_native as tn
    tried = []

    def fake(query, backend="auto"):
        tried.append(backend)
        if backend in ("auto", "duckduckgo,brave"):
            raise RuntimeError("No results found.")
        return [{"title": "NVDA", "snippet": "177.82", "url": "https://x"}]
    monkeypatch.setattr(tn, "_search", fake)
    monkeypatch.setattr(tn, "SEARCH_OK", True)
    out = json.loads(tn.tool_web_search("NVIDIA stock price")["result"])
    assert out["results"][0]["snippet"] == "177.82" and tried == ["auto", "duckduckgo,brave", "bing,mojeek"]


def test_classic_waiting_task_lets_other_requests_through():
    assert orch_mod.not_a_task_answer("Raise the daily limit to 5 Euros")
    assert orch_mod.not_a_task_answer("dropped the task")
    assert orch_mod.not_a_task_answer("How much do you need?")
    assert not orch_mod.not_a_task_answer("about 30 cm tall, electric")
    assert not orch_mod.not_a_task_answer("yes")


def test_budget_pause_names_test_mode(monkeypatch):
    from core import testmode
    from core.agent.runner import _budget_words
    from core.budget import get_budget
    testmode.set_mode(True)
    try:
        why = get_budget().why_not(0.19)
        assert "test mode is on" in why
        from core.agent.tasks import Task
        t = Task(id="t1", goal="g", title="skull")
        words = _budget_words(t, RuntimeError(f"this call could cost up to EUR 0.190; the budget doesn't allow it "
                                              f"(EUR 4.39 left): {why}"))
        assert "test mode is on" in words and "439 cents" not in words
    finally:
        testmode.set_mode(False)


def test_task_names_drop_the_glue():
    from core.agent.tasks import short_title
    assert short_title("Design and 3D print a skull") == "skull"
    assert short_title("can you design me a 3D skull?") == "3D skull"


def test_task_start_takes_her_title(monkeypatch):
    from core.agent import tools as agent_tools
    started = {}

    class M:
        def start(self, goal, budget, title, project):
            started.update(goal=goal, title=title)

            class T:
                id, title_ = "t1", title
            T.title = title
            return T
    monkeypatch.setattr(agent_tools, "_m", lambda: M())
    agent_tools.tool_task_start(goal="design a skull to 3D print", title="3D Skull Model")
    assert started["title"] == "3d skull model"


def test_classic_mode_still_routes_the_old_way(monkeypatch, eva):
    monkeypatch.setattr(one, "mode", lambda: "classic")
    b = script(eva, [Step(text="should not be used")])
    reply, _ = say(eva, "turn off the lights")
    assert reply == "Lights off, sir." and not b.calls_seen


def test_the_cloud_backend_talks_to_claude_the_right_way(eva, monkeypatch):
    """The real CloudBackend with the Claude client faked at message(): Claude-format tools, cache, purpose."""
    from core.one.backends import CloudBackend
    seen = []

    class FakeClaude:
        def available(self):
            return True

        def message(self, **kw):
            seen.append(kw)
            if len(seen) == 1:
                return {"text": "Let me look.", "thinking": "calendar first", "cost_eur": 0.002, "content": [
                    {"type": "text", "text": "Let me look."},
                    {"type": "tool_use", "id": "toolu_1", "name": "calendar_agenda", "input": {"day": "today"}}],
                        "tool_uses": [{"id": "toolu_1", "name": "calendar_agenda", "input": {"day": "today"}}]}
            return {"text": "Standup at 9, sir.", "thinking": "", "cost_eur": 0.001, "content": [
                {"type": "text", "text": "Standup at 9, sir."}], "tool_uses": []}
    eva.brain().backend_override = [CloudBackend("claude-haiku-5-5", "low", claude=FakeClaude())]
    reply, events = say(eva, "what's on today?")
    assert reply == "Standup at 9, sir."
    first, second = seen
    assert first["purpose"] == "brain" and first["cache"] is True and first["model"] == "claude-haiku-5-5"
    names = [t.get("name") for t in first["tools"]]
    assert "calendar_agenda" in names and "make_skill" in names and "think_harder" in names
    assert all("input_schema" in t for t in first["tools"] if t.get("name"))
    assert second["messages"][-1]["content"][0]["type"] == "tool_result"
    assert second["messages"][-2]["content"][1]["type"] == "tool_use"       # her own blocks replayed
    assert any(e["type"] == "token" and "Let me look." in e["text"] for e in events)   # the lead-in was spoken
    rec = journal.recent(1)[0]
    assert rec["cost_eur"] == 0.003 and rec["thinking"].startswith("calendar first")


def test_after_asking_claude_keeps_the_tools_but_uses_none():
    """Claude refuses tool blocks without tool definitions: after a 'needs his yes' she talks with tool_choice none."""
    from core.one.backends import CloudBackend
    seen = []

    class FakeClaude:
        def available(self):
            return True

        def message(self, **kw):
            seen.append(kw)
            return {"text": "Shall I?", "cost_eur": 0.0, "content": [{"type": "text", "text": "Shall I?"}],
                    "tool_uses": []}
    b = CloudBackend(claude=FakeClaude())
    tools = [{"type": "function", "function": {"name": "message_reply", "parameters": {"type": "object"}}}]
    asyncio.run(b.step("s", [{"role": "user", "text": "hi"}], tools, text_only=True))
    assert seen[0]["tool_choice"] == {"type": "none"} and seen[0]["tools"][0]["name"] == "message_reply"
    asyncio.run(b.step("s", [{"role": "user", "text": "hi"}], tools))
    assert seen[1]["tool_choice"] is None
