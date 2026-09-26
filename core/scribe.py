"""SCRIBE: email logic. Reads, searches, and DRAFTS. Never sends; drafts wait in Gmail."""

from __future__ import annotations

import re
from email.utils import parseaddr

import httpx
from loguru import logger


def who(from_header: str) -> str:
    name, addr = parseaddr(from_header or "")
    return (name or addr.split("@")[0] or "someone").strip('"')


def _join(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + ", and " + items[-1]


def unread(google, max_results: int = 5) -> dict:
    total, msgs = google.list_messages("is:unread in:inbox", max_results)
    widget = {"kind": "email", "title": "Unread", "emails": [{"from": who(m["from"]), "subject": m["subject"],
                                                              "snippet": m["snippet"][:120]} for m in msgs]}
    if not msgs:
        return {"count": 0, "say": "Your inbox is clear, sir. No unread emails.", "widget": widget}
    total = max(total, len(msgs))
    top = [f"{who(m['from'])} about {m['subject']}" for m in msgs[:3]]
    return {"count": total, "emails": widget["emails"], "widget": widget,
            "say": f"You have {total} unread email{'s' if total != 1 else ''}, sir. The latest: {_join(top)}."}


def important(google, max_scan: int = 25, use_llm=None) -> dict:
    """What deserves attention among recent unread mail."""
    from core.triage import classify
    total, msgs = google.list_messages("is:unread in:inbox newer_than:7d", max_scan)
    ranked = sorted(classify(msgs, google, use_llm), key=lambda m: m["score"], reverse=True)
    keep = [m for m in ranked if m["important"]]
    widget = {"kind": "email", "title": "Worth your attention", "emails": [
        {"from": who(m["from"]), "subject": m["subject"], "snippet": "; ".join(m["reasons"][:2])} for m in keep[:5]]}
    rest = max(total, len(msgs)) - len(keep)
    if not keep:
        return {"important": [], "widget": widget,
                "say": f"Nothing important, sir. The {rest} unread from this week are newsletters, promotions, "
                       f"or notifications." if rest else "Nothing new this week, sir."}
    names = [f"{who(m['from'])} about {m['subject']}" for m in keep[:3]]
    extra = f", plus {len(keep) - 3} more" if len(keep) > 3 else ""
    tail = f" The other {rest} can wait." if rest > 0 else ""
    return {"important": [{"from": who(m["from"]), "subject": m["subject"], "why": m["reasons"]} for m in keep],
            "widget": widget,
            "say": f"{len(keep)} email{'s look' if len(keep) != 1 else ' looks'} important, sir: "
                   f"{_join(names)}{extra}.{tail}"}


def search(google, query: str, max_results: int = 5) -> dict:
    total, msgs = google.list_messages(query, max_results)
    if not msgs:
        return {"count": 0, "say": f"I found no emails matching {query}, sir."}
    top = [f"{who(m['from'])} about {m['subject']}" for m in msgs[:3]]
    return {"count": total, "emails": [{"id": m["id"], "from": who(m["from"]), "subject": m["subject"],
                                        "snippet": m["snippet"][:160]} for m in msgs],
            "widget": {"kind": "email", "title": f"Search · {query}", "emails": [
                {"from": who(m["from"]), "subject": m["subject"], "snippet": m["snippet"][:120]} for m in msgs]},
            "say": f"I found {total} email{'s' if total != 1 else ''}, sir. Top: {_join(top)}."}


def read(google, query: str = "") -> dict:
    q = query.strip() or "in:inbox"
    _, msgs = google.list_messages(q, 1)
    if not msgs:
        return {"error": "not found", "say": "I couldn't find that email, sir."}
    m = msgs[0]
    body = re.sub(r"\n{3,}", "\n\n", google.get_body(m["id"]))[:3000]
    return {"from": who(m["from"]), "subject": m["subject"], "date": m["date"], "body": body}


def compose_body(instructions: str, original: str = "", sender_name: str = "", base_url: str = "",
                 model: str = "") -> str:
    """Write the email text with the local model: private, and it's only a draft anyway."""
    prompt = ("Write the body of an email. Plain text only, no subject line, no placeholders. Short, warm, "
              "direct. Never use em-dashes." + (f" Sign it '{sender_name}'." if sender_name else "") +
              f"\n\nWhat to say: {instructions}" + (f"\n\nThe email being replied to:\n{original[:1500]}" if original else ""))
    try:
        r = httpx.post(f"{base_url}/api/chat", timeout=120, json={
            "model": model, "stream": False, "options": {"temperature": 0.4},
            "messages": [{"role": "user", "content": prompt}]})
        text = r.json()["message"]["content"].strip()
    except Exception as e:
        logger.warning(f"SCRIBE: local model couldn't write the draft ({e})")
        text = instructions
    return text.replace("\u2014", ", ")


def _fold(text: str) -> str:
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFKD", text or "") if not unicodedata.combining(c)).lower()


def resolve_address(google, name: str):
    """A name -> the one email address it matches in your recent mail, or an exact question.

    Returns the address, or {"error", "say"} when there is none ("What is it?") or more than one ("Which one?").
    Never guesses: Gmail would reject a bare name anyway ("Invalid To header")."""
    import re as _re
    q = " ".join((name or "").split())
    key = _fold(q)
    try:
        _, msgs = google.list_messages(f'from:"{q}" OR to:"{q}" OR cc:"{q}"', 15)
    except Exception:
        msgs = []
    found: dict[str, str] = {}
    for m in msgs:
        header = ", ".join(m.get(k, "") or "" for k in ("from", "to", "cc"))
        # tolerant parse: email.utils.getaddresses drops non-ASCII names like "Ionuț Boțoroga"
        pairs = [(d.strip(' "\''), a) for d, a in _re.findall(r'([^<>,]*)<([^<>\s]+@[^<>\s]+)>', header)]
        pairs += [("", a) for a in _re.findall(r'(?<![<\w.+-])([\w.+-]+@[\w-]+\.[\w.-]+)(?![\w>])', header)]
        for disp, addr in pairs:
            if "@" in addr and key and (key in _fold(disp) or key in _fold(addr.split("@")[0])):
                found.setdefault(addr.lower(), disp or addr)
    if len(found) == 1:
        return next(iter(found))
    if not found:
        return {"error": "no address", "say": f"I don't have an email address for {q}, sir. What is it?"}
    opts = list(found)[:3]
    return {"error": "ambiguous", "say": f"I found more than one address for {q}, sir: {', '.join(opts)}. Which one?"}


def draft(google, instructions: str, reply_to: str = "", to: str = "", subject: str = "",
          sender_name: str = "", base_url: str = "http://localhost:11434", model: str = "qwen2.5:3b-instruct") -> dict:
    thread_id = in_reply_to = original = ""
    recipient = to
    if reply_to:
        _, msgs = google.list_messages(reply_to, 1)
        if not msgs:
            return {"error": "not found", "say": f"I couldn't find an email matching {reply_to} to reply to, sir."}
        m = msgs[0]
        thread_id, in_reply_to = m["thread_id"], m["message_id"]
        recipient = parseaddr(m["from"])[1]
        subject = m["subject"] if m["subject"].lower().startswith("re:") else f"Re: {m['subject']}"
        original = google.get_body(m["id"])
    if not recipient:
        return {"error": "no recipient", "say": "Who should the email go to, sir?"}
    if "@" not in recipient:                              # "Muaad": find the address in your mail
        found = resolve_address(google, recipient)
        if isinstance(found, dict):
            return found
        recipient = found
    body = compose_body(instructions, original, sender_name, base_url, model)
    google.create_draft(recipient, subject or "(no subject)", body, thread_id, in_reply_to)
    target = who(recipient) if not reply_to else who(m["from"])
    return {"drafted_to": recipient, "subject": subject, "body": body,
            "widget": {"kind": "note", "title": f"Draft to {target}", "text": body[:300]},
            "say": f"I've drafted {'a reply' if reply_to else 'an email'} to {target}, sir. It's in your Gmail "
                   f"drafts, nothing was sent."}
