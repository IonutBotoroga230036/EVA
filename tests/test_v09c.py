"""v0.9.1: local mode that says what's wrong instead of going quiet for minutes (27 Nov 11:07: a decision took
120 s, an answer 245 s), a smaller tool menu for the small model, Ollama's own timings, and dimming a coloured
strip. Ollama and the lights are fakes."""

import asyncio
import json
import time

import httpx

import core.orchestrator_hybrid as orch_mod
from core import localcheck
from core.lights import Light

from tests.test_v05_mind import say  # noqa: F401


def test_where_ollama_keeps_the_model(monkeypatch):
    def get(url, timeout=0):
        return httpx.Response(200, json={"models": [{"name": "qwen2.5:3b-instruct", "size": 3_000_000_000,
                                                    "size_vram": 1_500_000_000}]}, request=httpx.Request("GET", url))
    monkeypatch.setattr(localcheck.httpx, "get", get)
    line = localcheck.report("http://localhost:11434")
    assert line == "LOCAL: qwen2.5:3b-instruct is 50% on the GPU, the rest on the CPU (3.0 GB)"


def test_ollamas_own_timings():
    line = localcheck.timing("decision", {"load_duration": 0, "prompt_eval_count": 2950,
                                          "prompt_eval_duration": 900_000_000, "eval_count": 24}, 1.6)
    assert line == "LOCAL: decision 1.6 s (load 0.0 s, 2950 prompt tokens in 0.9 s, 24 out)"


def _local_eva(tmp_path, monkeypatch, handler):
    from core.memory.cortex import Cortex
    from skills.registry import SkillRegistry
    real = httpx.AsyncClient
    monkeypatch.setattr(orch_mod.httpx, "AsyncClient", lambda *a, **k: real(transport=httpx.MockTransport(handler)))
    monkeypatch.setattr(orch_mod, "ack_after", lambda: -1)
    monkeypatch.setattr(localcheck, "report", lambda base: "LOCAL: (test)")
    localcheck.SLOW.update(ts=0.0, why="")
    o = orch_mod.HybridOrchestrator(registry=SkillRegistry("skills"))
    o.cortex = Cortex(str(tmp_path / "c.db"))
    o.history = []
    return o


def test_a_stuck_local_brain_says_so_quickly(tmp_path, monkeypatch):
    monkeypatch.setattr(orch_mod, "LOCAL_DECISION_S", 0.2)
    asked = []

    def handler(req):
        body = json.loads(req.content)
        asked.append(body)
        if "format" in body and "tool" in json.dumps(body.get("format")):
            raise httpx.ReadTimeout("")                        # Ollama doesn't answer the decision
        return httpx.Response(200, text=json.dumps({"message": {"content": "the old way"}, "done": True}))
    eva = _local_eva(tmp_path, monkeypatch, handler)
    t0 = time.time()
    reply, _ = say(eva, "What's a skill you would like to have?")
    assert reply == orch_mod.LOCAL_SLOW_SAY and time.time() - t0 < 10
    assert localcheck.recently_slow()


def test_the_small_model_reads_a_short_menu(tmp_path, monkeypatch):
    menus = []

    def handler(req):
        body = json.loads(req.content)
        if body.get("format") and "tool" in json.dumps(body["format"]):
            menus.append(body["messages"][0]["content"])
            return httpx.Response(200, json={"message": {"content": json.dumps({"tool": "none"})},
                                             "prompt_eval_count": 900, "eval_count": 5, "done": True})
        return httpx.Response(200, text=json.dumps({"message": {"content": "Hello, sir."}, "done": True}))
    eva = _local_eva(tmp_path, monkeypatch, handler)
    asyncio.run(eva._decide_local(httpx.AsyncClient(transport=httpx.MockTransport(handler)), "", [],
                                  [{"role": "user", "content": "set the volume to 30"}]))
    names = set(eva.belt.names())
    listed = [l for l in menus[0].splitlines() if l.startswith("- ") and l[2:].split(":")[0] in names]
    assert len(listed) <= orch_mod.LOCAL_TOOLS and any(l.startswith("- set_volume") for l in listed)


class Strip:
    """A Tuya strip in colour mode: brightness alone isn't supported there."""
    def __init__(self):
        self.calls = []

    def turn_on(self):
        return {}

    def set_brightness_percentage(self, b):
        return {"Error": "Function Not Supported by Device", "Err": "907"}

    def colour_hsv(self):
        return (0.0, 1.0, 1.0)

    def set_hsv(self, h, s, v):
        self.calls.append((h, s, v))
        return {}


def test_dim_a_red_strip_through_its_colour():
    strip = Strip()
    light = Light({"name": "Led", "id": "x", "ip": "1.2.3.4", "key": "k", "version": "3.5"}, factory=lambda dev: strip)
    light.apply(brightness=25)
    assert strip.calls == [(0.0, 1.0, 0.25)]
