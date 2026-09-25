from skills.registry import SkillRegistry
from tests.helpers import ECHO_TOOLS, FakeEmbedder, make_skill


def test_trusted_skill_loads_tools_untrusted_does_not(tmp_path):
    make_skill(tmp_path, "good", "Echo words back", tools_py=ECHO_TOOLS)
    make_skill(tmp_path, "shady", "Echo words back", trusted=False,
               tools_py=ECHO_TOOLS.replace("echo_tool", "shady_tool"))
    reg = SkillRegistry(tmp_path).discover()
    names = [t["function"]["name"] for t in reg.tool_schemas()]
    assert names == ["echo_tool"]                       # AEGIS gate: untrusted code never imported
    assert "shady" in reg.skills and reg.skills["shady"].tools == []
    assert reg.acks()["echo_tool"] == "Echoing, sir."


def test_folders_without_skill_md_are_ignored(tmp_path):
    (tmp_path / "legacy_thing").mkdir()
    (tmp_path / "legacy_thing" / "stuff.py").write_text("x = 1", encoding="utf-8")
    assert SkillRegistry(tmp_path).discover().skills == {}


def test_trigger_and_keyword_matching_without_embeddings(tmp_path):
    make_skill(tmp_path, "morning-briefing", "Morning briefing with weather and date",
               triggers=["good morning"])
    make_skill(tmp_path, "vision", "Look at the user's screen and describe it")
    reg = SkillRegistry(tmp_path).discover()
    assert [s.name for s in reg.match("good morning eva")] == ["morning-briefing"]
    assert [s.name for s in reg.match("describe my screen please")] == ["vision"]
    assert reg.match("what's the capital of France") == []


def test_semantic_matching_with_embeddings(tmp_path):
    make_skill(tmp_path, "vision", "look at the screen and describe what is on the screen")
    make_skill(tmp_path, "memory", "remember and forget facts about the user")
    emb = FakeEmbedder()
    reg = SkillRegistry(tmp_path, embedder=emb, match_threshold=0.3).discover()
    import numpy as np
    q = emb(["search_query: what is on the screen"])[0]
    q = q / np.linalg.norm(q)
    assert reg.match("what is on the screen", query_vec=q)[0].name == "vision"


def test_bodies_are_capped_and_labelled(tmp_path):
    make_skill(tmp_path, "big", "Big skill", body="x" * 5000)
    reg = SkillRegistry(tmp_path, max_body_chars=100).discover()
    body = reg.bodies([reg.skills["big"]])[0]
    assert body.startswith("### Skill: big") and len(body) < 130


def test_shipped_skills_parse():
    reg = SkillRegistry("skills").discover()
    assert {"memory", "vision", "morning-briefing"} <= set(reg.skills)
    tool_names = {t["function"]["name"] for t in reg.tool_schemas()}
    assert {"remember_fact", "recall_memory", "forget_memory", "add_instruction", "see_screen"} <= tool_names
