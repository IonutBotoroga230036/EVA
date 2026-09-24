"""Memory skill tools. Thin wrappers over CORTEX and EVA.md."""

import json
import re

from core.memory.cortex import get_cortex
from core.prompt_builder import add_standing_instruction

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
        "description": "Look up what you remember about the user. Empty query lists the main facts.",
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
    return {"result": json.dumps(get_cortex().remember(to_third_person(text), category=category, source="user"))}


def recall_memory(query: str = "", **_):
    facts = get_cortex().recall(query, k=8)
    return {"result": json.dumps({"facts": [f["text"] for f in facts]} if facts
                                 else {"facts": [], "note": "nothing remembered on this yet"})}


def forget_memory(query: str = "", **_):
    return {"result": json.dumps(get_cortex().forget(query))}


def forget_recent_facts(minutes: int = 15, **_):
    return {"result": json.dumps(get_cortex().forget_recent(minutes or 15))}


def add_instruction(text: str = "", **_):
    return {"result": json.dumps(add_standing_instruction(text))}


FUNCTIONS = {"remember_fact": remember_fact, "recall_memory": recall_memory,
             "forget_memory": forget_memory, "forget_recent_facts": forget_recent_facts,
             "add_instruction": add_instruction}
ACKS = {}          # memory operations are instant; no spoken ack
ACTIONS = ["remember_fact", "forget_memory", "forget_recent_facts", "add_instruction"]
