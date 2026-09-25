"""Telegram channel against a mocked Telegram API: pairing, allowlist, replies, buttons, pushes."""

import asyncio
import json

import httpx
import pytest

from core.telegram_bridge import TelegramBridge


class FakeTelegram:
    def __init__(self):
        self.updates, self.sent, self.actions = [], [], []

    def handler(self, req: httpx.Request):
        method = req.url.path.rsplit("/", 1)[-1]
        body = json.loads(req.content or b"{}")
        if method == "getUpdates":
            ups, self.updates = self.updates, []
            return httpx.Response(200, json={"ok": True, "result": ups})
        if method == "sendMessage":
            self.sent.append(body)
        elif method in ("sendChatAction", "answerCallbackQuery"):
            self.actions.append(method)
        return httpx.Response(200, json={"ok": True, "result": {}})


class FakeOrch:
    def __init__(self, sid):
        self.sid, self.heard = sid, []

    async def process_stream(self, text):
        self.heard.append(text)
        if "delete" in text:
            yield {"type": "widget", "data": {"kind": "confirm", "title": "Confirm", "text": "x"}}
            yield {"type": "final", "text": "Just to confirm, sir: delete it. Shall I go ahead?"}
        else:
            yield {"type": "final", "text": f"You said {text}, sir."}


def bridge(tmp_path, allowed=None):
    tg = FakeTelegram()
    orchs = {}

    def factory(sid):
        orchs[sid] = FakeOrch(sid)
        return orchs[sid]
    b = TelegramBridge("T", allowed, orch_factory=factory, state_path=tmp_path / "tg.json",
                       http=httpx.AsyncClient(transport=httpx.MockTransport(tg.handler)))
    return b, tg, orchs


def text_update(uid, text, chat_type="private", n=1):
    return {"update_id": n, "message": {"text": text, "from": {"id": uid}, "chat": {"id": uid, "type": chat_type}}}


def test_pairing_links_only_the_person_with_the_code(tmp_path):
    b, tg, orchs = bridge(tmp_path)
    code = b.pair_code
    tg.updates = [text_update(666, "hello", n=1), text_update(42, "/pair 000000", n=2),
                  text_update(42, f"/pair {code}", n=3)]
    asyncio.run(b.poll_once())
    assert b.allowed == {42} and orchs == {}                          # stranger and wrong code ignored
    assert tg.sent[-1]["text"].startswith("Paired, sir.")
    again = TelegramBridge("T", state_path=tmp_path / "tg.json")      # pairing survives a restart
    assert again.allowed == {42} and again.pair_code is None


def test_messages_go_to_her_brain_and_back(tmp_path):
    b, tg, orchs = bridge(tmp_path, allowed={42})
    tg.updates = [text_update(42, "what's next on my schedule")]
    asyncio.run(b.poll_once())
    assert orchs["tg-42"].heard == ["what's next on my schedule"]
    assert tg.sent[-1] == {"chat_id": 42, "text": "You said what's next on my schedule, sir."}
    assert "sendChatAction" in tg.actions


def test_group_chats_and_strangers_are_ignored(tmp_path):
    b, tg, orchs = bridge(tmp_path, allowed={42})
    tg.updates = [text_update(42, "hi", chat_type="group", n=1), text_update(7, "hi", n=2)]
    asyncio.run(b.poll_once())
    assert orchs == {} and tg.sent == []


def test_confirmations_get_yes_no_buttons_and_the_tap_answers(tmp_path):
    b, tg, orchs = bridge(tmp_path, allowed={42})
    tg.updates = [text_update(42, "delete the dentist")]
    asyncio.run(b.poll_once())
    keyboard = tg.sent[-1]["reply_markup"]["inline_keyboard"][0]
    assert [k["callback_data"] for k in keyboard] == ["yes", "no"]
    tg.updates = [{"update_id": 9, "callback_query": {"id": "cb", "data": "yes", "from": {"id": 42},
                                                        "message": {"chat": {"id": 42}}}}]
    asyncio.run(b.poll_once())
    assert orchs["tg-42"].heard[-1] == "yes" and "answerCallbackQuery" in tg.actions


def test_oracle_push_reaches_paired_phones_and_long_text_is_split(tmp_path):
    b, tg, _ = bridge(tmp_path, allowed={42})
    assert asyncio.run(b.notify("Sir, a reminder: call mom.")) is True
    asyncio.run(b.send(42, "x" * 9000))
    assert tg.sent[0]["text"] == "Sir, a reminder: call mom." and len(tg.sent) == 4
    nobody, _, _ = bridge(tmp_path / "other")
    assert asyncio.run(nobody.notify("hi")) is False


def test_voice_note_without_whisper_explains_how(tmp_path, monkeypatch):
    import builtins
    real = builtins.__import__
    monkeypatch.setattr(builtins, "__import__",
                        lambda n, *a, **k: (_ for _ in ()).throw(ImportError()) if n == "faster_whisper" else real(n, *a, **k))
    b, tg, orchs = bridge(tmp_path, allowed={42})
    tg.updates = [{"update_id": 1, "message": {"voice": {"file_id": "f"}, "from": {"id": 42},
                                                "chat": {"id": 42, "type": "private"}}}]
    asyncio.run(b.poll_once())
    assert "faster-whisper" in tg.sent[-1]["text"] and orchs == {}
