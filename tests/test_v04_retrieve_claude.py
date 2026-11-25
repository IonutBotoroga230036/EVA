"""v0.4: tool retrieval for understanding, and the Claude client's full tool turns with prompt caching."""

import json

import httpx

from core.understand import retrieve


def tool(name, desc, **params):
    return {"type": "function", "function": {"name": name, "description": desc, "parameters": {
        "type": "object", "properties": {k: {"type": "string"} for k in params}}}}


MANY = [tool(f"skill_{i}", f"Does thing number {i} with widgets") for i in range(200)] + [
    tool("set_reminder", "Remind the user at a time", text="", when=""),
    tool("spotify_play", "Play music on Spotify", what=""),
    tool("task_answer", "Answer a task's question", answer="")]


def test_few_tools_go_through_untouched():
    assert retrieve.select([{"role": "user", "content": "x"}], MANY[:20]) == MANY[:20]


def test_many_tools_keep_the_ones_that_fit(monkeypatch):
    msgs = [{"role": "user", "content": "remind me to call Mom at six"}]
    out = retrieve.select(msgs, MANY)
    names = [t["function"]["name"] for t in out]
    assert len(out) == 50 and "set_reminder" in names and "task_answer" in names
    assert names.index("set_reminder") > names.index("skill_0") if "skill_0" in names else True   # order kept


def test_follow_ups_keep_the_tool_from_before():
    msgs = [{"role": "user", "content": "play some jazz"}, {"role": "assistant", "content": "Playing jazz on Spotify"},
            {"role": "user", "content": "louder"}]
    names = [t["function"]["name"] for t in retrieve.select(msgs, MANY)]
    assert "spotify_play" in names


def test_word_endings_fold():
    assert retrieve.stem("reminders") == "remind" and retrieve.stem("playing") == "play"


def test_the_claude_client_returns_every_tool_use_and_counts_cache(monkeypatch):
    from core.budget import get_budget
    from core.claude import ClaudeClient
    seen = {}

    def handler(req):
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, json={
            "content": [{"type": "text", "text": "Two things."},
                        {"type": "tool_use", "id": "a", "name": "write_file", "input": {"path": "x"}},
                        {"type": "tool_use", "id": "b", "name": "list_files", "input": {}}],
            "stop_reason": "tool_use",
            "usage": {"input_tokens": 100, "output_tokens": 50, "cache_creation_input_tokens": 1000,
                      "cache_read_input_tokens": 10000}})
    c = ClaudeClient(api_key="test", http=httpx.Client(transport=httpx.MockTransport(handler)))
    before = get_budget()._ensure_today()["total_euros"]
    out = c.message(system="s", messages=[{"role": "user", "content": "go"}], model="claude-sonnet-5-5",
                    tools=[{"name": "x"}], purpose="agent:test", cache=True)
    assert seen["body"]["cache_control"] == {"type": "ephemeral"}
    assert [u["id"] for u in out["tool_uses"]] == ["a", "b"] and out["content"][0]["text"] == "Two things."
    spent = get_budget()._ensure_today()["total_euros"] - before
    # (100 + 1250 + 1000 input-equivalent) x $2/M + 50 x $10/M, in euros
    assert abs(spent - ((2350 * 0.002 + 50 * 0.010) / 1000) * 0.92) < 1e-9
