import core.prompt_builder as pb


def test_standing_instructions_append_into_section(tmp_path, monkeypatch):
    f = tmp_path / "EVA.md"
    f.write_text("# EVA.md\n\n## About me\n- Ionut\n\n## Standing instructions\n\n## Later\n- x\n")
    monkeypatch.setattr(pb, "EVA_MD", f)
    assert pb.add_standing_instruction("always answer in metric units")["status"] == "added"
    assert pb.add_standing_instruction("Always answer in metric units.")["status"] == "duplicate"
    text = f.read_text()
    assert text.index("- Always answer in metric units.") < text.index("## Later")


def test_section_created_when_missing(tmp_path, monkeypatch):
    f = tmp_path / "EVA.md"
    f.write_text("# EVA.md\n")
    monkeypatch.setattr(pb, "EVA_MD", f)
    pb.add_standing_instruction("keep replies short")
    assert "## Standing instructions\n- Keep replies short." in f.read_text()


def test_answer_system_layers_everything(tmp_path, monkeypatch):
    f = tmp_path / "EVA.md"
    f.write_text("Never use em-dashes.")
    monkeypatch.setattr(pb, "EVA_MD", f)
    s = pb.build_answer_system("PERSONA", "RULES", [{"text": "Likes jazz"}],
                               ["### Skill: x\nbody"], ['{"temp_c": 17}'])
    order = [s.index(k) for k in ("PERSONA", "Never use em-dashes", "Likes jazz", "Skill: x", "RULES", "temp_c")]
    assert order == sorted(order)
