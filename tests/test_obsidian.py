import importlib.util
import json
from datetime import datetime

import pytest

spec = importlib.util.spec_from_file_location("obs_tools", "skills/obsidian/tools.py")
obs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(obs)


@pytest.fixture
def vault(tmp_path, monkeypatch):
    monkeypatch.setenv("EVA_VAULT", str(tmp_path / "vault"))
    return tmp_path / "vault"


def res(out):
    return json.loads(out["result"])


def test_quick_note_goes_into_todays_daily_note(vault):
    r = res(obs.obsidian_quick_note("follow up with Tom about Deloitte"))
    obs.obsidian_quick_note("buy chalk for the gym")
    day = vault / "Daily" / f"{datetime.now():%Y-%m-%d}.md"
    body = day.read_text(encoding="utf-8")
    assert r["status"] == "noted" and "tags: [daily, eva]" in body
    assert body.count("\n- ") == 2 and "follow up with Tom about Deloitte" in body


def test_write_creates_with_frontmatter_then_appends(vault):
    assert res(obs.obsidian_write("ZippZapp branding", "Purple and lime.", "Projects"))["status"] == "created"
    assert res(obs.obsidian_write("zippzapp branding", "Logo draft v2."))["status"] == "appended"
    body = (vault / "Projects" / "ZippZapp branding.md").read_text(encoding="utf-8")
    assert body.startswith("---\ncreated:") and "Purple and lime." in body and "Logo draft v2." in body


def test_unknown_folder_falls_back_to_inbox(vault):
    obs.obsidian_write("Loose idea", "something", "Secrets")
    assert (vault / "Inbox" / "Loose idea.md").exists()


@pytest.mark.parametrize("evil", ["../../outside", "..\\..\\outside", "C:/Windows/evil", "CON", "a/b\\c:d*e?"])
def test_titles_can_never_escape_the_vault(vault, evil):
    obs.obsidian_write(evil, "x")
    root = vault.resolve()
    for p in root.parent.rglob("*.md"):
        assert root in p.resolve().parents
    assert not (vault.parent / "outside.md").exists()


def test_search_ranks_titles_and_returns_snippets(vault):
    obs.obsidian_write("Deloitte prep", "Ask Tom about the design team.", "Projects")
    obs.obsidian_write("Groceries", "eggs, oats, Deloitte coffee mug", "Inbox")
    obs.obsidian_write("Poetry night", "Rilke readings", "Ideas")
    r = res(obs.obsidian_search("what did I note about Deloitte"))
    assert [h["note"] for h in r["results"]][:2] == ["Projects/Deloitte prep.md", "Inbox/Groceries.md"]
    assert "Tom" in r["results"][0]["snippet"]
    assert res(obs.obsidian_search("quantum zebra"))["results"] == []


def test_read_strips_frontmatter_and_finds_by_partial_title(vault):
    obs.obsidian_write("Deloitte prep", "Ask Tom about the design team.", "Projects")
    r = res(obs.obsidian_read("the deloitte note"))
    assert r["note"] == "Projects/Deloitte prep.md" and not r["content"].startswith("---")
    assert "error" in res(obs.obsidian_read("nonexistent thing"))


def test_empty_inputs_are_refused(vault):
    assert "error" in res(obs.obsidian_quick_note("   "))
    assert "error" in res(obs.obsidian_write("", "x"))
    assert "error" in res(obs.obsidian_search("the my a"))


def test_obsidian_skill_is_shipped_and_trusted():
    from skills.registry import SkillRegistry
    reg = SkillRegistry("skills").discover()
    assert {"obsidian_quick_note", "obsidian_write"} <= reg.actions()
    assert "obsidian_search" in {t["function"]["name"] for t in reg.tool_schemas()}
