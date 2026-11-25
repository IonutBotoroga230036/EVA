"""Chats (v0.7): conversations with names, like Gemini's sidebar. One memory across all of them.

Every window turn is written to a chat (data/chats/<id>.json): what you said, what she answered, the time. The
window's History drawer lists them (newest first), searches them, opens an old one and carries on in it, renames
and deletes. What she knows about you (CORTEX: facts, notes, past turns) is shared by every chat; a chat only
decides which recent turns she has in front of her.

    a window opens            -> it continues the chat from the last two hours, or starts a fresh one
    "New chat"                -> a fresh one (created on the first sentence, so empty chats never pile up)
    the first answer          -> a short title: Claude Haiku when the cloud is allowed, else the first words

Nothing here talks to the internet except the title (a few dozen tokens), and only when the cloud is allowed.
"""

from __future__ import annotations

import json
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Optional

from loguru import logger

DIR = Path("data/chats")
CONTINUE_WITHIN_S = 2 * 3600            # a window opened within two hours of the last turn carries on
MAX_TURNS = 2000                        # per chat file
THREADED = True                         # tests name chats inline
_lock = threading.RLock()


def _path(cid: str) -> Path:
    if not re.fullmatch(r"[a-f0-9]{10}", cid or ""):
        raise ValueError("no such chat")
    return DIR / f"{cid}.json"


def _read(cid: str) -> Optional[dict]:
    try:
        d = json.loads(_path(cid).read_text(encoding="utf-8"))
        return d if isinstance(d, dict) and d.get("id") == cid else None
    except (OSError, ValueError):
        return None


def _write(chat: dict) -> None:
    DIR.mkdir(parents=True, exist_ok=True)
    p = _path(chat["id"])
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(chat, ensure_ascii=False, indent=0), encoding="utf-8")
    tmp.replace(p)


def quick_title(text: str) -> str:
    """The first words of the first sentence, as a stand-in until (or instead of) a real title."""
    t = re.sub(r"^(hey |ok |okay )?(eva|e\.v\.a\.?)[,!.]?\s*", "", " ".join((text or "").split()), flags=re.I)
    t = re.sub(r"^(can you|could you|please|would you)\s+", "", t, flags=re.I)
    words = t.split()[:6]
    out = " ".join(words).rstrip(" ?.!,")
    return (out[:1].upper() + out[1:])[:48] or "New chat"


def new(title: str = "") -> dict:
    now = time.time()
    chat = {"id": uuid.uuid4().hex[:10], "title": title or "New chat", "titled": bool(title), "created": now,
            "updated": now, "turns": []}
    with _lock:
        _write(chat)
    return chat


def get(cid: str) -> Optional[dict]:
    with _lock:
        return _read(cid)


def add(cid: str, role: str, text: str, proactive: bool = False) -> Optional[dict]:
    """Append one line to a chat. role: 'you' or 'eva'."""
    text = (text or "").strip()
    if not text or role not in ("you", "eva"):
        return None
    with _lock:
        chat = _read(cid)
        if chat is None:
            return None
        turn = {"role": role, "text": text[:8000], "ts": round(time.time(), 3)}
        if proactive:
            turn["proactive"] = True
        chat["turns"] = (chat["turns"] + [turn])[-MAX_TURNS:]
        chat["updated"] = turn["ts"]
        if not chat.get("titled") and role == "you" and chat["title"] == "New chat":
            chat["title"] = quick_title(text)
        _write(chat)
        return chat


def _summary(chat: dict) -> dict:
    last = next((t["text"] for t in reversed(chat["turns"]) if t["role"] == "you"), "")
    return {"id": chat["id"], "title": chat["title"], "updated": chat["updated"], "created": chat["created"],
            "count": len(chat["turns"]), "preview": last[:90]}


def listing(limit: int = 200) -> list[dict]:
    with _lock:
        chats = [c for c in (_read(p.stem) for p in DIR.glob("*.json")) if c and c["turns"]] if DIR.exists() else []
    chats.sort(key=lambda c: -c["updated"])
    return [_summary(c) for c in chats[:limit]]


def search(q: str, limit: int = 30) -> list[dict]:
    """Chats whose title or words match, with the line that matched."""
    words = [w for w in re.findall(r"\w+", (q or "").lower()) if len(w) > 1]
    if not words:
        return listing(limit)
    out = []
    with _lock:
        chats = [c for c in (_read(p.stem) for p in DIR.glob("*.json")) if c and c["turns"]] if DIR.exists() else []
    for c in sorted(chats, key=lambda c: -c["updated"]):
        title = c["title"].lower()
        hit = None
        for t in c["turns"]:
            low = t["text"].lower()
            if all(w in low or w in title for w in words):
                hit = t["text"]
                break
        if hit is None and all(w in title for w in words):
            hit = c["turns"][0]["text"]
        if hit is not None:
            s = _summary(c)
            i = max(0, hit.lower().find(words[0]) - 30)
            s["match"] = ("..." if i else "") + hit[i:i + 110]
            out.append(s)
        if len(out) >= limit:
            break
    return out


def rename(cid: str, title: str) -> dict:
    title = " ".join((title or "").split())[:60]
    if not title:
        raise ValueError("a chat needs a name")
    with _lock:
        chat = _read(cid)
        if chat is None:
            raise ValueError("no such chat")
        chat["title"], chat["titled"] = title, True
        _write(chat)
        return _summary(chat)


def delete(cid: str) -> bool:
    with _lock:
        p = _path(cid)
        if not p.exists():
            return False
        p.unlink()
        return True


def latest_recent(within_s: float = CONTINUE_WITHIN_S) -> Optional[dict]:
    """The chat a newly opened window continues: the last one, if you talked in it lately."""
    items = listing(1)
    if items and time.time() - items[0]["updated"] < within_s:
        return get(items[0]["id"])
    return None


def history(chat: dict, turns: int = 24) -> list[dict]:
    """The chat's last lines as the orchestrator's history (roles alternate as they were said)."""
    out = []
    for t in chat.get("turns", [])[-turns:]:
        out.append({"role": "user" if t["role"] == "you" else "assistant", "content": t["text"]})
    return out


def public(chat: dict) -> dict:
    return {"id": chat["id"], "title": chat["title"], "updated": chat["updated"],
            "turns": chat["turns"][-400:]}


# ---------------------------------------------------------------- titles
def _cloud_ok() -> bool:
    try:
        from core.understand.brains import cloud_allowed
        return cloud_allowed()
    except Exception:
        return False


def make_title(cid: str, on_done=None) -> None:
    """After the first answer: a 2 to 5 word title from Claude Haiku (in a thread), if the cloud is allowed."""
    chat = get(cid)
    if not chat or chat.get("titled") or len(chat["turns"]) < 2:
        return
    lines = "\n".join(f"{'Him' if t['role'] == 'you' else 'Eva'}: {t['text'][:300]}" for t in chat["turns"][:4])

    def go():
        title = ""
        if _cloud_ok():
            try:
                from core.claude import get_claude
                out = get_claude().message(
                    system="Name this conversation in 2 to 5 words, like a chat title in a sidebar. Plain words, "
                           "no quotes, no punctuation at the end, no dashes.",
                    messages=[{"role": "user", "content": lines}], model="claude-haiku-5-5", max_tokens=20,
                    purpose="chat_title")
                title = re.sub(r"[\"'`*#]|[.!?]+$", "", (out.get("text") or "").strip().splitlines()[0])[:48]
            except Exception as e:
                logger.info(f"CHATS: no title from Claude ({str(e)[:80]})")
        with _lock:
            c = _read(cid)
            if not c or c.get("titled"):
                return
            if title:
                c["title"] = title
            c["titled"] = True
            _write(c)
        if title and on_done:
            try:
                on_done(cid, c["title"])
            except Exception:
                pass
    if THREADED:
        threading.Thread(target=go, name="chat-title", daemon=True).start()
    else:
        go()


# ---------------------------------------------------------------- her tool: look through earlier chats
def tool_find_in_chats(query: str = "", **_):
    """What was said in other chats ('what did we decide in the guitar chat?')."""
    hits = search(query, 5)
    out = []
    for h in hits:
        chat = get(h["id"]) or {"turns": []}
        words = [w for w in re.findall(r"\w+", (query or "").lower()) if len(w) > 1]
        lines = [t for t in chat["turns"] if any(w in t["text"].lower() for w in words)][:4] or chat["turns"][:2]
        out.append({"chat": h["title"], "when": time.strftime("%d %b %H:%M", time.localtime(h["updated"])),
                    "lines": [f"{'He' if t['role'] == 'you' else 'You'}: {t['text'][:400]}" for t in lines]})
    return {"result": json.dumps({"query": query, "chats": out} if out else
                                 {"query": query, "chats": [], "note": "nothing in earlier chats matches"},
                                 ensure_ascii=False)}


SCHEMAS = [{"type": "function", "function": {
    "name": "find_in_chats",
    "description": "Look through his earlier chats with you (the named conversations in the window) when he "
                   "refers to something said in another chat: 'what did we decide in the guitar chat', 'the shop "
                   "you found yesterday'. Give a few key words.",
    "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}}}]
FUNCTIONS = {"find_in_chats": tool_find_in_chats}
GUARDS = {"find_in_chats": r"\b(chat|chats|talked|said|told|decided|discussed|conversation|earlier|yesterday|"
                           r"last (time|week)|before)\b"}
