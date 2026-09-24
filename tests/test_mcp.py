"""MCP client against a REAL MCP server process (tests/fixtures/mcp_demo_server.py)."""

import asyncio
import json
import sys

import pytest

pytest.importorskip("mcp")

import core.orchestrator_hybrid as orch_mod
from core.mcp_client import MCPManager, result_to_tool_output, tool_id
from core.memory.cortex import Cortex
from core.orchestrator_hybrid import HybridOrchestrator
from skills.registry import SkillRegistry
from tests.test_orchestrator import fake_ollama

DEMO = {"name": "demo", "command": sys.executable, "args": ["tests/fixtures/mcp_demo_server.py"], "timeout": 30}


def test_tool_ids_are_prefixed_and_sanitized():
    assert tool_id("calendar", "list-events") == "mcp_calendar_list-events"
    assert tool_id("My Server", "do.thing") == "mcp_my_server_do_thing"


def test_connect_list_call_and_safety_flags():
    async def go():
        m = MCPManager()
        await m.start([DEMO])
        try:
            names = [t["function"]["name"] for t in m.tool_schemas()]
            assert names == ["mcp_demo_add", "mcp_demo_wipe_calendar", "mcp_demo_fail"]
            assert m.is_read_only("mcp_demo_add") and not m.needs_confirm("mcp_demo_add")
            assert m.needs_confirm("mcp_demo_wipe_calendar") and "mcp_demo_wipe_calendar" in m.actions()
            out = json.loads((await m.call("mcp_demo_add", {"a": 2, "b": 40}))["result"])
            assert "42" in json.dumps(out["output"])
            assert "error" in json.loads((await m.call("mcp_demo_fail", {}))["result"])
        finally:
            await m.stop()
    asyncio.run(go())


def test_allowlist_trusted_and_read_only_overrides():
    async def go():
        m = MCPManager()
        await m.start([{**DEMO, "tools": ["add", "wipe_calendar"], "trusted_tools": ["wipe_calendar"]}])
        try:
            assert [t["function"]["name"] for t in m.tool_schemas()] == ["mcp_demo_add", "mcp_demo_wipe_calendar"]
            assert not m.needs_confirm("mcp_demo_wipe_calendar")          # trusted by config
        finally:
            await m.stop()
        m2 = MCPManager()
        await m2.start([{**DEMO, "read_only_tools": ["wipe_calendar"]}])
        try:
            assert m2.is_read_only("mcp_demo_wipe_calendar") and "mcp_demo_wipe_calendar" not in m2.actions()
        finally:
            await m2.stop()
    asyncio.run(go())


def test_a_broken_server_is_skipped_not_fatal():
    async def go():
        m = MCPManager()
        await m.start([{"name": "ghost", "command": "no-such-program-xyz", "timeout": 5}, DEMO])
        try:
            st = {s["name"]: s for s in m.status()}
            assert not st["ghost"]["connected"] and st["ghost"]["error"]
            assert st["demo"]["connected"]
        finally:
            await m.stop()
    asyncio.run(go())


def test_result_translation_handles_errors_and_text():
    class R:
        def model_dump(self, **_):
            return {"content": [{"type": "text", "text": "hello"}], "isError": False}
    assert json.loads(result_to_tool_output("s", "t", R())["result"])["output"] == "hello"


def test_orchestrator_uses_mcp_tools_and_confirms_changes(tmp_path, monkeypatch):
    client, seen = fake_ollama(decisions=[{"tool": "mcp_demo_add", "a": 2, "b": 3}, {"tool": "none"},
                                          {"tool": "mcp_demo_wipe_calendar", "day": "friday"}],
                               answer="The answer is 5, sir.")
    monkeypatch.setattr(orch_mod.httpx, "AsyncClient", client)

    async def go():
        m = MCPManager()
        await m.start([DEMO])
        try:
            o = HybridOrchestrator(session_id="m", cortex=Cortex(str(tmp_path / "c.db")),
                                   registry=SkillRegistry(tmp_path / "none").discover(), mcp=m)
            first = [e async for e in o.process_stream("what is two plus three")]
            ask = [e async for e in o.process_stream("wipe my calendar for friday")]
            before = len([e for e in o.bus.recent(200) if e.get("tool") == "mcp_demo_wipe_calendar"
                          and e["channel"] == "tool.executed"])
            done = [e async for e in o.process_stream("yes, go ahead")]
            after = len([e for e in o.bus.recent(200) if e.get("tool") == "mcp_demo_wipe_calendar"
                         and e["channel"] == "tool.executed"])
            return first, ask, done, before, after
        finally:
            await m.stop()
    first, ask, done, before, after = asyncio.run(go())
    assert first[-1]["text"] == "The answer is 5, sir."                 # read-only: ran immediately
    assert "Shall I go ahead" in ask[-1]["text"]                        # changes things: asked first
    assert any(e["type"] == "widget" and e["data"]["kind"] == "confirm" for e in ask)
    assert done[-1]["type"] == "final" and after == before + 1         # "yes" actually ran it, once
