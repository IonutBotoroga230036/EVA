"""v0.3.2: Messages (9c: WhatsApp and Instagram through the phone's notifications, replies only after your yes)
and Romanian (9d: a Romanian voice, replies to Mom in Romanian, Romanian conversations).
Nothing here touches a real phone, Claude, Ollama or the Piper voice."""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

import core.messages as messages
import core.orchestrator_hybrid as orch_mod
import core.romanian as romanian
from core.memory.cortex import Cortex
from skills.registry import SkillRegistry


# ---------------------------------------------------------------- Romanian: recognising it
@pytest.mark.parametrize("text,ro", [
    ("Ajung acasă la 7.", True),
    ("Ce faci? Vin la tine mâine", True),
    ("Te iubesc mama", True),
    ("Good morning, Ionuț", False),              # his name has a ț; the sentence is English
    ("La La Land is playing", False),
    ("I will be home at 7 tonight, sir.", False),
    ("what is the weather in Bucharest", False),
    ("", False),
])
def test_is_romanian(text, ro):
    assert romanian.is_romanian(text) is ro


def test_quoted_romanian_goes_to_the_romanian_voice():
    assert romanian.split_languages("Message from Mom on WhatsApp: „Când vii acasă?”") == [
        ("en", "Message from Mom on WhatsApp:"), ("ro", "Când vii acasă?")]
    # the sentence chunker can cut a quote in two: both halves still go to the right voice
    assert romanian.split_languages("Just to confirm, sir: reply to Mom: „Ajung acasă la 7.") == [
        ("en", "Just to confirm, sir: reply to Mom:"), ("ro", "Ajung acasă la 7.")]
    assert romanian.split_languages("”. Shall I go ahead?") == [("en", "Shall I go ahead?")]


def test_translate_prefers_claude_when_the_cloud_is_allowed(monkeypatch):
    from core.understand import brains
    asked = []
    monkeypatch.setattr(brains, "cloud_allowed", lambda: True)
    monkeypatch.setattr(romanian, "_claude_translate", lambda p, t: asked.append(("claude", p)) or "„Ajung acasă la 7.”")
    monkeypatch.setattr(romanian, "_local_translate", lambda p, t: asked.append(("local", p)) or "x")
    assert romanian.translate("I'll be home at 7", style="family") == "„Ajung acasă la 7.”"
    assert asked[0][0] == "claude" and "informal 'tu'" in asked[0][1] and len(asked) == 1


def test_translate_stays_local_when_local_only(monkeypatch):
    from core.understand import brains
    monkeypatch.setattr(brains, "cloud_allowed", lambda: False)
    monkeypatch.setattr(romanian, "_claude_translate", lambda p, t: pytest.fail("Claude must not be asked"))
    monkeypatch.setattr(romanian, "_local_translate", lambda p, t: "Ajung acasă la 7.")
    assert romanian.translate("I'll be home at 7") == "Ajung acasă la 7."
    assert romanian.translate("Deja e în română, mulțumesc") == "Deja e în română, mulțumesc"   # nothing to do
    monkeypatch.setattr(romanian, "_local_translate", lambda p, t: None)
    assert romanian.translate("I'll be home at 7") is None


def test_clean_drops_quotes_and_thinking():
    assert romanian._clean('<think>hmm</think>\n„Ajung la 7.”') == "Ajung la 7."


def test_piper_audio_is_resampled_to_her_voice_rate(monkeypatch):
    class Voice:
        def synthesize(self, text):
            yield SimpleNamespace(audio_float_array=np.ones(22050, dtype=np.float32), sample_rate=22050)
    monkeypatch.setattr(romanian, "_voice", Voice())
    a = romanian.piper_audio("Bună ziua", 24000)
    assert a.dtype == np.float32 and abs(len(a) - 24000) <= 1


def test_no_romanian_voice_means_none(monkeypatch):
    assert romanian.piper_audio("Bună ziua", 24000) is None          # not downloaded: Kokoro reads it instead


def test_one_clip_two_voices(monkeypatch):
    from voice.speech import KokoroTTS
    used = []
    monkeypatch.setattr(romanian, "piper_audio", lambda t, r: used.append(("ro", t)) or np.zeros(100, np.float32))
    tts = KokoroTTS()
    monkeypatch.setattr(tts, "_kokoro", lambda t: used.append(("en", t)) or np.zeros(50, np.float32))
    wav = tts.synth("Message from Mom on WhatsApp: „Când vii acasă?”")
    assert used == [("en", "Message from Mom on WhatsApp:"), ("ro", "Când vii acasă?")]
    assert wav[:4] == b"RIFF"
    used.clear()
    tts.synth("Lights off, sir.")
    assert used == [("en", "Lights off, sir.")]                       # English answers: Kokoro only


def test_romanian_falls_back_to_kokoro_without_the_voice(monkeypatch):
    from voice.speech import KokoroTTS
    tts, used = KokoroTTS(), []
    monkeypatch.setattr(tts, "_kokoro", lambda t: used.append(t) or np.zeros(50, np.float32))
    assert tts.synth("Ajung acasă la 7.") and used == ["Ajung acasă la 7."]


# ---------------------------------------------------------------- Whisper: English or Romanian
class FakeWhisper:
    def __init__(self, lang, prob):
        self.lang, self.prob, self.calls = lang, prob, []

    def transcribe(self, audio, language=None, **k):
        self.calls.append(language)
        text = "Ce faci, Eva?" if (language or self.lang) == "ro" else "What's up, Eva?"
        return iter([SimpleNamespace(text=text, no_speech_prob=0.0, avg_logprob=-0.1)]), \
            SimpleNamespace(language=self.lang, language_probability=self.prob)


def make_stt(fake, languages=("en", "ro")):
    from voice.stt import WhisperSTT
    s = WhisperSTT(model="tiny", languages=list(languages), model_factory=lambda *a, **k: fake)
    s._model = fake
    return s


def test_whisper_hears_romanian():
    fake = FakeWhisper("ro", 0.95)
    s = make_stt(fake)
    assert "Ce faci" in s.transcribe(np.zeros(16000 * 2, np.float32) + 0.01) and s.last_language == "ro"
    assert fake.calls == [None]                                      # Whisper chose the language itself


def test_a_weak_guess_or_short_command_stays_english():
    fake = FakeWhisper("ro", 0.6)
    s = make_stt(fake)
    s.transcribe(np.zeros(16000 * 2, np.float32) + 0.01)
    assert fake.calls == [None, "en"] and s.last_language == "en"
    fake = FakeWhisper("ro", 0.99)
    s = make_stt(fake)
    s.transcribe(np.zeros(16000, np.float32) + 0.01)                  # 1 s: too short to guess
    assert fake.calls == ["en"] and s.last_language == "en"


def test_english_only_setting_never_guesses():
    fake = FakeWhisper("ro", 0.99)
    s = make_stt(fake, languages=("en",))
    s.transcribe(np.zeros(16000 * 3, np.float32) + 0.01)
    assert fake.calls == ["en"]


# ---------------------------------------------------------------- messages: the inbox
def test_inbox_dedupes_and_judges():
    box = messages.Inbox()
    mom = box.add("com.whatsapp", "Mama", "Când vii acasă?")
    assert mom and mom.important and mom.why == "vip" and mom.who == "Mom"
    assert box.add("com.whatsapp", "Mama", "Când vii acasă?") is None              # Android repeats itself
    assert box.add("com.whatsapp", "Andrei", "haha nice", "Boys", group=True).important is False
    assert box.add("com.whatsapp", "Andrei", "Ionut are you coming?", "Boys", group=True).why == "mention"
    assert box.add("com.instagram.android", "lisa", "can you call me", "lisa").why == "urgent"
    assert box.add("com.whatsapp", "Dan", "are you free tonight?").why == "question"
    assert box.add("com.whatsapp", "Dan", "ok").important is False
    assert box.add("com.whatsapp", "", "no sender") is None and box.add("com.whatsapp", "Dan", " ") is None
    assert [m.who for m in box.find(app="instagram")] == ["lisa"]
    assert len(box.find(who="mom")) == 1 and len(box.find(important=True)) == 4


def test_inbox_forgets_after_a_day():
    now = [1000.0]
    box = messages.Inbox(now=lambda: now[0])
    box.add("com.whatsapp", "Dan", "hi")
    now[0] += 24 * 3600 + 1
    assert box.find(unread=False) == []


def test_contacts_and_names():
    assert messages.person("Mami ❤️") == "Mom" and messages.person("Andrei") == "Andrei"
    assert messages.same_person("mama", "Mom") and not messages.same_person("Dan", "Mom")
    assert messages.writes_romanian("Mama") and not messages.writes_romanian("Dan")
    assert messages.app_name("com.whatsapp") == "WhatsApp" and messages.app_name("com.instagram.android") == "Instagram"


def test_any_messages_reads_new_ones_once():
    box = messages.get_inbox()
    box.add("com.whatsapp", "Mama", "Ai mâncat?")
    box.add("com.whatsapp", "Dan", "ok")
    out = messages.tool_messages_check()
    assert out["exact"] and out["say"].startswith("You have 2 new messages, sir, 1 that matter")
    assert "Mom on WhatsApp: „Ai mâncat?”" in out["say"]
    assert messages.tool_messages_check()["say"] == "No new messages, sir."
    assert messages.tool_messages_check(who="mom")["say"] == "No new messages from Mom, sir."


def test_announcement_text():
    m = messages.Inbox().add("com.whatsapp", "Andrei", "Ionut, call me", "Boys", group=True)
    assert messages.announcement(m) == "Message from Andrei in Boys on WhatsApp: „Ionut, call me”"
    assert messages.should_announce(m, {"announce": "important"})
    assert not messages.should_announce(m, {"announce": "off"})


# ---------------------------------------------------------------- messages: replying
def test_reply_to_mom_is_translated_before_you_confirm(monkeypatch):
    messages.get_inbox().add("com.whatsapp", "Mama", "Când vii?")
    calls = []
    monkeypatch.setattr(romanian, "translate", lambda t, to="ro", style="": calls.append((t, style)) or "Ajung acasă la 7.")
    args = messages.fill_reply({"to": "mom", "text": "I'll be home at 7"}, "reply to mom I'll be home at 7")
    assert args["text"] == "Ajung acasă la 7." and args["to"] == "Mom" and args["app"] == "WhatsApp"
    assert calls == [("I'll be home at 7", "family")]
    assert messages.fill_reply(args, "yes") is args and len(calls) == 1       # "yes" doesn't translate again
    assert messages.describe_reply(args) == "reply to Mom on WhatsApp: „Ajung acasă la 7.”"


def test_reply_to_others_stays_english_unless_asked(monkeypatch):
    messages.get_inbox().add("com.whatsapp", "Dan", "you coming?")
    monkeypatch.setattr(romanian, "translate", lambda t, to="ro", style="": "Vin.")
    assert messages.fill_reply({"to": "Dan", "text": "on my way"}, "tell Dan on my way")["text"] == "on my way"
    assert messages.fill_reply({"to": "Dan", "text": "on my way"}, "tell Dan in Romanian on my way")["text"] == "Vin."


def test_untranslated_reply_says_so(monkeypatch):
    messages.get_inbox().add("com.whatsapp", "Mama", "?")
    monkeypatch.setattr(romanian, "translate", lambda *a, **k: None)
    args = messages.fill_reply({"to": "Mom", "text": "home at 7"}, "reply to mom home at 7")
    assert "couldn't translate" in messages.describe_reply(args) and args["text"] == "home at 7"


def test_reply_goes_through_the_phone(monkeypatch):
    import core.device as dev
    messages.get_inbox().add("com.whatsapp", "Mama", "Când vii?")
    asked = []
    monkeypatch.setattr(dev, "_ask", lambda cap, action, args: asked.append((cap, action, args)) or {"ok": True})
    out = messages.tool_message_reply(to="Mom", text="Ajung acasă la 7.")
    assert out["say"] == "Sent to Mom, sir."
    assert asked == [("reply", "reply_message", {"conversation": "Mama", "sender": "Mama", "app": "com.whatsapp",
                                                 "text": "Ajung acasă la 7."})]
    monkeypatch.setattr(dev, "_ask", lambda *a: {"ok": False, "error": "the notification is gone"})
    assert "Open the chat and reply there" in messages.tool_message_reply(to="Mom", text="x")["say"]
    monkeypatch.setattr(dev, "_ask", lambda *a: {"ok": False, "error": "no phone"})
    assert messages.tool_message_reply(to="Mom", text="x")["say"] == dev.NO_PHONE


def test_no_message_no_reply():
    out = messages.tool_message_reply(to="Dan", text="hi")
    assert "don't have a recent message from Dan" in out["say"]
    assert messages.tool_message_reply(to="Dan", text="")["say"] == "What should I say, sir?"


@pytest.fixture
def eva(tmp_path, monkeypatch):
    ran = []

    async def aexecute(self, name, args):
        ran.append((name, dict(args)))
        return {"result": json.dumps({"ok": True}), "say": "Sent to Mom, sir.", "exact": True}

    monkeypatch.setattr(orch_mod.ToolBelt, "aexecute", aexecute)
    o = orch_mod.HybridOrchestrator(session_id="m", cortex=Cortex(str(tmp_path / "c.db")),
                                    registry=SkillRegistry("skills").discover())
    return o, ran


def turn(o, text):
    async def go():
        evs = [e async for e in o.process_stream(text)]
        if o._bg:
            await asyncio.gather(*o._bg, return_exceptions=True)
        return evs
    evs = asyncio.run(go())
    return [e for e in evs if e["type"] == "final"][-1]["text"], evs


def test_nothing_is_sent_without_your_yes(eva, monkeypatch):
    o, ran = eva
    messages.get_inbox().add("com.whatsapp", "Mama", "Când vii?")
    monkeypatch.setattr(romanian, "translate", lambda t, to="ro", style="": "Ajung acasă la 7.")

    async def decide(*a, **k):
        return {"tool": "message_reply", "to": "Mom", "text": "I'll be home at 7", "_by": "claude", "_trusted": True,
                "_args_done": True}
    monkeypatch.setattr(orch_mod.understanding, "understand", decide)
    monkeypatch.setattr(orch_mod.understanding, "cfg", lambda: {**orch_mod.understanding.DEFAULTS, "mode": "auto"})
    text, evs = turn(o, "reply to mom I'll be home at 7")
    assert text == "Just to confirm, sir: reply to Mom on WhatsApp: „Ajung acasă la 7.”. Shall I go ahead?"
    assert ran == []
    text, _ = turn(o, "da")                                          # Romanian yes works too
    assert ran and ran[0][0] == "message_reply" and ran[0][1]["text"] == "Ajung acasă la 7."
    assert text == "Sent to Mom, sir."


def test_messages_work_hands_free_with_the_phone_locked(monkeypatch):
    assert orch_mod._messages_when_locked("message_reply") and orch_mod._messages_when_locked("messages_check")
    assert not orch_mod._messages_when_locked("email_send")
    monkeypatch.setattr(messages, "cfg", lambda: {**messages.DEFAULTS, "when_locked": False})
    assert not orch_mod._messages_when_locked("messages_check")


def test_tools_are_registered_with_a_confirmation():
    belt = orch_mod.ToolBelt(SkillRegistry("skills").discover(), orch_mod.MCPManager())
    assert belt.has("messages_check") and belt.has("message_reply")
    assert belt.needs_confirm("message_reply") and not belt.needs_confirm("messages_check")
    assert "message_reply" in belt.slow_fill
    assert belt.describe("message_reply", {"to": "Dan", "text": "hi", "app": "Instagram"}) == \
        "reply to Dan on Instagram: „hi”"


# ---------------------------------------------------------------- Romanian conversations
def test_spoken_to_in_romanian_she_answers_in_romanian(eva, monkeypatch):
    o, _ = eva

    async def english(text):
        o.history.append({"role": "assistant", "content": "It's 14:05, sir."})
        yield {"type": "ack", "text": "One moment, sir."}
        yield {"type": "token", "text": "It's 14:05, "}
        yield {"type": "final", "text": "It's 14:05, sir."}
    monkeypatch.setattr(o, "_process_stream", english)
    monkeypatch.setattr(romanian, "translate", lambda t, to="ro", style="": "Este 14:05, domnule.")
    text, evs = turn(o, "Cât e ceasul acum?")
    assert text == "Este 14:05, domnule." and [e["type"] for e in evs] == ["final"] and evs[0]["replaced"]
    assert o.history[-1]["content"] == "Este 14:05, domnule."
    text, evs = turn(o, "what time is it")                          # English stays English, streamed
    assert text == "It's 14:05, sir." and evs[0]["type"] == "ack"


def test_whisper_hint_makes_a_romanian_turn(eva, monkeypatch):
    o, _ = eva

    async def english(text):
        yield {"type": "final", "text": "Sure, sir."}
    monkeypatch.setattr(o, "_process_stream", english)
    monkeypatch.setattr(romanian, "translate", lambda t, to="ro", style="": "Sigur, domnule.")
    o.reply_language = "ro"
    assert turn(o, "salut eva")[0] == "Sigur, domnule."
    assert turn(o, "hello eva")[0] == "Sure, sir."                     # the hint lasts one turn


# ---------------------------------------------------------------- the server and the app
def test_phone_message_endpoint(monkeypatch):
    from fastapi.testclient import TestClient
    import interfaces.web.server_stream as srv

    class TTS:
        def warm(self):
            return True

        def phrase(self, text):
            return None

        def synth(self, text):
            return b"RIFF-" + text.encode()
    said = []

    async def broadcast(text, widget=None):
        said.append(text)
        return True
    monkeypatch.setattr(srv, "get_tts", lambda: TTS())
    monkeypatch.setattr(srv, "broadcast", broadcast)
    with TestClient(srv.app) as tc:
        srv.get_oracle().quiet = (0, 0)                                # never quiet in this test
        r = tc.post("/api/phone/message", json={"app": "com.whatsapp", "sender": "Mama", "text": "Unde ești?",
                                               "conversation": "Mama", "group": False}).json()
        assert r["new"] and r["important"] and r["announce"] == "Message from Mom on WhatsApp: „Unde ești?”"
        assert r["audio"] and said == [r["announce"]]
        again = tc.post("/api/phone/message", json={"app": "com.whatsapp", "sender": "Mama", "text": "Unde ești?"})
        assert again.json()["new"] is False                            # the same notification again
        r = tc.post("/api/phone/message", json={"app": "com.whatsapp", "sender": "Dan", "text": "lol"}).json()
        assert r["new"] and not r["important"] and r["announce"] == ""
        srv.get_oracle().quiet = (0, 24)                               # quiet hours: kept, not said
        r = tc.post("/api/phone/message", json={"app": "com.whatsapp", "sender": "Mama", "text": "Noapte bună"}).json()
        assert r["new"] and r["announce"] == "" and len(said) == 1
        srv.get_oracle().quiet = (23, 8)
    assert messages.get_inbox().find(who="mom")[0].text == "Noapte bună"   # announced ones count as read


def test_android_app_has_the_listener():
    base = Path("apps/eva_android/android_overlay/app/src/main")
    kt = (base / "kotlin/nl/ionut/eva_android/MessageListener.kt").read_text(encoding="utf-8")
    assert "NotificationListenerService" in kt and "RemoteInput.addResultsToIntent" in kt
    assert '"/api/phone/message"' in kt and 'startsWith("https://")' in kt
    assert "com.whatsapp" in kt and "com.instagram.android" in kt
    manifest = (base / "AndroidManifest.xml").read_text(encoding="utf-8")
    assert "BIND_NOTIFICATION_LISTENER_SERVICE" in manifest and ".MessageListener" in manifest
    actions = (base / "kotlin/nl/ionut/eva_android/DeviceActions.kt").read_text(encoding="utf-8")
    assert '"reply_message" -> MessageListener.reply' in actions
    main = (base / "kotlin/nl/ionut/eva_android/MainActivity.kt").read_text(encoding="utf-8")
    assert '"setMessages"' in main and '"setLink"' in main
    dart = Path("apps/eva_android/lib/main.dart").read_text(encoding="utf-8")
    assert "'Messages: on'" in dart and "'setLink'" in dart
    # the generated android/ folder carries the same files
    for name in ("MessageListener.kt", "DeviceActions.kt", "MainActivity.kt", "WakeService.kt"):
        a = (base / "kotlin/nl/ionut/eva_android" / name).read_text(encoding="utf-8")
        b = Path("apps/eva_android/android/app/src/main/kotlin/nl/ionut/eva_android", name).read_text(encoding="utf-8")
        assert a == b, name
    page = Path("interfaces/web/eva.html").read_text(encoding="utf-8")
    assert "caps: ['alarm', 'timer', 'reply']" in page
