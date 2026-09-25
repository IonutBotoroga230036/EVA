"""Email importance, using the kinds of mail from Ionut's real inbox (Sep 25 log)."""

import json

import pytest

import core.triage as tr
from core import scribe
from core.triage import classify, score, set_sender_pref
from tests.fakes_google import FakeGoogle, msg


@pytest.fixture(autouse=True)
def prefs(tmp_path, monkeypatch):
    monkeypatch.setattr(tr, "PREFS", tmp_path / "prefs.json")
    monkeypatch.setattr(tr, "CONTACTS", tmp_path / "contacts.json")


INSTAGRAM = msg("i", "jeonhyerinnnn on Instagram <no-reply@mail.instagram.com>",
                "See jeonhyerinnnn, mihneaa.grigore and more in your feed", labels=["CATEGORY_SOCIAL"], bulk=True)
TLDR = msg("t", "TLDR DevOps <dan@tldrnewsletter.com>", "Faster AI Startup, 20x Cheaper Incident Triage",
           labels=["CATEGORY_PROMOTIONS"], bulk=True)
TOM = msg("m", "Tom Reinhoudt <tom@deloitte.nl>", "Design team", "Can we talk Friday?", labels=["IMPORTANT"])
RADBOUD = msg("r", "Radboud University <studentservices@ru.nl>", "Action required: enrolment deadline",
              labels=["CATEGORY_PERSONAL"])
STRANGER = msg("s", "Anna de Vries <anna@startup.nl>", "Quick question about your consultancy")


def test_promotions_and_social_are_noise():
    for m in (INSTAGRAM, TLDR):
        s, why = score(m, contacts=set())
        assert s < 0, (m["subject"], why)


def test_people_you_write_to_and_deadlines_are_important():
    assert score(TOM, contacts={"tom@deloitte.nl"})[0] >= tr.IMPORTANT
    s, why = score(RADBOUD, contacts=set())
    assert s >= tr.IMPORTANT and "sounds time-sensitive" in why


def test_vip_and_mute_lists_win():
    set_sender_pref("instagram", important=True)
    assert score(INSTAGRAM, set())[0] >= tr.IMPORTANT
    set_sender_pref("instagram", important=False)                      # changing your mind replaces it
    assert score(INSTAGRAM, set())[0] < -15 and tr.load_prefs()["vip"] == []


def test_borderline_mail_asks_the_local_model(monkeypatch):
    monkeypatch.setattr(tr, "llm_opinion", lambda m, url, model: True)
    g = FakeGoogle(contacts=set())
    out = classify([STRANGER], g, use_llm=True)[0]
    assert 0 <= out["score"] < tr.IMPORTANT and out["important"]
    assert "the local model thinks it matters" in out["reasons"]
    assert not classify([STRANGER], g, use_llm=False)[0]["important"]


def test_anything_interesting_answer(monkeypatch):
    g = FakeGoogle(messages=[INSTAGRAM, TOM, TLDR, RADBOUD], contacts={"tom@deloitte.nl"})
    out = scribe.important(g, use_llm=False)
    assert out["say"].startswith("2 emails look important, sir: ")
    assert "Tom Reinhoudt about Design team" in out["say"] and "Radboud University" in out["say"]
    assert "The other 2 can wait." in out["say"] and "Instagram" not in out["say"]


def test_nothing_important_is_said_plainly():
    g = FakeGoogle(messages=[INSTAGRAM, TLDR])
    assert scribe.important(g, use_llm=False)["say"].startswith("Nothing important, sir.")


def test_contacts_are_cached_for_a_day(tmp_path):
    g = FakeGoogle(contacts={"tom@deloitte.nl"})
    assert tr.known_contacts(g) == {"tom@deloitte.nl"}
    g.contacts = set()
    assert tr.known_contacts(g) == {"tom@deloitte.nl"}                 # cached, no second Gmail scan


@pytest.mark.parametrize("text,expected", [
    ("anything interesting in my emails", ("email_important", {})),
    ("any important emails?", ("email_important", {})),
    ("emails from Tom are important", ("email_sender_pref", {"who": "Tom", "important": True})),
    ("don't tell me about Instagram", ("email_sender_pref", {"who": "Instagram", "important": False})),
])
def test_fast_paths(text, expected):
    from core.orchestrator_hybrid import ToolBelt, fast_path
    from skills.registry import SkillRegistry
    assert fast_path(text, ToolBelt(SkillRegistry("skills").discover())) == expected
