"""FORGE fact checks (Nov 23, the moon-phase lesson).

A skill's tests were written from the same belief as its code, so a wrong belief passed every test (the moon
phase was off by days). Before any code is written, the planner's test cases that state a real-world fact (a
date, an astronomical event, a conversion rate, a holiday, a law) are checked against the web by Claude with web
search. A wrong expectation is corrected in the plan, so the coder and the tester both work from the truth, and
the approval card says what was checked and where.

Only when FORGE may use the cloud (the "forge" brain feature resolves to Claude). Locally, nothing can be
checked, and the card says so instead of pretending. Cost: the searches ($10 per 1,000, so about a cent each) plus
tokens, roughly EUR 0.02 to 0.06 per skill.
"""

from __future__ import annotations

import json
import re
from typing import Optional

from loguru import logger

WEB_SEARCH = {"type": "web_search_20250305", "name": "web_search", "max_uses": 6}
SYSTEM = """You check the test cases of a small software skill BEFORE its code is written.
For each test case, decide whether its expected result states a real-world fact that could be wrong (a date, an
astronomical event like a moon phase or sunrise, a holiday, an exchange or conversion rate, a law, a population,
a sports result). Pure logic or properties ("returns an error", "a number between 0 and 1") are not facts.
For every factual one, verify it with web search against a reliable source (official, scientific, or a well
known reference site). Today is {today}.
End your reply with ONE JSON object in a ```json block, nothing after it:
{{"checks": [{{"index": 0, "factual": true, "correct": true, "fixed_expect": "", "source": "https://...",
"note": "short reason"}}]}}
fixed_expect: when correct is false, the expected result rewritten with the right value; else "".
"""


def _client():
    from core.claude import ClaudeClient, _load_key
    if not _load_key():                    # first the cheap check: no key, no cloud (and no slow brain lookup)
        return None
    from core.brain import get_brain
    if get_brain().pick("forge") != "cloud":
        return None
    from core.settings import get_settings
    model = ((get_settings().get("inference") or {}).get("cloud") or {}).get("model_heavy") or "claude-sonnet-5"
    return ClaudeClient(model=model, timeout=120)


def parse(text: str) -> list[dict]:
    blocks = re.findall(r"```json\s*(\{.*?\})\s*```", text or "", re.S)
    raw = blocks[-1] if blocks else (re.findall(r"(\{\s*\"checks\".*\})", text or "", re.S) or [""])[-1]
    try:
        checks = json.loads(raw).get("checks") or []
    except (ValueError, AttributeError):
        return []
    return [c for c in checks if isinstance(c, dict) and isinstance(c.get("index"), int)]


def check(plan: dict, request: str, client=None) -> tuple[dict, list[dict], float]:
    """(plan with wrong expectations corrected, findings for the approval card, cost in EUR)."""
    cases = list(plan.get("test_cases") or [])
    if not cases:
        return plan, [], 0.0
    client = client if client is not None else _client()
    if client is None:
        return plan, [{"level": "warn", "msg": "facts not checked: FORGE ran locally, so real-world answers "
                                               "(dates, rates, events) weren't compared with any source"}], 0.0
    from datetime import date
    listing = "\n".join(f"{i}. {c.get('tool')}({json.dumps(c.get('args') or {}, ensure_ascii=False)}) -> "
                        f"{c.get('expect')}" for i, c in enumerate(cases))
    try:
        out = client.message(system=SYSTEM.format(today=date.today().isoformat()), max_tokens=3000,
                             messages=[{"role": "user", "content": f"The skill: {request}\nMethod: "
                                                                   f"{plan.get('method', '')}\n\nTest cases:\n{listing}"}],
                             tools=[WEB_SEARCH], purpose="forge-facts")
    except Exception as e:
        logger.warning(f"FORGE facts: check failed ({str(e)[:120]})")
        return plan, [{"level": "warn", "msg": f"facts not checked: {str(e)[:120]}"}], 0.0
    cost = float(out.get("cost_eur", 0.0))                     # tokens and searches
    findings, fixed = [], 0
    checks = parse(out.get("text", ""))
    factual = [c for c in checks if c.get("factual") and 0 <= c["index"] < len(cases)]
    for c in factual:
        case = cases[c["index"]]
        src = str(c.get("source") or "")[:160]
        if c.get("correct") is False and str(c.get("fixed_expect") or "").strip():
            old = case.get("expect")
            case["expect"] = str(c["fixed_expect"]).strip()
            fixed += 1
            findings.append({"level": "warn", "msg": f"fact check corrected a test: {case.get('tool')} expected "
                                                     f"'{old}', the source says '{case['expect']}' ({src})"[:400]})
    if factual:
        ok = len(factual) - fixed
        sources = sorted({str(c.get("source") or "") for c in factual if c.get("source")})[:3]
        findings.append({"level": "info", "msg": f"facts checked: {len(factual)} against sources"
                                                 f"{f', {fixed} corrected' if fixed else ''} ({', '.join(sources)})"[:400]})
        logger.info(f"FORGE facts: {len(factual)} checked, {ok} right, {fixed} corrected")
    else:
        findings.append({"level": "info", "msg": "facts checked: no test depends on a real-world fact"})
    plan = {**plan, "test_cases": cases}
    return plan, findings, cost
