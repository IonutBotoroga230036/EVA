"""Messages (v0.3.2, milestone 9c): WhatsApp and Instagram, through your phone's notifications.

The Android app (Messages: on, plus Android's Notification access) sends each new chat notification here:
POST /api/phone/message {app, sender, text, conversation, group, key}. Nothing else is read: no chat history,
no contacts, no other apps.

- The inbox lives in this process's memory for 24 hours and is never written to disk. The log names the sender
  and the app, never the text.
- Important (settings messages.*): someone on your VIP list (Mom), an urgent word ("urgent", "call me",
  "sună-mă"), a group message that says your name, or a direct question. She tells you about those at once
  (announce: important | all | off), and keeps quiet during ORACLE's quiet hours and do-not-disturb.
- "Any messages?" reads what's new. "Reply to Mom: I'll be home at 7" shows and reads you the exact text first
  (in Romanian for the people in messages.romanian) and sends only after your yes. Replies go through the
  notification's own reply button, so they only work while that notification is still on your phone.

Message text is other people's words: it is quoted, never obeyed.
"""

from __future__ import annotations

import hashlib
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from loguru import logger

KEEP_S = 24 * 3600
APPS = {"com.whatsapp": "WhatsApp", "com.whatsapp.w4b": "WhatsApp Business", "com.instagram.android": "Instagram",
        "whatsapp": "WhatsApp", "instagram": "Instagram"}
DEFAULTS = {"announce": "important", "vip": ["Mom"], "contacts": {"Mom": ["Mom", "Mama", "Mami", "Mum"]},
            "romanian": ["Mom"], "keywords": ["urgent", "asap", "call me", "emergency", "sună-mă", "suna-ma"],
            "my_names": ["Ionut", "Ionuț"], "when_locked": True}


def cfg() -> dict:
    try:
        from core.settings import get_settings
        return {**DEFAULTS, **(get_settings().get("messages") or {})}
    except Exception:
        return dict(DEFAULTS)


def app_name(app: str) -> str:
    a = (app or "").strip()
    return APPS.get(a) or APPS.get(a.lower()) or (a[:30] if a else "your phone")


def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9ăâîșşțţ ]", "", (name or "").lower()).strip()


def person(name: str, c: Optional[dict] = None) -> str:
    """The one name for someone: 'Mama' and 'Mami ❤️' are both 'Mom' (settings messages.contacts)."""
    c = c or cfg()
    n = _norm(name)
    if not n:
        return ""
    for who, aliases in (c.get("contacts") or {}).items():
        for a in [who, *(aliases or [])]:
            an = _norm(a)
            if an and (n == an or re.search(rf"\b{re.escape(an)}\b", n)):
                return who
    return (name or "").strip()


def same_person(a: str, b: str, c: Optional[dict] = None) -> bool:
    c = c or cfg()
    pa, pb = _norm(person(a, c)), _norm(person(b, c))
    return bool(pa and pb) and (pa == pb or pa.startswith(pb + " ") or pb.startswith(pa + " "))


def writes_romanian(name: str, c: Optional[dict] = None) -> bool:
    c = c or cfg()
    return any(same_person(name, r, c) for r in (c.get("romanian") or []))


@dataclass
class Message:
    key: str
    app: str
    sender: str
    text: str
    conversation: str
    group: bool = False
    at: float = field(default_factory=time.time)
    important: bool = False
    why: str = ""
    read: bool = False

    @property
    def who(self) -> str:
        return person(self.sender)

    def line(self) -> str:
        where = f" in {self.conversation}" if self.group and self.conversation else ""
        return f"{self.who}{where} on {app_name(self.app)}: „{clip(self.text)}”"


def clip(text: str, n: int = 300) -> str:
    t = re.sub(r"\s+", " ", text or "").strip()
    return t if len(t) <= n else t[:n - 1].rstrip() + "…"


def judge(m: Message, c: Optional[dict] = None) -> tuple[bool, str]:
    c = c or cfg()
    if any(same_person(m.sender, v, c) or (not m.group and same_person(m.conversation, v, c))
           for v in (c.get("vip") or [])):
        return True, "vip"
    low = (m.text or "").lower()
    if any(k and re.search(rf"(?<!\w){re.escape(k.lower())}(?!\w)", low) for k in (c.get("keywords") or [])):
        return True, "urgent"
    if m.group:
        if any(n and re.search(rf"(?<!\w){re.escape(n.lower())}(?!\w)", low) for n in (c.get("my_names") or [])):
            return True, "mention"
        return False, ""
    if low.rstrip().endswith("?"):
        return True, "question"
    return False, ""


class Inbox:
    def __init__(self, keep_s: float = KEEP_S, now: Callable[[], float] = time.time):
        self.keep_s, self.now = keep_s, now
        self.items: list[Message] = []
        self._lock = threading.Lock()

    def _prune(self) -> None:
        cut = self.now() - self.keep_s
        self.items = [m for m in self.items if m.at >= cut]

    def add(self, app: str, sender: str, text: str, conversation: str = "", group: bool = False,
            key: str = "") -> Optional[Message]:
        """The new message, or None when it is empty or one we already have (Android repeats notifications)."""
        sender, text = (sender or "").strip(), (text or "").strip()
        if not text or not sender:
            return None
        conversation = (conversation or sender).strip()
        digest = hashlib.sha1(f"{app}|{conversation}|{sender}|{text}".encode("utf-8")).hexdigest()[:16]
        with self._lock:
            self._prune()
            if any(m.key == digest for m in self.items):
                return None
            m = Message(key=digest, app=app, sender=sender, text=text, conversation=conversation,
                        group=bool(group), at=self.now())
            m.important, m.why = judge(m)
            self.items.append(m)
            self.items = self.items[-300:]
        logger.info(f"MESSAGES: new from {m.who} on {app_name(app)}" + (f" (important: {m.why})" if m.important else ""))
        return m

    def find(self, who: str = "", app: str = "", unread: bool = True, important: Optional[bool] = None) -> list[Message]:
        with self._lock:
            self._prune()
            out = list(self.items)
        if unread:
            out = [m for m in out if not m.read]
        if who:
            out = [m for m in out if same_person(m.sender, who) or same_person(m.conversation, who)]
        if app:
            a = app_name(app).lower()
            out = [m for m in out if a.split()[0] in app_name(m.app).lower()]
        if important is not None:
            out = [m for m in out if m.important == important]
        return out

    def latest_from(self, who: str, app: str = "") -> Optional[Message]:
        got = self.find(who=who, app=app, unread=False)
        return got[-1] if got else None

    def latest(self) -> Optional[Message]:
        got = self.find(unread=False)
        return got[-1] if got else None

    def mark_read(self, msgs: list[Message]) -> None:
        for m in msgs:
            m.read = True

    def clear(self) -> None:
        with self._lock:
            self.items = []


_inbox: Optional[Inbox] = None


def get_inbox() -> Inbox:
    global _inbox
    if _inbox is None:
        _inbox = Inbox()
    return _inbox


def should_announce(m: Message, c: Optional[dict] = None) -> bool:
    mode = str((c or cfg()).get("announce", "important")).lower()
    return mode == "all" or (mode == "important" and m.important)


def announcement(m: Message) -> str:
    where = f" in {m.conversation}" if m.group and m.conversation else ""
    return f"Message from {m.who}{where} on {app_name(m.app)}: „{clip(m.text)}”"


# ------------------------------------------------------------ tools
def tool_messages_check(who: str = "", app: str = "", **_):
    import json
    box = get_inbox()
    new = box.find(who=who, app=app)
    if not new:
        target = f" from {person(who)}" if who else ""
        return {"result": json.dumps({"new": 0}), "say": f"No new messages{target}, sir.", "exact": True}
    box.mark_read(new)
    important = [m for m in new if m.important]
    first = (important or new)[-5:]
    head = (f"You have {len(new)} new message{'s' if len(new) != 1 else ''}, sir"
            + (f", {len(important)} that matter" if important and len(important) < len(new) else "") + ". ")
    body = " ".join(m.line() + "." for m in first)
    more = f" And {len(new) - len(first)} more." if len(new) > len(first) else ""
    # the text is other people's words: spoken exactly as written, never handed to a model as instructions
    return {"result": json.dumps({"new": len(new), "important": len(important)}),
            "say": head + body + more, "exact": True}


def reply_target(to: str = "", app: str = "") -> Optional[Message]:
    box = get_inbox()
    if to and not re.fullmatch(r"\s*(them|him|her|it|that|this)\s*", to, re.I):
        return box.latest_from(to, app)
    return box.latest()


def _echoes_the_request(words: str, said: str) -> bool:
    """The 'message' is the command itself ('reply to my mom', 'send her a message'), not what to say."""
    w = re.sub(r"[^\w\s]", "", words.lower()).strip()
    s = re.sub(r"[^\w\s]", "", (said or "").lower())
    if not re.match(r"^(?:please\s+)?(?:reply|respond|answer|send|write|text|message|tell)\b", w):
        return False
    return w in s or bool(re.fullmatch(r"(?:please\s+)?(?:reply|respond|answer|send|write|text|message|tell)"
                                        r"(?:\s+(?:a|an|the|my|her|him|them|to|back|message|text|reply|mom|mum|"
                                        r"mama|dad))*", w))


_COMMAND_WORDS = set("""can could would will you please eva hey i i'd id want would like to reply respond answer send
write text message messages msg tell let know a an the my her him them back on in via whatsapp instagram insta dm
reply say saying that this it now quickly just and so""".split())


def said_a_message(said: str, who: str = "") -> bool:
    """Is there anything to send in this sentence, once the command and the person are taken out?
    'reply to my mum' -> no; 'tell Mom I'm on my way' -> yes."""
    names = {n.lower() for n in aliases(who)} if who else set()
    names |= {"mom", "mum", "mama", "mami", "mother", "dad", "tata"}
    words = re.findall(r"[\wăâîșşțţ']+", (said or "").lower())
    return any(w not in _COMMAND_WORDS and w not in names for w in words)


def fill_reply(args: dict, text: str) -> dict:
    """Before the confirmation: who exactly, which app, and the final words (Romanian for Mom). Runs once."""
    if args.get("final"):
        return args
    out = dict(args)
    m = reply_target(out.get("to", ""), out.get("app", ""))
    if m:
        out["to"] = m.who
        out.setdefault("app", app_name(m.app))
    elif out.get("to"):
        out["to"] = person(out["to"]) or out["to"]          # "mom" -> "Mom"
    if out.get("app"):
        out["app"] = app_name(out["app"])                   # "whatsapp" -> "WhatsApp"
    words = (out.get("text") or "").strip()
    if words and (_echoes_the_request(words, text) or not said_a_message(text, out.get("to", ""))):
        # Nov 23: "reply to my mum" came with a whole message the model took from earlier in the conversation.
        # The words to send must be in what you said now; otherwise she asks.
        out.pop("text", None)
        words = ""
    if not words:
        return out
    ro = bool(re.search(r"\bin romanian\b|\bîn română\b|\bin romana\b", text or "", re.I)) or \
        (str(out.get("language", "")).lower().startswith("ro")) or writes_romanian(out.get("to", ""))
    if ro:
        from core import romanian
        got = romanian.translate(words, "ro", "family")
        if got and len(got) > 2.2 * len(words) + 30:          # a translation adds nothing: this one invented
            logger.warning("MESSAGES: the translation came back much longer than the message; not using it")
            got = None
        if got:
            out["text"] = got
            out["language"] = "ro"
        else:
            out["untranslated"] = True
    out["final"] = True
    return out


def describe_reply(args: dict) -> str:
    to, app = args.get("to") or "them", args.get("app") or ""
    where = f" on {app}" if app else ""
    if not reply_target(args.get("to", ""), args.get("app", "")):
        note = " (I couldn't translate it, so it's in English)" if args.get("untranslated") else ""
        return f"message {to}{where or ' on WhatsApp'}{note}: „{(args.get('text') or '').strip()}”"
    note = " (I couldn't translate it, so it's in English)" if args.get("untranslated") else ""
    return f"reply to {to}{where}{note}: „{(args.get('text') or '').strip()}”"


def aliases(who: str, c: Optional[dict] = None) -> list[str]:
    """Every name a chat with this person may have on the phone ('Mom' -> Mom, Mama, Mami, Mum)."""
    c = c or cfg()
    p = person(who, c)
    return list(dict.fromkeys([p, *((c.get("contacts") or {}).get(p) or [])])) if p else []


def number_of(who: str, c: Optional[dict] = None) -> str:
    """A WhatsApp number from settings messages.numbers (in config/settings.local.yaml), digits only."""
    c = c or cfg()
    p = person(who, c)
    raw = next((v for k, v in (c.get("numbers") or {}).items() if same_person(k, p, c)), "")
    return re.sub(r"\D", "", str(raw or ""))


def tool_message_reply(to: str = "", text: str = "", app: str = "", **_):
    """Reply, or (Nov 23) send a new message: 'tell Mom I'm on my way' with no message from her to answer.
    1. a chat whose notification arrived since the phone started: its own Reply button (works locked, invisible);
    2. otherwise WhatsApp opens on that chat with the text written, and you tap send (needs their number in
       settings messages.numbers and an unlocked phone)."""
    import json
    from core import device
    words = (text or "").strip()
    if not words:
        return {"result": json.dumps({"error": "no text"}), "say": "What should I say, sir?"}
    m = reply_target(to, app)
    who = m.who if m else (person(to) if to else "")
    if not who:
        return {"result": json.dumps({"error": "no one"}), "say": "Who should I send it to, sir?", "exact": True}
    if m:
        out = device._ask("reply", "reply_message", {"conversation": m.conversation, "sender": m.sender,
                                                      "app": m.app, "text": words})
    else:
        out = device._ask("reply", "send_message", {"to": who, "names": aliases(who), "number": number_of(who),
                                                     "app": app_name(app or "whatsapp"), "text": words})
    if out.get("error") == "no phone":
        return {"result": json.dumps({"error": "no phone"}), "say": device.NO_PHONE, "exact": True}
    if out.get("ok") and out.get("opened"):
        logger.info(f"MESSAGES: chat with {who} opened with the text ready")
        return {"result": json.dumps({"opened": True, "to": who}),
                "say": f"It's written in your chat with {who}, sir. Tap send.", "exact": True}
    if not out.get("ok"):
        reason = out.get("error") or "no reason given"
        if reason == "no chat":
            reason = (f"{who} hasn't written since your phone started, and I don't have their number. Add it under "
                      f"messages, numbers in your settings, and I'll open the chat with the text ready"
                      if not number_of(who) else "WhatsApp didn't open")
        elif "notification" in reason:
            reason = "that chat's reply button doesn't work anymore. Open the chat and send it there"
        return {"result": json.dumps({"error": reason}), "say": f"I couldn't send it, sir: {reason}.", "exact": True}
    logger.info(f"MESSAGES: sent to {who}")
    return {"result": json.dumps({"sent": True, "to": who}), "say": f"Sent to {who}, sir.", "exact": True}


SCHEMAS = [
    {"type": "function", "function": {
        "name": "messages_check",
        "description": "Read the user's new WhatsApp or Instagram messages ('any messages?', 'what did Mom say?', "
                       "'read my WhatsApp').",
        "parameters": {"type": "object", "properties": {
            "who": {"type": "string", "description": "only messages from this person, if named"},
            "app": {"type": "string", "enum": ["whatsapp", "instagram"]}}}}},
    {"type": "function", "function": {
        "name": "message_reply",
        "description": "Send a WhatsApp or Instagram message: a reply to one the user received, or a new one ('reply "
                       "to Mom: I'll be home at 7', 'tell her I'm on my way', 'text Dan that I'm late', 'send my mom "
                       "a message'). The user's words go in text, as they said them. When they haven't said what "
                       "to write ('send a message to my mom'), call it without text: she asks what to say.",
        "parameters": {"type": "object", "properties": {
            "to": {"type": "string", "description": "who to reply to; empty for the last message"},
            "text": {"type": "string", "description": "what to say, in the user's words"},
            "app": {"type": "string", "enum": ["whatsapp", "instagram"]},
            "language": {"type": "string", "description": "'ro' only when the user asks for Romanian"}}}}},
]
FUNCTIONS = {"messages_check": tool_messages_check, "message_reply": tool_message_reply}
GUARDS = {"messages_check": r"\b(messages?|texts?|whats ?app|insta(gram)?|dm'?s?|wrote|say|said|mesaje?)\b",
          "message_reply": r"\b(reply|respond|answer|tell|text|write|send|message|răspunde|raspunde|spune|trimite)\b"}
CONFIRM = {"message_reply": describe_reply}
ASKS = {"message_reply": ("text", "What should I tell {to}, sir?")}
