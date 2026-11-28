"""Skills she makes herself, mid-sentence (v1.0).

When no tool, and no few tool calls, do what you asked, she writes a small Python function, the Ponytail way (is it
needed? can her tools do it? one line? then a little code):

    def run(eva, day="today", start="09:00", end="09:45", minutes=15):
        return eva.call("calendar_shift", minutes=minutes, day=day, from_time=start, until_time=end)

make_skill checks it (only plain Python: no files, no processes, no network except through her own tools), saves
it, and runs it once as a DRY RUN: her reading tools answer for real, her actions are only written down ("would move
3 events"). The risk judge looks at those planned actions. If nothing needs your yes, she runs it for real with
run_skill and tells you what changed; every action inside it goes into the journal with its undo, like any other.
Next time it's a tool of its own: skill_<name>.

Sandboxed: each run is a separate `python -I` process with a 30 s limit and at most 60 of her tools, talking to
E.V.A. over stdin/stdout. Files: data/skills_made/<name>/skill.py and skill.json.
"""

from __future__ import annotations

import ast
import json
import re
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable, Optional

from loguru import logger

DIR = Path("data/skills_made")
RUNNER = Path(__file__).with_name("sandbox_runner.py")
TIME_LIMIT_S = 30
MAX_CALLS = 60
_NAME = re.compile(r"^[a-z][a-z0-9_]{2,40}$")
ALLOWED_IMPORTS = {"json", "re", "math", "datetime", "statistics", "itertools", "collections", "functools",
                   "random", "string", "textwrap", "time", "zoneinfo", "decimal", "fractions", "calendar",
                   "difflib", "unicodedata", "urllib.parse", "heapq", "bisect", "dataclasses", "typing"}
BANNED_NAMES = {"open", "exec", "eval", "compile", "__import__", "globals", "locals", "vars", "input", "breakpoint",
                "getattr", "setattr", "delattr", "memoryview", "exit", "quit", "help", "dir", "type", "super",
                "classmethod", "staticmethod", "property"}


def check(code: str) -> list[str]:
    """Problems that stop the code from running at all (empty list = fine)."""
    if not code or len(code) > 12000:
        return ["the code is empty or longer than 12,000 characters"]
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return [f"syntax error on line {e.lineno}: {e.msg}"]
    problems = []
    has_run = False
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            mods = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""]
            for m in mods:
                if m not in ALLOWED_IMPORTS and m.split(".")[0] not in {x for x in ALLOWED_IMPORTS if "." not in x}:
                    problems.append(f"import {m} isn't allowed (only {', '.join(sorted(ALLOWED_IMPORTS))})")
        elif isinstance(node, ast.Name) and node.id in BANNED_NAMES:
            problems.append(f"{node.id} isn't allowed")
        elif isinstance(node, ast.Attribute) and node.attr.startswith("_"):
            problems.append(f"names starting with _ aren't allowed ({node.attr})")
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            problems.append("global/nonlocal aren't allowed")
        elif isinstance(node, ast.FunctionDef) and node.name == "run" and node in tree.body:
            has_run = True
            if not node.args.args or node.args.args[0].arg != "eva":
                problems.append("run's first parameter must be eva")
    if not has_run:
        problems.append("there must be a top-level def run(eva, ...)")
    return sorted(set(problems))


def _folder(name: str) -> Path:
    if not _NAME.match(name or ""):
        raise ValueError("a skill name is 3 to 41 lowercase letters, digits or _")
    return DIR / name


def manifest(name: str) -> Optional[dict]:
    try:
        return json.loads((_folder(name) / "skill.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def code_of(name: str) -> str:
    try:
        return (_folder(name) / "skill.py").read_text(encoding="utf-8")
    except (OSError, ValueError):
        return ""


def listing() -> list[dict]:
    if not DIR.exists():
        return []
    out = []
    for p in sorted(DIR.iterdir()):
        m = manifest(p.name) if p.is_dir() else None
        if m:
            out.append(m)
    return out


def schemas() -> list[dict]:
    """Ready skills as tools: skill_<name>."""
    out = []
    for m in listing():
        if m.get("status") != "ready":
            continue
        out.append({"type": "function", "function": {
            "name": f"skill_{m['name']}", "description": f"(a skill you made) {m.get('purpose', '')}"[:600],
            "parameters": m.get("parameters") or {"type": "object", "properties": {}}}})
    return out


def delete(name: str) -> bool:
    folder = _folder(name)
    if not folder.exists():
        return False
    for p in folder.iterdir():
        p.unlink()
    folder.rmdir()
    return True


def risk_of(name: str, args: dict) -> tuple[str, str]:
    m = manifest(name)
    if not m:
        return "ask", "I don't know that skill"
    if m.get("kind") == "pc":                      # v1.1: a script on his PC he approved; changed since = ask again
        from core.one import files
        if files.approved(m.get("script", "")):
            return "tell", "a script you approved as one of my skills"
        return "ask", "its script changed since you approved it"
    level = m.get("risk") or "ask"
    if level == "ask":
        return "ask", m.get("why") or "its dry run planned something that needs your yes"
    return level, m.get("why") or "its dry run only planned undoable changes"


# ---------------------------------------------------------------- running
Execute = Callable[[str, dict], dict]          # (tool, args) -> out (her tools, synchronous)


def _run(name: str, args: dict, on_call: Callable[[str, dict], dict]) -> dict:
    """One sandboxed run. on_call answers each eva.call. -> {"ok", "value"|"error", "calls"}."""
    path = _folder(name) / "skill.py"
    proc = subprocess.Popen([sys.executable, "-I", "-X", "utf8", str(RUNNER), str(path)], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8")
    killer = threading.Timer(TIME_LIMIT_S, proc.kill)
    killer.start()
    calls, t0 = 0, time.time()
    try:
        proc.stdin.write(json.dumps({"args": args}) + "\n")
        proc.stdin.flush()
        for line in proc.stdout:
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if "call" in msg:
                calls += 1
                if calls > MAX_CALLS:
                    proc.kill()
                    return {"ok": False, "error": f"it called her tools more than {MAX_CALLS} times", "calls": calls}
                try:
                    reply = on_call(str(msg["call"]), dict(msg.get("args") or {}))
                except Exception as e:
                    reply = {"error": str(e)[:300]}
                proc.stdin.write(json.dumps(reply, default=str) + "\n")
                proc.stdin.flush()
            elif "done" in msg:
                return {"ok": True, "value": msg.get("value"), "calls": calls}
            elif "error" in msg:
                return {"ok": False, "error": str(msg["error"])[:600], "calls": calls}
        err = (proc.stderr.read() or "")[-600:]
        if time.time() - t0 >= TIME_LIMIT_S - 0.5:
            return {"ok": False, "error": f"it took longer than {TIME_LIMIT_S} s", "calls": calls}
        return {"ok": False, "error": err or "it stopped without an answer", "calls": calls}
    finally:
        killer.cancel()
        try:
            proc.kill()
        except Exception:
            pass


def _reply(out: dict) -> dict:
    """What eva.call returns inside a skill: the tool's data (and its line for him)."""
    try:
        data = json.loads(out.get("result") or "{}")
    except ValueError:
        data = {"text": out.get("result")}
    if not isinstance(data, dict):
        data = {"value": data}
    if out.get("say"):
        data.setdefault("say", out["say"])
    return data


def make(name: str, purpose: str, code: str, parameters: Optional[dict], example_args: Optional[dict],
         execute: Execute, is_action: Callable[[str], bool], has: Callable[[str], bool]) -> dict:
    """Check, save, dry-run. -> {"ok", "problems", "would", "value", "risk", "why"}."""
    from core.one import risk
    problems = check(code)
    if problems:
        return {"ok": False, "problems": problems}
    folder = _folder(name)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "skill.py").write_text(code, encoding="utf-8")
    params = parameters if isinstance(parameters, dict) and parameters.get("type") == "object" else \
        {"type": "object", "properties": {}}
    would: list[dict] = []

    def dry(tool: str, args: dict) -> dict:
        if tool.startswith("skill_") or tool in ("make_skill", "run_skill"):
            return {"error": "a skill can't run skills"}
        if not has(tool):
            return {"error": f"there's no tool called {tool}"}
        if is_action(tool):
            would.append({"tool": tool, "args": args})
            return {"dry_run": True, "ok": True, "note": "not done: this is the dry run"}
        return _reply(execute(tool, args))
    res = _run(name, dict(example_args or {}), dry)
    levels = [risk.judge(w["tool"], w["args"])[0] for w in would]
    level = risk.worst(levels) if would else "free"
    why = ("its dry run planned: " + ", ".join(sorted({w["tool"] for w in would}))) if would else "it only reads"
    m = {"name": name, "purpose": purpose[:600], "parameters": params, "status": "ready" if res["ok"] else "draft",
         "risk": level, "why": why, "created": (manifest(name) or {}).get("created", time.time()),
         "updated": time.time(), "uses": (manifest(name) or {}).get("uses", 0),
         "last_error": "" if res["ok"] else res.get("error", "")}
    (folder / "skill.json").write_text(json.dumps(m, indent=1), encoding="utf-8")
    logger.info(f"SKILLS: made {name} ({m['status']}, {level}): {purpose[:80]}")
    return {"ok": res["ok"], "problems": [] if res["ok"] else [res.get("error", "")], "would": would,
            "value": res.get("value"), "risk": level, "why": why}


def run(name: str, args: dict, execute: Execute, is_action: Callable[[str], bool], has: Callable[[str], bool],
        judge: Callable[[str, dict], tuple[str, str]], record: Callable[[str, dict, str, str, str, dict], None]) -> dict:
    """The real run: reads answer, actions run unless the judge says ask (then that call fails inside the skill).
    record(tool, args, risk, why, decision, out) puts each action in the journal."""
    m = manifest(name)
    if not m:
        return {"ok": False, "error": f"there's no skill called {name}"}
    if m.get("kind") == "pc":
        return _run_pc(m, args, record)
    done: list[dict] = []

    def real(tool: str, a: dict) -> dict:
        if tool.startswith("skill_") or tool in ("make_skill", "run_skill"):
            return {"error": "a skill can't run skills"}
        if not has(tool):
            return {"error": f"there's no tool called {tool}"}
        if is_action(tool):
            level, why = judge(tool, a)
            if level == "ask":
                record(tool, a, level, why, "refused", {})
                return {"error": f"not done: {tool} needs his yes ({why})"}
            out = execute(tool, a)
            record(tool, a, level, why, "ran", out)
            done.append({"tool": tool, "ok": '"error"' not in (out.get("result") or "")})
            return _reply(out)
        return _reply(execute(tool, a))
    res = _run(name, dict(args or {}), real)
    m["uses"] = int(m.get("uses", 0)) + 1
    m["last_used"] = time.time()
    if not res["ok"]:
        m["last_error"] = res.get("error", "")
    try:
        (_folder(name) / "skill.json").write_text(json.dumps(m, indent=1), encoding="utf-8")
    except OSError:
        pass
    return {**res, "done": done}


# ---------------------------------------------------------------- v1.1: scripts on his PC as skills
def _fill(template: list, args: dict) -> list[str]:
    out = []
    for part in template or []:
        part = str(part)
        for k, v in (args or {}).items():
            part = part.replace("{" + str(k) + "}", str(v))
        if "{" in part and "}" in part:            # an argument he didn't give: leave that flag out
            if out and str(out[-1]).startswith("-"):
                out.pop()
            continue
        out.append(part)
    return out


def adopt(name: str, purpose: str, script: str, args: Optional[list] = None, parameters: Optional[dict] = None,
          code: str = "") -> dict:
    """Make one of her scripts (in her folder) a skill: skill_<name> runs it with her own Python.
    v1.2: script is the file's path or name; if she passes the code itself (Nov 28 00:45, four times), it is saved
    as a file first (or matched to the file that already has it)."""
    from core.one import files
    if not code and script and ("\n" in script or len(script) > 300):
        code, script = script, ""
    p = None
    if script:
        p = files.inside(script)
        if not p or not p.is_file() or p.suffix.lower() != ".py":
            p = files.by_name(script, ".py") or files.by_name(Path(script).name + ("" if script.endswith(".py") else ".py"))
    if not p and code:
        want = code.strip()
        for h in files.find(name, 200) + files.find("py", 200):
            hp = Path(h["path"])
            try:
                if hp.suffix == ".py" and hp.read_text(encoding="utf-8").strip() == want:
                    p = hp
                    break
            except OSError:
                continue
        if not p:
            same = files.by_name(f"{name}.py")
            folder_ = same.parent if same else files.root() / "skills"
            folder_.mkdir(parents=True, exist_ok=True)
            p = folder_ / f"{name}.py"
            if p.exists():
                p = folder_ / f"{name}_skill.py"
            p.write_text(code, encoding="utf-8")
            logger.info(f"SKILLS: saved the code for skill_{name} as {p}")
    if not p:
        return {"ok": False, "error": f"no script called {script or name} in your folder: give its path (my_files "
                                      f"finds it) or the code"}
    folder = _folder(name)
    folder.mkdir(parents=True, exist_ok=True)
    params = parameters if isinstance(parameters, dict) and parameters.get("type") == "object" else \
        {"type": "object", "properties": {}}
    m = {"name": name, "kind": "pc", "purpose": purpose[:600], "parameters": params, "script": str(p),
         "args": [str(a) for a in (args or [])][:20], "status": "ready", "risk": "tell",
         "why": "a script you approved as one of my skills", "created": time.time(), "updated": time.time(), "uses": 0}
    (folder / "skill.json").write_text(json.dumps(m, indent=1), encoding="utf-8")
    files.approve(p)
    logger.info(f"SKILLS: adopted {p.name} as skill_{name}")
    return {"ok": True, "skill": f"skill_{name}", "script": str(p)}


def _run_pc(m: dict, args: dict, record) -> dict:
    from core.one import files
    out = files.run_script(m["script"], _fill(m.get("args") or [], args or {}), timeout=float(m.get("timeout", 120)))
    m["uses"] = int(m.get("uses", 0)) + 1
    m["last_used"] = time.time()
    m["last_error"] = "" if out.get("ok") else str(out.get("error") or out.get("errors") or "")[:300]
    try:
        (_folder(m["name"]) / "skill.json").write_text(json.dumps(m, indent=1), encoding="utf-8")
    except OSError:
        pass
    return {"ok": bool(out.get("ok")), "value": out.get("json", out.get("output", "")[-1500:]),
            "error": out.get("error") or out.get("errors", ""), "done": []}


def review(unused_days: int = 30) -> dict:
    """Once a day: the skills whose last run failed are listed for her notes. v1.2: nothing is ever retired or
    deleted (he keeps everything); a skill retired by v1.1 is a tool again."""
    failing = []
    for m in listing():
        if m.get("status") == "retired":
            m["status"] = "ready"
            (_folder(m["name"]) / "skill.json").write_text(json.dumps(m, indent=1), encoding="utf-8")
            logger.info(f"SKILLS: skill_{m['name']} is back (skills are never retired now)")
        if m.get("status") == "ready" and m.get("last_error"):
            failing.append(m["name"])
    if failing:
        logger.info(f"SKILLS: review: failing {failing}")
    return {"retired": [], "failing": failing}