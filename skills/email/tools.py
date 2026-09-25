"""Email skill (SCRIBE): thin voice wrappers over core/scribe.py. Drafts only, never sends."""

import json

from core import scribe
from core.google_api import NeedAuth, auth_message, get_google
from core.prompt_builder import about_lines
from core.settings import local_cfg

TOOLS = [
    {"type": "function", "function": {
        "name": "email_unread",
        "description": "Check the inbox for unread emails.",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "email_important",
        "description": "Find the emails that actually matter (people, work, study, deadlines), ignoring promotions "
                       "and newsletters. Use for 'anything interesting or important in my email'.",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "email_sender_pref",
        "description": "Remember that emails from a sender or about a topic are important, or should be ignored.",
        "parameters": {"type": "object", "properties": {
            "who": {"type": "string", "description": "A name, address, domain, or word, e.g. 'tom', 'instagram'."},
            "important": {"type": "boolean"}}, "required": ["who", "important"]}}},
    {"type": "function", "function": {
        "name": "email_search",
        "description": "Search emails. query uses Gmail search, e.g. 'from:tom', 'Deloitte', 'subject:invoice'.",
        "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}}},
    {"type": "function", "function": {
        "name": "email_read",
        "description": "Read one email (the latest, or the latest matching a search) to summarise it.",
        "parameters": {"type": "object", "properties": {"query": {"type": "string"}}}}},
    {"type": "function", "function": {
        "name": "email_draft",
        "description": "Draft an email or a reply into Gmail drafts. It is never sent automatically.",
        "parameters": {"type": "object", "properties": {
            "instructions": {"type": "string", "description": "What the email should say, in the user's words."},
            "reply_to": {"type": "string", "description": "Search for the email to reply to, e.g. 'from:tom'."},
            "to": {"type": "string", "description": "Recipient address for a new email."},
            "subject": {"type": "string"}}, "required": ["instructions"]}}},
]


def _google():
    return get_google()


def _wrap(out: dict, exact: bool = True) -> dict:
    res = {"result": json.dumps({k: v for k, v in out.items() if k not in ("say", "widget")}, default=str)}
    if out.get("say"):
        res["say"] = out["say"]
        res["exact"] = exact
    if out.get("widget"):
        res["widget"] = out["widget"]
    return res


def _guarded(fn, *a, exact=True, **k):
    try:
        return _wrap(fn(_google(), *a, **k), exact)
    except NeedAuth:
        return {"result": json.dumps({"error": "not connected to Google"}), "say": auth_message(), "exact": True}
    except Exception as e:
        return {"result": json.dumps({"error": str(e)}), "say": f"Gmail didn't answer, sir: {str(e)[:120]}.",
                "exact": True}


def email_unread(**_):
    return _guarded(scribe.unread)


def email_important(**_):
    return _guarded(scribe.important)


def email_sender_pref(who: str = "", important: bool = True, **_):
    from core.triage import set_sender_pref
    if not who.strip():
        return {"result": json.dumps({"error": "who?"}), "say": "Which sender, sir?"}
    set_sender_pref(who, bool(important))
    say = (f"Noted, sir. I'll always tell you about {who}." if important
           else f"Understood, sir. I won't bring up {who} again.")
    return {"result": json.dumps({"who": who, "important": bool(important)}), "say": say}


def email_search(query: str = "", **_):
    return _guarded(scribe.search, query)


def email_read(query: str = "", **_):
    return _guarded(scribe.read, query, exact=False)          # the model summarises the body


def email_draft(instructions: str = "", reply_to: str = "", to: str = "", subject: str = "", **_):
    name = next((ln.split("name is", 1)[1].split(".")[0].strip() for ln in about_lines() if "name is" in ln), "")
    cfg = local_cfg()
    return _guarded(scribe.draft, instructions, reply_to=reply_to, to=to, subject=subject, sender_name=name,
                    base_url=cfg["base_url"], model=cfg["answer_model"])


FUNCTIONS = {"email_unread": email_unread, "email_important": email_important,
             "email_sender_pref": email_sender_pref, "email_search": email_search,
             "email_read": email_read, "email_draft": email_draft}
ACKS = {"email_unread": "Checking your inbox, sir.", "email_important": "Going through your inbox, sir.",
        "email_search": "Searching your email, sir.",
        "email_read": "Opening it, sir.", "email_draft": "Writing it now, sir."}
ACTIONS = ["email_draft", "email_sender_pref"]
GUARDS = {n: r"\b(e-?mails?|mail|inbox|gmail|messages?|reply|replies|respond|draft|write to|wrote|sent me)\b"
          for n in FUNCTIONS}
