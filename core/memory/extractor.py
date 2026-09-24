"""
CORTEX fact extractor. Runs in the BACKGROUND after E.V.A. has answered, so it
never adds latency to a reply. It asks the local model, constrained to a JSON
schema, for durable facts the user stated about themselves, then hands each
one to Cortex.remember(), which deduplicates.

A cheap gate skips turns that can't contain a personal fact (no first-person
words), which saves a model call on most commands like "pause" or "what time is it".
"""

from __future__ import annotations

import json
import re

import httpx
from loguru import logger

from core.memory.cortex import Cortex

CATEGORIES = ["identity", "preference", "person", "project", "plan", "place", "general"]

SCHEMA = {
    "type": "object",
    "properties": {
        "facts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "category": {"type": "string", "enum": CATEGORIES},
                },
                "required": ["text", "category"],
            },
        }
    },
    "required": ["facts"],
}

PROMPT = (
    "Extract durable facts about the USER from their message: identity, preferences, "
    "people in their life, projects, plans, places. Write each fact as a short "
    "third-person statement, e.g. 'Prefers jazz while working'. Only include what the "
    "user actually stated. Ignore questions, commands, small talk, and anything "
    "temporary like the current weather. If there is nothing durable, return an empty list."
)

_FIRST_PERSON = re.compile(r"\b(i|i'm|im|i've|i'd|my|me|mine|we|our)\b", re.I)


def worth_extracting(user_text: str) -> bool:
    return len(user_text) >= 12 and bool(_FIRST_PERSON.search(user_text))


async def extract_and_store(cortex: Cortex, user_text: str, *, model: str,
                            base_url: str = "http://localhost:11434",
                            keep_alive: str = "30m", max_facts: int = 3) -> list[dict]:
    if not worth_extracting(user_text):
        return []
    try:
        async with httpx.AsyncClient(timeout=60) as c:
            r = await c.post(f"{base_url}/api/chat", json={
                "model": model, "stream": False, "format": SCHEMA, "keep_alive": keep_alive,
                "options": {"temperature": 0},
                "messages": [{"role": "system", "content": PROMPT},
                             {"role": "user", "content": user_text}],
            })
        facts = json.loads(r.json()["message"]["content"]).get("facts", [])
    except Exception as e:
        logger.debug(f"extractor skipped: {e}")
        return []
    stored = []
    for f in facts[:max_facts]:
        res = cortex.remember(f.get("text", ""), category=f.get("category", "general"), source="auto")
        if res.get("status") in ("added", "updated"):
            stored.append(res)
    return stored
