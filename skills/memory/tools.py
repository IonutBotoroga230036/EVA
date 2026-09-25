"""Memory skill tools. Thin wrappers over CORTEX, EVA.md, and (for recall) the notes vault."""

import importlib.util
import json
import re
from datetime import datetime
from pathlib import Path

from core.memory.cortex import get_cortex
from core.prompt_builder import about_lines, add_standing_instruction

TOOLS = [
    {"type": "function", "function": {
        "name": "remember_fact",
        "description": "Store a durable fact about the user when they ask you to remember it.",
        "parameters": {"type": "object", "properties": {
            "text": {"type": "string", "description": "The fact to remember, in the user's words."},
            "category": {"type": "string", "description": "identity, preference, person, project, plan, place or general"},
        }, "required": ["text"]}}},
    {"type": "function", "function": {
        "name": "recall_memory",
        "description": ("Look up what the user told you before: remembered facts, past conversations, and "
                        "notes. Use for 'what did I tell you about X', 'do you remember X'. Empty query lists main facts."),
        "parameters": {"type": "object", "properties": {"query": {"type": "string"}}}}},
    {"type": "function", "function": {
        "name": "forget_memory",
        "description": "Delete ONE remembered fact when the user asks you to forget or delete it.",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string", "description": "Words from the fact to delete."}}, "required": ["query"]}}},
    {"type": "function", "function": {
        "name": "forget_recent_facts",
        "description": "Delete everything learned in the last few minutes, when the user says to forget what you just remembered.",
        "parameters": {"type": "object", "properties": {
            "minutes": {"type": "integer", "description": "How far back, default 15."}}}}},
    {"type": "function", "function": {
        "name": "add_instruction",
        "description": "Save a standing instruction for how E.V.A. should behave from now on.",
        "parameters": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}}},
]

# First person -> third person, so stored facts read clearly in the prompt
# ("What you remember about the user: - Goes to the gym on Tuesdays").
_VERBS = {"am": "is", "have": "has", "go": "goes", "do": "does", "like": "likes", "love": "loves",
          "hate": "hates", "prefer": "prefers", "live": "lives", "work": "works", "study": "studies",
          "train": "trains", "want": "wants", "need": "needs", "play": "plays", "speak": "speaks",
          "drive": "drives", "own": "owns", "use": "uses", "eat": "eats", "drink": "drinks",
          "don't": "doesn't", "dont": "doesn't", "can": "can", "can't": "can't", "was": "was",
          "usually": "usually", "always": "always", "never": "never"}


def to_third_person(text: str) -> str:
    t = " ".join((text or "").split()).strip().rstrip(".")
    t = re.sub(r"^i'?m\b", "is", t, flags=re.I)
    m0 = re.match(r"^i\s+(also|really|just|still)\s+(\S+)\s*(.*)$", t, flags=re.I)
    if m0 and m0.group(2).lower() in _VERBS:
        t = f"{m0.group(1)} {_VERBS[m0.group(2).lower()]} {m0.group(3)}".strip()
    m = re.match(r"^i\s+(\S+)\s*(.*)$", t, flags=re.I)
    if m and m.group(1).lower() in _VERBS:
        t = f"{_VERBS[m.group(1).lower()]} {m.group(2)}".strip()
        # "usually go" -> "usually goes"
        m2 = re.match(r"^(usually|always|never)\s+(\S+)(.*)$", t, flags=re.I)
        if m2 and m2.group(2).lower() in _VERBS:
            t = f"{m2.group(1)} {_VERBS[m2.group(2).lower()]}{m2.group(3)}"
    t = re.sub(r"\bmy\b", "their", t, flags=re.I)
    t = re.sub(r"\bmyself\b", "themselves", t, flags=re.I)
    return t[:1].upper() + t[1:] if t else t


def remember_fact(text: str = "", category: str = "general", **_):
    res = get_cortex().remember(to_third_person(text), category=category, source="user")
    says = {"added": "Noted, sir.", "duplicate": "I already knew that, sir.",
            "refused": "I don't store passwords, keys, or card numbers, sir.",
            "ignored": "I didn't catch what to remember, sir."}
    say = f"Updated, sir. I had it as: {res.get('replaced')}." if res.get("status") == "updated" \
        else says.get(res.get("status"), "Noted, sir.")
    return {"result": json.dumps(res), "say": say}


_META_Q = re.compile(r"\b(what did i (tell|say)|do you remember|what do you (know|remember))\b", re.I)
_notes_mod = None


def _notes_search(query: str) -> list[dict]:
    """Borrow the obsidian skill's search if it is installed; memory must work without it."""
    global _notes_mod
    try:
        if _notes_mod is None:
            path = Path(__file__).resolve().parents[1] / "obsidian" / "tools.py"
            spec = importlib.util.spec_from_file_location("eva_notes_for_recall", path)
            _notes_mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(_notes_mod)
        return json.loads(_notes_mod.obsidian_search(query)["result"]).get("results", [])[:3]
    except Exception:
        return []


def recall_memory(query: str = "", **_):
    cortex = get_cortex()
    facts = [f["text"] for f in cortex.recall(query, k=8)]
    said, seen = [], set()
    if query.strip():
        for ep in cortex.search_episodes(query, limit=12):
            text = ep["content"].strip()
            if ep["role"] != "user" or _META_Q.search(text) or text.lower() in seen:
                continue
            seen.add(text.lower())
            said.append({"when": datetime.fromtimestamp(ep["ts"]).strftime("%a %d %b %H:%M"), "you_said": text[:200]})
            if len(said) == 3:
                break
    notes = _notes_search(query) if query.strip() else []
    about = about_lines()
    if not query.strip() and about:                         # "what do you know about me": exact, no model
        def you(line):
            line = re.sub(r"^My name is ([^.]+)\.\s*(?:Address me as \S+\.)?", r"You're \1", line)
            line = re.sub(r"\bI speak\b", "You speak", line)
            line = re.sub(r"\bmy\b", "your", line, flags=re.I)
            return line.split(" (")[0].rstrip(". ")
        picked = [you(l) for l in about if not re.match(r"^(Home city|I speak)", l)][:4]
        extra = len([f for f in facts if f not in about])
        tail = f" I also remember {extra} other thing{'s' if extra != 1 else ''} you've told me." if extra else ""
        return {"result": json.dumps({"facts": about + facts}), "exact": True,
                "say": "Here's what I know, sir. " + ". ".join(picked) + "." + tail}
    if not query.strip() or re.search(r"\b(name|who am i|about me|live|home|language)\b", query, re.I):
        facts = about + [f for f in facts if f not in about]
    if not (facts or said or notes):
        return {"result": json.dumps({"facts": [], "note": "nothing remembered on this yet"}),
                "say": "I don't have anything stored about that yet, sir.", "exact": True}
    return {"result": json.dumps({"facts": facts, "from_past_conversations": said, "from_notes": notes,
                                  "rule": "answer only from these; never invent anything else about the user"})}


def forget_memory(query: str = "", **_):
    res = get_cortex().forget(query)
    st = res.get("status")
    if st == "forgotten":
        say = f"Forgotten, sir: {res['text']}."
    elif st == "ambiguous":
        say = "Which one, sir? I have: " + "; ".join(res["candidates"]) + "."
    elif st == "need_query":
        say = "Which fact should I forget, sir?"
    else:
        say = "I don't have anything like that stored, sir."
    return {"result": json.dumps(res), "say": say}


def forget_recent_facts(minutes: int = 15, **_):
    res = get_cortex().forget_recent(minutes or 15)
    n = len(res.get("forgotten", []))
    say = f"Done, sir. I've forgotten the last {n} thing{'s' if n != 1 else ''} I learned." if n \
        else "There was nothing new to forget, sir."
    return {"result": json.dumps(res), "say": say}


def add_instruction(text: str = "", **_):
    res = add_standing_instruction(text)
    say = {"added": "Understood, sir. That's now a standing instruction.",
           "duplicate": "That's already one of my standing instructions, sir."}.get(res.get("status"),
                                                                                    "I didn't catch the instruction, sir.")
    return {"result": json.dumps(res), "say": say}


FUNCTIONS = {"remember_fact": remember_fact, "recall_memory": recall_memory,
             "forget_memory": forget_memory, "forget_recent_facts": forget_recent_facts,
             "add_instruction": add_instruction}
ACKS = {}          # memory operations are instant; no spoken ack
ACTIONS = ["remember_fact", "forget_memory", "forget_recent_facts", "add_instruction"]

# An action only runs if the user's words ask for it (stops "how do you like it" -> add_instruction).
GUARDS = {
    "remember_fact": r"\b(remember|keep in mind|don'?t forget|store|save|note that i|memori[sz]e)\b",
    "forget_memory": r"\b(forget|delete|remove|erase|wipe)\b",
    "forget_recent_facts": r"\b(forget|delete|remove|erase|wipe|undo)\b",
    "add_instruction": r"\b(from now on|going forward|always|never|don'?t ever|stop (doing|saying|using)|in the future)\b",
}
# Asks "shall I go ahead?" first; {minutes} is filled from the arguments.
CONFIRM = {"forget_recent_facts": "forget everything I learned in the last {minutes} minutes"}
