"""
FORGE crew (v0.3 milestone 13b): one skill, five roles, instead of one model grading its own homework.

    planner   feasibility + a short spec (tools, parameters, guards, confirmations, test cases). The ONLY role
              that may say "can't be done".
    FORGE     writes SKILL.md and the whole contract block (TOOLS, FUNCTIONS, ACTIONS, GUARDS, CONFIRM) itself,
              from the plan. That boilerplate is where small models failed; a program never gets it wrong.
    coder     only the Python functions, in a plain code block, copying a working example.
    tester    test_skill.py from the plan, WITHOUT seeing the code (independent tests catch real bugs).
    sandbox   the existing static review and offline test run.
    fixer     gets the real errors back, up to MAX_FIX rounds; may correct the code, or a test that
              contradicts the plan.
    reviewer  a last read for safety and "does this do what was asked"; the static review still decides.

On your GPU the roles run one after another (and yield to your conversation, see core/activity.py). With Claude
for FORGE, the coder and the tester run at the same time.
"""

from __future__ import annotations

import ast
import json
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Optional

from loguru import logger

MAX_FIX = 4

PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "feasible": {"type": "boolean"},
        "reason": {"type": "string"},
        "name": {"type": "string"},
        "description": {"type": "string"},
        "summary_for_user": {"type": "string"},
        "method": {"type": "string", "description": "How it works, e.g. 'offline formula' or 'GET https://...'"},
        "network_hosts": {"type": "array", "items": {"type": "string"}},
        "tools": {"type": "array", "items": {"type": "object", "properties": {
            "name": {"type": "string"},
            "description": {"type": "string"},
            "params": {"type": "object", "additionalProperties": {"type": "string"}},
            "changes_something": {"type": "boolean"},
            "confirm": {"type": "string"},
            "trigger_words": {"type": "array", "items": {"type": "string"}},
        }, "required": ["name", "description", "params", "changes_something", "trigger_words"]}},
        "test_cases": {"type": "array", "items": {"type": "object", "properties": {
            "tool": {"type": "string"}, "args": {"type": "object"}, "expect": {"type": "string"}},
            "required": ["tool", "args", "expect"]}},
    },
    "required": ["feasible"],
}

REVIEW_SCHEMA = {"type": "object", "properties": {
    "safe": {"type": "boolean"}, "matches_request": {"type": "boolean"},
    "concerns": {"type": "array", "items": {"type": "string"}}}, "required": ["safe", "matches_request"]}

PLANNER = """ROLE: planner. You plan a skill for E.V.A., a local voice assistant (Python 3.12, Windows).
Decide if it can be built as a small Python skill, and if so, write a short spec.
- Prefer OFFLINE methods when a formula exists (moon phase: synodic month 29.530588853 days from the new moon
  of 2000-01-06 18:14 UTC; unit and date maths; calendars). Otherwise a free https API with no key.
- feasible=false ONLY if it needs a paid key, an account or login, hardware, other software, or admin rights.
  Needing the internet is never a reason.
- 1 to 3 tools. snake_case names. params: {"param_name": "string" | "number" | "boolean"} (flat).
- changes_something: true only if the tool changes something outside itself. confirm: what it will do, when
  it can't be undone. trigger_words: 2-6 words a user's request would contain.
- 2-4 test_cases with concrete args and the expected result in words. Expected results must follow from the
  method itself, never from memory: use the method's own anchor (the epoch new moon 2000-01-06 18:14 UTC is a
  new moon; 14.77 days later is a full moon) or a property ("phase is one of the 8 names", "result is a number
  between 0 and 29.53"). Include one bad-input case that expects an error.
Reply with the JSON only."""

EXAMPLE = '''import json
from datetime import date


def days_until(target: str = "", **_):
    """Days from today until a YYYY-MM-DD date."""
    try:
        d = date.fromisoformat(target)
    except ValueError:
        return {"result": json.dumps({"error": "bad date"}), "say": "I need a date like 2026-12-24, sir."}
    n = (d - date.today()).days
    return {"result": json.dumps({"days": n, "date": d.isoformat()}), "say": f"{n} days until {d:%d %B}, sir."}'''

CODER = f"""ROLE: coder. Write ONLY the Python functions of a skill, in one ```python block.
Rules:
- One function per planned tool, with exactly the planned name, keyword parameters with defaults, and **_.
- Return {{"result": json.dumps({{...}}), "say": "<one short spoken sentence ending with ', sir.'>"}}.
- Never raise: on any failure return {{"result": json.dumps({{"error": "..."}}), "say": "<honest sentence>"}}.
- Standard library only, plus httpx if the plan uses the network. Every network call goes through ONE helper:
  def _fetch(url, params=None): return httpx.get(url, params=params, timeout=10).json()
- Do NOT write TOOLS, FUNCTIONS, GUARDS, ACTIONS or CONFIRM: FORGE generates those from the plan.
- Never use subprocess, socket, eval, exec, os.system, file deletion, or anything outside the task.
A complete example of the style:
```python
{EXAMPLE}
```"""

TESTER = """ROLE: tester. Write test_skill.py for a skill you have NOT seen, from its plan only, in one ```python block.
- `import tools` and `import json`. Plain functions named test_* with assert statements. No pytest imports.
- Call tools like: out = tools.<tool_name>(**args); data = json.loads(out["result"]); assert out["say"]
- Turn each planned test case into one test. Add one test with bad input that expects an "error" key and no crash.
- Tests run OFFLINE. If the plan uses the network, first replace the helper:
  tools._fetch = lambda url, params=None: {<realistic fake JSON for that API>}
- Check what the plan promises; avoid exact wording of "say".
- Give EVERY assert a message with the actual value, e.g.
  assert data["phase"] == "new moon", f"got {data!r}"   (and the same for every assert)."""

FIXER = """ROLE: fixer. A skill failed its checks. Return the corrected code in one ```python block with the complete
functions (same rules as before: planned names, **_, return result/say, never raise, no TOOLS/GUARDS blocks).
Each problem shows the failing line and what the tool actually returned: use that. If the code follows the plan's
method and a test expects a wrong fact or wrong format, the TEST is wrong: then also return the complete
corrected test_skill.py in a second ```python block that contains the test_ functions."""

JUDGE_SCHEMA = {"type": "object", "properties": {
    "wrong_tests": {"type": "array", "items": {"type": "string"}}, "explain": {"type": "string"}},
    "required": ["wrong_tests"]}

JUDGE = """ROLE: judge. The same tests keep failing. For each failing test decide who is wrong: the code, or the
test's expectation. Use the plan's method and simple arithmetic, never memory. A test is wrong when it expects a
fact the method does not produce (e.g. a date "is a full moon" that the formula shows isn't) or checks exact
wording. List only the tests that are wrong in wrong_tests (their exact names). Reply with the JSON only."""

REVIEWER = """ROLE: reviewer. Read a skill's plan and code. Is it safe (no hidden network calls, no file or system
changes beyond the plan, no data sent anywhere unexpected) and does it do what the user asked? Be strict but
fair; list concrete concerns only. Reply with the JSON only."""


# ------------------------------------------------------------ helpers (pure, tested)
_FENCE = re.compile(r"```(?:python|py)?[ \t]*\n(.*?)```", re.S)


def code_blocks(text: str) -> list[str]:
    blocks = [b.strip() for b in _FENCE.findall(text or "")]
    if not blocks and re.search(r"^\s*(?:def |import |from )", text or "", re.M):
        blocks = [text.strip()]
    return blocks


def clean_name(name: str) -> str:
    n = re.sub(r"[^a-z0-9_]", "_", (name or "").strip().lower())[:31].strip("_")
    return n if re.match(r"^[a-z][a-z0-9_]{2,30}$", n) and n != "forge" else "new_skill"


_TYPES = {"string": "string", "str": "string", "number": "number", "float": "number", "int": "integer",
          "integer": "integer", "boolean": "boolean", "bool": "boolean"}


def normalize_plan(plan: dict) -> dict:
    p = dict(plan or {})
    p["name"] = clean_name(p.get("name", ""))
    if p["name"] == "new_skill":                     # no usable name: the first tool's name is a good one
        first = next((clean_name(t.get("name", "")) for t in (plan or {}).get("tools") or []), "new_skill")
        p["name"] = first
    tools = []
    for t in (p.get("tools") or [])[:3]:
        name = clean_name(t.get("name", ""))
        if name == "new_skill" or any(x["name"] == name for x in tools):
            continue
        params = {str(k).lower(): _TYPES.get(str(v).lower(), "string")
                  for k, v in (t.get("params") or {}).items() if re.match(r"^[a-z_][a-z0-9_]{0,30}$", str(k).lower())}
        words = [w.strip().lower() for w in (t.get("trigger_words") or []) if w and w.strip()][:6]
        tools.append({"name": name, "description": (t.get("description") or name.replace("_", " "))[:200],
                      "params": params, "changes_something": bool(t.get("changes_something")),
                      "confirm": (t.get("confirm") or "").strip()[:120], "trigger_words": words or [name.split("_")[0]]})
    p["tools"] = tools
    p["network_hosts"] = [h.strip().lower() for h in (p.get("network_hosts") or []) if re.match(r"^[a-z0-9.-]+$", h.strip().lower())]
    p["test_cases"] = [c for c in (p.get("test_cases") or []) if c.get("tool") in {t["name"] for t in tools}][:4]
    return p


def contract_block(plan: dict) -> str:
    """The boilerplate small models got wrong, written by FORGE from the plan."""
    tools = [{"type": "function", "function": {
        "name": t["name"], "description": t["description"],
        "parameters": {"type": "object", "properties": {k: {"type": v} for k, v in t["params"].items()},
                       "required": []}}} for t in plan["tools"]]
    guards = {t["name"]: r"\b(" + "|".join(re.escape(w) for w in t["trigger_words"]) + r")\b" for t in plan["tools"]}
    actions = [t["name"] for t in plan["tools"] if t["changes_something"]]
    confirm = {t["name"]: t["confirm"] for t in plan["tools"] if t["changes_something"] and t["confirm"]}
    funcs = ", ".join(f'"{t["name"]}": {t["name"]}' for t in plan["tools"])
    return ("\n\n# ---- contract: generated by FORGE from the plan (not by a model) ----\n"
            f"TOOLS = {json.dumps(tools, indent=1)}\n"
            f"FUNCTIONS = {{{funcs}}}\n"
            f"ACTIONS = {json.dumps(actions)}\n"
            f"GUARDS = {json.dumps(guards)}\n"
            f"CONFIRM = {json.dumps(confirm)}\n")


def assemble(plan: dict, impl: str) -> str:
    # anything the coder wrote for these names is dropped: the generated block is the only source
    impl = re.sub(r"(?ms)^(TOOLS|FUNCTIONS|ACTIONS|GUARDS|CONFIRM|ACKS)\s*=.*?(?=^\S|\Z)", "", impl or "").rstrip()
    head = "" if re.search(r"^\s*import json\b", impl, re.M) else "import json\n"
    return f'"""{plan["name"]}: {plan.get("description", "")[:150]} (built by FORGE)"""\n{head}{impl}\n' + contract_block(plan)


def skill_md(plan: dict) -> str:
    triggers = sorted({w for t in plan["tools"] for w in t["trigger_words"]})
    lines = "\n".join(f"- {t['name']}: {t['description']}" for t in plan["tools"])
    return ("---\n"
            f"name: {plan['name']}\n"
            f"description: {json.dumps(plan.get('description') or plan['name'])}\n"
            "enabled: true\ntrusted: false\n"
            f"triggers: {json.dumps(triggers)}\n"
            "permissions:\n"
            f"  network: {json.dumps(plan['network_hosts'])}\n"
            f"  filesystem: [\"data/skills/{plan['name']}\"]\n"
            "---\n"
            f"{lines}\nConfirm results in a few words.\n")


_SAY_EQ = re.compile(r"""^(\s*)assert\s+([\w.]+\[["']say["']\])\s*==.*$|^(\s*)assert\s+["'].*["']\s*==\s*([\w.]+\[["']say["']\]).*$""",
                     re.M)


def lint_tests(tests: str) -> str:
    """Enforce what the tester was told: never compare the exact spoken sentence, only that there is one."""
    def fix(m: re.Match) -> str:
        indent, expr = (m.group(1), m.group(2)) if m.group(2) else (m.group(3), m.group(4))
        return f"{indent}assert {expr}"
    return _SAY_EQ.sub(fix, tests or "")


def drop_tests(tests: str, names: list[str]) -> str:
    """Remove whole test functions by name (the judge found their expectations wrong)."""
    for n in names:
        tests = re.sub(rf"(?ms)^def {re.escape(n)}\(.*?(?=^def |^\S|\Z)", "", tests)
    return tests


def missing_functions(plan: dict, impl: str) -> list[str]:
    try:
        tree = ast.parse(impl or "")
    except SyntaxError as e:
        return [f"the code has a syntax error: {e}"]
    defined = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    return [f"function {t['name']} is missing" for t in plan["tools"] if t["name"] not in defined]


# ------------------------------------------------------------ the crew
class Crew:
    """brain: .code_json(system, messages, tool) -> (dict, cost, provider), .code_text(system, messages) -> (str, cost,
    provider), .pick(feature). Tests pass a fake brain; production uses core.brain."""

    def __init__(self, brain, forge, progress: Optional[Callable[[str], None]] = None, max_fix: int = MAX_FIX):
        self.brain, self.forge, self.max_fix = brain, forge, max_fix
        self.progress = progress or (lambda stage: None)
        self.cost = 0.0
        self.provider = ""

    def _json(self, system: str, user: str, schema: dict) -> dict:
        data, cost, provider = self.brain.code_json(system, [{"role": "user", "content": user}],
                                                    {"name": "answer", "description": "Answer.", "input_schema": schema})
        self.cost += cost
        self.provider = provider
        return data or {}

    def _text(self, system: str, user: str) -> str:
        text, cost, provider = self.brain.code_text(system, [{"role": "user", "content": user}])
        self.cost += cost
        self.provider = provider
        return text or ""

    def build(self, request: str):
        from core.forge_engine import Proposal, review_code, run_sandbox
        self.progress("planning")
        plan = self._json(PLANNER, f"The user's request:\n{request}", PLAN_SCHEMA)
        if not plan.get("feasible", False):
            return Proposal(name=clean_name(plan.get("name", "")) if plan.get("name") else "unbuildable",
                            request=request, status="infeasible", provider=self.provider, cost_eur=self.cost,
                            reason=plan.get("reason") or "it can't be built safely as a skill")
        plan = normalize_plan(plan)
        if not plan["tools"]:
            return Proposal(name=plan["name"], request=request, status="rejected", provider=self.provider,
                            cost_eur=self.cost, reason="the plan had no usable tools")
        name = plan["name"] if not self.forge._taken(plan["name"]) else f"{plan['name']}_2"[:31]
        plan["name"] = name
        spec = json.dumps({k: plan[k] for k in ("name", "description", "method", "network_hosts", "tools",
                                                "test_cases") if k in plan}, indent=1)
        coder_msg = f"The plan:\n{spec}\n\nWrite the functions."
        tester_msg = f"The plan:\n{spec}\n\nWrite test_skill.py."
        self.progress("coding and writing tests")
        if self.brain.pick("forge") == "cloud":                   # Claude: two roles at once
            with ThreadPoolExecutor(max_workers=2) as pool:
                fc, ft = pool.submit(self._text, CODER, coder_msg), pool.submit(self._text, TESTER, tester_msg)
                code_reply, test_reply = fc.result(), ft.result()
        else:                                                     # your GPU: one after the other
            code_reply, test_reply = self._text(CODER, coder_msg), self._text(TESTER, tester_msg)
        impl = (code_blocks(code_reply) or [""])[0]
        tests = lint_tests((code_blocks(test_reply) or [""])[0])
        findings, result, problems = [], {"passed": [], "failed": {}, "contract": []}, []
        previous, judged, notes = None, False, []
        for rnd in range(self.max_fix + 1):
            tools_py = assemble(plan, impl)
            d = self.forge._write(name, {"skill_md": skill_md(plan), "tools_py": tools_py, "test_py": tests})
            findings = review_code(tools_py, tests, plan["network_hosts"])
            blocks = [f["msg"] for f in findings if f["level"] == "block"]
            missing = missing_functions(plan, impl)
            result = {"passed": [], "failed": {}, "contract": []} if (blocks or missing) else run_sandbox(d)
            problems = blocks + missing + result["contract"] + [f"{k}: {v}" for k, v in result["failed"].items()]
            if not problems:
                break
            if rnd == self.max_fix:
                break
            failing = sorted(result["failed"])
            signature = (tuple(failing), tuple(blocks + missing + result["contract"]))
            if signature == previous:                 # the fixer changed nothing that matters
                if judged or not failing:
                    logger.info("FORGE crew: no progress; stopping instead of repeating the same round")
                    break
                judged = True
                self.progress("judging: code or test?")
                verdict = self._json(JUDGE, f"The plan:\n{spec}\n\nThe functions:\n```python\n{impl}\n```\n\n"
                                            f"The tests:\n```python\n{tests}\n```\n\nFailing:\n- "
                                            + "\n- ".join(f"{k}: {v}" for k, v in result["failed"].items()), JUDGE_SCHEMA)
                wrong = [t for t in (verdict.get("wrong_tests") or []) if t in result["failed"]]
                keep = len(result["passed"]) + len(failing) - len(wrong)
                if wrong and keep >= 1:
                    logger.info(f"FORGE crew: the judge found these tests wrong, dropping them: {wrong}")
                    notes.append({"level": "warn", "msg": f"judge dropped tests with wrong expectations: {', '.join(wrong)}"})
                    tests = drop_tests(tests, wrong)
                    previous = None
                    continue                          # re-run the sandbox with the corrected tests
            previous = signature
            logger.info(f"FORGE crew: round {rnd + 1} problems: {'; '.join(problems)[:300]}")
            self.progress(f"fixing, round {rnd + 1} of {self.max_fix}")
            reply = self._text(FIXER, f"The plan:\n{spec}\n\nThe functions:\n```python\n{impl}\n```\n\n"
                                      f"The tests:\n```python\n{tests}\n```\n\nThe problems:\n- " + "\n- ".join(problems))
            blocks_out = code_blocks(reply)
            if blocks_out:
                impl = blocks_out[0]
                if len(blocks_out) > 1 and "def test_" in blocks_out[1]:
                    tests = lint_tests(blocks_out[1])
        findings = findings + notes
        status, reason = ("ready", "") if not problems else ("rejected", "; ".join(problems)[:600])
        if status == "ready":
            self.progress("reviewing")
            review = self._json(REVIEWER, f"The user's request: {request}\n\nThe plan:\n{spec}\n\n"
                                          f"tools.py:\n```python\n{tools_py}\n```", REVIEW_SCHEMA)
            for c in (review.get("concerns") or [])[:5]:
                findings.append({"level": "warn", "msg": f"reviewer: {c}"[:200]})
            if review and review.get("safe") is False:
                status, reason = "rejected", "the reviewer flagged it as unsafe: " + "; ".join(review.get("concerns") or [])[:400]
            elif review and review.get("matches_request") is False:
                status, reason = "rejected", "the reviewer says it doesn't do what you asked: " + \
                    "; ".join(review.get("concerns") or [])[:400]
        return Proposal(name=name, request=request, description=plan.get("description", ""),
                        summary=plan.get("summary_for_user", ""), network_hosts=plan["network_hosts"],
                        tools=[t["name"] for t in plan["tools"]], review=findings,
                        tests_passed=len(result["passed"]),
                        tests_failed=len(result["failed"]) + len(result["contract"]),
                        status=status, reason=reason, provider=self.provider, cost_eur=self.cost)
