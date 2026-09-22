"""
IRIS - Input Routing & Intent Sorting.
"""

import json
import httpx
from loguru import logger


class TaskCategory:
    TRIVIAL = "trivial"
    ROUTINE = "routine"
    COMPLEX = "complex"
    SKILL_MISSING = "skill_missing"
    SENSITIVE = "sensitive"


ROUTER_PROMPT = """You are IRIS, the routing module for E.V.A.
Classify the user input into exactly ONE category and identify which skill handles it.

Categories:
- TRIVIAL: greetings, simple facts, timers, quick math, casual chat
- ROUTINE: calendar ops, email drafting, file management, web search
- COMPLEX: multi-step reasoning, code generation, research, creative writing
- SKILL_MISSING: user wants something no available skill can do
- SENSITIVE: contains passwords, financial details, health info, private data

Available skills: {skills}

Respond with ONLY valid JSON:
{{"category": "trivial|routine|complex|skill_missing|sensitive", "skill": null, "reason": "one sentence"}}

User input: {input}"""


class Router:
    def __init__(self, ollama_url: str = "http://localhost:11434", model: str = "llama3.2:3b"):
        self.ollama_url = ollama_url
        self.model = model
        self.available_skills: list[str] = []

    def register_skills(self, skill_names: list[str]) -> None:
        self.available_skills = skill_names
        logger.info(f"IRIS: Registered skills: {skill_names}")

    async def classify(self, user_input: str) -> dict:
        lower = user_input.lower().strip()
        sensitive_keywords = ["password", "credit card", "ssn", "social security", "bank account", "pin code"]
        if any(kw in lower for kw in sensitive_keywords):
            return {"category": TaskCategory.SENSITIVE, "skill": None, "reason": "Contains sensitive information"}

        prompt = ROUTER_PROMPT.format(
            skills=", ".join(self.available_skills) or "none yet",
            input=user_input,
        )
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.post(
                    f"{self.ollama_url}/api/generate",
                    json={"model": self.model, "prompt": prompt, "stream": False, "format": "json"},
                )
                result = response.json()
                parsed = json.loads(result["response"])
                logger.info(f"IRIS: '{user_input[:50]}' -> {parsed['category']} (skill: {parsed.get('skill')})")
                return parsed
        except Exception as e:
            logger.error(f"IRIS: Classification failed: {e}")
            return {"category": TaskCategory.COMPLEX, "skill": None, "reason": f"Fallback: {e}"}