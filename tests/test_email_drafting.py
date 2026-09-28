"""Sep 26 log: she drafted three emails nobody asked for. The content was invented ("Take his email"), the targets were
invented (a Canva no-reply, a LinkedIn digest via 'from:tom'), and nothing was asked or confirmed. Never again."""

import asyncio
import json

import httpx
import pytest

import core.orchestrator_hybrid as orch_mod
import core.scribe as scribe
from core.memory.cortex import Cortex
from core.orchestrator_hybrid import HybridOrchestrator
from skills.registry import SkillRegistry


@pytest.fixture
def email_mod():
    from skills.email import tools
    return tools


@pytest.mark.parametrize("text,content", [
    ("draft an email to Muaad saying the poetry workshop moved to Friday", "the poetry workshop moved to Friday"),
    ("email Muaad and tell him the workshop moved to Friday", "the workshop moved to Friday"),
    ("write to Tom about the Deloitte meeting next week", "the Deloitte meeting next week"),
    ("ask Muaad whether he's free on Saturday", "he's free on Saturday"),
    ("Can you draft an email to Muaad?", ""),
    ("Can you take his email and email him something?", ""),
    ("take his email", ""),
])
def test_content_only_from_your_words(email_mod, text, content):
    assert email_mod.draft_content(text) == content


def test_filler_discards_the_models_paraphrase_and_invented_reply(email_mod):
    model = {"instructions": "Take his email", "reply_to": "from:tom", "to": "Muaad Sucule (Canva)"}
    out = email_mod._fill_draft(model, "Can you take his email and email him something?")
    assert out == {"instructions": "", "to": "Muaad Sucule"}
    kept = email_mod._fill_draft({"reply_to": "from:tom"}, "reply to Tom saying Friday works")
    assert kept["reply_to"] == "from:tom" and kept["instructions"] == "Friday works"


def test_no_content_means_a_question_not_a_draft(email_mod, monkeypatch):
    monkeypatch.setattr(email_mod, "_google", lambda: pytest.fail("Gmail must not be touched"))
    out = email_mod.email_draft(instructions="", to="Muaad")
    assert out["say"] == "What should the email to Muaad say, sir?" and out["ask_next"] == {"field": "instructions"}


def test_meta_request_about_email_is_not_a_draft_request(email_mod):
    import re
    rx = email_mod.GUARDS["email_draft"]
    assert not re.search(rx, "Before you're writing emails, can you ask me what the emails should contain?", re.I)
    assert not re.search(rx, "from now on ask me before you draft an email", re.I)
    assert re.search(rx, "draft an email to Muaad saying hi", re.I)


def test_automated_senders_are_never_targets():
    assert scribe.is_automated("no-reply@canva.com") and scribe.is_automated("messaging-digest-noreply@linkedin.com")
    assert not scribe.is_automated("muaad.j@example.com")

    class G:
        def list_messages(self, q, n=5):
            return 1, [{"from": "Muaad Sucule (Canva) <no-reply@canva.com>", "to": "", "cc": "",
                        "thread_id": "t", "message_id": "m", "subject": "A design has been shared with you!", "id": "1"}]

        def create_draft(self, *a):
            pytest.fail("never draft to a no-reply address")
    assert "don't have an email address for Muaad Sucule" in scribe.resolve_address(G(), "Muaad Sucule (Canva)")["say"]
    out = scribe.draft(G(), "Friday works", reply_to="from:Muaad")
    assert out["error"] == "automated sender" and "no-reply@canva.com" in out["say"]


# ------------------------------------------------------------ the whole conversation, shipped email skill
def fake_ollama(decisions):
    def handler(request):
        body = json.loads(request.content)
        props = (body.get("format") or {}).get("properties", {})
        if "steps" in props:
            return httpx.Response(200, json={"message": {"content": json.dumps({"steps": []})}})
        if "facts" in props:
            return httpx.Response(200, json={"message": {"content": json.dumps({"facts": []})}})
        if "tool" in props:
            last = body["messages"][-1]["content"].lower()
            d = next((v for k, v in decisions.items() if k in last), {"tool": "none"})
            return httpx.Response(200, json={"message": {"content": json.dumps(d)}})
        if body.get("format"):
            return httpx.Response(200, json={"message": {"content": "{}"}})
        lines = [json.dumps({"message": {"content": "Of course, sir, I'll ask first."}, "done": False}),
                 json.dumps({"message": {"content": ""}, "done": True})]
        return httpx.Response(200, text="\n".join(lines))
    real = httpx.AsyncClient
    return lambda *a, **k: real(transport=httpx.MockTransport(handler))


def turn(o, text):
    async def go():
        evs = [e async for e in o.process_stream(text)]
        if o._bg:
            await asyncio.gather(*o._bg, return_exceptions=True)
        return [e for e in evs if e["type"] == "final"][-1]["text"]
    return asyncio.run(go())


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(orch_mod, "fast_path", lambda *a, **k: None)        # only the email skill in play
    drafts = []

    def fake_draft(google, instructions, reply_to="", to="", subject="", **k):
        drafts.append({"instructions": instructions, "to": to, "reply_to": reply_to})
        return {"drafted_to": "muaad.j@example.com", "say": f"I've drafted an email to {to}, sir. Nothing was sent."}
    monkeypatch.setattr(scribe, "draft", fake_draft)
    reg = SkillRegistry("skills").discover()
    mod = next(s for s in reg.enabled() if s.name == "email").functions["email_draft"].__globals__
    monkeypatch.setitem(mod, "_google", lambda: object())
    decisions = {"muaad": {"tool": "email_draft", "to": "Muaad", "instructions": "Draft an email to Muaad."},
                 "before you": {"tool": "email_draft", "to": "Muaad", "reply_to": "from:tom",
                                "instructions": "Before you're writing emails..."}}
    monkeypatch.setattr(orch_mod.httpx, "AsyncClient", fake_ollama(decisions))
    o = HybridOrchestrator(session_id="e", cortex=Cortex(str(tmp_path / "c.db")), registry=reg)
    return o, drafts


def test_she_asks_what_to_write_then_confirms_then_drafts(setup):
    o, drafts = setup
    assert turn(o, "Can you draft an email to Muaad?") == "What should the email to Muaad say, sir?"
    assert drafts == []
    text = turn(o, "that the poetry workshop moved to Friday")
    assert text == ('Just to confirm, sir: draft an email to Muaad saying: '
                    '"that the poetry workshop moved to Friday". Shall I go ahead?')
    assert drafts == []
    assert turn(o, "yes") == "I've drafted an email to Muaad, sir. Nothing was sent."
    assert drafts == [{"instructions": "that the poetry workshop moved to Friday", "to": "Muaad", "reply_to": ""}]


def test_content_in_the_first_sentence_goes_straight_to_the_confirmation(setup):
    o, drafts = setup
    assert turn(o, "draft an email to Muaad saying the workshop moved to Friday").startswith(
        'Just to confirm, sir: draft an email to Muaad saying: "the workshop moved to Friday"')
    assert turn(o, "no") == "Cancelled, sir." and drafts == []


def test_never_mind_cancels_the_question(setup):
    o, drafts = setup
    turn(o, "Can you draft an email to Muaad?")
    assert turn(o, "never mind") == "Okay, sir." and drafts == []


def test_the_meta_request_from_the_log_drafts_nothing(setup):
    o, drafts = setup
    text = turn(o, "Before you're writing emails, can you ask me what the emails should contain?")
    assert drafts == [] and "confirm" not in text.lower() and o.pending is None
