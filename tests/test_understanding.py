"""v0.3.1 understanding step: Jev picks, Claude decides when Jev isn't sure, local is the fallback, and a sure
decision isn't blocked by word guards. Nothing here reaches a real service."""

import asyncio
import json
import sqlite3
import time
from datetime import datetime

import httpx
import pytest

import core.orchestrator_hybrid as orch_mod
import core.spotify as sp
import core.understand.brains as U
from core.memory.cortex import Cortex
from core.understand import cases as cases_mod
from core.understand import eval as eval_mod
from core.understand.jev import Jev, JevError
from skills.registry import SkillRegistry

REAL_ASYNC = httpx.AsyncClient          # the turn tests patch httpx.AsyncClient for Ollama; Jev keeps its own

SCHEMAS = [
    {"type": "function", "function": {"name": "calendar_free", "description": "Find free time in the calendar.",
                                      "parameters": {"type": "object", "properties": {"day": {"type": "string"}}}}},
    {"type": "function", "function": {"name": "get_datetime", "description": "The time.",
                                      "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "mcp.weird/name", "description": "An MCP tool.",
                                      "parameters": {"properties": {}}}},
]


def jev_reply(choice, conf, probs=None):
    return {"model": "jev-1.14.0", "answers": {"action": {"type": "choice", "choice": choice, "confidence": conf,
                                                         "probabilities": probs or {choice: conf}}}}


def fake_jev(choice="calendar_free", conf=0.95, status=200, seen=None):
    def handler(req):
        if seen is not None:
            seen.append(req)
        if status != 200:
            return httpx.Response(status, text="nope")
        return httpx.Response(200, json=jev_reply(choice, conf))
    return Jev(provider="typesafe", api_key="k", http=REAL_ASYNC(transport=httpx.MockTransport(handler)))


class FakeClaude:
    def __init__(self, name="calendar_free", args=None, fail=False):
        self.name, self.args, self.fail, self.calls = name, args or {}, fail, []

    def available(self):
        return True

    def message(self, **kw):
        self.calls.append(kw)
        if self.fail:
            raise RuntimeError("Claude API error 529: overloaded")
        return {"tool_input": self.args, "tool_name": self.name, "cost_eur": 0.0004}


# ---------------------------------------------------------------- Jev client
def test_jev_request_and_answer_shape():
    seen = []
    j = fake_jev("calendar_free", 0.93, seen=seen)
    choice, conf, probs = asyncio.run(j.choose("Latest message: any gaps tomorrow?", "Which tool?",
                                               U.catalog(SCHEMAS)))
    assert (choice, conf) == ("calendar_free", 0.93)
    req = seen[0]
    body = json.loads(req.content)
    assert str(req.url) == "https://api.typesafe.ai/v1/systemone" and req.headers["authorization"] == "Bearer k"
    assert body["model"] == "jev-latest" and body["questions"]["action"]["type"] == "choice"
    assert {"calendar_free", "none", "unavailable"} <= set(body["questions"]["action"]["criteria"])


def test_jev_openrouter_route_and_no_key():
    j = Jev(provider="openrouter", api_key="k")
    assert j.url == "https://openrouter.ai/api/alpha/decisions" and j.model.startswith("typesafe/jev")
    with pytest.raises(JevError):
        asyncio.run(Jev(provider="typesafe", api_key="").ask("s", {}))
    with pytest.raises(JevError):
        asyncio.run(fake_jev(status=401).choose("s", "q", {"a": "b"}))


def test_transcript_keeps_the_latest_apart():
    convo, latest = U.transcript([{"role": "user", "content": "weather in Breda"},
                                  {"role": "assistant", "content": "7 degrees, sir."},
                                  {"role": "user", "content": "and tomorrow?"}])
    assert latest == "and tomorrow?" and convo == "User: weather in Breda\nE.V.A.: 7 degrees, sir."


def test_claude_tool_names_are_safe_and_map_back():
    tools, back = U.claude_tools(SCHEMAS)
    names = [t["name"] for t in tools]
    assert "mcp_weird_name" in names and back["mcp_weird_name"] == "mcp.weird/name"
    assert back["just_answer"] == "none" and back["cannot_do_yet"] == "unavailable"
    assert all(t["input_schema"]["type"] == "object" for t in tools)
    assert tools[-1].get("cache_control") == {"type": "ephemeral"}


# ---------------------------------------------------------------- the policy
MSGS = [{"role": "user", "content": "check my calendar for any empty slots for tomorrow"}]


def run_policy(mode, jev=None, claude=None, allowed=True, monkeypatch=None):
    calls = {"legacy": 0, "fill": []}

    async def legacy():
        calls["legacy"] += 1
        return {"tool": "none"}

    async def fill(tool):
        calls["fill"].append(tool)
        return {"day": "tomorrow"}
    if monkeypatch:
        monkeypatch.setattr(U, "cloud_allowed", lambda: allowed)
    d = asyncio.run(U.understand(MSGS, SCHEMAS, legacy, fill, mode=mode, jev=jev, claude=claude))
    return d, calls


def test_auto_jev_sure_is_trusted_and_fills_details(monkeypatch):
    claude = FakeClaude()
    d, calls = run_policy("auto", jev=fake_jev("calendar_free", 0.95), claude=claude, monkeypatch=monkeypatch)
    assert d["tool"] == "calendar_free" and d["_trusted"] and d["day"] == "tomorrow"
    assert calls["fill"] == ["calendar_free"] and claude.calls == [] and calls["legacy"] == 0


def test_auto_jev_unsure_asks_claude(monkeypatch):
    claude = FakeClaude("calendar_free", {"day": "tomorrow"})
    d, calls = run_policy("auto", jev=fake_jev("get_datetime", 0.41), claude=claude, monkeypatch=monkeypatch)
    assert d["tool"] == "calendar_free" and d["_by"] == "claude" and d["_trusted"]
    assert claude.calls[0]["tool_choice"] == {"type": "any"}          # forced call: answers without thinking first


def test_auto_falls_back_step_by_step(monkeypatch):
    d, calls = run_policy("auto", jev=fake_jev(status=500), claude=FakeClaude(fail=True), monkeypatch=monkeypatch)
    assert d["_by"] == "local-fallback" and calls["legacy"] == 1


def test_local_only_never_leaves_the_pc(monkeypatch):
    claude = FakeClaude()
    seen = []
    d, calls = run_policy("auto", jev=fake_jev(seen=seen), claude=claude, allowed=False, monkeypatch=monkeypatch)
    assert calls["legacy"] == 1 and seen == [] and claude.calls == []


def test_brain_mode_controls_cloud_understanding():
    from core.brain import get_brain
    assert U.cloud_allowed() is False                       # shipped default: local
    get_brain().set_feature("understanding", "auto")
    assert U.cloud_allowed() is True
    get_brain().set_feature("understanding", "default")
    get_brain().set_mode("cloud")
    assert U.cloud_allowed() is True


def test_saying_local_only_beats_the_settings_file(monkeypatch):
    from core.brain import get_brain
    import core.brain as brain_mod
    real = brain_mod.Brain._cfg
    monkeypatch.setattr(brain_mod.Brain, "_cfg", lambda self: {**real(self), "features": {"understanding": "auto"}})
    assert U.cloud_allowed() is True                        # settings.local.yaml: understanding auto
    get_brain().set_mode("local")                           # "Eva, local only"
    assert U.cloud_allowed() is False
    get_brain().set_feature("understanding", "auto")        # "Eva, use auto for understanding" wins again
    assert U.cloud_allowed() is True


def test_jev_mode_acts_even_when_unsure_but_keeps_the_guards(monkeypatch):
    d, _ = run_policy("jev", jev=fake_jev("calendar_free", 0.5), monkeypatch=monkeypatch)
    assert d["tool"] == "calendar_free" and d["_trusted"] is False


# ---------------------------------------------------------------- in a real turn
@pytest.fixture
def eva(tmp_path, monkeypatch):
    ran = []

    async def aexecute(self, name, args):
        ran.append((name, dict(args)))
        return {"result": json.dumps({"ok": True}), "say": "You're free from 14:00, sir.", "exact": True}

    def handler(req):
        body = json.loads(req.content)
        props = (body.get("format") or {}).get("properties", {})
        if "steps" in props:
            return httpx.Response(200, json={"message": {"content": json.dumps({"steps": []})}})
        if "facts" in props:
            return httpx.Response(200, json={"message": {"content": json.dumps({"facts": []})}})
        if "tool" in props:                                   # the old 3B decision: it gets this one wrong
            return httpx.Response(200, json={"message": {"content": json.dumps({"tool": "none"})}})
        if body.get("format"):                                # one tool's details
            return httpx.Response(200, json={"message": {"content": json.dumps({"day": "tomorrow"})}})
        return httpx.Response(200, text=json.dumps({"message": {"content": "ok"}, "done": True}))
    real = httpx.AsyncClient
    monkeypatch.setattr(orch_mod.ToolBelt, "aexecute", aexecute)
    monkeypatch.setattr(orch_mod.httpx, "AsyncClient", lambda *a, **k: real(transport=httpx.MockTransport(handler)))
    monkeypatch.setattr(U, "cloud_allowed", lambda: True)
    o = orch_mod.HybridOrchestrator(session_id="u", cortex=Cortex(str(tmp_path / "c.db")),
                                    registry=SkillRegistry("skills").discover())
    return o, ran


def turn(o, text):
    async def go():
        evs = [e async for e in o.process_stream(text)]
        if o._bg:
            await asyncio.gather(*o._bg, return_exceptions=True)
        return [e for e in evs if e["type"] == "final"][-1]["text"]
    return asyncio.run(go())


def test_a_sure_decision_is_not_blocked_by_word_guards(eva, monkeypatch):
    o, ran = eva
    monkeypatch.setattr(U, "get_jev", lambda: fake_jev("calendar_free", 0.96))
    o.understand_mode = "auto"
    turn(o, "is tomorrow looking open for me")                 # no 'free', no 'slot': the old guard said no
    assert ran == [("calendar_free", {"day": "tomorrow"})]


def test_legacy_mode_is_unchanged(eva):
    o, ran = eva
    turn(o, "is tomorrow looking open for me")
    assert ran == []                                           # the 3B said none, as before


def test_sure_nothing_fits_offers_to_build_it(eva, monkeypatch):
    o, ran = eva
    monkeypatch.setattr(U, "get_jev", lambda: fake_jev("unavailable", 0.97))
    o.understand_mode = "auto"
    text = turn(o, "order me a pizza")                         # no 'can you' words: the old hint regex missed it
    assert ran == [] and o.last_missing == "order me a pizza" and "skill" in text.lower()


# ---------------------------------------------------------------- queue my music, free slots
class LikedAPI:
    def __init__(self, n=25):
        self.calls, self.n = [], n

    def handler(self, req):
        path = req.url.path.replace("/v1", "")
        self.calls.append((req.method, path, dict(req.url.params)))
        if path == "/me/tracks":
            return httpx.Response(200, json={"items": [{"track": {"uri": f"spotify:track:{i}"}} for i in range(self.n)]})
        return httpx.Response(204)


def spotify(api):
    return sp.Spotify({"access_token": "a", "refresh_token": "r", "expires_at": 9e12},
                      http=httpx.Client(transport=httpx.MockTransport(api.handler)))


@pytest.mark.parametrize("what", ["my music", "my liked songs", "music", "some of my favourites"])
def test_queue_my_music_queues_ten_liked_songs(what):
    api = LikedAPI()
    assert spotify(api).queue(what) == "10 of your liked songs"
    queued = [q["uri"] for m, p, q in api.calls if p == "/me/player/queue"]
    assert len(queued) == 10 == len(set(queued))


def test_queue_with_few_liked_songs_and_with_none():
    assert spotify(LikedAPI(3)).queue("my music") == "3 of your liked songs"
    with pytest.raises(LookupError):
        spotify(LikedAPI(0)).queue("my music")


def test_free_time_words_pass_the_guard():
    belt = orch_mod.ToolBelt(SkillRegistry("skills").discover(), orch_mod.MCPManager())
    for t in ["check my calendar for any empty slots for tomorrow", "any gaps on friday", "can I fit it in tomorrow"]:
        assert belt.guard_ok("calendar_free", t), t
    assert belt.guard_ok("spotify_queue", "cue my music")


# ---------------------------------------------------------------- your sentences: harvest, review, eval
def test_harvest_pairs_sentences_with_what_she_did(tmp_path):
    db = tmp_path / "cortex.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE episodes (id INTEGER PRIMARY KEY, session_id TEXT, role TEXT, content TEXT, ts REAL)")
    now = time.time()
    rows = [("s", "user", "play my music", now - 100), ("s", "assistant", "Playing your liked songs, sir.", now - 99),
            ("s", "user", "yes", now - 60), ("s", "assistant", "Done.", now - 59),
            ("s", "user", "how are you", now - 30), ("s", "assistant", "Very well, sir.", now - 29)]
    con.executemany("INSERT INTO episodes (session_id, role, content, ts) VALUES (?,?,?,?)", rows)
    con.commit()
    con.close()
    logs = tmp_path / "harvest_logs"
    logs.mkdir()
    entry = {"timestamp": datetime.fromtimestamp(now - 101).isoformat(), "action": "tool_call",
             "details": {"tool": "spotify_play"}}
    (logs / f"audit_{datetime.now():%Y-%m-%d}.jsonl").write_text(json.dumps(entry) + "\n", encoding="utf-8")
    got = cases_mod.harvest(str(db), str(logs), days=1)
    assert [(c["say"], c["she_did"]) for c in got] == [("play my music", ["spotify_play"]), ("how are you", ["none"])]
    assert got[1]["history"][-1] == ["assistant", "Done."]


def test_review_saves_your_verdicts(tmp_path):
    items = [{"say": "queue my music", "she_did": ["none"], "history": []},
             {"say": "what time is it", "she_did": ["get_datetime"], "history": []},
             {"say": "something odd", "she_did": ["none"], "history": []}]
    answers = iter(["spotify_q", "", "s"])
    out = tmp_path / "cases.jsonl"
    n = cases_mod.review(items, ["spotify_queue", "spotify_play", "get_datetime"], out=out,
                         ask=lambda _: next(answers), say=lambda *_: None)
    saved = [json.loads(l) for l in out.read_text(encoding="utf-8").splitlines()]
    assert n == 2 and [s["expect"] for s in saved] == [["spotify_queue"], ["get_datetime"]]
    assert cases_mod.resolve("spotify", ["spotify_queue", "spotify_play"]) is None     # ambiguous: asks again
    assert cases_mod.resolve("u", []) == ["unavailable"]


def test_seed_cases_load_and_name_real_tools():
    belt = orch_mod.ToolBelt(SkillRegistry("skills").discover(), orch_mod.MCPManager())
    cs = cases_mod.load([cases_mod.SEED])
    assert len(cs) >= 50
    unknown = {t for c in cs for t in c["expect"]} - set(belt.by_name) - {"none", "unavailable"}
    assert unknown == set()


def test_eval_scores_without_executing_anything(eva, monkeypatch):
    o, ran = eva
    monkeypatch.setattr(U, "get_jev", lambda: fake_jev("calendar_free", 0.96))
    cs = [{"say": "is tomorrow looking open for me", "expect": ["calendar_free"], "args": {"day": "tomorrow"}},
          {"say": "what time is it", "expect": ["get_datetime"]}]
    text = asyncio.run(eval_mod.run(["legacy", "jev"], fast=True, case_list=cs, orchestrator=o, say=lambda *_: None))
    assert ran == []                                                   # nothing ran for real
    assert "| legacy | 1/2 (50%)" in text and "| jev | 2/2 (100%)" in text
    assert o.understand_mode is None
    text.encode("cp1252")                                              # prints fine in a Windows console


def test_args_ok_is_forgiving_about_form():
    assert eval_mod.args_ok({"day": "tomorrow"}, {"day": "tomorrow afternoon"})
    assert eval_mod.args_ok({"on": "false"}, {"on": False})
    assert not eval_mod.args_ok({"title": "gym"}, {})
