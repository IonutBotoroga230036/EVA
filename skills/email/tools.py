"""Email skill (SCRIBE): thin voice wrappers over core/scribe.py. Drafts only, never sends."""

import json
import re

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
            "reply_to": {"type": "string", "description": "Only when the user says to reply: search for that email, e.g. 'from:tom'."},
            "to": {"type": "string", "description": "Recipient name or address for a new email."},
            "subject": {"type": "string"}}, "required": []}}},
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
        return {"result": json.dumps({"error": str(e)}), "say": gmail_error(e), "exact": True}


def gmail_error(e: Exception) -> str:
    """Plain words, not the raw HttpError with its URL."""
    import re
    m = re.search(r'returned "([^"]+)"', str(e)) or re.search(r"'message': '([^']+)'", str(e))
    if m:
        return f"Gmail refused that, sir: {m.group(1).rstrip('.')}."
    return f"Gmail didn't answer, sir: {str(e)[:100].rstrip('.')}."


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


# ---- drafting: content only from the user's own words, never invented (Sep 26 log)
_CONTENT = re.compile(
    r"\b(?:saying|that says|to say|say(?:ing)? that|tell(?:ing)? (?:him|her|them|\w+)(?: that)?|"
    r"ask(?:ing)? (?:him|her|them|\w+)(?: if| whether| to)?|let (?:him|her|them|\w+) know(?: that)?|about)\s+(.+)$", re.I)
_VAGUE = {"something", "anything", "something nice", "a message", "an email", "a mail", "it", "that", "this",
          "stuff", "whatever", "something for me", "something to him", "something to her"}
_REPLY_WORDS = re.compile(r"\b(reply|replies|respond|answer|get back to|write back)\b", re.I)


def draft_content(text: str) -> str:
    """What the email should say, taken from the user's own sentence; "" when they didn't say."""
    m = _CONTENT.search(text or "")
    content = m.group(1).strip(" .!?") if m else ""
    return "" if content.lower() in _VAGUE else content


_TO_RX = [re.compile(r"\b(?:e-?mail|mail|message|note|write)\b.{0,20}?\bto\s+([A-Z][\w'-]+(?:\s+[A-Z][\w'-]+)?)"),
          re.compile(r"\b(?:e-?mail|write to|message)\s+(?!Him\b|Her\b|Them\b|Me\b)([A-Z][\w'-]+(?:\s+[A-Z][\w'-]+)?)")]


def recipient_from(text: str) -> str:
    """'send an email to Muaad' -> 'Muaad' (Whisper capitalises names)."""
    for rx in _TO_RX:
        m = rx.search(text or "")
        if m and m.group(1).lower() not in {"him", "her", "them", "me", "you", "someone", "somebody"}:
            return m.group(1)
    return ""


def _fill_draft(args: dict, text: str) -> dict:
    out = dict(args)
    if not out.get("to") and not out.get("reply_to"):
        who = recipient_from(text)
        if who:
            out["to"] = who
    out["instructions"] = draft_content(text)            # the model's paraphrase is never used
    if not _REPLY_WORDS.search(text or ""):
        out.pop("reply_to", None)                        # no "reply" in what you said: no reply target
    if out.get("to"):
        out["to"] = re.sub(r"\s*\([^)]*\)", "", out["to"]).strip()   # "Muaad Sucule (Canva)" -> "Muaad Sucule"
    return out


def describe_draft(args: dict) -> str:
    who = args.get("to") or (f"the sender of the email matching {args['reply_to']}" if args.get("reply_to") else "them")
    kind = "a reply to" if args.get("reply_to") else "an email to"
    return f'draft {kind} {who} saying: "{args.get("instructions", "")}"'


def email_draft(instructions: str = "", reply_to: str = "", to: str = "", subject: str = "", **_):
    if not (instructions or "").strip():
        who = to or "them"
        return {"result": json.dumps({"need": "what the email should say"}), "exact": True,
                "say": f"What should the email to {who} say, sir?", "ask_next": {"field": "instructions"}}
    name = next((ln.split("name is", 1)[1].split(".")[0].strip() for ln in about_lines() if "name is" in ln), "")
    cfg = local_cfg()
    return _guarded(scribe.draft, instructions, reply_to=reply_to, to=to, subject=subject, sender_name=name,
                    base_url=cfg["base_url"], model=cfg["answer_model"])


def email_delete_drafts(**_):
    made = scribe.made_drafts()
    if not made:
        return {"result": json.dumps({"deleted": 0}), "exact": True,
                "say": "There are no drafts of mine to delete, sir. I never touch drafts you wrote yourself."}
    try:
        deleted, gone = scribe.delete_made_drafts(_google())
    except Exception as e:
        return {"result": json.dumps({"error": str(e)}), "exact": True, "say": gmail_error(e)}
    extra = f" {gone} {'was' if gone == 1 else 'were'} already gone." if gone else ""
    return {"result": json.dumps({"deleted": deleted, "already_gone": gone}), "exact": True,
            "say": f"Deleted the {deleted} draft{'s' if deleted != 1 else ''} I made, sir.{extra} Your own drafts are untouched."}


def _describe_delete(args: dict) -> str:
    made = scribe.made_drafts()
    who = ", ".join(dict.fromkeys(d.get("to", "") for d in made if d.get("to")))
    return f"delete the {len(made)} email draft{'s' if len(made) != 1 else ''} I made" + (f" (to {who})" if who else "")


FUNCTIONS = {"email_unread": email_unread, "email_important": email_important,
             "email_sender_pref": email_sender_pref, "email_search": email_search,
             "email_read": email_read, "email_draft": email_draft, "email_delete_drafts": email_delete_drafts}
TOOLS = TOOLS + [{"type": "function", "function": {"name": "email_delete_drafts", "description":
                  "Delete the Gmail drafts E.V.A. made (never the user's own drafts).",
                  "parameters": {"type": "object", "properties": {}}}}]
ACKS = {"email_unread": "Checking your inbox, sir.", "email_important": "Going through your inbox, sir.",
        "email_search": "Searching your email, sir.",
        "email_read": "Opening it, sir.", "email_draft": "Writing it now, sir."}
ACTIONS = ["email_draft", "email_sender_pref", "email_delete_drafts"]
GUARDS = {n: r"\b(e-?mails?|mail|inbox|gmail|messages?|reply|replies|respond|draft|write to|wrote|sent me)\b"
          for n in FUNCTIONS}
# a request ABOUT how she writes email ("before you write emails, ask me...") is not a request to write one
GUARDS["email_delete_drafts"] = r"\b(delete|remove|discard|clear|get rid of|trash)\b.{0,40}\bdrafts?\b"
GUARDS["email_draft"] = (r"^(?!.*\b(?:before you|from now on|next time|in the future|whenever you|every time you)\b)"
                         r".*\b(e-?mail|mail|reply|respond|draft|write to|write back)\b")
CONFIRM = {"email_draft": describe_draft, "email_delete_drafts": _describe_delete}
def _ask_draft(args: dict):
    """Who first, then what. Only then "shall I go ahead?"."""
    if not args.get("to") and not args.get("reply_to"):
        return ("to", "Who should the email go to, sir?")
    if not str(args.get("instructions") or "").strip():
        return ("instructions", "What should the email to {to} say, sir?")
    return None


ASK = {"email_draft": _ask_draft}
FILLERS = {"email_draft": _fill_draft}
