"""Sep 26 evening log: acks only when slow and varied; email to a bare name; plain Gmail errors."""

import asyncio
import json
import time

import pytest

import core.orchestrator_hybrid as orch_mod
from core.scribe import resolve_address
from skills.email.tools import gmail_error


class FakeBelt:
    def __init__(self, delay):
        self.delay = delay

    def ack(self, name):
        return "Looking into that now, sir."

    async def aexecute(self, tool, args):
        await asyncio.sleep(self.delay)
        return {"result": json.dumps({"ok": True}), "say": "Done."}


def acks_for(delay, after, runs=1):
    o = orch_mod.HybridOrchestrator.__new__(orch_mod.HybridOrchestrator)
    o._last_ack = None
    o.belt = FakeBelt(delay)
    o._post = lambda tool, args, out, gathered: [{"type": "result"}]

    async def go():
        seen = []
        for _ in range(runs):
            seen += [e async for e in o._run("web_search", {}, [])]
        return seen
    orch_mod.ACK_AFTER_OVERRIDE = after
    return [e["text"] for e in asyncio.run(go()) if e["type"] == "ack"]


def test_a_quick_tool_gets_no_ack():
    assert acks_for(delay=0.01, after=0.3) == []


def test_a_slow_tool_gets_one():
    t0 = time.monotonic()
    got = acks_for(delay=0.4, after=0.1)
    assert len(got) == 1 and got[0] in orch_mod.GENERIC_ACKS and time.monotonic() - t0 < 1.0


def test_acks_can_be_switched_off():
    assert acks_for(delay=0.3, after=-1) == []


def test_generic_acks_never_repeat_back_to_back():
    got = acks_for(delay=0.0, after=0.0, runs=12)
    assert len(got) == 12 and all(a != b for a, b in zip(got, got[1:]))
    assert len(set(got)) >= 3


def test_ack_delay_comes_from_settings(monkeypatch):
    monkeypatch.setattr(orch_mod, "ACK_AFTER_OVERRIDE", None)
    assert orch_mod.ack_after() == 0.9


# ------------------------------------------------------------ email to a name
class FakeGoogle:
    def __init__(self, msgs):
        self.msgs, self.queries = msgs, []

    def list_messages(self, q, n=5):
        self.queries.append(q)
        return len(self.msgs), self.msgs


def test_a_name_resolves_to_the_address_in_your_mail():
    g = FakeGoogle([{"from": "Muaad Jestora <muaad.j@example.com>", "to": "me@example.com", "cc": ""},
                    {"from": "me@example.com", "to": "Muaad Jestora <muaad.j@example.com>", "cc": ""}])
    assert resolve_address(g, "Muaad") == "muaad.j@example.com"
    assert 'from:"Muaad"' in g.queries[0]


def test_accents_and_case_do_not_matter():
    g = FakeGoogle([{"from": "Ionuț Boțoroga <ionut@example.com>", "to": "", "cc": ""}])
    assert resolve_address(g, "ionut") == "ionut@example.com"


def test_no_address_asks_for_it():
    assert resolve_address(FakeGoogle([]), "Muaad")["say"] == \
        "I don't have an email address for Muaad, sir. What is it?"


def test_two_addresses_asks_which():
    g = FakeGoogle([{"from": "Tom <tom@work.com>", "to": "", "cc": "Tom R <tom@home.nl>"}])
    out = resolve_address(g, "Tom")
    assert out["error"] == "ambiguous" and "tom@work.com" in out["say"] and "tom@home.nl" in out["say"]


def test_draft_never_sends_a_bare_name_to_gmail(monkeypatch):
    import core.scribe as scribe
    made = []

    class G(FakeGoogle):
        def create_draft(self, *a, **k):
            made.append(a)
    monkeypatch.setattr(scribe, "compose_body", lambda *a, **k: "Hi")
    out = scribe.draft(G([]), "say hi", to="Muaad")
    assert made == [] and "What is it?" in out["say"]


def test_gmail_errors_come_out_in_plain_words():
    raw = Exception('<HttpError 400 when requesting https://gmail.googleapis.com/gmail/v1/users/me/drafts?alt=json '
                    'returned "Invalid To header". Details: "[...]">')
    assert gmail_error(raw) == "Gmail refused that, sir: Invalid To header."
    assert "https://" not in gmail_error(raw)
