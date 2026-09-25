"""Deep thinking: one Claude call with extended thinking, answer ready to speak."""

import json

from core.brain import LocalUnavailable, get_brain
from core.budget import BudgetExceeded
from core.prompt_builder import EVA_MD, _read, _without_vocab

SYSTEM = (
    "You are the deep-thinking module of E.V.A., a personal voice assistant. Think the question through "
    "carefully, then answer for SPEECH: lead with the answer, then the key reasons, 3 to 6 short sentences, "
    "no lists, no markdown, no em dashes. Be direct and honest, including about uncertainty. Address the user "
    "as 'sir'. Use metric units and euros unless told otherwise.\n\nWhat the assistant knows about the user "
    "(public profile only):\n"
)

TOOLS = [{"type": "function", "function": {
    "name": "think_deeply",
    "description": "Reason carefully about a hard question, decision, or plan using a stronger model.",
    "parameters": {"type": "object", "properties": {
        "question": {"type": "string", "description": "The question, in the user's words."}},
        "required": ["question"]}}}]


def think_deeply(question: str = "", **_):
    question = (question or "").strip()
    if not question:
        return {"result": json.dumps({"error": "no question"}), "say": "What should I think about, sir?", "exact": True}
    brain = get_brain()
    profile = _without_vocab(_read(EVA_MD))[:2000]           # never the private EVA.local.md
    try:
        text, cost, provider = brain.think(SYSTEM + profile, question, effort="medium")
    except BudgetExceeded as e:
        return {"result": json.dumps({"error": str(e)}), "exact": True,
                "say": f"That would go over your budget, sir. {e}. Say 'switch to local mode' to think locally."}
    except LocalUnavailable as e:
        return {"result": json.dumps({"error": str(e)}), "exact": True, "say": f"I can't think locally yet, sir: {e}."}
    except Exception as e:
        return {"result": json.dumps({"error": str(e)}), "exact": True,
                "say": f"I couldn't finish thinking that through, sir: {str(e)[:120]}."}
    answer = " ".join(text.split()) or "I came up empty on that one, sir."
    return {"result": json.dumps({"answer": answer, "provider": provider, "cost_eur": round(cost, 4)}), "say": answer,
            "exact": True, "widget": {"kind": "note", "title": "Thought it through", "text": answer[:400]}}


FUNCTIONS = {"think_deeply": think_deeply}
ACKS = {"think_deeply": "Let me think that through properly, sir."}
GUARDS = {"think_deeply": r"\b(think|reason|decide|decision|consider|figure|analy[sz]e|claude|trade-?offs?|pros|plan)\b"}
