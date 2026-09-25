"""
Email triage: which emails deserve your attention.

Every email gets a score from signals, fast and deterministic:
  + a person you have emailed before (from your Sent mail, cached daily)       +3
  + on your VIP list ("emails from Tom are important")        +15 (your word decides)
  + Gmail marked it Important, or it's in the Primary tab                      +2 each
  + a reply in one of your threads                                             +2
  + urgency or business words (deadline, interview, invoice, offer, contract)   +2
  + sent to you directly, not a list                                           +1
  - Promotions / Social / Forums tabs, or a newsletter (List-Unsubscribe)       -4
  - noreply / notifications / marketing senders                                -3
  - on your mute list ("don't tell me about Instagram")       -15 (your word decides)
Score >= 3 is important, < 0 is noise, 0 to 2 is borderline. Borderline emails can get a
second opinion from the LOCAL model (settings email.llm_triage), so nothing leaves the PC.
"""

from __future__ import annotations

import json
import re
import time
from email.utils import parseaddr
from pathlib import Path
from typing import Optional

import httpx
from loguru import logger

PREFS = Path("data/email_prefs.json")
CONTACTS = Path("data/google/contacts.json")
IMPORTANT, NOISE = 3, 0
_URGENT = re.compile(r"\b(urgent|asap|deadline|due|overdue|interview|offer|contract|invoice|payment|"
                     r"application|admission|enrol+ment|exam|grade|appointment|meeting|call|proposal|"
                     r"quote|order confirm|job|position|visa|tax|belasting|rent|lease|important|action required)\b",
                     re.I)
_AUTOMATED = re.compile(r"(no-?reply|do-?not-?reply|notifications?|newsletter|marketing|mailer|news@|info@|"
                        r"updates?@|promo|offers?@|hello@|team@|digest)", re.I)


def _addr(header: str) -> str:
    return parseaddr(header or "")[1].lower()


def load_prefs() -> dict:
    try:
        return json.loads(PREFS.read_text(encoding="utf-8"))
    except Exception:
        return {"vip": [], "mute": []}


def save_prefs(prefs: dict) -> None:
    PREFS.parent.mkdir(parents=True, exist_ok=True)
    PREFS.write_text(json.dumps(prefs, indent=2), encoding="utf-8")


def set_sender_pref(who: str, important: bool) -> dict:
    who = who.strip().lower()
    prefs = load_prefs()
    for key in ("vip", "mute"):
        prefs[key] = [x for x in prefs.get(key, []) if x != who]
    prefs["vip" if important else "mute"].append(who)
    save_prefs(prefs)
    return prefs


def known_contacts(google, max_age_s: int = 86400) -> set[str]:
    try:
        data = json.loads(CONTACTS.read_text(encoding="utf-8"))
        if time.time() - data["ts"] < max_age_s:
            return set(data["addresses"])
    except Exception:
        pass
    try:
        addrs = google.sent_recipients()
    except Exception as e:
        logger.warning(f"TRIAGE: couldn't read Sent mail ({e})")
        return set()
    CONTACTS.parent.mkdir(parents=True, exist_ok=True)
    CONTACTS.write_text(json.dumps({"ts": time.time(), "addresses": sorted(addrs)}), encoding="utf-8")
    return addrs


def _matches(entry: str, m: dict) -> bool:
    hay = f"{m.get('from', '')} {m.get('subject', '')}".lower()
    return entry in hay


def score(m: dict, contacts: set[str], prefs: Optional[dict] = None) -> tuple[int, list[str]]:
    prefs = prefs or load_prefs()
    labels = set(m.get("labels", []))
    sender = _addr(m.get("from", ""))
    s, why = 0, []

    def add(n, reason):
        nonlocal s
        s += n
        why.append(reason)
    if any(_matches(v, m) for v in prefs.get("vip", [])):
        add(15, "on your VIP list")
    if any(_matches(v, m) for v in prefs.get("mute", [])):
        add(-15, "muted")
    if sender and sender in contacts:
        add(3, "someone you've emailed")
    if "IMPORTANT" in labels:
        add(2, "Gmail marked it important")
    if "CATEGORY_PERSONAL" in labels:
        add(2, "primary inbox")
    if labels & {"CATEGORY_PROMOTIONS", "CATEGORY_SOCIAL", "CATEGORY_FORUMS"}:
        add(-4, "promotions or social")
    if m.get("bulk"):
        add(-4, "newsletter or mailing list")
    if _AUTOMATED.search(sender):
        add(-3, "automated sender")
    if m.get("in_reply_to") or re.match(r"^\s*re:", m.get("subject", ""), re.I):
        add(2, "a reply in your thread")
    if _URGENT.search(f"{m.get('subject', '')} {m.get('snippet', '')}"):
        add(2, "sounds time-sensitive")
    if not m.get("bulk") and m.get("to") and "," not in m.get("to", ""):
        add(1, "sent directly to you")
    return s, why


def llm_opinion(m: dict, base_url: str, model: str) -> Optional[bool]:
    """Second opinion from the local model for borderline mail. None if it can't say."""
    schema = {"type": "object", "properties": {"important": {"type": "boolean"}}, "required": ["important"]}
    prompt = ("Is this email something a busy person would want to be told about now: a real person, work, "
              "study, money, deadlines, appointments? Promotions, newsletters, social notifications and "
              f"automated updates are NOT important.\nFrom: {m.get('from')}\nSubject: {m.get('subject')}\n"
              f"Preview: {m.get('snippet', '')[:300]}")
    try:
        r = httpx.post(f"{base_url}/api/chat", timeout=20, json={
            "model": model, "stream": False, "format": schema, "options": {"temperature": 0},
            "messages": [{"role": "user", "content": prompt}]})
        return bool(json.loads(r.json()["message"]["content"])["important"])
    except Exception:
        return None


def classify(msgs: list[dict], google, use_llm: Optional[bool] = None) -> list[dict]:
    """Adds 'score', 'reasons', 'important' to each message."""
    from core.settings import get_settings, local_cfg
    if use_llm is None:
        use_llm = bool(get_settings().get("email", {}).get("llm_triage", True))
    contacts, prefs, cfg = known_contacts(google), load_prefs(), local_cfg()
    out = []
    for m in msgs:
        s, why = score(m, contacts, prefs)
        important = s >= IMPORTANT
        if use_llm and NOISE <= s < IMPORTANT:
            verdict = llm_opinion(m, cfg["base_url"], cfg["decision_model"])
            if verdict is not None:
                important = verdict
                why.append("the local model thinks it matters" if verdict else "the local model thinks it can wait")
        out.append({**m, "score": s, "reasons": why, "important": important})
    return out
