"""Memory skill tools. Thin wrappers over CORTEX and EVA.md."""

import json

from core.memory.cortex import get_cortex
from core.prompt_builder import add_standing_instruction

TOOLS = [
    {"type": "function", "function": {
        "name": "remember_fact",
        "description": "Store a durable fact about the user when they ask you to remember it.",
        "parameters": {"type": "object", "properties": {
            "text": {"type": "string", "description": "The fact, as a short statement."},
            "category": {"type": "string", "description": "identity, preference, person, project, plan, place or general"},
        }, "required": ["text"]}}},
    {"type": "function", "function": {
        "name": "recall_memory",
        "description": "Look up what you remember about the user. Empty query lists the main facts.",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string"}}}}},
    {"type": "function", "function": {
        "name": "forget_memory",
        "description": "Delete a remembered fact when the user asks you to forget it.",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string"}}, "required": ["query"]}}},
    {"type": "function", "function": {
        "name": "add_instruction",
        "description": "Save a standing instruction for how E.V.A. should behave from now on.",
        "parameters": {"type": "object", "properties": {
            "text": {"type": "string"}}, "required": ["text"]}}},
]


def remember_fact(text: str = "", category: str = "general", **_):
    return {"result": json.dumps(get_cortex().remember(text, category=category, source="user"))}


def recall_memory(query: str = "", **_):
    facts = get_cortex().recall(query, k=8)
    return {"result": json.dumps({"facts": [f["text"] for f in facts]} if facts
                                 else {"facts": [], "note": "nothing remembered on this yet"})}


def forget_memory(query: str = "", **_):
    return {"result": json.dumps(get_cortex().forget(query))}


def add_instruction(text: str = "", **_):
    return {"result": json.dumps(add_standing_instruction(text))}


FUNCTIONS = {"remember_fact": remember_fact, "recall_memory": recall_memory,
             "forget_memory": forget_memory, "add_instruction": add_instruction}
ACKS = {}   # memory operations are instant; no spoken ack needed
