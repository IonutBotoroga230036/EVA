import pytest

import core.prompt_builder as pb
from core.vocab import correct, parse_vocabulary

VOCAB = """
- Radboud University: roundabout university, read about university
- Nijmegen: neymar can, nay megan
- ZippZapp: zips up, zip zap
- Breda
- Tilburg
- Deloitte
"""


@pytest.mark.parametrize("heard,meant", [
    ("I go to roundabout University in Neymar can", "I go to Radboud University in Nijmegen"),
    ("make a project note about zips up branding", "make a project note about ZippZapp branding"),
    ("what's the weather in Tilbourg tomorrow", "what's the weather in Tilburg tomorrow"),
    ("send a message about Deloite", "send a message about Deloitte"),
])
def test_real_mishearings_are_fixed(heard, meant):
    assert correct(heard, VOCAB)[0] == meant


@pytest.mark.parametrize("text", ["I read about the news today", "I want some bread", "Tom works at Deloitte",
                                  "the zipper is broken", "breakfast in Breda"])
def test_normal_speech_is_left_alone(text):
    assert correct(text, VOCAB)[0] == text


def test_parse_vocabulary_entries():
    entries = dict(parse_vocabulary(VOCAB))
    assert entries["Nijmegen"] == ("neymar can", "nay megan") and entries["Breda"] == ()


def test_vocabulary_is_hidden_from_the_prompt_and_local_file_is_layered(tmp_path, monkeypatch):
    (tmp_path / "EVA.md").write_text("# EVA\n## About me\n- Ionut\n\n## Vocabulary\n- Breda\n\n## Standing instructions\n- Be brief.\n")
    (tmp_path / "EVA.local.md").write_text("## Private\n- Secret project Z\n\n## Vocabulary\n- ZippZapp: zip zap\n")
    monkeypatch.setattr(pb, "EVA_MD", tmp_path / "EVA.md")
    monkeypatch.setattr(pb, "EVA_LOCAL_MD", tmp_path / "EVA.local.md")
    prompt = pb.load_eva_md()
    assert "Vocabulary" not in prompt and "Be brief." in prompt and "Secret project Z" in prompt
    vocab = pb.vocabulary_text()
    assert "Breda" in vocab and "ZippZapp" in vocab
