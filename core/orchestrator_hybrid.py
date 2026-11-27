"""
HybridOrchestrator: E.V.A.'s brain (v0.2).

Per turn:
  0. PENDING CONFIRMATION. If the last turn asked "shall I go ahead?", a yes runs
     the held action, a no cancels it, anything else drops it and continues.
  1. FAST PATH for unambiguous commands (time, volume, media, open X, remember,
     forget, notes, screen). No LLM, and no embedding call either.
  2. Otherwise embed the message ONCE; the vector drives CORTEX recall and SKILL.md
     matching. Small talk and capability questions skip tools entirely.
  3. CONSTRAINED DECISION over a flat schema built from every loaded tool: built-ins,
     skills, and MCP servers. "unavailable" is an explicit choice -> honest answer.
  4. Before a tool runs: INTENT GUARD (the user's words must plausibly ask for that
     action), argument hygiene (an argument equal to a tool name is discarded),
     ARGUMENT REPAIR for missing required fields, repeat and dedup guards, and the
     CONFIRMATION GATE for tools that change things (MCP non-read-only, bulk deletes).
  5. Tools run in worker threads (MCP tools on the event loop), so the spoken ack
     goes out BEFORE the tool runs and audio keeps streaming while it works.
  6. ANSWER. If the turn used only actions that returned an exact "say" line, those
     lines are spoken verbatim with no LLM (instant, and never misreported).
     Otherwise the model phrases the answer; a DENIAL GUARD replaces "I can't see your
     screen" with the real observation when a tool just succeeded.
  7. Log both turns to CORTEX; extract facts in the background.

Events: ack / token / widget / final (unchanged).
"""

from __future__ import annotations

import asyncio
from collections import deque
import json
import re
import time
import uuid
from pathlib import Path
from typing import AsyncIterator, Callable

import httpx
import yaml
from loguru import logger

from core.events.bus import get_bus
from core.mcp_client import MCPManager, get_mcp
from core.memory.cortex import Cortex, get_cortex
from core.memory.extractor import extract_and_store
from core.prompt_builder import build_answer_system, build_decision_system
from core.security.audit import audit
from core.settings import get_settings, local_cfg
from core.tools_native import ACTION_TOOLS as BUILTIN_ACTIONS
from core.tools_native import ARG_FILLERS as BUILTIN_FILLERS
from core.tools_native import CONFIRM_TOOLS as BUILTIN_CONFIRM
from core.tools_native import SLOW_FILLERS as BUILTIN_SLOW_FILL
from core.tools_native import PRECHECK_TOOLS as BUILTIN_PRECHECK
from core.tools_native import ASK_TOOLS as BUILTIN_ASKS
from core.tools_native import EXACT_TOOLS as BUILTIN_EXACT
from core.tools_native import GUARDS as BUILTIN_GUARDS
from core.tools_native import REGISTRY as BUILTIN_FUNCS
from core.tools_native import TOOL_SCHEMAS as BUILTIN_SCHEMAS
from core.tools_native import ack_for as builtin_ack
from core.tools_native import execute_tool as builtin_execute
from core.tools_native import snip
from skills.registry import SkillRegistry, get_registry

MAX_TOOL_ITERS = 3
MAX_HISTORY = 12
CONFIRM_TTL_S = 120

STYLE_RULES = (
    "RESPONSE RULES:\n"
    "- Reply in one or two short sentences. Every word earns its place.\n"
    "- Give ONLY the final answer. Never narrate your steps or which tool you used.\n"
    "- Use the real data provided. Never invent facts, times, weather, notes, or memories.\n"
    "- If a tool result shows you did something or looked at something, you DID. Never say you can't.\n"
    "- Use remembered facts only when they genuinely help; don't recite them unprompted.\n"
    "- For weather, always say the place and the time the numbers are for, use the numbers "
    "exactly as given, and if the data says a time was assumed, say that time.\n"
    "- Only offer abilities you actually have (listed below). If asked for something you can't do, say so and "
    "offer to build it as a new skill. Never say you did something unless a tool result shows it.\n"
    "- Address the user as 'sir'."
)
# She claims an action happened ("has been added", "I've sent") but no action tool succeeded this turn
_CLAIM = re.compile(r"\b(?:has|have|had) been (?:added|scheduled|created|booked|sent|deleted|removed|cancel+ed|set|"
                    r"saved|installed|moved|updated)\b|\bi(?:'ve| have| just)? (?:added|scheduled|created|booked|sent|"
                    r"deleted|removed|cancel+ed|set up|saved|installed|moved|updated)\b|^\s*yes,? (?:it|that|an event)"
                    r"[^.]*\b(?:added|scheduled|sent|done)\b|^\s*(?:now )?playing\b|^\s*(?:i'?m |i am )?opening\b"
                    r"|\b(?:reminder|alarm|timer) (?:is |has been )?(?:set|added|created|started)\b|^\s*(?:done|added|scheduled|created|set)\b[ ,:]"
                    r"|\badded\b.{0,60}\bto your (?:calendar|list|notes|shopping list|reminders)\b"
                    r"|\blights? (?:are |is )?(?:now )?set to\b"
                    r"|\bi(?:'ve| have| just)? (?:ordered|bought|purchased|paid for|placed (?:the|an|your) order)\b"
                    # Nov 27 10:59: "Energy saver settings disabled." / "... stopped, sir." with nothing done
                    r"|\bi(?:'ve| have| just)? (?:disabled|enabled|stopped|turned (?:on|off)|switched (?:on|off)|dropped|"
                    r"cancel+ed|paused|ended|aborted)\b"
                    r"|^[^.?!]{0,60}\b(?:disabled|enabled|stopped|turned off|switched off|dropped|cancel+ed|paused|"
                    r"aborted)(?:,? sir)?[.!]?\s*$"
                    r"|\bi(?:'ve| have| just) (?:set|turned|changed|played|started|opened|put|drafted|sent|made)\b"
                    r"|^\s*i (?:set|turned|changed|played|started|opened|put|drafted|sent)\b"
                    r"|\b(?:e-?mail|message|draft|reminder)s? (?:was |were |has been |have been )?sent\b"
                    r"|\bsent (?:it |the e-?mail |your e-?mail |a message |the message )?to\b"
                    r"|\b(?:tasks?|all|everything) (?:is |are |has been |have been )?(?:completed|done|finished)\b"
                    r"|\b(?:calendar|event|reminder|list|playlist|routine)s? (?:is |was |has been )?(?:updated|changed|moved|booked)\b"
                    r"|^\s*(?:spotify |pc |system )?(?:volume|brightness) (?:is |set )?(?:now )?(?:at|to) \d+", re.I)

WEATHER_RX = re.compile(r"\b(weather|forecast|temperature|rain(?:ing|y)?|raincoat|umbrella|snow(?:ing)?|how (?:cold|warm|hot))\b",
                        re.I)
_PLACE_RX = re.compile(r"\b(?:in|for|at|around)\s+([A-Z][A-Za-z'\-]+(?:\s+[A-Z][A-Za-z'\-]+){0,2})")


def volume_level(n: int) -> int:
    """Whisper hears "to fifty" as "250": 200-300 means "to" + the number. Always 0-100."""
    if 200 <= n <= 300:
        n -= 200
    return max(0, min(100, n))


_CANCEL = re.compile(r"^\s*(?:no[,.!]?\s*)?(?:never ?mind|forget (?:it|about it)|don'?t(?: do it| bother)?|do not|cancel(?: that| it)?|"
                     r"nothing|no thanks?|it'?s (?:fine|ok|okay|alright)|leave it)(?:[\s,.!]+(?:it'?s (?:fine|ok|okay)|thanks?|"
                     r"please|sir))*[\s.!]*$", re.I)
_REPEAT = re.compile(r"^\s*(?:sorry,?\s*)?(?:can you |could you |would you |please )?(?:say (?:that|it) again|"
                     r"repeat (?:that|it|yourself|the last (?:part|thing))?|what did you (?:just )?say|come again|pardon)"
                     r"(?: please)?[\s?.!]*$", re.I)
_MD_LINK = re.compile(r"\[([^\]]+)\]\((?:https?://)[^)]+\)")


def spoken(text: str) -> str:
    """Markdown links and bold are for screens; she says the words."""
    return re.sub(r"\*\*|__", "", _MD_LINK.sub(r"\1", text or "")).strip()


def place_from(text: str) -> str:
    """'weather for Nijmegen tomorrow' -> 'Nijmegen'. Capitalised words only, so 'in the morning' isn't a city."""
    for m in _PLACE_RX.finditer(text or ""):
        cand = m.group(1).strip()
        if cand.lower() not in {"the", "my", "a", "this", "next", "monday", "tuesday", "wednesday", "thursday",
                                "friday", "saturday", "sunday", "i", "celsius", "fahrenheit"}:
            return cand
    return ""


NOT_DONE = "No, sir, I haven't done that. Nothing was changed. Tell me exactly what you'd like and I'll do it."
MISSING_SKILL = ("I don't have a skill for that yet, sir. Once FORGE is live I can build "
                 "one, with your approval.")
MISSING_SKILL_FORGE = "I don't have a skill for that yet, sir. Say 'build it' and I'll draft one for your approval."
# "unavailable" is only believed when the request actually names an ability we lack
_ABILITY_HINT = re.compile(
    r"\b(calendar|schedule|meeting|appointment|email|e-mail|mail|inbox|message|text|sms|whatsapp|call|phone|"
    r"light|lights|lamp|thermostat|heating|alarm|remind|reminder|timer|order|buy|book|reserve|pay|install|"
    r"download|print|3d|convert|translate|record|photo|picture|camera|upload|send|post|tweet|contact)\b", re.I)
_CAPABILITY_Q = re.compile(r"what (?:else )?can you (?:do|help)|what (?:else )?are you (?:able|capable)"
                           r"|your (?:capabilities|skills|features|abilities)|what do you do\b", re.I)
_SMALLTALK_Q = re.compile(          # questions about her: small talk at any length
    r"^\s*(?:and |so |well |okay |ok |yeah )?(?:how are you|how(?:'s| is) it going|how do you (?:feel|like)|"
    r"do you (?:like|enjoy|feel|think|want|love|prefer)|are you (?:ok|okay|happy|sad|alive|conscious|there)|"
    r"what do you think|what would you like|would you (?:like|want) to|who are you|what(?:'s| is) your name|"
    r"(?:it'?s|it is|that'?s|this is) (?:so |really |very |actually |pretty |super )*"
    r"(?:cool|nice|great|amazing|awesome|impressive|good|funny|interesting|crazy|wild))\b", re.I)
_INTERJECTION = re.compile(         # "thanks", "great" ... only when that's (nearly) the whole message
    r"^\s*(?:thank(?:s| you)|good (?:job|work)|well done|nice|cool|great|awesome|perfect|"
    r"that'?s (?:better|right|good|great|perfect)|alright|all right|okay|ok)\b", re.I)


def is_small_talk(text: str) -> bool:
    return bool(_SMALLTALK_Q.search(text)) or (bool(_INTERJECTION.search(text)) and len(text.split()) <= 4)
_DENIAL = re.compile(r"\b(?:i (?:can ?not|can't|am unable to|do not have|don't have)|i'?m (?:unable|not able)|"
                     r"as an ai)\b.{0,40}\b(?:see|access|view|look|open|do|help|read)\b", re.I)
# No made-up facts (Nov 22): "Tomorrow looks clear" with no calendar check, "Music stopped at that time" with
# nothing planned. A sentence about your calendar, email, weather or music, when no tool of that kind ran this turn,
# is replaced by an honest offer to check; "yes" then runs the check.
_FACTS = {
    "calendar": (re.compile(r"\byou(?:'re| are) (?:free|busy|clear)\b(?! to)|\b(?:looks?|is) (?:clear|wide open|busy|"
                            r"packed)\b|\b(?:day|morning|afternoon|evening|night|schedule|calendar|agenda|today|tonight|"
                            r"tomorrow|week|weekend|monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b[^.]{0,25}?"
                            r"\b(?:is|looks?|seems?) (?:free|open)\b|\bfree (?:slot|time|window)s?\b|\bno (?:meetings?|events?|class(?:es)?|"
                            r"appointments?|lectures?)\b|\byou have (?:a |an |no |\d+ |two |three )?(?:meetings?|events?|"
                            r"class(?:es)?|appointments?|lectures?)\b|\bnothing (?:on (?!your (?:pc|computer|laptop|phone|screen|disk|drive)\b)|scheduled|planned)\b|\byour "
                            r"(?:calendar|schedule) (?:is|looks)\b", re.I), ("calendar_",)),
    "email": (re.compile(r"\byou have (?:\d+|no|a|an|two|three|several) (?:new |unread )?(?:e-?mails?|messages?)\b|"
                         r"\bno new (?:e-?mails?|messages?)\b|\binbox is\b", re.I), ("email_", "messages_")),
    "weather": (re.compile(r"\b(?:it'?s|it is|it will be|expect|high of|low of|currently|temperature)\b[^.]{0,30}?-?\d+ ?"
                           r"(?:°|degrees)|\b(?:sunny|rainy|cloudy|overcast|snow(?:y|ing)?|rain(?:ing)?) (?:today|tomorrow|"
                           r"tonight|this (?:morning|afternoon|evening))\b", re.I), ("get_weather",)),
    "music": (re.compile(r"\b(?:music|song|playback|spotify)\b[^.]{0,25}\b(?:stopped|paused|resumed|will stop|"
                         r"will pause)\b|\b(?:lights?|music)\b[^.]{0,30}\bat \d{1,2}[:.]\d{2}\b", re.I),
              ("media_control", "spotify_", "later_", "lights_")),
}
_DAY_WORD = re.compile(r"\b(today|tonight|tomorrow|monday|tuesday|wednesday|thursday|friday|saturday|sunday|"
                       r"this week|next week)\b", re.I)


from core.mind import MIND_ONLY as _MIND_ONLY  # noqa: E402  (tools that need the mind's eyes)


_NEGATED = re.compile(r"(?:\bnothing|\bnot|n't|\bnever|\bno\b|\bwithout|\bwon't|\bcan't|\bwasn't|\bisn't)[^.!?]{0,30}$", re.I)


_NEGATION_IN = re.compile(r"\b(?:nothing|not|never|no|none|neither)\b|n't", re.I)


def claims(text: str) -> bool:
    """She says she did something (v0.8 _CLAIM), not counting 'Nothing has been moved' (Nov 27 10:01: the guard
    caught her saying nothing changed)."""
    for sent in re.split(r"(?<=[.!?])\s+", (text or "").strip()):      # v0.9: each sentence on its own
        for m in _CLAIM.finditer(sent):
            before = sent[max(0, m.start() - 40):m.start()]
            if not (_NEGATED.search(before) or _NEGATION_IN.search(m.group(0))):
                return True
    return False


# Nov 27 02:08: a story ("someone who had been sent to fix...") tripped the claim guard and was replaced by "No, sir,
# I haven't done that." Stories, poems and talks are words, not claims.
_CREATIVE = re.compile(r"\b(?:stor(?:y|ies)|tale|poem|poetry|haiku|joke|riddle|bedtime|fairy ?tale|once upon|imagine|"
                       r"pretend|role ?play|write (?:me )?(?:a|an|some) (?:story|poem|song|tale|letter)|make up|"
                       r"for \d+ minutes|\d+ minutes? long|continue|go on|keep going|next part)\b",
                       re.I)


def creative(text: str) -> bool:
    return bool(_CREATIVE.search(text or ""))


def unchecked_fact(text: str, tools: list[str]) -> str | None:
    """The kind of personal fact this answer states without having looked it up this turn, or None."""
    for kind, (rx, prefixes) in _FACTS.items():
        if rx.search(text or "") and not any(t.startswith(p) for t in tools for p in prefixes):
            return kind
    return None


def offer_for(kind: str, user_text: str, answer: str) -> tuple[str, tuple[str, dict] | None]:
    """(what she says instead, the check that 'yes' runs)."""
    m = _DAY_WORD.search(user_text) or _DAY_WORD.search(answer)
    day = (m.group(1).lower() if m else "today").replace("tonight", "today evening")
    if kind == "calendar":
        free = re.search(r"\b(free|open|slots?|empty|available|gaps?|clear|fit)\b", user_text + " " + answer, re.I)
        tool = "calendar_free" if free else "calendar_agenda"
        return "I haven't checked your calendar for that yet, sir. Should I?", (tool, {"day": day})
    if kind == "email":
        return "I haven't checked your email yet, sir. Should I?", ("email_unread", {})
    if kind == "weather":
        return "I haven't looked up the weather yet, sir. Should I?", ("get_weather", {"day": day})
    return ("I haven't done that, sir. Say it again with the time, like 'stop the music at 2:15' or 'in 10 "
            "minutes'."), None


# v0.3 9b: while the phone is locked, anyone near it could talk to her. Harmless things work, adding a meeting,
# reminder or shopping item works (she reads it back), everything private waits for an unlock (approved split).
LOCKED_OK = {"get_datetime", "get_weather", "calculate", "convert_currency", "crypto_price", "web_search",
             "media_control", "set_volume", "spotify_play", "spotify_queue", "spotify_now_playing",
             "lights_power", "lights_set", "set_mood", "list_moods", "phone_alarm", "phone_timer",
             "later_list", "later_cancel", "keep_listening", "calendar_add", "set_reminder", "shopping_add",
             "phone_stay_dark", "phone_volume", "spotify_volume", "set_brain_mode", "set_conversation_mode",
             "place_remember", "place_reminder", "where_am_i", "meeting_record",
             "train_times", "commute_plan", "phone_ring", "focus_mode", "test_mode"}     # Nov 23 afternoon
LOCKED_SAY = "Unlock your phone for that, sir, and I'll carry on."


def _fine_locked(tool: str, args: dict) -> bool:
    """Nov 23 ("the point is not touching the phone"): with 'Eva, work while locked' everything works locked.
    Switching that ON needs one unlock (a stranger saying it to your locked phone must not); 'ask me to unlock
    again' works locked."""
    if tool == "phone_ask_unlock":
        return bool((args or {}).get("on", True))
    from core import phone_prefs
    return not phone_prefs.get().get("ask_unlock", True)


def _messages_when_locked(tool: str) -> bool:
    """Reading and replying to messages hands-free with the phone locked (settings messages.when_locked, default on:
    the point is not touching the phone; a reply still needs your spoken yes)."""
    if tool not in ("messages_check", "message_reply"):
        return False
    from core import messages
    return bool(messages.cfg().get("when_locked", True))


_YES = re.compile(r"^\s*(?:yes|yeah|yep|yup|sure|do it|go ahead|go on|confirm(?:ed)?|ok(?:ay)?|please do|"
                  r"affirmative|correct|absolutely|of course|da|sigur|trimite|trimite-l|fă-o|fa-o)(?!\w)", re.I)
_PLAIN_YES = re.compile(r"^\W*(?:(?:yes|yeah|yep|yup|sure|ok(?:ay)?|alright|go ahead|do it|please do|send it|go for it|"
                        r"confirm(?:ed)?|absolutely|definitely|of course)\b[\s,.!]*)+(?:(?:please|sir|eva|thanks|"
                        r"thank you|now)\b[\s,.!]*)*$", re.I)      # v1.0: only a plain yes runs it untouched
_NEW_STOP = re.compile(r"^\W*(?:(?:hey|ok|okay)\W+)?(?:eva\W+)?(?:stop|pause|mute)\s+(?!it\b|that\b|please\b|eva\b|now\b)"
                       r"(?:the |my )?(?:music|song|spotify|playing|playback|video|timer|alarm|sound|audio|volume)", re.I)
_NO = re.compile(r"^\s*(?:no|nope|nah|cancel|don'?t|stop|never ?mind|abort|forget it|leave it|nu|anulează|anuleaza|"
                 r"lasă|lasa)(?!\w)", re.I)
# Nov 23: "Hey Eva, can you..." and a pause: the phone sent only "can you", and she answered a guess. Half a
# sentence gets "Yes, sir?" and she listens for the rest.
_DANGLING = re.compile(r"(?:(?:hey\s+)?eva[,.!]?\s*)?(?:(?:can|could|would|will) you(?: please)?|please|so|and|um+|uh+|"
                       r"hmm+|i (?:want|need|would like) (?:you )?to|can i|let'?s|okay,? so|could i)[\s.,?!…]*", re.I)
_LOCAL = re.compile(r"\b(?:locally|local(?:ly)?|offline|on (?:my|the|your) (?:pc|computer|gpu|laptop)|without "
                    r"(?:claude|the cloud|paying)|for free|free of charge)\b", re.I)


def local_answer(text: str) -> bool:
    """Nov 25: 'can you do it locally' to 'shall I go ahead?' meant: yes, but not with Claude."""
    return bool(_LOCAL.search(text or "")) and not _NO.match(text or "")


_CHANGE = re.compile(r"\b(?:change|different|instead|rewrite|re-?word|edit|something else|other (?:text|words))\b", re.I)
_PHONE_WORD = re.compile(r"\b(?:i ?|my |the )?phone'?s?\b", re.I)      # Whisper hears "iPhone" for "my phone"
_WAKE_PREFIX = re.compile(r"^\s*(?:hey\s+)?(?:eva\b|e\.v\.a\.?)[\s,:!.-]*", re.I)


_NUM = re.compile(r"(?<![\w.])\d{1,3}(?:[,.\s]\d{3})+(?:[.,]\d+)?|\d+(?:[.,]\d+)?")


def _to_float(tok: str) -> float | None:
    t = tok.replace(" ", "")
    if "," in t and "." in t:                     # the later separator is the decimal one
        t = t.replace(",", "") if t.rfind(".") > t.rfind(",") else t.replace(".", "").replace(",", ".")
    elif "," in t:
        t = t.replace(",", "") if re.fullmatch(r"\d{1,3}(,\d{3})+", t) else t.replace(",", ".")
    elif re.fullmatch(r"\d{1,3}(\.\d{3}){2,}", t):
        t = t.replace(".", "")
    try:
        return float(t)
    except ValueError:
        return None


def ungrounded_numbers(answer: str, data: str) -> list[str]:
    """Significant numbers in the answer that appear nowhere in the tool data (rounding allowed).
    Single digits, years, and times are ignored. (v0.2.5: was < 100, which let invented temperatures through.)"""
    have = [v for v in (_to_float(m.group(0)) for m in _NUM.finditer(data)) if v is not None]
    bad = []
    for m in _NUM.finditer(answer):
        v = _to_float(m.group(0))
        if v is None or v < 10 or (1900 <= v <= 2100 and v.is_integer()):    # v0.2.5: 10+, temperatures count
            continue
        if not any(abs(v - d) <= max(0.006 * abs(d), 0.5) for d in have):
            bad.append(m.group(0))
    return bad


def cost_snapshot(budget) -> tuple[float, int]:
    """(euros spent today, cloud calls today) before a turn."""
    day = budget._ensure_today()
    return float(day["total_euros"]), len(day["calls"])


def turn_cost(budget, before: tuple[float, int]) -> str:
    """Nov 23: the log line for what one turn cost, or '' when it was free (test mode: free turns say so too)."""
    day = budget._ensure_today()
    eur = float(day["total_euros"]) - before[0]
    calls = day["calls"][before[1]:]
    if not calls or eur <= 0:
        from core import testmode
        return "COST turn: free" if testmode.is_on() else ""
    purposes = sorted({c.get("purpose") or c.get("model") or "?" for c in calls})
    return (f"COST turn: EUR {eur:.5f}, {len(calls)} cloud call{'s' if len(calls) != 1 else ''} "
            f"({', '.join(purposes)})")


def load_persona(name: str = "eva") -> str:
    try:
        return yaml.safe_load(Path(f"personas/{name}.yaml").read_text(encoding="utf-8"))["personality"]
    except Exception:
        return "You are E.V.A., a concise JARVIS-style assistant. Address the user as 'sir'."


# ============================================================ tool belt
from core import multistep  # noqa: E402  (v0.2.5 multi-step commands)
from core import conversation  # noqa: E402  (v0.2.5 conversation lane)
from core import later as later_mod  # noqa: E402  (v0.3 actions later)
from core.understand import brains as understanding  # noqa: E402  (v0.3.1 understanding step)
import random  # noqa: E402

# Acks (v0.2.5): spoken only when a tool is still busy after this long, so quick things stay silent.
# settings voice.acks.after_ms (default 1500 since v0.8). 0 = always at once, a negative value = never.
ACK_AFTER_OVERRIDE: float | None = None        # tests pin this
# v0.8: "Let me check, sir." went (he heard it all the time); the mind has its own few
MIND_ACKS = ("One moment, sir.", "Give me a second, sir.", "Thinking it through, sir.", "Let me think, sir.")
GENERIC_ACKS = ("One moment, sir.", "On it, sir.", "Give me a second, sir.",
                "Looking into it, sir.", "Checking now, sir.", "Right away, sir.", "Just a moment, sir.")
_GENERIC_SOURCES = {"Looking into that now, sir.", "One moment, sir.", "On it, sir."}


def _vague_site(tool: str, decision: dict) -> bool:
    """'open the projector's shop page': a site named by what was said before, not by its name (Nov 27: it
    searched the web for those words). The mind knows which page."""
    if tool != "open_website":
        return False
    site = str(decision.get("site") or (decision.get("args") or {}).get("site") or "")
    return "." not in site and bool(re.search(r"\b(?:the|that|this|its|their|his|her|same)\b.*\b(?:page|site|link|"
                                              r"shop|store|listing|product|one)\b|\b(?:it|that|this one)$", site, re.I))


def _needs_mind(tool: str, decision: dict) -> bool:
    """Understanding's pick needs a detail it can't guess: the mind asks or works it out."""
    if _vague_site(tool, decision):
        return True
    args = decision.get("args") if isinstance(decision.get("args"), dict) else decision
    # Nov 27 09:51: "move all items from 9" (no amount) became "move everything still ahead 9 60 minutes earlier"
    return tool == "calendar_shift" and not args.get("minutes")


_GO_ON_INVITE = re.compile(r"\b(?:say|tell me) [\"']?(?:continue|go on|next)\b|\bwant (?:the|me to) (?:next|go on|"
                           r"continue)|\bshall i (?:go on|continue)\b", re.I)
LOCAL_TOOLS = 24                 # v0.9.1: tools the local model chooses from in one sentence
LOCAL_DECISION_S = 25.0          # was the client's 120 s
LOCAL_ANSWER_S = 30.0            # until the first words of a local answer
LOCAL_SLOW_SAY = ("My local brain isn't answering in time, sir: Ollama is too slow right now. Say 'Eva, use auto "
                  "for understanding' to let Claude in, or check the log line that starts with LOCAL.")


_ANSWER_WORDS = re.compile(
    r"^(?:(?:hey |ok |okay )?eva,?\s+)?(?:continue|go on|keep going|carry on|next(?: part| one)?|more|and then|"
    r"what happen(?:s|ed) next|yes|yeah|yep|yup|sure|ok(?:ay)?|alright|go ahead|do it|do that|please(?: do)?|"
    r"sounds good|perfect|great|no|nope|nah|not (?:now|yet)|maybe|the (?:first|second|third|last|other) one|"
    r"(?:the )?(?:first|second|third|last) (?:one|option)|option \w+|both|neither|that one|this one)\b", re.I)


class _Speech:
    """The mind's words as checked sentences (v0.8): each finished sentence is spoken while the rest is still
    coming, unless it claims an action nobody ran or states an unchecked fact; from then on she holds back and the
    whole answer goes through the usual guards."""
    def __init__(self, ran: list, words: bool = False):
        from voice.speech import SentenceChunker
        self.chunker, self.ran, self.blocked, self.held = SentenceChunker(), ran, False, []
        self.words = words                         # a story: nothing to guard

    def _pass(self, sentences: list[str]) -> list[str]:
        from core import mind
        out = []
        for raw in sentences:
            sent = mind.spoken(raw)
            if not sent:
                continue
            if not self.words and (self.blocked or claims(sent) or unchecked_fact(sent, self.ran)):
                self.blocked = True
                self.held.append(sent)
                continue
            out.append(sent)
        return out

    def feed(self, text: str) -> list[str]:
        return self._pass(self.chunker.feed(text))

    def flush(self) -> list[str]:
        rest = self.chunker.flush()
        return self._pass([rest]) if rest else []


def ack_after() -> float:
    if ACK_AFTER_OVERRIDE is not None:
        return ACK_AFTER_OVERRIDE
    ms = (get_settings().get("voice", {}).get("acks", {}) or {}).get("after_ms", 1500)
    try:
        return float(ms) / 1000
    except (TypeError, ValueError):
        return 1.5


class ToolBelt:
    """One view over built-in, skill, and MCP tools: schemas, guards, safety, execution."""

    def __init__(self, registry: SkillRegistry | None = None, mcp: MCPManager | None = None):
        self.registry, self.mcp = registry, mcp
        self.skill_funcs: dict[str, Callable] = registry.functions() if registry else {}
        self.skill_acks: dict[str, str] = registry.acks() if registry else {}
        self.guards: dict[str, str] = {**BUILTIN_GUARDS, **(registry.guards() if registry else {})}
        self.confirm_text: dict = {**BUILTIN_CONFIRM, **(registry.confirmations() if registry else {})}
        self.slow_fill: set[str] = set(BUILTIN_SLOW_FILL)
        self.actions: set[str] = set(BUILTIN_ACTIONS) | (registry.actions() if registry else set())
        self.exact: set[str] = set(BUILTIN_EXACT)
        self.fillers: dict[str, Callable] = {**BUILTIN_FILLERS, **(registry.fillers() if registry else {})}
        self.asks: dict = {**BUILTIN_ASKS, **(registry.asks() if registry and hasattr(registry, "asks") else {})}
        self.prechecks: dict = dict(BUILTIN_PRECHECK)
        schemas = list(BUILTIN_SCHEMAS)
        taken = {t["function"]["name"] for t in schemas}
        # built-ins first, then skills, then MCP: nothing can shadow a trusted tool
        for extra in ((registry.tool_schemas() if registry else []), (mcp.tool_schemas() if mcp else [])):
            for t in extra:
                if t["function"]["name"] not in taken:
                    schemas.append(t)
                    taken.add(t["function"]["name"])
        if mcp:
            self.actions |= mcp.actions()
        self.schemas = schemas
        self.by_name = {t["function"]["name"]: t for t in schemas}

    def has(self, name: str) -> bool:
        return name in self.by_name

    def names(self) -> list[str]:
        return list(self.by_name)

    def is_action(self, name: str) -> bool:
        return name in self.actions

    def is_mcp(self, name: str) -> bool:
        return bool(self.mcp) and self.mcp.has(name)

    def required(self, name: str) -> list[str]:
        return list(self.by_name[name]["function"].get("parameters", {}).get("required", []))

    def fill(self, name: str, args: dict, text: str) -> dict:
        fn = self.fillers.get(name)
        try:
            return fn(args, text) if fn else args
        except Exception as e:
            logger.warning(f"argument filler for {name} failed: {e}")
            return args

    def guard_ok(self, name: str, text: str) -> bool:
        rx = self.guards.get(name)
        return True if not rx else bool(re.search(rx, text, re.I))

    def needs_confirm(self, name: str) -> bool:
        from core.tools_native import CONFIRM_UNLESS
        if name in CONFIRM_UNLESS:
            try:
                if CONFIRM_UNLESS[name]():
                    return False
            except Exception:
                pass
        return name in self.confirm_text or (self.is_mcp(name) and self.mcp.needs_confirm(name))

    def acts_alone(self, name: str) -> bool:
        """v0.8: an action he switched to 'without asking' in the settings."""
        from core.tools_native import CONFIRM_UNLESS
        try:
            return name in CONFIRM_UNLESS and bool(CONFIRM_UNLESS[name]())
        except Exception:
            return False

    def describe(self, name: str, args: dict) -> str:
        if name in self.confirm_text:
            text = self.confirm_text[name]
            try:
                return text(args or {}) if callable(text) else text.format(**{"minutes": 15, **(args or {})})
            except Exception:
                return text if isinstance(text, str) else f"run {name}"
        if self.is_mcp(name):
            return self.mcp.describe(name, args)
        details = ", ".join(f"{k.replace('_', ' ')} {v}" for k, v in (args or {}).items() if v not in (None, "", [], {}))
        return name.replace("_", " ") + (f" ({details})" if details else "")

    def list_text(self) -> str:
        return "\n".join(f"- {n}: {t['function']['description']}" for n, t in self.by_name.items())

    def ack(self, name: str) -> str | None:
        if name in BUILTIN_FUNCS:
            return builtin_ack(name)
        if self.is_mcp(name):
            return "One moment, sir."
        return self.skill_acks.get(name)

    def _execute_skill(self, name: str, args: dict) -> dict:
        fn = self.skill_funcs.get(name)
        if fn is None:
            return {"result": json.dumps({"missing_skill": name}), "missing": True}
        last = None
        for attempt in range(2):
            try:
                out = fn(**(args or {}))
                if '"error"' in (out.get("result") or ""):
                    logger.warning(f"TOOL {name}({args}) returned an error: {out.get('result')}")
                else:
                    logger.info(f"TOOL {name}({args}) ok -> {snip(out.get('result'))}")
                return out
            except Exception as e:
                last = e
                logger.warning(f"TOOL {name} attempt {attempt + 1} failed: {e}")
        return {"result": json.dumps({"error": str(last)}), "failed": True}

    def execute(self, name: str, args: dict) -> dict:
        """Synchronous path for built-in and skill tools (tests, scripts)."""
        audit.log("tool_call", "orchestrator", {"tool": name, "args": args})
        if name in BUILTIN_FUNCS:
            return builtin_execute(name, args)
        return self._execute_skill(name, args)

    async def aexecute(self, name: str, args: dict) -> dict:
        """Turn path: MCP on the loop, everything else in a worker thread."""
        if self.is_mcp(name):
            audit.log("tool_call", "mcp", {"tool": name, "args": args})
            out = await self.mcp.call(name, args)
            level = "warning" if '"error"' in out["result"] else "info"
            getattr(logger, level)(f"TOOL {name}({args}) -> {snip(out['result'])}")
            return out
        return await asyncio.to_thread(self.execute, name, args)


def build_decision_schema(schemas: list[dict]) -> dict:
    """Flat union of every tool's parameters (flat on purpose: small models
    struggle with nested schemas). Enums survive only if no other tool reuses
    the same parameter name with a different meaning."""
    props: dict[str, dict] = {}
    for t in schemas:
        for pname, spec in t["function"].get("parameters", {}).get("properties", {}).items():
            if pname == "tool":
                continue
            if pname not in props:
                entry = {"type": spec.get("type", "string")}
                if "enum" in spec:
                    entry["enum"] = list(spec["enum"])
                props[pname] = entry
            elif props[pname].get("enum") != spec.get("enum"):
                props[pname].pop("enum", None)
    names = [t["function"]["name"] for t in schemas]
    return {"type": "object",
            "properties": {"tool": {"type": "string", "enum": names + ["none", "unavailable"]}, **props},
            "required": ["tool"]}


def tool_arg_schema(schema: dict) -> dict:
    """Schema containing ONLY one tool's parameters, all its required ones enforced."""
    params = schema["function"].get("parameters", {})
    return {"type": "object", "properties": dict(params.get("properties", {})),
            "required": list(params.get("required", []))}


def missing_required(schema: dict, args: dict) -> list[str]:
    req = schema["function"].get("parameters", {}).get("required", [])
    return [r for r in req if args.get(r) in (None, "")]


def args_for(schema: dict, decision: dict, tool_names: list[str] | None = None) -> dict:
    """Keep only the parameters the chosen tool declares, dropping empty values and
    values that are just a tool name (the 3B sometimes writes the tool into a field)."""
    allowed = schema["function"].get("parameters", {}).get("properties", {})
    names = {n.lower() for n in (tool_names or [])}
    return {k: v for k, v in decision.items()
            if k in allowed and v not in ("", None)
            and not (isinstance(v, str) and v.strip().lower() in names)}


# ============================================================ fast path
# Nov 27: "what's on my calendar tomorrow and do I have time to go to a makerspace?" only listed the calendar;
# "it was a busy day, you can see my schedule and actually talk about it with me" got a list too
_MORE_THAN_CALENDAR = re.compile(
    r"\b(?:and|also|plus|then)\b[^.?!]{0,60}\b(?:time (?:to|for)|go(?:ing)? to|get to|visit|makerspace|shop|store|"
    r"gym|open|weather|train|is there|are there|any \w+ (?:near|available|open))|\btalk (?:about|through|with me)|"
    r"\bdiscuss\b|\bhow was (?:my|the) day|\b(?:busy|long|rough|hard|crazy) day\b", re.I)


# Nov 27 10:59: "Can you drop the energy saver settings thing?" and "can you stop this?" while a computer task
# clicked on: the local model said "Energy saver settings disabled." and nothing stopped.
_STOP_TASK = re.compile(r"\b(?:cancel|stop|drop|abort|kill|end|quit|halt|forget(?: about)?|leave)\b(?:\s+(?:doing|working "
                        r"on|with))?\s*(?P<what>(?:the|that|this|it|my|your)\b.*?)?\s*(?:\b(?:task|thing|job|search|"
                        r"research|for now|please|now|eva|sir)\b\s*)*[.!?]*$", re.I)
_MUSIC_WORDS = re.compile(r"\b(?:music|song|songs|spotify|playing|playlist|track|album|volume|sound|video|"
                          r"timer|alarm|reminder)\b", re.I)


def stop_task(text: str) -> dict | None:
    """'drop the energy saver thing', 'stop this', 'cancel that task': the running task, if there is one."""
    t = (text or "").strip()
    if _MUSIC_WORDS.search(t) or len(t.split()) > 14:
        return None
    m = _STOP_TASK.search(t)
    if not m:
        return None
    what = " ".join(re.sub(r"\b(?:the|that|this|it|my|your|task|thing|job|settings? thing)\b", " ",
                           m.group("what") or "", flags=re.I).split())
    try:
        from core.agent.tasks import ACTIVE, get_store
        tasks = get_store().all()
    except Exception:
        return None
    if what:                                     # a named one: any task that isn't finished
        words = [w for w in re.findall(r"[a-z0-9]+", what.lower()) if len(w) > 2]
        named = [x for x in tasks if x.status in ACTIVE and any(w in f"{x.title} {x.goal}".lower() for w in words)]
        return {"which": what} if named else None
    # "stop this", "drop it": only while one is actually working (a bare "stop" stays the music's)
    if re.search(r"\b(?:task|job)\b", m.group(0), re.I):      # Nov 27 19:53: "Stop the task." went to the task
        return {"which": ""} if any(x.status in ACTIVE for x in tasks) else None
    if not re.search(r"\b(?:this|that|it|doing)\b", m.group(0), re.I):
        return None
    return {"which": ""} if any(x.status in ("running", "queued") for x in tasks) else None


# Nov 27 19:50-19:53: a task waiting for an answer took "Raise the daily limit to 5 Euros", "How much do you need?",
# "dropped the task" and "Stop the task." as its answer. These are never answers to a task's question.
_NOT_AN_ANSWER = re.compile(r"\b(?:daily limit|the limit|budget|settings?|raise|lower|increase)\b|^\s*(?:what|how|why|"
                            r"when|where|who|can you|could you|do you|did you|are you|is it|what's)\b.*\?\s*$", re.I)


def not_a_task_answer(text: str) -> bool:
    t = (text or "").strip()
    if stop_task(t) or re.search(r"\b(?:drop(?:ped)?|cancel(?:led)?|stop(?:ped)?|abort(?:ed)?|kill(?:ed)?)\b.{0,20}"
                                 r"\b(?:task|job|it|this|that)\b", t, re.I):
        return True
    return bool(_NOT_AN_ANSWER.search(t)) and len(t.split()) > 2


def fast_path(text: str, belt: ToolBelt) -> tuple[str, dict] | None:
    t = _WAKE_PREFIX.sub("", text).strip()
    polite = r"(?:(?:i want you to|i need you to|can you|could you|would you|please|and|also|now|don't forget|i)\s+)*"

    if belt.has("test_mode"):                                   # Nov 23: never needs a cloud call to switch
        from core.testmode import from_words as test_words
        on = test_words(text)
        if on is not None:
            return "test_mode", {"on": on}
    if belt.has("focus_mode"):
        from core.focus import from_words as focus_words
        on = focus_words(text)
        if on is not None:
            return "focus_mode", {"on": on}
    if belt.has("phone_ring"):                                  # Nov 23: find my phone
        if re.search(r"\b(?:find|ring|locate|call)\s+my\s+(?:phone|mobile|pixel)\b|\bwhere(?:'s| is| did i put)\s+my\s+"
                     r"(?:phone|mobile|pixel)\b|\b(?:can'?t|cannot) find my (?:phone|mobile|pixel)\b|"
                     r"\b(?:lost|misplaced) my (?:phone|mobile|pixel)\b", t, re.I):
            return "phone_ring", {"on": True}
        from core.device import ringing
        if re.fullmatch(r"(?:(?:ok(?:ay)?|good|great|thanks)[,.!]?\s*)?(?:stop (?:the )?ringing|i found (?:it|my phone)|"
                        r"found my phone|(?:i )?got my phone)(?:,? thanks?(?: you)?)?[.!]*", t.strip(), re.I) or \
                (ringing() and re.fullmatch(r"(?:(?:ok(?:ay)?|good|great|thanks)[,.!]?\s*)?(?:found it|got it|"
                                            r"(?:that'?s|it'?s) (?:fine|ok(?:ay)?)|you can stop|stop)(?:,? thanks?(?: you)?)?"
                                            r"[.!]*", t.strip(), re.I)):
            return "phone_ring", {"on": False}
    if belt.has("meeting_todos_add") and re.search(r"\b(?:put|add|turn|move|schedule|save|copy)\b", t, re.I) and \
            re.search(r"\b(?:to-?dos?|todos|action items?)\b", t, re.I) and \
            not re.search(r"\b(?:what|which|read|tell me)\b", t, re.I):       # Nov 23: meeting to-dos, after a yes
        where = "calendar" if re.search(r"\b(?:calendar|agenda|schedule)\b", t, re.I) else "reminders"
        args = {"where": where}
        if re.search(r"\ball\b", t, re.I):
            args["which"] = "all"
        return "meeting_todos_add", args
    if belt.has("commute_plan"):                                # Nov 23: NS and your lectures
        m = re.search(r"\b(?:when|what time) (?:should|do|must|would) i (?:need to |have to )?leave(?: home| the house)?"
                      r"(?: (?:for|to) (?:uni|university|radboud|nijmegen|class|campus|my (?:lectures?|class(?:es)?)))?"
                      r"(?: (today|tomorrow|on \w+day|\w+day))?\s*[?.!]*$", t, re.I)
        if m:
            return "commute_plan", {"day": (m.group(1) or "").replace("on ", "")}
    if belt.has("train_times"):
        m = re.search(r"\b(?:next|first|last) trains?\b|\btrains? (?:to|towards|home|from|back)\b|\btrain times\b|"
                      r"\bis (?:my|the) train (?:delayed|late|on time|cancelled|running)\b|"
                      r"\bwhen(?:'s| is| does) (?:the|my) (?:next )?train\b", t, re.I)
        if m:
            to = re.search(r"\btrains? (?:to|towards) ((?!home\b)[a-z'][\w' -]{1,30}?)(?: at | tomorrow| today|[?.!]|$)",
                           t, re.I)
            return "train_times", ({"to_station": to.group(1).strip()} if to else {})

    m = re.search(r"\b(?:add|put)\s+(.+?)\s+(?:to|on)\s+(?:my |the )?(?:shopping|grocery)\s+list\b", t, re.I)
    if m and belt.has("shopping_add"):
        return "shopping_add", {"items": m.group(1)}
    m = re.search(r"(?:set |turn |change )?(?:the )?volume (?:to |at )?(\d{1,3})\s*%?", t, re.I)
    if m and _PHONE_WORD.search(t) and belt.has("phone_volume"):   # Nov 23: "my iPhone volume" changed the PC
        return "phone_volume", {"level": volume_level(int(m.group(1)))}
    if m and re.search(r"\bspotify\b", t, re.I):   # "the Spotify volume": never the PC's volume
        if belt.has("spotify_volume"):
            return "spotify_volume", {"level": volume_level(int(m.group(1)))}
        m = None
    if m and belt.has("set_volume"):
        return "set_volume", {"level": int(m.group(1))}
    if belt.has("set_volume") and re.search(r"\b(max|full|maximum) volume\b", t, re.I):
        return "set_volume", {"level": 100}

    if belt.has("forget_recent_facts") and re.search(
            r"\b(?:delete|forget|remove|erase)\b.{0,40}\b(?:just|recently)\s+"
            r"(?:remembered|learned|saved|stored|noted)", t, re.I):
        return "forget_recent_facts", {"minutes": 15}
    if belt.has("schedule_routine") and re.match(polite + r"(?:every|each)\s+\S+", t, re.I) and \
            re.search(r"\b(brief|remind|tell|read|check|give|summari[sz]e)\b", t, re.I):
        return "schedule_routine", {"request": t}
    if belt.has("list_routines") and re.search(r"\b(what are|list|show) (?:my )?routines\b", t, re.I):
        return "list_routines", {}
    m = re.search(r"\b(?:send|text|message) me(?: a message| a text)?(?: on| via| through| to)? (?:telegram|my phone)"
                  r"(?:[:,]?\s*(?:saying|that says|that|with)?\s*(.*))?$", t, re.I) or \
        re.search(r"\bsend (?:this |that )?to my phone[:,]?\s*(?:saying|that)?\s*(.*)$", t, re.I)
    if m and belt.has("send_to_phone"):
        return "send_to_phone", {"text": (m.group(1) or "").strip().rstrip("?.!")}
    m = re.match(polite + r"remind me (in .+?|at .+?|tomorrow(?: at [^ ]+)?|tonight|this (?:evening|afternoon)) to (.+)$", t, re.I)
    if m and belt.has("set_reminder"):
        return "set_reminder", {"text": m.group(2).strip().rstrip("?.!"), "when": m.group(1).strip()}
    m = re.match(polite + r"(?:remind me|remember) to (.+?)\s+((?:in|at|on|tomorrow|tonight|this|next)\b.*)$", t, re.I)
    if m and belt.has("set_reminder"):
        return "set_reminder", {"text": m.group(1).strip(), "when": m.group(2).strip().rstrip("?.!")}
    if belt.has("snooze_reminder") and re.match(r"^\s*snooze(?: it| that)?(?: for (\d+) minutes?)?", t, re.I):
        m = re.match(r"^\s*snooze(?: it| that)?(?: for (\d+) minutes?)?", t, re.I)
        return "snooze_reminder", {"minutes": int(m.group(1) or 10)}
    if belt.has("do_not_disturb"):
        if re.search(r"\b(i'?m back|you can (?:talk|speak) again|end do not disturb|disturb me again)\b", t, re.I):
            return "do_not_disturb", {"minutes": 0}
        m = re.search(r"\b(?:do not disturb|don'?t disturb me|quiet mode|leave me alone|let me focus)\b"
                      r"(?:.*?(?:for|next)\s+(\d+|an?|one|two)\s*(hours?|minutes?|mins?))?", t, re.I)
        if m:
            qty = {"a": 1, "an": 1, "one": 1, "two": 2}.get((m.group(1) or "").lower(), None)
            qty = qty if qty is not None else int(m.group(1)) if m.group(1) else 60
            mins = qty * 60 if (m.group(2) or "").startswith("hour") else (qty if m.group(1) else 60)
            return "do_not_disturb", {"minutes": mins}
    if belt.has("list_reminders") and re.search(r"\b(what are|list|show) (?:my )?reminders\b|any reminders", t, re.I):
        return "list_reminders", {}
    if belt.has("morning_briefing") and re.match(r"^\s*(?:good morning|morning briefing|brief me|daily briefing|"
                                                 r"what'?s my day(?: look)?(?: like)?)\b", t, re.I):
        return "morning_briefing", {}
    m = re.match(r"^(?:please )?(?:don'?t (?:let me )?forget to|remind me to)\s+(.+)$", t, re.I)
    if m and belt.has("set_reminder"):
        return "set_reminder", {"text": m.group(1).strip().rstrip("?.!")}       # no time given: she asks when
    m = re.match(polite + r"remember(?: that)?\s+(.{4,})$", t, re.I)
    if m and belt.has("remember_fact") and not re.match(r"(to|when|what|who|where|if|how)\b", m.group(1), re.I):
        return "remember_fact", {"text": m.group(1).strip().rstrip("?.!")}
    m = re.match(r"(?:from now on|going forward)[,\s]+(.{4,})$", t, re.I)
    if m and belt.has("add_instruction"):
        return "add_instruction", {"text": m.group(1).strip()}
    m = re.match(polite + r"(?:forget|delete|remove)(?: the fact| that| about)?\s+(.{4,})$", t, re.I)
    vague = ("about it", "it", "that", "this", "that one", "all of it", "everything")
    if m and belt.has("forget_memory") and m.group(1).lower().strip(" .!?") not in vague \
            and not re.search(r"\b(note|drafts?|e-?mails?|mails?|inbox)\b", m.group(1), re.I):
        return "forget_memory", {"query": m.group(1).strip().rstrip("?.!")}
    m = re.search(r"what (?:did|have) i (?:tell|told|say|said to) you about (.{2,})$"
                  r"|do you remember (?:anything )?about (.{2,})$", t, re.I)
    if m and belt.has("recall_memory"):
        return "recall_memory", {"query": (m.group(1) or m.group(2)).strip().rstrip("?.!")}
    if belt.has("recall_memory") and re.search(r"what do you (?:know|remember) about me", t, re.I):
        return "recall_memory", {"query": ""}

    m = re.match(polite + r"(?:make|create|start|write|add)\s+(?:a |an |new )*(project|people|person|idea)\s+note"
                          r"\s+(?:about|on|for|called|titled)\s+(.{2,})$", t, re.I)
    if m and belt.has("obsidian_write"):
        folder = {"project": "Projects", "people": "People", "person": "People", "idea": "Ideas"}[m.group(1).lower()]
        return "obsidian_write", {"title": m.group(2).strip().rstrip("?.!"), "content": "", "folder": folder}
    m = re.match(polite + r"(?:note that|make a note(?: that)?|take a note(?: that)?|add a note(?: that)?|"
                          r"write (?:a |me a )?note(?: that)?|write down(?: that)?|jot down(?: that)?)[:,]?\s+(.{3,})$",
                 t, re.I)
    if m and belt.has("obsidian_quick_note"):
        return "obsidian_quick_note", {"text": m.group(1).strip()}
    m = re.search(r"(?:search|check|look (?:in|through)) my notes (?:for|about|on) (.{2,})$"
                  r"|what (?:did|have) i (?:note|noted|written|write|jotted)(?: down)? (?:about|on) (.{2,})$", t, re.I)
    if m and belt.has("obsidian_search"):
        return "obsidian_search", {"query": (m.group(1) or m.group(2)).strip().rstrip("?.!")}

    m = re.match(polite + r"(?:think (?:hard|deeply|carefully|properly|it through|this through)|deep ?think|"
                          r"ask claude|use claude)(?: about| on| to| whether)?[:,]?\s+(.{5,})$", t, re.I)
    if m and belt.has("think_deeply"):
        return "think_deeply", {"question": m.group(1).strip()}
    m = re.match(polite + r"(?:i want you to |i'd like you to )?(?:build|make|create|write|develop) (?:a |an |yourself a |"
                          r"me a |your own )?(?:new )?(?:skill|ability|tool|capability)(?: that| to| for| which| so you can)?"
                          r"\s+(.{5,})$", t, re.I) or \
        re.match(polite + r"(?:learn|teach yourself)(?: how)? to\s+(.{5,})$", t, re.I)
    if m and belt.has("forge_build"):
        return "forge_build", {"request": m.group(1).strip()}
    if belt.has("forge_list") and re.search(r"\b(pending|drafted|waiting) skills?\b|skills? (?:waiting|pending)", t, re.I):
        return "forge_list", {}
    m = re.search(r"\b(?:switch|go|change|set|use|stay|keep|put)(?: back)?(?: \w+)?(?: to| on)?(?: the)?\s+"
                  r"(local|offline|cloud|online|claude|auto|automatic)(?: mode| models?)?\b|\b(local|cloud|auto) mode\b"
                  r"|\b(?:use|with)\s+(claude)\s+for\b", t, re.I)
    if not m and belt.has("set_brain_mode") and len(t.split()) <= 7:
        # short commands naming a feature and a mode: "auto for understanding", "understanding local only"
        from core.tools_native import feature_from_words
        if feature_from_words(t):
            m = re.search(r"\b(local|offline|cloud|online|claude|auto|automatic)\b()()", t, re.I)
    if m and belt.has("set_brain_mode"):
        word = (m.group(1) or m.group(2) or m.group(3)).lower()
        mode = {"offline": "local", "online": "cloud", "claude": "cloud", "automatic": "auto"}.get(word, word)
        from core.tools_native import feature_from_words
        feature = feature_from_words(t)
        return "set_brain_mode", ({"mode": mode, "feature": feature} if feature else {"mode": mode})
    if belt.has("phone_stay_dark"):
        if re.fullmatch(r"(?:(?:hey )?eva[,.]? )?(?:stay dark|keep (?:the screen|it) (?:off|dark)|don'?t (?:light|turn on) "
                        r"the screen)(?: on| please| from now on)?[.!]*", t.strip(), re.I):
            return "phone_stay_dark", {"on": True}
        if re.fullmatch(r"(?:(?:hey )?eva[,.]? )?(?:stay dark off|turn (?:off )?stay dark(?: off)?|no more staying dark|"
                        r"(?:light|wake|turn on) the screen(?: when i call you| again)?|stop staying dark)[.!]*",
                        t.strip(), re.I):
            return "phone_stay_dark", {"on": False}
    if belt.has("phone_ask_unlock"):
        if re.fullmatch(r"(?:(?:hey )?eva[,.]? )?(?:(?:you can )?work (?:while|when) (?:the phone is |my phone is |it'?s )?"
                        r"locked|(?:stop|don'?t|no more) ask(?:ing)? me to unlock(?: (?:it|my phone|the phone))?|"
                        r"(?:i )?don'?t (?:want|need) to unlock(?: (?:it|my phone|the phone))?(?: anymore)?|"
                        r"unlock (?:mode|setting) off)(?: please| anymore| from now on)?[.!]*", t.strip(), re.I):
            return "phone_ask_unlock", {"on": False}
        if re.fullmatch(r"(?:(?:hey )?eva[,.]? )?(?:ask me to unlock(?: (?:it|my phone|the phone))?(?: again)?"
                        r"(?: for private things)?|unlock (?:mode|setting) on)(?: please)?[.!]*", t.strip(), re.I):
            return "phone_ask_unlock", {"on": True}
    if belt.has("keep_listening"):
        if re.fullmatch(r"(?:(?:hey )?eva[,.]? )?(?:let'?s (?:talk|chat)(?: for a (?:bit|while))?|keep listening|"
                        r"(?:start |turn on )?conversation mode(?: on)?|just listen|talk to me)[.!]*", t.strip(), re.I):
            return "keep_listening", {"on": True}
        from core.tools_native import talk_mode
        if talk_mode() and re.fullmatch(r"(?:(?:ok(?:ay)?|thanks|thank you|alright)[,.]? )?(?:that'?s all|that'?s it|"
                                        r"stop listening|we'?re done|i'?m done|goodbye|bye|good night)"
                                        r"(?: for now)?(?:[,.]? (?:thanks|thank you|eva))?[.!]*", t.strip(), re.I):
            return "keep_listening", {"on": False}
    if belt.has("budget_status") and re.search(r"\b(budget|how much (?:have you|did you|did we) spen[dt]|api costs?)\b", t, re.I):
        return "budget_status", {}

    m = re.search(r"\b(?:push|move|shift|delay|postpone)\s+(?:everything|all(?: of)?(?: my)?(?: events| meetings| appointments)?|"
                  r"my (?:schedule|calendar|day)|the rest of (?:my|the) day)(?: back| forward)?\s+(?:by\s+)?"
                  r"(an?|one|two|three|half an?|\d+)\s*(hours?|minutes?|mins?)(?:\s+(later|earlier|back|forward))?", t, re.I)
    if m and belt.has("calendar_shift"):
        qty = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "half a": 0.5, "half an": 0.5}.get(m.group(1).lower())
        qty = qty if qty is not None else int(m.group(1))
        mins = int(qty * 60) if m.group(2).lower().startswith("hour") else int(qty)
        return "calendar_shift", {"minutes": -mins if (m.group(3) or "").lower() in ("earlier", "forward") else mins,
                                  "day": "today"}
    m = re.search(r"\b(?:price|value|worth) of (bitcoin|btc|ethereum|eth|solana|dogecoin|cardano|xrp)\b|"
                  r"\b(bitcoin|btc|ethereum|eth|solana|dogecoin)\b (?:price|at|worth)", t, re.I)
    if m and belt.has("crypto_price"):
        cur = "USD" if re.search(r"\b(dollars?|usd)\b", t, re.I) else "EUR"
        return "crypto_price", {"coin": (m.group(1) or m.group(2)).lower(), "currency": cur}
    m = re.search(r"([\d][\d.,]*)\s*(dollars?|usd|\$|euros?|eur|€|pounds?|gbp|£|lei|ron)\s+(?:in|to|into)\s+"
                  r"(euros?|eur|dollars?|usd|pounds?|gbp|lei|ron)\b", t, re.I)
    if m and belt.has("convert_currency"):
        return "convert_currency", {"amount": float(m.group(1).replace(",", "")), "from_currency": m.group(2),
                                    "to_currency": m.group(3)}
    if belt.has("spotify_now_playing") and re.search(r"what(?:'s| is) (?:this song|playing|this track)|what song is this|"
                                                     r"who (?:sings|is singing) this", t, re.I):
        return "spotify_now_playing", {}
    m = re.match(polite + r"(?:add|put)\s+(.+?)\s+(?:to|in|on)\s+(?:the\s+)?queue$|^queue\s+(?:up\s+)?(.+)$", t, re.I)
    if m and belt.has("spotify_queue"):
        return "spotify_queue", {"what": (m.group(1) or m.group(2)).strip()}
    if belt.has("open_shown"):                                   # v0.6: a video or picture on screen comes first
        from core.show import pick_from_words
        pick = pick_from_words(t)
        if pick:
            return "open_shown", pick
    if belt.has("spotify_play") and re.match(r"^\s*(?:play|resume|continue)(?: the music| it| playing)?[.!?]?\s*$", t, re.I):
        return "spotify_play", {"what": ""}                         # bare "play": resume, don't pick liked songs
    m = re.match(r"^(?:and |now )?(?:(?:play|put|move) )?(?:it |the music |this )?(?:to|on)\s+(?:my\s+|the\s+)?"
                 r"(phone|mobile|laptop|computer|pc|desktop|speaker|tv)[.!?]?$", t, re.I)
    if m and belt.has("spotify_play"):
        return "spotify_play", {"what": "", "device": m.group(1).lower()}
    m = re.match(polite + r"(?:play|put on)(?: me)?\s+(.+?)\s+on\s+(?:my\s+|the\s+)?(phone|mobile|laptop|computer|pc|desktop|speaker|tv)[.!?]?$",
                 t, re.I)
    if m and belt.has("spotify_play"):
        return "spotify_play", {"what": m.group(1).strip(), "device": m.group(2).lower()}
    m = re.match(polite + r"(?:play|put on)(?: me)?(?: some| a bit of)?\s+(.{2,})$", t, re.I)
    if m and belt.has("spotify_play") and not re.search(r"\b(game|chess|video|movie|film|youtube|role|part|piano|guitar)\b",
                                                         m.group(1), re.I):
        return "spotify_play", {"what": m.group(1).strip().rstrip("?.!")}
    # lights and moods
    m = re.search(r"\bi(?:'m| am) home\b(?:.*?\bi (?:feel|am feeling|'m feeling)\s+(?:like\s+)?(\w+(?:\s\w+)?))?|"
                  r"^(?:eva,?\s*)?i (?:feel|am feeling|'m feeling)\s+(?:like\s+)?(\w+(?:\s\w+)?)[.!]?$|"
                  r"\b(?:set|change|switch) (?:the )?mood (?:to\s+)?(\w+(?:\s\w+)?)|\b(\w+) mood(?: please)?[.!]?$", t, re.I)
    if m and belt.has("set_mood") and not re.search(r"\b(create|make|new|save|add)\b", t, re.I):
        from core.lights import find_mood
        name = (next((g for g in m.groups() if g), None) or "home").strip().lower()
        explicit = bool(re.search(r"\b(mood|i'?m home|i am home)\b", t, re.I))
        if name not in ("my", "the", "a", "your", "this", "good", "bad", "what") and (explicit or find_mood(name)):
            return "set_mood", {"name": name}                # "I feel tired" stays a conversation
    if belt.has("list_moods") and re.search(r"\b(what|which|list|show)\b.*\bmoods\b", t, re.I):
        return "list_moods", {}
    m = re.search(r"\b(?:turn|switch)\s+(on|off)\s+(?:the\s+)?(?:(\w+)\s+)?(?:lights?|leds?|strips?)\b|"
                  r"\b(?:lights?|leds?|strips?)\s+(on|off)\b|\b(?:turn|switch)\s+(?:the\s+)?(?:(\w+)\s+)?"
                  r"(?:lights?|leds?|strips?)\s+(on|off)\b", t, re.I)
    if m and belt.has("lights_power"):
        state = (m.group(1) or m.group(3) or m.group(5)).lower()      # the three (on|off) groups
        which = (m.group(2) or m.group(4) or "").lower()              # the two "which lights" groups
        which = "" if which in ("the", "all", "my") else which
        return "lights_power", {"on": state == "on", **({"which": which} if which else {})}
    if belt.has("lights_set") and re.search(r"\b(lights?|leds?|strips?)\b", t, re.I) and \
            re.search(r"\b(make|set|turn|change|put|dim|brighten|go)\b|\bto\s+\d|%|percent", t, re.I):
        from core.lights import parse_brightness, parse_color
        c, b = parse_color(t), parse_brightness(t)
        if c or b:
            return "lights_set", {**({"color": c["name"]} if c else {}), **({"brightness": b} if b else {})}
    if belt.has("calendar_next") and re.search(
            r"what(?:'s| is) (?:coming )?(?:up )?next|my next (?:meeting|event|appointment|thing|call)|"
            r"what do i have next|what(?:'s| is) coming up\b", t, re.I):
        return "calendar_next", {}
    if belt.has("task_cancel"):                    # v0.9: stopping her work beats every other reading
        st = stop_task(t)
        if st is not None:
            return "task_cancel", st
    if re.search(r"\b(?:calendar|schedule|agenda|free|time)\b", t, re.I) and _MORE_THAN_CALENDAR.search(t):
        return "work_it_out", {}              # v0.8: "...and do I have time for a makerspace?" needs thinking
    if belt.has("calendar_agenda") and re.search(
            r"what(?:'s| is) (?:on )?my (?:calendar|schedule|agenda)|what am i doing\b|"
            r"what do i have (?:on|planned)|do i have (?:anything|any meetings|any plans|plans)\b|my agenda\b", t, re.I):
        from core.weather import extract_when
        week = re.search(r"\b(next week|this week|the week)\b", t, re.I)
        named = re.search(r"\b(yesterday|last night|(?:on )?the \d{1,2}(?:st|nd|rd|th))\b", t, re.I)
        return "calendar_agenda", {"day": week.group(1) if week else named.group(1) if named else
                                   (extract_when(t)[0] or "today")}
    if belt.has("calendar_free") and re.search(
            r"\b(?:any|an) (?:opening|gap|free (?:time|slot))|when am i free|am i free\b|do i have time", t, re.I) \
            and not _MORE_THAN_CALENDAR.search(t):
        from core.weather import extract_when
        d, h = extract_when(t)
        day = d or ""
        if h and h.lower() not in day.lower():
            day = f"{day} {h}".strip()
        return "calendar_free", {"day": day or "today"}
    if belt.has("email_important") and re.search(
            r"\b(?:anything|something|any) (?:interesting|important|urgent|worth)\b.{0,20}\b(?:e-?mails?|mail|inbox)\b|"
            r"\b(?:important|interesting|urgent) (?:e-?mails?|mail)\b|what(?:'s| is) important in my (?:e-?mail|inbox)", t, re.I):
        return "email_important", {}
    m = re.search(r"\b(?:e-?mails?|mail) (?:from|about) (.+?) (?:are|is) (important|not important|spam|noise)\b", t, re.I)
    if m and belt.has("email_sender_pref"):
        return "email_sender_pref", {"who": m.group(1).strip(), "important": m.group(2).lower() == "important"}
    m = re.search(r"\b(?:don'?t|do not|never) (?:tell|bother|notify) me (?:about|with) (?:e-?mails? (?:from|about) )?(.+?)(?: e-?mails?)?[.!]?$", t, re.I)
    if m and belt.has("email_sender_pref"):
        return "email_sender_pref", {"who": m.group(1).strip(), "important": False}
    if belt.has("email_unread") and re.search(
            r"\b(?:check|read|any|new|unread)(?: new| unread)? (?:my )?(?:e-?mails?|mail|inbox)\b|"
            r"do i have (?:any )?(?:new |unread )?e-?mails?|what(?:'s| is) in my inbox", t, re.I):
        return "email_unread", {}

    if belt.has("see_screen") and re.search(
            r"\b(what'?s on|look at|read|check|describe|see) (?:my |the |this )?screen\b"
            r"|what am i looking at", t, re.I):
        return "see_screen", {"question": t}

    m = re.match(polite + r"(?:open|launch|start|pull up|go to)\s+(?:the |my )?(.{2,40}?)[.!?]?$", t, re.I)
    if m:
        from core.tools_native import _APPS, _SITES
        target = re.sub(r"\s+(website|site|page|app|application)$", "", m.group(1).strip().lower())
        if target in _APPS and belt.has("open_app"):          # installed apps win over websites
            return "open_app", {"app": target}
        if (target in _SITES or re.match(r"^[a-z0-9-]+\.[a-z]{2,}$", target)) and belt.has("open_website"):
            return "open_website", {"site": target}

    m = re.match(r"^(?:what'?s|what is|how much is|calculate|compute)?\s*"
                 r"((?:[\d.,]+\s*%\s*of\s*[\d.,]+)|(?:square root of\s*[\d.]+)|"
                 r"(?:[-(]*\s*[\d.,]+\s*(?:[-+*/x×÷^]|\*\*|plus|minus|times|divided by)\s*[-\d.,() +*/x×÷^]*))\s*[?=.]*$", t, re.I)
    if m and belt.has("calculate"):
        return "calculate", {"expression": m.group(1).strip()}

    # "volume up to 75%... or no, actually 90%": a number beats "up", and the LAST number is what you meant
    if re.search(r"\bvolume\b|\b(?:put|set|turn) (?:it|them) (?:up |down )?to\b", t, re.I):
        nums = re.findall(r"\b(\d{1,3})\s*(?:%|percent)", t, re.I) or \
            re.findall(r"\bvolume(?: up| down)?(?: to| at)? (\d{1,3})\b", t, re.I)
        if nums:
            level = volume_level(int(nums[-1]))
            if _PHONE_WORD.search(t) and belt.has("phone_volume"):
                return "phone_volume", {"level": level}
            if re.search(r"\bspotify\b", t, re.I):
                if belt.has("spotify_volume"):
                    return "spotify_volume", {"level": level}
            elif belt.has("set_volume"):
                return "set_volume", {"level": level}
    if belt.has("email_delete_drafts") and re.search(
            r"\b(delete|remove|discard|clear|get rid of|trash)\b.{0,40}\bdrafts?\b", t, re.I):
        return "email_delete_drafts", {}

    m = re.search(r"\b(?:turn|switch|set|put)\s+(on|off)\s+(?:the\s+)?conversation(?: mode| lane)?\b"
                  r"|\b(?:turn|switch|set|put)\s+(?:the\s+)?conversation(?: mode| lane)?\s+(on|off)\b"
                  r"|\bconversation (?:mode|lane) (on|off)\b|\b(enable|disable)\s+(?:the\s+)?conversation", t, re.I)
    if m and belt.has("set_conversation_mode"):
        word = next(g for g in m.groups() if g).lower()
        return "set_conversation_mode", {"on": word in ("on", "enable")}

    # "add coffee with Tom tomorrow at 3": a calendar event (it asks first), never a silent refusal
    m = re.match(r"^(?:please |can you |could you |would you )?(?:add|put|schedule|book)\s+(.+?)\s+"
                 r"((?:tomorrow|today|tonight|on \w+day|next \w+day)(?:\s+at\s+[\w:. ]+?)?|at\s+\d{1,2}(?:[:.]\d{2})?"
                 r"(?:\s*(?:am|pm|o'?clock))?(?:\s+(?:tomorrow|today|tonight|on \w+day))?)[?.!]*$", t, re.I)
    if m and belt.has("calendar_add") and not re.search(r"\b(remind|reminder|list|note|shopping|grocery)\b", t, re.I):
        from core.weather import extract_when
        day, hour = extract_when(t)
        tm = re.search(r"\bat\s+(\d{1,2}(?:[:.]\d{2})?\s*(?:am|pm|o'?clock)?)", m.group(2), re.I)
        return "calendar_add", {"title": re.sub(r"^(?:a |an |my )", "", m.group(1).strip()),
                                **({"day": day} if day else {}), **({"time": tm.group(1).strip()} if tm else {})}
    m = re.search(r"\b(?:move|reschedule|push|change)\s+(?:my\s+|the\s+)?(?!everything\b|all\b)(.+?)\s+(?:to|for|at)\s+"
                  r"(\d{1,2}(?:[:.]\d{2})?\s*(?:am|pm|o'?clock)?)", t, re.I)
    if m and belt.has("calendar_move"):
        return "calendar_move", {"title": re.sub(r"\s+(?:tomorrow|today)$", "", m.group(1).strip()),
                                 "time": m.group(2).strip()}
    if belt.has("forge_status") and re.search(
            r"\bhow(?:'s| is) (?:the |my |that |this )?(?:skill\w*|build\w*|forge)\b|\bis (?:the skill|the build|it) "
            r"(?:done|ready|finished)\b|\bhow(?:'s| is) (?:it|that|this) going\b", t, re.I):
        from core.forge_jobs import get_jobs
        last = get_jobs().latest()
        if re.search(r"\b(skill|build|forge)\w*", t, re.I) or (last and time.time() - (last.finished or last.created) < 1800):
            return "forge_status", {}

    # phone alarms and timers (v0.3 9c)
    m = re.search(r"\balarm\b(.*)$|\bwake me(?: up)?\b(.*)$", t, re.I)
    if m and belt.has("phone_alarm") and not re.search(r"\b(cancel|delete|remove|turn off|stop)\b", t, re.I):
        from core.device import alarm_time
        rest = (m.group(1) or m.group(2) or "").strip(" ?.!")
        if alarm_time(rest):
            return "phone_alarm", {"time": re.sub(r"^(?:for|at)\s+", "", rest, flags=re.I)}
    m = re.search(r"\b(?:set (?:an? )?timer|start (?:an? )?timer|timer)\s+(?:for\s+|of\s+)?(.+?)[?.!]*$", t, re.I)
    if m and belt.has("phone_timer"):
        from core.device import parse_duration
        if parse_duration(m.group(1)):
            return "phone_timer", {"duration": m.group(1)}

    # weather words always mean the weather tool, never a web search (Sep 27: invented 12 and 3 degrees)
    if belt.has("get_weather") and WEATHER_RX.search(t) and not re.search(
            r"\b(remind|calendar|note|email|skill|app|website)\b", t, re.I):
        place = place_from(t)
        return "get_weather", ({"city": place} if place else {})

    simple = [
        (r"what time is it|what'?s the time|^\s*time\s*\??$", "get_datetime", {}),
        (r"\bvolume up\b|\blouder\b|\bturn (?:(?:it|this|that|the music|the volume|the sound) )?up\b",
         "media_control", {"action": "volup"}),
        (r"\bvolume down\b|\bquieter\b|\bturn (?:(?:it|this|that|the music|the volume|the sound) )?down\b"
         r"|\blower (?:it|the volume|the music)\b", "media_control", {"action": "voldown"}),
        (r"^\s*(mute|unmute)\b", "media_control", {"action": "mute"}),
        (r"^\s*(pause|resume)\b", "media_control", {"action": "playpause"}),
        (r"\b(stop|pause) (the |this )?(music|song|playback)\b|\bstop playing\b", "media_control",
         {"action": "playpause"}),
        (r"\bnext (track|song)\b|^\s*skip\b", "media_control", {"action": "next"}),
        (r"\b(previous|last) (track|song)\b", "media_control", {"action": "previous"}),
    ]
    for rx, tool, args in simple:
        if belt.has(tool) and re.search(rx, t, re.I):
            return tool, args
    return None


# ============================================================ orchestrator
class _Blank(dict):
    def __missing__(self, key):
        return "them"


class HybridOrchestrator:
    def __init__(self, persona: str = "eva", session_id: str | None = None,
                 cortex: Cortex | None = None, registry: SkillRegistry | None = None,
                 mcp: MCPManager | None = None):
        self.cfg = local_cfg()
        self.mem_cfg = get_settings().get("memory", {})
        self.persona = load_persona(persona)
        self.session_id = session_id or uuid.uuid4().hex[:12]
        self.cortex = cortex or get_cortex()
        self.registry = registry or get_registry()
        self.belt = ToolBelt(self.registry, mcp if mcp is not None else get_mcp())
        self.schema = build_decision_schema(self.belt.schemas)
        self.bus = get_bus()
        self.pending: dict | None = None
        self.last_tools: set[str] = set()          # tools used last turn: follow-ups pass the intent guard
        self.recent_tools: deque[set[str]] = deque(maxlen=3)   # the last three turns, for longer follow-ups
        self.pending_queue: list[dict] = []        # more confirmations waiting after the current one
        self._last_ack: str | None = None
        self.awaiting: dict | None = None          # a question is open ("What should the email to Muaad say?")
        self._conv_left = 0                        # conversation lane: follow-ups that stay in it
        self._conv_turn = False                    # this turn was answered in the conversation lane
        self.last_lane = "task"
        self.last_missing: str | None = None       # what FORGE would build if the user says "build it"
        self.last_action: dict | None = None       # the last action that ran: "no, I said in 20 minutes" re-times it
        self.last_planned: dict | None = None      # the last thing planned for later, for the same correction
        self._turn_input = ""
        self.pending_offer: dict | None = None      # "Should I check?" -> "yes" runs it
        self.locked = False                          # the phone overlay over the lock screen (v0.3 9b)
        self.after_unlock: tuple[str, float] | None = None
        self.understand_mode: str | None = None     # None = settings; the eval sets each mode in turn
        self._stale = False
        self._bg: set[asyncio.Task] = set()
        self.bus.subscribe("skills.changed", lambda _e: setattr(self, "_stale", True))
        hours = float(self.mem_cfg.get("continuity_hours", 24))
        self.history: list[dict] = self.cortex.recent_turns(limit=6, within_hours=hours)
        if self.history:
            logger.info(f"CORTEX: resumed {len(self.history)} recent turns")

    def refresh_tools(self) -> None:
        """Pick up skills FORGE installed (hot reload, no restart)."""
        self.belt = ToolBelt(self.registry, self.belt.mcp)
        self.schema = build_decision_schema(self.belt.schemas)
        self._stale = False
        logger.info(f"tools refreshed: {len(self.belt.schemas)} available")

    def _missing_answer(self, user_input: str) -> str:
        self.last_missing = user_input
        forge_ready = self.belt.has("forge_build")
        return MISSING_SKILL_FORGE if forge_ready else MISSING_SKILL

    # ------------------------------------------------------------ llm calls
    def _recent(self) -> set[str]:
        return set().union(self.last_tools, *self.recent_tools)

    def _mode(self) -> str:
        return self.understand_mode or understanding.cfg()["mode"]

    def _local_model(self) -> str:
        return (understanding.cfg().get("local_model") if self._mode() != "legacy" else "") or self.cfg["decision_model"]

    def _ollama_body(self, model: str, **body) -> dict:
        body = {"model": model, "stream": False, "keep_alive": self.cfg["keep_alive"],
                "options": {"temperature": 0}, **body}
        if model.startswith("qwen3"):
            body["think"] = False                  # qwen3.x thinks by default: seconds we don't have in a voice turn
        return body

    async def _decide(self, client, skill_bodies, gathered, messages: list[dict] | None = None) -> dict:
        """Which tool, and its arguments. understanding.mode picks who decides (core/understand/brains.py); after a
        tool has already answered (gathered), the local model decides whether one more is needed, as before."""
        msgs = messages or self.history[-MAX_HISTORY:]
        if self._mode() == "legacy" or gathered:
            return await self._decide_local(client, skill_bodies, gathered, msgs)

        async def fill(tool: str) -> dict:
            return args_for(self.belt.by_name[tool], await self._repair_args(client, tool, msgs), self.belt.names())

        return await understanding.understand(msgs, self.belt.schemas,
                                              lambda: self._decide_local(client, skill_bodies, gathered, msgs), fill,
                                              mode=self._mode(), private=self.understand_mode is None)

    async def _decide_local(self, client, skill_bodies, gathered, messages: list[dict]) -> dict:
        from core import localcheck
        from core.understand import retrieve
        # v0.9.1: the small model reads only the tools that fit this sentence (all 72 made a 3,000 token menu)
        subset = retrieve.select(messages, self.belt.schemas, used=sorted(self.last_tools), above=LOCAL_TOOLS,
                                 keep=LOCAL_TOOLS)
        menu = "\n".join(f"- {t['function']['name']}: {t['function']['description']}" for t in subset)
        system = build_decision_system(menu, skill_bodies, [out["result"] for _, out in gathered])
        t0 = time.time()
        try:
            r = await client.post(f"{self.cfg['base_url']}/api/chat", json=self._ollama_body(
                self._local_model(), format=build_decision_schema(subset) if len(subset) < len(self.belt.schemas)
                else self.schema, messages=[{"role": "system", "content": system}, *messages]),
                timeout=httpx.Timeout(LOCAL_DECISION_S, connect=3.0))
            data = r.json()
            localcheck.timing("decision", data, time.time() - t0)
            return json.loads(data["message"]["content"])
        except Exception as e:
            waited = time.time() - t0
            logger.warning(f"decision failed after {waited:.0f} s, answering directly: {type(e).__name__} {e}")
            if waited >= LOCAL_DECISION_S - 1:
                localcheck.mark_slow(f"the local model didn't decide in {LOCAL_DECISION_S:.0f} seconds")
                await asyncio.to_thread(localcheck.report, self.cfg["base_url"])
            return {"tool": "none"}

    async def _repair_args(self, client, tool: str, messages: list[dict] | None = None) -> dict:
        """Second tiny call constrained to ONE tool's schema, so required fields get filled."""
        fn = self.belt.by_name[tool]["function"]
        params = fn.get("parameters", {}).get("properties", {})
        spec = "\n".join(f"- {k}: {v.get('description', v.get('type', 'string'))}" for k, v in params.items())
        system = (f"Fill in the arguments for the tool '{tool}' ({fn['description']}) from the user's "
                  f"latest message and the conversation. Copy the user's words; never invent content. "
                  f"Parameters:\n{spec}")
        try:
            r = await client.post(f"{self.cfg['base_url']}/api/chat", json=self._ollama_body(
                self._local_model(), format=tool_arg_schema(self.belt.by_name[tool]),
                messages=[{"role": "system", "content": system}, *(messages or self.history[-MAX_HISTORY:])]))
            return json.loads(r.json()["message"]["content"])
        except Exception as e:
            logger.warning(f"argument repair failed for {tool}: {e}")
            return {}

    async def _stream_answer(self, client, facts, skill_bodies, gathered, user_input) -> AsyncIterator[dict]:
        persona = self.persona + "\n\nWhat you can do: " + ", ".join(
            s.name for s in self.registry.enabled()) + ", weather, time, calculator, web search, Spotify play and " \
            "media keys, volume, opening apps and websites, messages to the user's phone (Telegram)."
        if _CAPABILITY_Q.search(user_input):
            persona += "\n\nYour current tools:\n" + self.belt.list_text() + \
                       "\n\nYour skills:\n" + self.registry.index_text()
        system = build_answer_system(persona, STYLE_RULES, facts, skill_bodies,
                                     [out["result"] for _, out in gathered])
        full: list[str] = []
        from core import localcheck
        slow = localcheck.recently_slow()
        if slow and not gathered:                    # v0.9.1: don't wait another two minutes; say what's wrong
            yield {"type": "final", "text": LOCAL_SLOW_SAY}
            return
        t0 = time.time()
        try:
            async with client.stream("POST", f"{self.cfg['base_url']}/api/chat", timeout=httpx.Timeout(
                    LOCAL_ANSWER_S, connect=3.0), json={
                "model": self.cfg["answer_model"], "stream": True,
                "keep_alive": self.cfg["keep_alive"], "options": {"temperature": 0.3},
                "messages": [{"role": "system", "content": system}, *self.history[-MAX_HISTORY:]],
            }) as resp:
                async for line in resp.aiter_lines():
                    if not line.strip():
                        continue
                    chunk = json.loads(line)
                    tok = chunk.get("message", {}).get("content", "")
                    if tok:
                        full.append(tok)
                        yield {"type": "token", "text": tok}
                    if chunk.get("done"):
                        localcheck.timing("answer", chunk, time.time() - t0)
                        break
        except Exception as e:
            logger.error(f"answer stream failed after {time.time() - t0:.0f} s: {type(e).__name__} {e}")
            if not full:
                localcheck.mark_slow("the local model didn't start an answer in time")
                await asyncio.to_thread(localcheck.report, self.cfg["base_url"])
                yield {"type": "final", "text": LOCAL_SLOW_SAY}
                return
        yield {"type": "final", "text": "".join(full).strip() or "I had trouble forming that reply, sir."}

    # ------------------------------------------------------------ helpers
    def _context(self, user_input: str, ctx: dict) -> dict:
        """Embedding, recall, and skill matching, computed at most once and only when needed."""
        if "facts" not in ctx:
            qvec = self.cortex.embed_query(user_input)
            ctx["facts"] = [] if getattr(self, "locked", False) else \
                self.cortex.recall(user_input, k=int(self.mem_cfg.get("recall_k", 6)), query_vec=qvec)
            ctx["skills"] = self.registry.bodies(self.registry.match(user_input, qvec))
        return ctx

    def _vary(self, ack: str | None) -> str | None:
        """Generic acks rotate; a specific one ("Checking your inbox") stays, unless it was the last thing said."""
        if not ack:
            return None
        if ack in _GENERIC_SOURCES or ack == self._last_ack:
            ack = random.choice([a for a in GENERIC_ACKS if a != self._last_ack])
        self._last_ack = ack
        return ack

    def _answers_mind(self, text: str) -> bool:
        """His reply to a question the mind just asked (once: the next sentence only)."""
        asked, self._mind_asked = getattr(self, "_mind_asked", 0.0), 0.0
        if not asked or time.time() - asked > 180 or not self._mind_ready(text):
            return False
        words = len(text.split())
        return bool(_ANSWER_WORDS.match(text.strip())) and words <= 45

    async def _streamed(self, work, q: asyncio.Queue, ack: str | None, ack_after_s: float):
        """Run a streamed Claude call: ("text", words) as they come, ("ack", ...) once if nothing came for
        ack_after_s, then ("done", result)."""
        task = asyncio.ensure_future(work)
        loop = asyncio.get_running_loop()
        start, got_any = loop.time(), False
        try:
            while True:
                getter = asyncio.ensure_future(q.get())
                timeout = None
                if ack and not got_any:
                    timeout = max(0.0, start + ack_after_s - loop.time())
                done, _ = await asyncio.wait({task, getter}, timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
                if getter in done:
                    got_any = True
                    yield "text", getter.result()
                    continue
                getter.cancel()
                if task in done:
                    while not q.empty():
                        yield "text", q.get_nowait()
                    yield "done", task.result()
                    return
                if ack and not got_any:                       # the wait timed out: still silent
                    pick = random.choice([a for a in MIND_ACKS if a != getattr(self, "_last_ack", None)])
                    self._last_ack = pick
                    yield "ack", pick
                    ack = None
        except BaseException:
            task.cancel()
            raise

    async def _with_ack(self, ack: str | None, work) -> AsyncIterator[tuple[str, object]]:
        """Run `work`; yield ("ack", text) only if it's still busy after ack_after(), then ("done", result)."""
        task = asyncio.ensure_future(work)
        try:
            delay = ack_after()
            if ack and delay >= 0:
                if delay == 0:
                    yield "ack", self._vary(ack)
                else:
                    done, _ = await asyncio.wait({task}, timeout=delay)
                    if not done:
                        yield "ack", self._vary(ack)
            yield "done", await task
        except BaseException:
            task.cancel()
            raise

    async def _run(self, tool: str, args: dict, gathered: list, ack: bool = True) -> AsyncIterator[dict]:
        if getattr(self, "locked", False) and tool not in LOCKED_OK and not _messages_when_locked(tool) \
                and not _fine_locked(tool, args):
            logger.info(f"LOCKED: {tool} waits for an unlock")
            self.after_unlock = (self._turn_input, time.time())
            yield {"type": "unlock"}
            out = {"result": json.dumps({"error": "phone locked"}), "say": LOCKED_SAY, "exact": True}
            for ev in self._post(tool, args, out, gathered):
                yield ev
            return
        async for kind, value in self._with_ack(self.belt.ack(tool) if ack else None, self.belt.aexecute(tool, args)):
            if kind == "ack":
                yield {"type": "ack", "text": value}      # only when the tool is taking a while
            else:
                for ev in self._post(tool, args, value, gathered):
                    yield ev

    def _post(self, tool: str, args: dict, out: dict, gathered: list) -> list[dict]:
        """After a tool ran: honest failure line, PULSE, follow-up confirmation, widget, gathered."""
        events = []
        ok = '"error"' not in (out.get("result") or "")
        if not ok and self.belt.is_action(tool) and "say" not in out:
            try:
                err = json.loads(out["result"]).get("error", "")
            except Exception:
                err = ""
            out["say"] = f"That didn't work, sir. {err[:160]}".strip()
        self.bus.publish("tool.executed", {"tool": tool, "args": args, "ok": ok})
        if out.get("ask_next"):
            self.awaiting = {"tool": tool, "args": args, "field": out["ask_next"]["field"], "ts": time.time()}
        nxt = out.get("confirm_next")
        if nxt and ok:
            self.pending = {"tool": nxt["tool"], "args": nxt.get("args", {}), "desc": nxt.get("desc", ""), "ts": time.time()}
        if out.get("widget"):
            events.append({"type": "widget", "data": out["widget"]})
        if ok and self.belt.is_action(tool):
            self.last_action = {"tool": tool, "args": dict(args), "request": self._turn_input, "ts": time.time()}
        gathered.append((tool, out))
        return events

    def _question(self, tool: str, args: dict) -> str | None:
        """The tool's required question if its field is still empty; also opens the answer slot."""
        spec = self.belt.asks.get(tool)
        if callable(spec):
            spec = spec(args)                            # a skill can ask different things in turn
        field, question = spec or (None, None)
        if not field or str(args.get(field) or "").strip():
            return None
        self.awaiting = {"tool": tool, "args": args, "field": field, "ts": time.time()}
        return question.format_map(_Blank({k: v for k, v in args.items() if v}))

    async def _place_rule(self, client, clean: str, place: str, event: str, once: bool, user_input: str,
                          ctx: dict) -> str:
        """Understood now, done when you arrive or leave: an action (decided now, like 'in 10 minutes') or a
        reminder."""
        from core import places as places_mod
        pl = places_mod.get_places()
        if place not in pl.data["places"]:
            return (f"I don't know where {place} is yet, sir. When you're there, say 'remember this place as "
                    f"{place}', and ask me again.")
        when = places_mod.phrase(event, place)
        m = re.match(r"^(?:please\s+)?(?:can you\s+)?(?:remind me|tell me|ask me)(?:\s+(?:to|that|about))?\s+(.+)$",
                     clean, re.I)
        if not m:
            self._context(clean, ctx)
            picked = await self._pick(client, clean, user_input, ctx, [], False)
            if picked and self.belt.is_action(picked[0]) and not self.belt.needs_confirm(picked[0]):
                pl.add_rule(place, event, clean, tool=picked[0], args=picked[1], once=once)
                return f"Okay, sir: when you {when}, I'll {later_mod.tidy(clean)}."
        what = (m.group(1) if m else clean).strip().rstrip(".")
        pl.add_rule(place, event, what, once=once)
        return f"Okay, sir: when you {when}, I'll remind you to {what}."

    async def _fill(self, name: str, args: dict, text: str) -> dict:
        """The tool's argument filler. A slow one (a reply translated to Romanian) runs off the event loop."""
        if name in getattr(self.belt, "slow_fill", ()):
            return await asyncio.to_thread(self.belt.fill, name, args, text)
        return self.belt.fill(name, args, text)

    def _ask_confirmation(self, tool: str, args: dict, user_input: str, used: set) -> list[dict]:
        check = getattr(self.belt, "prechecks", {}).get(tool)
        stop = check(args) if check else None
        if stop:                                         # Nov 23: nothing to do, so no "shall I go ahead?"
            self._finish(user_input, stop, used)
            return [{"type": "final", "text": stop}]
        q = self._question(tool, args)
        if q:                                            # first what, then "shall I go ahead?"
            self._finish(user_input, q, used)
            return [{"type": "final", "text": q}]
        desc = self.belt.describe(tool, args)
        self.pending = {"tool": tool, "args": args, "desc": desc, "ts": time.time()}
        text = f"Just to confirm, sir: {desc}. Shall I go ahead?"
        audit.log("confirm_requested", "orchestrator", {"tool": tool, "args": args})
        self.bus.publish("confirm.requested", {"tool": tool, "desc": desc})
        self._finish(user_input, text, used)
        return [{"type": "widget", "data": {"kind": "confirm", "title": "Confirm", "text": desc}},
                {"type": "final", "text": text}]

    async def _answer(self, client, gathered, user_input, ctx) -> AsyncIterator[dict]:
        says = [out.get("say") for _, out in gathered]
        only_exact = gathered and all(self.belt.is_action(t) or t in self.belt.exact or out.get("exact")
                                      or '"error"' in out.get("result", "") for t, out in gathered)
        if only_exact and all(says):                      # exact lines, no LLM: instant and truthful
            yield {"type": "final", "text": " ".join(dict.fromkeys(says))}
            return
        if any(t == "web_search" for t, _ in gathered):   # web answers are checked BEFORE they are spoken
            self._context(user_input, ctx)
            text = spoken(await self._grounded_web_answer(client, ctx, gathered, user_input))
            acted = any(self.belt.is_action(t) and '"error"' not in out.get("result", "") for t, out in gathered)
            if not acted and claims(text):
                logger.warning(f"claim guard (web): {text!r} but no action ran")
                text = NOT_DONE
            yield {"type": "final", "text": text}
            return
        if len(gathered) == 1 and gathered[0][0] in ("forget_memory",) and says[0]:
            yield {"type": "final", "text": says[0]}       # ambiguity questions are exact too
            return
        self._context(user_input, ctx)
        final = ""
        async for ev in self._stream_answer(client, ctx["facts"], ctx["skills"], gathered, user_input):
            if ev["type"] == "final":
                final = ev["text"]
                fallback = next((out.get("say") for _, out in reversed(gathered)
                                 if out.get("say") and '"error"' not in out.get("result", "")), None)
                if fallback and (_DENIAL.search(final) or re.search(r"\b(?:see|found|have) nothing\b|\bnothing (?:on|to see)\b", final, re.I)):
                    logger.warning(f"denial guard: replaced {final!r}")
                    ev = {"type": "final", "text": fallback}
                acted = any(self.belt.is_action(t) and '"error"' not in out.get("result", "") for t, out in gathered)
                if not acted and claims(ev["text"]):
                    logger.warning(f"claim guard: {ev['text']!r} but no action ran")
                    ev = {"type": "final", "text": NOT_DONE}
            yield ev

    def _resolve_vague(self, request: str) -> str:
        """Nov 25: 'can you develop a skill to open it?' built a skill for "open it?". A request that leans on 'it' or
        'that' gets the sentence before it."""
        words = re.findall(r"[a-z']+", (request or "").lower())
        if len(words) > 6 or not set(words) & {"it", "that", "this", "them", "those", "one"}:
            return request
        prev = next((m["content"] for m in reversed(self.history[:-1]) if m.get("role") == "user"
                     and isinstance(m.get("content"), str)), "")
        return f"{prev.strip().rstrip('?.!')} ({request.strip().rstrip('?.!')})" if prev else request

    # ------------------------------------------------------------ the mind (v0.5): what needs thinking
    def _mind_ready(self, user_input: str) -> bool:
        from core import mind
        if is_small_talk(user_input) and len(user_input.split()) <= 8:
            return False                                  # "how are you?" stays a quick local answer
        return mind.allowed()

    async def _mind(self, client, user_input: str, ctx: dict, gathered: list, used: set) -> AsyncIterator[dict]:
        """Think it through with every tool (core/mind.py). Sets self._mind_done when it answered; if Claude fails
        on the way, the old path answers with whatever was gathered."""
        from core import mind
        from core.budget import BudgetExceeded
        from core.claude import ClaudeUnavailable, get_claude
        self._mind_done = False
        c = mind.cfg()
        if getattr(self, "_mind_system", None) is None:
            self._mind_system = mind.system_prompt()
        from core import browser as brw
        with_browser = brw.allowed()
        if getattr(self, "_mind_tools_for", None) is not self.belt or getattr(self, "_mind_browser", None) != with_browser:
            self._mind_tools = mind.tools_for(self.belt.schemas) + ([brw.ENTRY] if with_browser else [])
            self._mind_tools_for, self._mind_browser = self.belt, with_browser
        browsed = False
        self._context(user_input, ctx)
        hint, self._mind_hint = getattr(self, "_mind_hint", None), None
        situation = mind.situation(self)
        if hint:
            situation += (f"\nUnderstanding's guess for this sentence: {hint[0]}({json.dumps(hint[1], ensure_ascii=False)}). "
                          f"Use it if it's right, and answer like a thoughtful assistant, not a form.")
        msgs = mind.build_messages(self.history, user_input, situation, mind.facts_text(ctx.get("facts")),
                                   int(c["history_turns"]))
        if hint and hint[0] in ("look_at_shown", "see_screen"):   # Nov 25/27: she answers from what she SEES
            img, note = None, ""
            try:
                if hint[0] == "see_screen":
                    from skills.vision.tools import grab_screen
                    img = await asyncio.to_thread(grab_screen)
                    note = "(Here is his screen right now. Read it carefully: the address bar, the seller, the price.)"
                else:
                    from core.show import tool_look_at_shown
                    img = (tool_look_at_shown(**hint[1]) or {}).get("_image")
                    note = "(Here is that picture.)"
            except Exception as e:
                logger.warning(f"MIND: couldn't get the picture ({e})")
            if img:
                msgs[-1]["content"] = [{"type": "text", "text": msgs[-1]["content"] + "\n" + note},
                                       {"type": "image", "source": {"type": "base64", "media_type": img["media_type"],
                                                                    "data": img["b64"]}}]
                logger.info(f"MIND: looking at {'his screen' if hint[0] == 'see_screen' else 'the picture he asked about'}")
        from core.claude import ClaudeCut, line_trouble
        chat = not getattr(self, "_mind_handed", False)   # v0.8: plain talk thinks briefly, never says "one moment"
        model, claude = c["model"], get_claude()
        effort = (c.get("chat_effort") if chat else c.get("effort")) or None
        last_text, spoke = "", False
        words = creative(user_input)                    # a story or a talk: words, not claims (Nov 27 02:08)
        max_tokens = int(c["max_tokens"])
        mins = re.search(r"\b(\d{1,2})\s*(?:-\s*)?min(?:ute)?s?\b", user_input)
        if words:                                       # v0.9: "a story for 10 minutes" is told in one go
            minutes = min(25, int(mins.group(1))) if mins else 4
            max_tokens = max(max_tokens, min(16000, int(minutes * 140 * 1.45) + 1500))
        loop = asyncio.get_running_loop()
        for step in range(int(c["max_steps"])):
            q: asyncio.Queue = asyncio.Queue()
            on_text = (lambda t: loop.call_soon_threadsafe(q.put_nowait, t)) if c.get("stream", True) else None
            call = asyncio.to_thread(claude.message, system=self._mind_system, messages=msgs, tools=self._mind_tools,
                                     model=model, max_tokens=max_tokens, effort=effort, purpose="mind",
                                     cache=True, on_text=on_text)
            speech = _Speech([t for t, _ in gathered], words)
            ack = None if (chat or step or spoke) else "One moment, sir."
            try:
                resp = None
                async for kind, value in self._streamed(call, q, ack, float(c.get("ack_after_s", 2.5))):
                    if kind == "ack":
                        yield {"type": "ack", "text": value}
                    elif kind == "text":
                        for sent in speech.feed(value):
                            spoke = True
                            yield {"type": "token", "text": sent + " "}
                    else:
                        resp = value
            except Exception as e:
                if isinstance(e, ClaudeCut) or line_trouble(e):
                    # Nov 27: the old way then promised a task it never started. Say what happened instead.
                    logger.warning(f"MIND: lost the line to Claude ({str(e)[:120]})")
                    self._mind_done = True
                    text = ("Sorry, sir, the line to Claude dropped mid-answer. Ask me again and I'll finish that."
                            if spoke or isinstance(e, ClaudeCut) else
                            "I lost the line to Claude for a moment, sir. Could you say that again?")
                    self._finish(user_input, text, used)
                    yield {"type": "final", "text": text, "replaced": True}
                    return
                if isinstance(e, (BudgetExceeded, ClaudeUnavailable, RuntimeError, httpx.HTTPError, OSError)):
                    logger.warning(f"MIND: {str(e)[:160]}; answering the old way")
                    return
                raise
            for sent in speech.flush():                    # the last words, or what she said before a tool
                spoke = True
                yield {"type": "token", "text": sent + " "}
            msgs.append({"role": "assistant", "content": resp.get("content") or
                         [{"type": "text", "text": resp.get("text") or "..."}]})
            uses = resp.get("tool_uses") or []
            text = mind.spoken(resp.get("text") or "")
            last_text = text or last_text
            if not uses:
                answer = text or "I'm not sure, sir. Could you say that another way?"
                ran = [t for t, _ in gathered]
                kind = None if words else unchecked_fact(answer, ran)   # the same guards: no made-up facts
                if kind:
                    logger.warning(f"MIND: fact guard ({kind}): {answer!r}")
                    answer, check = offer_for(kind, user_input, answer)
                    if check:
                        self.pending_offer = {"tool": check[0], "args": check[1], "ts": time.time()}
                acted = any(self.belt.is_action(t) and '"error"' not in out.get("result", "") for t, out in gathered)
                if not acted and not words and claims(answer):
                    logger.warning(f"MIND: claim guard: {answer[:300]!r} but no action ran")
                    answer = NOT_DONE
                replaced = answer != text
                if not replaced and speech.held:            # a sentence looked risky but the whole answer is fine
                    yield {"type": "token", "text": " ".join(speech.held) + " "}
                logger.info(f"MIND: answered after {step + 1} step{'s' if step else ''} ({model})")
                self._mind_asked = time.time() if (answer.rstrip().endswith("?") or _GO_ON_INVITE.search(
                    answer[-200:])) else 0.0
                self._mind_done = True
                self._finish(user_input, answer, used)
                if browsed and brw.SHOT.exists():            # what she saw, in the window
                    b = brw.get_browser()
                    yield {"type": "widget", "data": {"kind": "media", "media": "image", "title": b.last_title[:80]
                                                      or "Her browser", "source": b.last_url[:120],
                                                      "image": f"/api/browser/shot.png?v={int(time.time())}",
                                                      "url": b.last_url}}
                yield {"type": "final", "text": answer, **({"replaced": True} if replaced and spoke else {})}
                return
            logger.info(f"MIND: step {step + 1}: {', '.join(u['name'] for u in uses)}")
            results, failed_set = [], set()
            for u in uses:
                name, args = u["name"], mind.json_args(u)
                if u.get("toolset_name") == "browser":     # v0.7: her own browser, inside the answer
                    if "browser" in failed_set:
                        results.append(brw.skipped(u, "browser"))
                        continue
                    if not browsed and not spoke:              # v0.8: only if she hasn't said what she's doing
                        site = re.sub(r"^https?://(www\.)?", "", str((u.get("input") or {}).get("url") or "")).split("/")[0]
                        yield {"type": "ack", "text": f"Checking {site}, sir." if site else "Checking the page, sir."}
                    res = await asyncio.to_thread(brw.result, u)
                    browsed = True
                    if res.get("is_error"):
                        failed_set.add("browser")
                    results.append(res)
                    continue
                if u.get("toolset_name"):
                    results.append({"type": "tool_result", "tool_use_id": u["id"], "toolset_name": u["toolset_name"],
                                    "is_error": True, "content": f"{u['toolset_name']} isn't available here; use "
                                                                 f"use_computer for the screen"})
                    continue
                if name == "think_harder":
                    model, effort = c["hard_model"], c.get("hard_effort") or None
                    results.append({"type": "tool_result", "tool_use_id": u["id"],
                                    "content": "A stronger model answers from here on."})
                    continue
                if not self.belt.has(name) or name in mind.SKIP_TOOLS:
                    results.append({"type": "tool_result", "tool_use_id": u["id"], "is_error": True,
                                    "content": f"there's no tool called {name}"})
                    continue
                args = await self._fill(name, args, user_input)
                if self.belt.needs_confirm(name) or (self.belt.is_action(name) and not (
                        self.belt.guard_ok(name, user_input) or name in self._recent() or self.belt.acts_alone(name))):
                    # it changes something: E.V.A. asks, his yes runs it; the turn ends here
                    self._mind_done = True
                    for ev in self._ask_confirmation(name, args, user_input, used):
                        if ev["type"] == "final" and spoke:       # her words are out already: say the question too
                            yield {"type": "token", "text": ev["text"] + " "}
                        if ev["type"] == "final" and text and text not in ev["text"]:
                            ev = {**ev, "text": f"{text} {ev['text']}"}
                        yield ev
                    return
                used.add(name)
                shown = False
                async for ev in self._run(name, args, gathered):
                    shown = shown or ev.get("type") == "widget"
                    yield ev
                out = gathered[-1][1] if gathered else {}
                res = {"type": "tool_result", "tool_use_id": u["id"], "content": mind.tool_result_content(out, shown)}
                if '"error"' in (out.get("result") or ""):
                    res["is_error"] = True
                results.append(res)
            msgs.append({"role": "user", "content": results})
        says = [out.get("say") for _, out in gathered if out.get("say")]
        answer = last_text or " ".join(dict.fromkeys(says)) or "I looked into it, sir, but I need another try."
        logger.info(f"MIND: stopped after {c['max_steps']} steps")
        self._mind_done = True
        self._finish(user_input, answer, used)
        yield {"type": "final", "text": answer}

    # ------------------------------------------------------------ conversation lane (v0.2.5)
    @staticmethod
    def _alternating(history: list[dict]) -> list[dict]:
        """Claude wants user/assistant turns alternating, starting with the user."""
        out: list[dict] = []
        for m in history:
            if m.get("role") not in ("user", "assistant") or not m.get("content"):
                continue
            if out and out[-1]["role"] == m["role"]:
                out[-1] = {"role": m["role"], "content": out[-1]["content"] + "\n" + m["content"]}
            else:
                out.append({"role": m["role"], "content": m["content"]})
        while out and out[0]["role"] != "user":
            out.pop(0)
        return out

    async def _converse(self, client, user_input: str, ctx: dict) -> AsyncIterator[dict]:
        self._context(user_input, ctx)
        system = conversation.system_prompt(self.persona, ctx.get("facts") or [], voice=True)
        text, streamed = "", False
        try:
            from core.brain import get_brain
            b = get_brain()
            if b.pick("conversation") == "cloud":
                out = await asyncio.to_thread(b.claude.message, system=system,
                                              messages=self._alternating(self.history[-MAX_HISTORY:]),
                                              max_tokens=500, purpose="conversation")
                text = (out.get("text") or "").strip()
                logger.info("CONVERSATION: answered with Claude")
        except Exception as e:
            logger.warning(f"CONVERSATION: Claude unavailable ({e}); answering locally")
            text = ""
        if not text:
            full: list[str] = []
            try:
                async with client.stream("POST", f"{self.cfg['base_url']}/api/chat", json={
                    "model": self.cfg["answer_model"], "stream": True, "keep_alive": self.cfg["keep_alive"],
                    "options": {"temperature": 0.6, "num_predict": 320},
                    "messages": [{"role": "system", "content": system}, *self.history[-MAX_HISTORY:]],
                }) as resp:
                    async for line in resp.aiter_lines():
                        if not line.strip():
                            continue
                        chunk = json.loads(line)
                        tok = chunk.get("message", {}).get("content", "")
                        if tok:
                            full.append(tok)
                            streamed = True
                            yield {"type": "token", "text": tok}
                        if chunk.get("done"):
                            break
            except Exception as e:
                logger.error(f"CONVERSATION: local answer failed: {e}")
            text = "".join(full).strip()
        if claims(text):                         # a chat never claims an action
            logger.warning(f"claim guard (conversation): {text!r}")
            text = NOT_DONE
        yield {"type": "final", "text": text or "I lost my train of thought there, sir. Say that again?"}

    # ------------------------------------------------------------ multi-step (v0.2.5)
    def _next_confirmation(self, prefix: str, first: bool = False) -> tuple[str, dict]:
        nxt = self.pending_queue.pop(0)
        self.pending = {**nxt, "ts": time.time()}
        lead = "Just to confirm, sir:" if first else "Next, sir:"
        text = f"{prefix} {lead} {nxt['desc']}. Shall I go ahead?".strip()
        return text, {"type": "widget", "data": {"kind": "confirm", "title": "Confirm", "text": nxt["desc"]}}

    def _schedule(self, picked: tuple, clean: str, delay: float) -> str:
        """Actions later: planned now, run by core/later.py at the time, reported with the tool's own result."""
        from datetime import datetime
        tool, args = picked
        if self.belt.needs_confirm(tool):
            return ("That one needs your yes at the moment it happens, sir, so I won't do it unattended. "
                    "I can set a reminder instead.")
        clean = later_mod.tidy(clean)
        item = later_mod.get_later().add(tool, args, delay, clean, session=self.session_id)
        self.last_planned = {**item, "ts": time.time()}
        return f"Okay, sir: {clean} in {later_mod.human(delay)}, at {datetime.fromtimestamp(item['due']):%H:%M}."

    def _when_later(self, text: str) -> tuple[str, float | None]:
        """'in 10 minutes', 'at 2:15', 'at that time' (= the last thing planned), and in a correction a bare
        '211' ('No, not right now, but 211'). (command without the time words, seconds from now) or (text, None)."""
        clean, delay = later_mod.split_delay(text)
        if delay:
            return clean, delay
        bare_clean, delay = later_mod.split_clock(text, bare=True)
        if delay and later_mod.is_correction(bare_clean):
            return bare_clean, delay                 # "No, not right now, but 211": re-time what she just did
        if not re.search(r"\b(turn|switch|stop|pause|play|start|resume|dim|close|open|mute|unmute|skip|put|lower|"
                         r"raise|kill|shut|lock|send|set (?:the )?(?:lights?|volume))\b", text, re.I):
            return text, None              # 'what's the weather at 6' is a question, not something to do later
        clean, same = later_mod.split_same_time(text)
        if same:
            item = self.last_planned
            pending = item and any(i["id"] == item["id"] for i in later_mod.get_later().pending())
            if pending and item["due"] - time.time() > 5:
                return clean, item["due"] - time.time()
        clean, delay = later_mod.split_clock(text)
        if delay:
            return clean, delay
        return text, None

    # what puts things back the way they were, for "no, not now, in 20 minutes" after she already did it
    _UNDO = {"media_control": lambda a: {"playpause": {"action": "playpause"}, "pause": {"action": "play"},
                                         "play": {"action": "pause"}}.get(a.get("action")),
             "spotify_play": lambda a: {"action": "pause"},
             "lights_power": lambda a: {**a, "on": not a.get("on")}}
    _UNDO_TOOL = {"spotify_play": "media_control"}

    async def _retime(self, delay: float) -> str | None:
        """'No, not now, I said in 20 minutes': move the previous command to later instead of running the words
        'no, not now, I said'. If it already ran and can be undone (music, lights), undo it first. None when there
        is nothing recent to re-time."""
        from datetime import datetime
        lt, now = later_mod.get_later(), time.time()
        planned = self.last_planned if self.last_planned and now - self.last_planned["ts"] < 600 else None
        action = self.last_action if self.last_action and now - self.last_action["ts"] < 600 else None
        if planned and (not action or planned["ts"] >= action["ts"]):
            if lt.cancel_id(planned["id"]):          # it was planned already: just move it
                text = self._schedule((planned["tool"], planned["args"]), planned["text"], delay)
                return text.replace("Okay, sir:", "Got it, sir, moved:", 1)
        if not action:
            return None
        tool, args = action["tool"], action["args"]
        clean = later_mod.tidy(later_mod.split_delay(action["request"])[0] or action["request"])
        undo = self._UNDO.get(tool, lambda a: None)(args)
        if undo is None:
            lead = f"Sorry, sir, I already did that and can't undo it. I'll do it again in {later_mod.human(delay)}"
        else:
            out = await self.belt.aexecute(self._UNDO_TOOL.get(tool, tool), undo)
            ok = '"error"' not in (out.get("result") or "")
            lead = ("Sorry, sir, I did that straight away. I've put it back" if ok
                    else "Sorry, sir, I did that straight away and couldn't put it back")
        self.last_action = None
        if self.belt.needs_confirm(tool):
            return f"{lead}. That one needs your yes at the moment it happens, so I won't redo it on my own."
        item = lt.add(tool, args, delay, clean, session=self.session_id)
        self.last_planned = {**item, "ts": time.time()}
        when = f"{later_mod.human(delay)}, at {datetime.fromtimestamp(item['due']):%H:%M}"
        if undo is None:
            return f"{lead}, at {datetime.fromtimestamp(item['due']):%H:%M}."
        return f"{lead}, and I'll {clean} in {when}."

    async def _pick(self, client, step: str, original: str, ctx: dict, gathered: list, dependent: bool):
        """Which tool, with which arguments, for one step. None when nothing fits or a guard says no."""
        fp = fast_path(step, self.belt)
        if fp:
            return fp[0], await self._fill(fp[0], fp[1], step)
        msgs = [*self.history[-MAX_HISTORY:-1], {"role": "user", "content": step}]
        decision = await self._decide(client, ctx["skills"], gathered if dependent else [], messages=msgs)
        tool = decision.get("tool", "none")
        if tool in ("none", "unavailable") or not self.belt.has(tool):
            return None
        if not (decision.get("_trusted") or self.belt.guard_ok(tool, step) or self.belt.guard_ok(tool, original)
                or tool in self._recent()):
            logger.info(f"MULTI guard: {tool} blocked for {step!r}")
            return None
        schema = self.belt.by_name[tool]
        args = await self._fill(tool, args_for(schema, decision, self.belt.names()), step)
        if missing_required(schema, args):
            args = await self._fill(tool, {**args, **args_for(schema, await self._repair_args(client, tool, msgs),
                                                            self.belt.names())}, step)
            if missing_required(schema, args):
                return None
        return tool, args

    async def _flush(self, batch: list, gathered: list) -> AsyncIterator[dict]:
        """Run independent steps: each family (spotify_*, lights_*) in order, different families at once.
        Results are reported in the order you said them."""
        if not batch:
            return
        groups: dict[str, list[int]] = {}
        for i, (tool, _) in enumerate(batch):
            groups.setdefault(multistep.family(tool), []).append(i)
        if len(groups) == 1:
            for tool, args in batch:
                async for ev in self._run(tool, args, gathered):
                    yield ev
            return
        ack = next((self.belt.ack(t) for t, _ in batch if self.belt.ack(t)), None) or "On it, sir."
        outs: dict[int, dict] = {}

        async def run_group(idx: list[int]) -> None:
            for i in idx:
                tool, args = batch[i]
                try:
                    outs[i] = await self.belt.aexecute(tool, args)
                except Exception as e:                       # one failed step never sinks the others
                    outs[i] = {"result": json.dumps({"error": str(e)})}
        async for kind, value in self._with_ack(ack, asyncio.gather(*(run_group(idx) for idx in groups.values()))):
            if kind == "ack":
                yield {"type": "ack", "text": value}
        for i, (tool, args) in enumerate(batch):
            for ev in self._post(tool, args, outs[i], gathered):
                yield ev

    async def _multi(self, client, steps, user_input, gathered, used, ctx) -> AsyncIterator[dict]:
        self._context(user_input, ctx)
        batch, confirms, skipped = [], [], []
        skipped_q = None
        planned: list[str] = []
        # Nov 23: "turn off the lights and stop the music in 10 minutes" ran both at once: the planner dropped the
        # time words from both steps. When no step keeps its own time, the sentence's time is for every step.
        # (relative times only: "the PC at 100" is a volume, not 1 o'clock)
        whole_clean, whole = later_mod.split_delay(user_input)
        own = any(later_mod.split_delay(s["command"])[1] for s in steps)
        shared = whole if whole and not own and not later_mod.is_correction(whole_clean) else None
        for step in steps:
            if step["uses_previous"]:
                async for ev in self._flush(batch, gathered):
                    yield ev
                batch = []
            clean, delay = later_mod.split_delay(step["command"])
            if not delay and shared and not step["uses_previous"]:
                clean, delay = step["command"], shared
            if delay:                                      # "stop the music in 10 minutes": later, not now
                later_pick = await self._pick(client, clean, user_input, ctx, gathered, step["uses_previous"])
                if later_pick and self.belt.is_action(later_pick[0]):
                    planned.append(self._schedule(later_pick, clean, delay))
                    continue
            picked = await self._pick(client, step["command"], user_input, ctx, gathered, step["uses_previous"])
            if not picked:
                skipped.append(step["command"])
                continue
            tool, args = picked
            q = self._question(tool, args)
            if q:
                skipped_q = q                             # asked at the end; nothing runs for this step
                continue
            if self.belt.needs_confirm(tool):
                confirms.append({"tool": tool, "args": args, "desc": self.belt.describe(tool, args)})
                continue
            used.add(tool)
            if step["uses_previous"]:
                async for ev in self._run(tool, args, gathered):
                    yield ev
            else:
                batch.append((tool, args))
        async for ev in self._flush(batch, gathered):
            yield ev

        # one combined answer: exact lines for actions and exact tools, the model only for plain data
        parts, info = [], []
        for tool, out in gathered:
            exactish = (self.belt.is_action(tool) or tool in self.belt.exact or out.get("exact")
                        or '"error"' in out.get("result", ""))
            if exactish and out.get("say"):
                parts.append(out["say"])
            elif not exactish:
                info.append((tool, out))
        if info:
            ask = " and ".join(s["command"] for s in steps if s["command"] not in skipped)
            text = await self._collect(client, ctx["facts"], ctx["skills"], info, ask)
            if claims(text):                  # data answers never claim actions
                logger.warning(f"claim guard (multi): {text!r}")
                text = ""
            if text:
                parts.append(text)
        parts.extend(planned)
        for cmd in skipped:
            parts.append(f"I didn't do \"{cmd}\", sir; I wasn't sure how.")
        if skipped_q:
            parts.append(skipped_q)
            confirms = []                                 # one open question at a time
        answer = " ".join(dict.fromkeys(p.strip() for p in parts if p.strip()))
        if confirms:
            self.pending_queue = confirms
            answer, widget = self._next_confirmation(answer, first=True)
            yield widget
        yield {"type": "final", "text": answer or "Done, sir."}

    async def _collect(self, client, facts, skills, gathered, user_input) -> str:
        text = ""
        async for ev in self._stream_answer(client, facts, skills, gathered, user_input):
            if ev["type"] == "final":
                text = ev["text"]
        return text

    async def _grounded_web_answer(self, client, ctx, gathered, user_input) -> str:
        data = " ".join(out.get("result", "") for _, out in gathered)
        text = await self._collect(client, ctx["facts"], ctx["skills"], gathered, user_input)
        bad = ungrounded_numbers(text, data)
        if not bad:
            return text
        logger.warning(f"grounding: {bad} not in the search results; retrying")
        note = [{"result": json.dumps({"correction": f"The numbers {', '.join(bad)} do not appear in the search "
                                                     "results. Use only numbers that appear in them, or say the "
                                                     "results don't give that number."})}]
        retry = await self._collect(client, ctx["facts"], ctx["skills"], gathered + [("note", note[0])], user_input)
        if not ungrounded_numbers(retry, data):
            return retry
        logger.warning("grounding: retry still ungrounded; quoting the source instead")
        try:
            top = next(r for _, out in gathered for r in json.loads(out.get("result", "{}")).get("results", []))
            return f"I found this, sir, from {top['title'][:60]}: {top['snippet'][:200]}"
        except (StopIteration, Exception):
            return "The search results didn't give a clear number, sir."

    def _finish(self, user_input: str, answer: str, used: set[str] | None = None) -> None:
        from core import activity
        activity.mark()
        conv, self._conv_turn = self._conv_turn, False
        self.last_lane = "conversation" if conv else "task"
        if used:
            self._conv_left = 0                        # a task ends the conversation lane's stickiness
        self.last_tools = set(used or ())
        self.recent_tools.append(set(used or ()))
        self.history.append({"role": "assistant", "content": answer})
        self.cortex.log_turn(self.session_id, "user", user_input)
        self.cortex.log_turn(self.session_id, "assistant", answer)
        self.bus.publish("turn.completed", {"session": self.session_id})
        memory_ops = {"remember_fact", "forget_memory", "forget_recent_facts", "add_instruction", "recall_memory"}
        if self.mem_cfg.get("extract_facts", True) and not (used and used & memory_ops) and not conv \
                and not self.pending:                    # Nov 23: "Sent a message to Dan" was learned from a question
            task = asyncio.create_task(self._extract(user_input))
            self._bg.add(task)
            task.add_done_callback(self._bg.discard)

    async def _extract(self, user_input: str) -> None:
        stored = await extract_and_store(self.cortex, user_input, model=self.cfg["decision_model"],
                                         base_url=self.cfg["base_url"], keep_alive=self.cfg["keep_alive"])
        for f in stored:
            self.bus.publish("memory.fact_stored", f)

    # ------------------------------------------------------------ main loop
    async def process_stream(self, user_input: str) -> AsyncIterator[dict]:
        """One turn, and what it cost in the log (Nov 23: 'COST turn: ...', free turns too in test mode)."""
        from core.budget import get_budget
        budget = get_budget()
        snap = cost_snapshot(budget)
        try:
            async for ev in self._process_language(user_input):
                yield ev
        finally:
            line = turn_cost(budget, snap)
            if line:
                logger.info(line)

    async def _process_language(self, user_input: str) -> AsyncIterator[dict]:
        """Spoken to in Romanian (v0.3.2, 9d): she understands it as usual and answers in Romanian, read
        by the Romanian voice. Her streamed English words are held back and the finished answer is translated."""
        from core import romanian
        hint, self.reply_language = getattr(self, "reply_language", None), None
        if hint != "ro" and not romanian.is_romanian(user_input):
            async for ev in self._process_stream(user_input):
                yield ev
            return
        logger.info("ROMANIAN: answering in Romanian")
        async for ev in self._process_stream(user_input):
            kind = ev.get("type")
            if kind in ("token", "ack"):
                continue                                  # English on the way; the Romanian answer comes whole
            if kind == "final" and ev.get("text"):
                ro = await asyncio.to_thread(romanian.translate, ev["text"], "ro", "assistant")
                if ro and ro != ev["text"]:
                    ev = {**ev, "text": ro, "replaced": True, "language": "ro"}
                    for m in reversed(self.history):
                        if m.get("role") == "assistant":
                            m["content"] = ro              # so "say that again" repeats the Romanian
                            break
            yield ev

    # ------------------------------------------------------------ v1.0: one brain
    def brain(self):
        """The one brain (core/one): created on first use, kept for the session."""
        if getattr(self, "_one", None) is None:
            from core.one.loop import OneBrain
            self._one = OneBrain(self)
        return self._one

    async def _one_stream(self, user_input: str) -> AsyncIterator[dict]:
        """Reflexes stay instant; an open question of hers becomes context; everything else is the brain's."""
        from core import one
        from core.one import journal, risk
        brain = self.brain()
        used: set[str] = set()
        if _REPEAT.match(user_input):
            last = next((m["content"] for m in reversed(self.history[:-1]) if m.get("role") == "assistant"), "")
            if last:
                self._finish(user_input, last, used)
                yield {"type": "final", "text": last}
                return
        note, ran = "", []
        if self.pending:
            p, self.pending = self.pending, None
            fresh = time.time() - p["ts"] < CONFIRM_TTL_S
            desc = p.get("desc") or p["tool"].replace("_", " ")
            short = len(user_input.split()) <= 6
            if fresh and _PLAIN_YES.match(user_input) and self.belt.has(p["tool"]):
                gathered: list = []
                async for ev in self._run(p["tool"], dict(p["args"]), gathered, ack=False):
                    yield ev
                out = gathered[-1][1] if gathered else {}
                audit.log("confirm_granted", "orchestrator", {"tool": p["tool"]})
                ran.append((p["tool"], dict(p["args"]), out, p.get("why") or "he said yes"))
                note = (f"He said yes, so you just did this: {desc}. Result: {(out.get('say') or out.get('result') or '')[:600]} "
                        f"Tell him how it went, in a sentence of your own.")
            elif fresh and _NO.match(user_input) and short:
                audit.log("confirm_denied", "orchestrator", {"tool": p["tool"]})
                note = f"You had asked whether to {desc}; he said no, so it's not done. Acknowledge it briefly."
            elif fresh:
                if re.search(r"\b(yes|yeah|yep|sure|go ahead|do it|please do|okay|ok)\b", user_input, re.I) and \
                        not _NO.search(user_input):
                    brain.approved = {p["tool"]}
                note = (f"You had asked whether to {desc} ({p['tool']} {json.dumps(p['args'], ensure_ascii=False)[:300]}). "
                        f"This is his answer: if it's a yes with a change, do it with the change; a no, leave it; "
                        f"something new, handle that.")
        if self.awaiting:
            a, self.awaiting = self.awaiting, None
            if time.time() - a["ts"] < CONFIRM_TTL_S:
                note += f" (You had asked him for the {a['field']} for {a['tool']}; this may be his answer.)"
        if not note and not ran:
            fp = fast_path(user_input, self.belt)
            if fp and fp[0] in set(one.cfg().get("reflexes") or []) and self.belt.has(fp[0]):
                tool, args = fp[0], await self._fill(fp[0], fp[1], user_input)
                level, why = risk.judge(tool, args, self.belt)
                if level != "ask":
                    rec = journal.Turn(user_input, chat=getattr(self, "chat_id", "") or "")
                    rec.data["reflex"] = True
                    gathered = []
                    async for ev in self._run(tool, args, gathered, ack=False):
                        yield ev
                    out = gathered[-1][1] if gathered else {}
                    s = rec.step(tool, args, level, why, "ran", out)
                    used.add(tool)
                    text = out.get("say") or "Done, sir."
                    self._finish(user_input, text, used)
                    rec.finish(text)
                    logger.info(f"REFLEX: {tool}")
                    yield {"type": "step", "data": {k: s.get(k) for k in ("id", "tool", "risk", "decision", "ok",
                                                                           "result", "changes")} | {"undo": bool(s.get("undo"))}}
                    yield {"type": "final", "text": text}
                    return
        async for ev in brain.turn(user_input, note=note.strip(), ran=ran):
            yield ev

    async def _process_stream(self, user_input: str) -> AsyncIterator[dict]:
        from core import activity
        from core import one as _one
        if _one.enabled():
            activity.mark()
            if self._stale:
                self.refresh_tools()
            self.history.append({"role": "user", "content": user_input})
            self._turn_input = user_input
            self.bus.publish("turn.user", {"session": self.session_id, "text": user_input})
            async for ev in self._one_stream(user_input):
                yield ev
            return
        if _DANGLING.fullmatch((user_input or "").strip()):
            logger.info(f"half a sentence ({user_input!r}): listening for the rest")
            yield {"type": "final", "text": "Yes, sir?"}
            return
        activity.mark()                                  # background GPU work steps aside
        if self._stale:
            self.refresh_tools()
        self.history.append({"role": "user", "content": user_input})
        self._turn_input = user_input
        self.bus.publish("turn.user", {"session": self.session_id, "text": user_input})
        gathered: list = []
        used: set[str] = set()
        ctx: dict = {}
        handed = False                                   # v0.5: understanding said "work it out"
        if self.last_missing and self.belt.has("forge_build") and re.match(
                r"^\s*(?:yes,? )?(?:please )?(?:build it|build that|make it|go build it|draft it|create it)\b", user_input, re.I):
            user_input_for_forge = self.last_missing
            self.last_missing = None
            fp_override = ("forge_build", {"request": user_input_for_forge})
        elif (self.pending_offer and time.time() - self.pending_offer["ts"] < 120 and _YES.match(user_input)
              and self.belt.has(self.pending_offer["tool"])):
            fp_override = (self.pending_offer["tool"], dict(self.pending_offer["args"]))   # "Should I?" "Yes."
        else:
            fp_override = None
        self.pending_offer = None

        async with httpx.AsyncClient(timeout=120) as client:
            # -2. "say that again": her last answer, word for word (Sep 28: it took a screenshot instead)
            if _REPEAT.match(user_input):
                last = next((m["content"] for m in reversed(self.history[:-1]) if m.get("role") == "assistant"), "")
                text = last or "I haven't said anything yet, sir."
                self._finish(user_input, text, used)
                yield {"type": "final", "text": text}
                return

            # -1. "never mind" / "don't" / "it's fine": drop whatever was open, do nothing
            if _CANCEL.match(user_input):                  # never a request of its own: always a quiet no-op
                self.pending, self.awaiting, self.pending_queue = None, None, []
                text = "Okay, sir."
                self._finish(user_input, text, used)
                yield {"type": "final", "text": text}
                return

            if self.awaiting and self.awaiting["tool"] == "task_answer" and not_a_task_answer(user_input):
                logger.info(f"ORCH: '{user_input}' isn't an answer to the waiting task; handling it on its own")
                self.awaiting = None                       # the task keeps waiting; this sentence is its own request
            if self.pending and self.pending["tool"] == "task_answer" and not_a_task_answer(user_input):
                self.pending = None
            # 0. an answer to "shall I go ahead?"
            if self.pending:
                p, self.pending = self.pending, None
                fresh = time.time() - p["ts"] < CONFIRM_TTL_S
                # Nov 27 09:51: "stop the music" while she asked about the calendar was taken as "no" and the music
                # played on. A new command cancels the question and runs.
                if fresh and _NEW_STOP.match(user_input):
                    logger.info(f"ORCH: '{user_input}' is a new command; dropping the question about {p['tool']}")
                    self.pending_queue, fresh = [], False
                loose_yes = (re.search(r"\b(yes|yeah|yep|sure|go ahead|do it|please do|correct|right)\b", user_input, re.I)
                             and len(user_input.split()) <= 10 and not _NO.search(user_input))
                if fresh and p["tool"] == "forge_build" and local_answer(user_input):
                    p["args"] = {**p["args"], "where": "local"}                         # build it, but locally
                    loose_yes = True
                if fresh and p["tool"] == "task_answer" and (_YES.search(user_input) or loose_yes or
                                                             re.search(r"\balways\b", user_input, re.I)):
                    p["args"] = {**p["args"], "answer": user_input}    # v0.4: "yes, always" keeps its words
                    loose_yes = True
                if fresh and (_YES.search(user_input) or loose_yes):
                    extra = await self._fill(p["tool"], dict(p["args"]), user_input)      # "At 3 o'clock, yes"
                    p["args"] = {**p["args"], **{k: extra[k] for k in ("day", "time", "when", "hour", "level")
                                                 if extra.get(k) and extra.get(k) != p["args"].get(k)}}
                    audit.log("confirm_granted", "orchestrator", {"tool": p["tool"]})
                    used.add(p["tool"])
                    async for ev in self._run(p["tool"], p["args"], gathered):
                        yield ev
                    answer = ""
                    async for ev in self._answer(client, gathered, user_input, ctx):
                        if ev["type"] == "final":
                            answer = ev["text"]
                            if self.pending_queue:          # multi-step: the next action that needs a yes
                                answer, widget = self._next_confirmation(answer)
                                yield widget
                                ev = {"type": "final", "text": answer}
                        yield ev
                    self._finish(user_input, answer, used)
                    return
                spec = self.belt.asks.get(p["tool"])
                if fresh and not callable(spec) and spec and _CHANGE.search(user_input):
                    # Nov 23: "no, change the message" read the message out again instead of asking for a new one
                    field, question = spec
                    args = {k: v for k, v in p["args"].items() if k not in (field, "final", "language", "untranslated")}
                    self.awaiting = {"tool": p["tool"], "args": args, "field": field, "ts": time.time()}
                    text = question.format_map(_Blank({k: v for k, v in args.items() if v}))
                    self._finish(user_input, text, used)
                    yield {"type": "final", "text": text}
                    return
                if fresh and _NO.search(user_input) and p.get("on_no"):          # v0.4: a task hears your no
                    no = p["on_no"]
                    out = await self.belt.aexecute(no["tool"], {**no["args"], "answer": user_input})
                    text = out.get("say") or "Alright, sir."
                    self._finish(user_input, text, used)
                    yield {"type": "final", "text": text}
                    return
                if fresh and _NO.search(user_input):
                    audit.log("confirm_denied", "orchestrator", {"tool": p["tool"]})
                    rest = _NO.sub("", user_input, count=1).lstrip(" ,.!")
                    if len(rest.split()) < 3:                      # a plain "no"
                        text = "Cancelled, sir."
                        if self.pending_queue:
                            text, widget = self._next_confirmation(text)
                            yield widget
                        self._finish(user_input, text, used)
                        yield {"type": "final", "text": text}
                        return
                    self.pending_queue = []
                    user_input = rest                              # "no, make a skill that ...": handle the rest

            # 0a. the answer to an open question ("What should the email to Muaad say, sir?")
            if self.awaiting:
                a, self.awaiting = self.awaiting, None
                ttl = 1800 if a["tool"] == "task_answer" else CONFIRM_TTL_S     # v0.4: a task can wait for you
                if time.time() - a["ts"] < ttl:
                    if a["tool"] != "task_answer" and _NO.search(user_input) and len(user_input.split()) <= 4:
                        text = "Alright, sir, I won't write it."
                        self._finish(user_input, text, used)
                        yield {"type": "final", "text": text}
                        return
                    args = {**a["args"], a["field"]: user_input.strip()}
                    if a["tool"] in getattr(self.belt, "slow_fill", ()):     # the message, now in Romanian for Mom
                        args = await self._fill(a["tool"], {**args, "final": False}, user_input)
                    if self.belt.needs_confirm(a["tool"]):
                        for ev in self._ask_confirmation(a["tool"], args, user_input, used):
                            yield ev
                        return
                    used.add(a["tool"])
                    async for ev in self._run(a["tool"], args, gathered):
                        yield ev
                    answer = ""
                    async for ev in self._answer(client, gathered, user_input, ctx):
                        answer = ev["text"] if ev["type"] == "final" else answer
                        yield ev
                    self._finish(user_input, answer, used)
                    return

            # v0.8: her question, his answer: "Shall I open it in Notepad?" "Yes." goes back to the mind, which knows
            # what it offered (Nov 27: understanding made open_file('""') of it, and "yes, but rename the courses"
            # was cut into two commands)
            answering_mind = not fp_override and self._answers_mind(user_input)
            if answering_mind:
                logger.info("MIND: his answer to her question; back to the mind")

            # 0a. "when I get home, turn on the lights" / "remind me to buy milk when I leave work" (Nov 23)
            if not fp_override and not answering_mind and self.belt.has("place_reminder"):
                from core import places as places_mod
                t_clean, t_place, t_event, once = places_mod.split_trigger(user_input)
                if t_place and t_clean:
                    text = await self._place_rule(client, t_clean, t_place, t_event, once, user_input, ctx)
                    self._finish(user_input, text, used)
                    yield {"type": "final", "text": text}
                    return

            # 0b. several commands in one sentence (v0.2.5): plan, run, one combined answer
            if not fp_override and not answering_mind and multistep.looks_multi(user_input):
                steps = await multistep.plan(client, self.cfg, user_input)
                if steps:
                    answer = ""
                    async for ev in self._multi(client, steps, user_input, gathered, used, ctx):
                        if ev["type"] == "final":
                            answer = ev["text"]
                        yield ev
                    self._finish(user_input, answer, used)
                    return

            # 0c. "... in 5 minutes": understood now, done then (v0.3 10b)
            if not fp_override and not answering_mind:
                clean, delay = self._when_later(user_input)
                if delay and later_mod.is_correction(clean):
                    text = await self._retime(delay)
                    if text is None:
                        text = f"What should I do in {later_mod.human(delay)}, sir?"
                    self._finish(user_input, text, used)
                    yield {"type": "final", "text": text}
                    return
                if delay:
                    self._context(clean, ctx)
                    picked = await self._pick(client, clean, user_input, ctx, [], False)
                    if picked and self.belt.is_action(picked[0]):
                        text = self._schedule(picked, clean, delay)
                        self._finish(user_input, text, used)
                        yield {"type": "final", "text": text}
                        return

            # 1. fast path: no LLM, no embeddings
            fp = None if answering_mind else (fp_override or fast_path(user_input, self.belt))
            if answering_mind:
                handed = True
            if fp and fp[0] == "work_it_out":            # v0.8: the fast path knows it needs thinking
                fp = None
                if self._mind_ready(user_input):
                    handed = True
            elif fp and fp[0] in _MIND_ONLY and not fp_override and self._mind_ready(user_input):
                self._mind_hint, fp, handed = (fp[0], dict(fp[1])), None, True      # v0.8: e.g. see_screen
            if fp and fp[0] == "forge_build" and not fp_override:
                fp = (fp[0], {**fp[1], "request": self._resolve_vague(fp[1].get("request", ""))})
            if fp:
                tool, args = fp[0], await self._fill(fp[0], fp[1], user_input)
                if self.belt.needs_confirm(tool):
                    for ev in self._ask_confirmation(tool, args, user_input, used):
                        yield ev
                    return
                used.add(tool)
                async for ev in self._run(tool, args, gathered):
                    yield ev

            # 2-4. the model decides, behind guards
            elif not handed and not (_CAPABILITY_Q.search(user_input) or is_small_talk(user_input)):
                self._context(user_input, ctx)
                called, succeeded = set(), set()
                for _ in range(MAX_TOOL_ITERS):
                    decision = await self._decide(client, ctx["skills"], gathered)
                    tool = decision.get("tool", "none")
                    if (tool == "work_it_out" or tool in _MIND_ONLY or _needs_mind(tool, decision)) and not gathered \
                            and self._mind_ready(user_input):
                        handed = True                      # v0.5: it needs thinking: the mind answers below
                        flat = {k: v for k, v in decision.items() if k not in ("tool", "args") and not k.startswith("_")}
                        self._mind_hint = (tool, {**flat, **dict(decision.get("args") or {})}) \
                            if tool != "work_it_out" else None
                        break
                    if tool == "work_it_out":
                        break                              # no mind (local only, no key): the old answer
                    if tool == "unavailable" and not gathered and self._mind_ready(user_input):
                        break                              # v0.5: the mind looks for a way (or offers to build it)
                    if tool == "unavailable" and (gathered or not (decision.get("_trusted")
                                                                   or _ABILITY_HINT.search(user_input))):
                        tool = "none"                      # conversation, or we already have data: just answer
                    if tool == "unavailable":
                        logger.info(f"no tool covers: {user_input!r}")
                        self.bus.publish("capability.missing", {"request": user_input})
                        text = self._missing_answer(user_input)
                        self._finish(user_input, text, used)
                        yield {"type": "final", "text": text}
                        return
                    if tool == "none" or not self.belt.has(tool) or tool in succeeded:
                        break                      # a tool that already answered is not asked again
                    if tool in ("web_search", "think_deeply") and not gathered and conversation.enabled() and \
                            conversation.wants(user_input) and not WEATHER_RX.search(user_input):
                        logger.info(f"conversation: talking, not a {tool}")
                        break                                  # the conversation lane answers below
                    if tool == "web_search" and self.belt.has("get_weather") and \
                            WEATHER_RX.search(f"{user_input} {decision.get('query', '')}"):
                        place = place_from(f"{user_input} {decision.get('query', '')}")
                        logger.info("weather: web search rerouted to the weather tool")
                        tool, decision = "get_weather", {"tool": "get_weather", **({"city": place} if place else {})}
                        rerouted = True
                    else:
                        rerouted = False
                    if not (rerouted or decision.get("_trusted") or self.belt.guard_ok(tool, user_input)
                            or tool in self._recent()):
                        logger.info(f"guard: {tool} blocked, the request doesn't ask for it")
                        if self.belt.is_action(tool) and not gathered and not self.belt.needs_confirm(tool):
                            # never let the model claim or deny it: ask about the exact action instead
                            schema = self.belt.by_name[tool]
                            args = await self._fill(tool, args_for(schema, decision, self.belt.names()), user_input)
                            if not missing_required(schema, args):
                                for ev in self._ask_confirmation(tool, args, user_input, used):
                                    yield ev
                                return
                        break
                    schema = self.belt.by_name[tool]
                    args = await self._fill(tool, args_for(schema, decision, self.belt.names()), user_input)
                    if missing_required(schema, args):
                        args = await self._fill(tool, {**args, **args_for(schema, await self._repair_args(client, tool),
                                                                        self.belt.names())}, user_input)
                        if missing_required(schema, args):
                            logger.warning(f"{tool}: still missing {missing_required(schema, args)}; skipping")
                            break
                    sig = (tool, json.dumps(args, sort_keys=True).lower())
                    if sig in called:
                        break
                    called.add(sig)
                    if self.belt.needs_confirm(tool):
                        for ev in self._ask_confirmation(tool, args, user_input, used):
                            yield ev
                        return
                    used.add(tool)
                    async for ev in self._run(tool, args, gathered):
                        yield ev
                    out = gathered[-1][1]
                    if out.get("missing"):
                        text = self._missing_answer(user_input)
                        self._finish(user_input, text, used)
                        yield {"type": "final", "text": text}
                        return
                    ok = '"error"' not in (out.get("result") or "")
                    if ok:
                        succeeded.add(tool)
                    if self.belt.is_action(tool) and ok:
                        break                      # an action is the whole job; don't chain more
                    if ok and out.get("say") and (tool in self.belt.exact or out.get("exact")):
                        break                      # Nov 23: "any messages?" then 27 s deciding what else to do
                    if self.belt.is_action(tool) and not ok and out.get("say"):
                        break                      # it failed and said why honestly: don't retry blindly

            # v0.5: nothing ran, and it isn't personal chat: the mind thinks it through with every tool
            from core import mind as _mind_mod
            chat = conversation.enabled() and _mind_mod.is_chat(user_input) and not handed
            if not gathered and not chat and self._mind_ready(user_input):
                self._mind_handed = handed
                async for ev in self._mind(client, user_input, ctx, gathered, used):
                    yield ev
                if self._mind_done:
                    return

            # 5-6. answer (v0.2.5: no tool and you're talking, not commanding -> the conversation lane)
            answer = ""
            talk = not gathered and conversation.enabled() and (conversation.wants(user_input) or self._conv_left > 0)
            if talk:
                self._conv_left = conversation.sticky_turns() if conversation.wants(user_input) else self._conv_left - 1
                self._conv_turn = True
            # nothing looked up: each sentence is checked before it's spoken; a made-up fact stops the stream
            tools_now, buf, tripped = [t for t, _ in gathered], "", False
            tool_says = {out.get("say") for _, out in gathered if out.get("say")}   # a tool's own line was checked
            async for ev in (self._converse(client, user_input, ctx) if talk
                             else self._answer(client, gathered, user_input, ctx)):
                if ev["type"] == "token":
                    if tripped:
                        continue
                    buf += ev["text"]
                    m = None
                    for m in re.finditer(r"[.!?](?:\s|$)", buf):
                        pass
                    if not m:
                        continue
                    done, buf = buf[:m.end()], buf[m.end():]
                    if unchecked_fact(done, tools_now):
                        tripped = True
                        continue
                    ev = {**ev, "text": done}
                if ev["type"] == "final":
                    kind = None if ev["text"].strip() in tool_says else unchecked_fact(ev["text"], tools_now)
                    if kind:
                        text, check = offer_for(kind, user_input, ev["text"])
                        logger.warning(f"fact guard ({kind}): {ev['text']!r} without checking")
                        if check:
                            self.pending_offer = {"tool": check[0], "args": check[1], "ts": time.time()}
                        ev = {**ev, "text": text, "replaced": True}
                    elif buf.strip() and not tripped:
                        yield {"type": "token", "text": buf}   # the last sentence had no full stop yet
                    buf = ""
                    answer = ev["text"]
                yield ev
        self._finish(user_input, answer, used)


# quick manual test:  python -m core.orchestrator_hybrid
if __name__ == "__main__":
    async def main():
        eva = HybridOrchestrator(mcp=MCPManager())
        for q in ["what time is it", "remember that my favourite music for work is jazz",
                  "what do you know about me", "what's the weather in Breda right now"]:
            print(f"\n>>> {q}")
            async for ev in eva.process_stream(q):
                if ev["type"] == "ack":
                    print(f"[ack] {ev['text']}")
                elif ev["type"] == "token":
                    print(ev["text"], end="", flush=True)
                elif ev["type"] == "widget":
                    print(f"\n[widget:{ev['data'].get('kind')}]")
                elif ev["type"] == "final":
                    print(f"\n[final] {ev['text']}")
        await asyncio.sleep(2)

    asyncio.run(main())
