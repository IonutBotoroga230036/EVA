"""
FORGE v1: E.V.A. builds new skills for herself, and a human approves them.

    request -> Claude writes SKILL.md + tools.py + test_skill.py (structured, via a forced tool call)
            -> files land in skills/_forge/<name>/  (quarantine: the registry never loads them)
            -> AEGIS review: static analysis blocks dangerous code
            -> sandbox: the skill's own tests run in a separate process with the network
               disabled and file writes confined to a scratch folder
            -> one repair round if review or tests fail
            -> proposal presented; install only on the user's "yes"
            -> install: moved to skills/<name>/, trusted: true, registry hot-reloads

Honest scope: the sandbox is a separate process with network and file-write guards,
not a VM. The static review is what keeps dangerous code from ever running, and a
Docker sandbox (docker/Dockerfile.skill) remains the stronger future option.
Skills may use the Python standard library plus httpx, numpy, and yaml.
"""

from __future__ import annotations

import ast
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

from loguru import logger

from core.security.audit import audit

NAME_RX = re.compile(r"^[a-z][a-z0-9_]{2,30}$")
ALLOWED_THIRD_PARTY = {"httpx", "numpy", "yaml"}
BLOCKED_MODULES = {"subprocess", "ctypes", "socket", "multiprocessing", "pickle", "marshal", "importlib",
                   "pty", "telnetlib", "ftplib", "smtplib", "winreg", "shutil", "signal", "threading",
                   "requests", "urllib3", "asyncio", "sys", "builtins", "code", "inspect"}
BLOCKED_CALLS = {"eval", "exec", "compile", "__import__", "globals", "locals", "vars", "setattr", "delattr",
                 "breakpoint", "input"}
BLOCKED_OS = {"system", "popen", "remove", "unlink", "rmdir", "removedirs", "rename", "replace", "kill",
              "chmod", "chown", "startfile", "execv", "execl", "execve", "spawnl", "spawnv", "fork", "putenv",
              "unsetenv"}
MAX_FILE_BYTES = 40_000
SANDBOX_TIMEOUT_S = 60

CONTRACT = """You write skills for E.V.A., a local voice assistant (Python 3.12, Windows). A skill is 3 files.

1. SKILL.md: YAML frontmatter then short instructions for the assistant.
---
name: <name>
description: <one line: what it does and when to use it>
enabled: true
trusted: false
triggers: ["<phrases a user would say>"]
permissions:
  network: [<hosts it calls, or empty>]
  filesystem: ["data/skills/<name>"]
---
<2-5 lines: when to use each tool; confirm results in a few words.>

2. tools.py, defining:
  TOOLS = [ {"type": "function", "function": {"name": ..., "description": ..., "parameters": {...}}} ]
     Flat parameters (strings/numbers/booleans only). Descriptions say exactly when to use the tool.
  FUNCTIONS = {"tool_name": function}
  ACKS = {"tool_name": "One moment, sir."}      (optional, for slow tools)
  ACTIONS = ["tool_name", ...]                   (tools that change something)
  GUARDS = {"tool_name": r"regex of words the user's request must contain"}   (for every action)
  CONFIRM = {"tool_name": "what it will do, e.g. delete {name}"}              (for irreversible actions)
  Every function takes keyword arguments with defaults plus **_ and returns
  {"result": json.dumps({...}), "say": "<one short spoken sentence ending with ', sir.'>"}.
  Never raise: on failure return {"result": json.dumps({"error": "..."}), "say": "<honest sentence>"}.

3. test_skill.py: plain functions named test_* with assert statements. `import tools` to use the skill.
  Tests must pass OFFLINE: never call the network; replace network helpers with fakes, e.g.
  tools._fetch = lambda *a, **k: {...}. Cover the normal case and one error case.

Rules:
- Imports: Python standard library, and only httpx, numpy, yaml beyond it. Never import subprocess, socket,
  ctypes, shutil, sys, importlib, threading, asyncio, pickle, requests. Never call eval, exec, compile,
  __import__, os.system, os.remove or anything that deletes, runs programs, or changes the system.
- Network: only if the task needs it, only https, only hosts you list in permissions.network and
  network_hosts, always with a timeout. Prefer free services that need no API key.
- Files: only read or write under data/skills/<name>/ (relative path).
- Keep tools.py under 200 lines. Simple, readable, correct.
- If the request can't be done safely this way (needs hardware, a paid key, other software, or admin
  rights), set feasible=false and explain in reason."""

WRITE_SKILL_TOOL = {
    "name": "write_skill",
    "description": "Return the complete skill as files.",
    "input_schema": {"type": "object", "properties": {
        "feasible": {"type": "boolean"},
        "reason": {"type": "string", "description": "Why not, when feasible is false."},
        "name": {"type": "string", "description": "snake_case, 3 to 31 characters"},
        "description": {"type": "string"},
        "summary_for_user": {"type": "string", "description": "One spoken sentence: what the skill does."},
        "network_hosts": {"type": "array", "items": {"type": "string"}},
        "skill_md": {"type": "string"}, "tools_py": {"type": "string"}, "test_py": {"type": "string"}},
        "required": ["feasible"]},
}


@dataclass
class Proposal:
    name: str
    request: str
    description: str = ""
    summary: str = ""
    network_hosts: list[str] = field(default_factory=list)
    tools: list[str] = field(default_factory=list)
    review: list[dict] = field(default_factory=list)
    tests_passed: int = 0
    tests_failed: int = 0
    status: str = "draft"          # draft | ready | rejected | infeasible | installed | discarded
    provider: str = ""
    reason: str = ""
    cost_eur: float = 0.0
    created: float = field(default_factory=time.time)


# ================================================================== AEGIS static review
def review_code(tools_py: str, test_py: str, network_hosts: list[str]) -> list[dict]:
    findings: list[dict] = []

    def block(msg):
        findings.append({"level": "block", "msg": msg})

    def warn(msg):
        findings.append({"level": "warn", "msg": msg})

    for label, src in (("tools.py", tools_py), ("test_skill.py", test_py)):
        if len(src.encode()) > MAX_FILE_BYTES:
            block(f"{label} is too large")
            continue
        try:
            tree = ast.parse(src)
        except SyntaxError as e:
            block(f"{label} has a syntax error: {e}")
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                mods = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""]
                for m in mods:
                    root = m.split(".")[0]
                    if root in BLOCKED_MODULES:
                        block(f"{label} imports {root}")
                    elif root not in sys.stdlib_module_names and root not in ALLOWED_THIRD_PARTY \
                            and not (label == "test_skill.py" and root == "tools"):
                        block(f"{label} imports {root}, which isn't allowed")
            elif isinstance(node, ast.Call):
                f = node.func
                if isinstance(f, ast.Name) and f.id in BLOCKED_CALLS:
                    block(f"{label} calls {f.id}()")
                if isinstance(f, ast.Attribute) and f.attr in BLOCKED_OS and isinstance(f.value, ast.Name) \
                        and f.value.id in ("os", "shutil", "pathlib", "Path"):
                    block(f"{label} calls {f.value.id}.{f.attr}()")
                if isinstance(f, ast.Attribute) and f.attr in ("unlink", "rmdir", "rmtree", "chmod"):
                    block(f"{label} deletes or changes files ({f.attr})")
            elif isinstance(node, ast.Attribute) and node.attr.startswith("__") and node.attr not in ("__name__", "__init__"):
                block(f"{label} touches internals ({node.attr})")
    urls = re.findall(r"https?://([a-zA-Z0-9.-]+)", tools_py)
    declared = {h.lower().lstrip("*.") for h in network_hosts}
    for host in set(urls):
        h = host.lower()
        if not any(h == d or h.endswith("." + d) for d in declared):
            block(f"tools.py contacts {host}, which isn't declared")
    if "http://" in tools_py:
        warn("uses plain http somewhere; https is expected")
    uses_net = bool(re.search(r"\bimport httpx\b|\bfrom httpx\b|\burllib\.request\b|\bfrom urllib\b", tools_py))
    if uses_net and not declared:
        block("uses the network but declares no hosts")
    if uses_net:
        warn(f"needs internet access to: {', '.join(sorted(declared))}")
    return findings


# ================================================================== sandbox
RUNNER = r'''
import builtins, json, os, socket, sys, traceback
ROOT = os.path.abspath(os.getcwd())
def _no_net(*a, **k):
    raise PermissionError("network is disabled in the FORGE sandbox")
socket.socket.connect = _no_net
socket.socket.connect_ex = _no_net
socket.create_connection = _no_net
socket.getaddrinfo = _no_net
_open = builtins.open
def _guarded_open(file, mode="r", *a, **k):
    if isinstance(file, (str, bytes, os.PathLike)) and any(c in mode for c in "wax+"):
        if not os.path.abspath(os.fspath(file)).startswith(ROOT):
            raise PermissionError("writes outside the sandbox are blocked")
    return _open(file, mode, *a, **k)
builtins.open = _guarded_open
sys.path.insert(0, ROOT)
res = {"contract": [], "passed": [], "failed": {}}
try:
    import tools
    T = getattr(tools, "TOOLS", None); F = getattr(tools, "FUNCTIONS", None)
    if not isinstance(T, list) or not T:
        res["contract"].append("TOOLS must be a non-empty list")
    if not isinstance(F, dict) or not F:
        res["contract"].append("FUNCTIONS must be a non-empty dict")
    if isinstance(T, list) and isinstance(F, dict):
        names = []
        for t in T:
            try:
                names.append(t["function"]["name"]); t["function"]["description"]; t["function"]["parameters"]
            except Exception:
                res["contract"].append("every TOOLS entry needs function.name, description, parameters")
        for n in names:
            if n not in F or not callable(F[n]):
                res["contract"].append(f"FUNCTIONS is missing {n}")
        for a in getattr(tools, "ACTIONS", []):
            if a not in getattr(tools, "GUARDS", {}):
                res["contract"].append(f"action {a} has no GUARDS entry")
except Exception:
    res["contract"].append("tools.py failed to import: " + traceback.format_exc(limit=2)[-400:])
try:
    import test_skill
    for name in sorted(n for n in dir(test_skill) if n.startswith("test_")):
        fn = getattr(test_skill, name)
        if not callable(fn):
            continue
        try:
            fn(); res["passed"].append(name)
        except Exception as e:
            res["failed"][name] = (type(e).__name__ + ": " + str(e))[:300] or "assertion failed"
    if not res["passed"] and not res["failed"]:
        res["contract"].append("test_skill.py has no test_ functions")
except Exception:
    res["contract"].append("test_skill.py failed to import: " + traceback.format_exc(limit=2)[-400:])
print("FORGE_RESULT " + json.dumps(res))
'''


def run_sandbox(skill_dir: Path, timeout: int = SANDBOX_TIMEOUT_S) -> dict:
    with tempfile.TemporaryDirectory(prefix="eva_forge_") as tmp:
        work = Path(tmp)
        for fname in ("tools.py", "test_skill.py", "SKILL.md"):
            if (skill_dir / fname).exists():
                shutil.copy2(skill_dir / fname, work / fname)
        (work / "_runner.py").write_text(RUNNER, encoding="utf-8")
        env = {k: v for k, v in os.environ.items() if k.upper() in ("PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP",
                                                                     "HOME", "USERPROFILE", "LANG", "PYTHONIOENCODING")}
        env.update({"HTTP_PROXY": "http://127.0.0.1:9", "HTTPS_PROXY": "http://127.0.0.1:9",
                    "NO_PROXY": "", "PYTHONDONTWRITEBYTECODE": "1", "PYTHONIOENCODING": "utf-8"})
        try:
            proc = subprocess.run([sys.executable, "-I", "_runner.py"], cwd=work, env=env, capture_output=True,
                                  text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            return {"contract": [f"tests took longer than {timeout}s"], "passed": [], "failed": {}}
        line = next((ln for ln in proc.stdout.splitlines() if ln.startswith("FORGE_RESULT ")), None)
        if not line:
            return {"contract": ["the sandbox produced no result: " + (proc.stderr or proc.stdout)[-400:]],
                    "passed": [], "failed": {}}
        return json.loads(line[len("FORGE_RESULT "):])


# ================================================================== engine
class Forge:
    def __init__(self, skills_dir: str | Path = "skills", claude=None, max_attempts: int = 2, brain=None):
        self.skills_dir = Path(skills_dir)
        self.quarantine = self.skills_dir / "_forge"
        self.claude = claude            # tests inject a fake Claude; normally the Brain decides
        self.brain = brain
        self.max_attempts = max_attempts
        self.proposals: dict[str, Proposal] = {}
        self._state = Path("data/forge/proposals.json")
        self._load_state()

    # ------------------------------------------------------------ persistence
    def _load_state(self) -> None:
        try:
            for d in json.loads(self._state.read_text(encoding="utf-8")):
                self.proposals[d["name"]] = Proposal(**d)
        except Exception:
            pass

    def _save_state(self) -> None:
        self._state.parent.mkdir(parents=True, exist_ok=True)
        self._state.write_text(json.dumps([asdict(p) for p in self.proposals.values()], indent=2), encoding="utf-8")

    def _brain(self):
        if self.brain is None:
            from core.brain import get_brain
            self.brain = get_brain()
        return self.brain

    def available(self) -> bool:
        if self.claude is not None:
            return self.claude.available()
        return True                     # local mode is always possible (the model may still need pulling)

    def _generate(self, messages: list[dict]) -> tuple[dict, float, str]:
        if self.claude is not None:
            out = self.claude.message(system=CONTRACT, messages=messages, max_tokens=8000, tools=[WRITE_SKILL_TOOL],
                                      tool_choice={"type": "tool", "name": "write_skill"}, purpose="forge")
            return out.get("tool_input") or {}, out["cost_eur"], "Claude"
        return self._brain().code_json(CONTRACT, messages, WRITE_SKILL_TOOL, purpose="forge")

    # ------------------------------------------------------------ build
    def _taken(self, name: str) -> bool:
        return (self.skills_dir / name).exists()

    def _write(self, name: str, spec: dict) -> Path:
        d = self.quarantine / name
        if d.exists():
            shutil.rmtree(d)
        d.mkdir(parents=True)
        md = spec.get("skill_md", "")
        md = re.sub(r"(?m)^trusted:\s*\S+\s*$", "trusted: false", md) if re.search(r"(?m)^trusted:", md) \
            else md.replace("\n---", "\ntrusted: false\n---", 1)
        (d / "SKILL.md").write_text(md, encoding="utf-8")
        (d / "tools.py").write_text(spec.get("tools_py", ""), encoding="utf-8")
        (d / "test_skill.py").write_text(spec.get("test_py", ""), encoding="utf-8")
        return d

    def build(self, request: str) -> Proposal:
        request = " ".join((request or "").split())[:500]
        messages = [{"role": "user", "content": f"Build a skill for this request from the user:\n{request}"}]
        total_cost = 0.0
        last: Optional[Proposal] = None
        for attempt in range(self.max_attempts):
            spec, cost, provider = self._generate(messages)
            total_cost += cost
            if not spec.get("feasible", False):
                p = Proposal(name=spec.get("name") or "unbuildable", request=request, status="infeasible",
                             reason=spec.get("reason") or "it isn't buildable safely as a skill",
                             cost_eur=total_cost, provider=provider)
                audit.log("forge_infeasible", "forge", {"request": request, "reason": p.reason})
                return p
            name = (spec.get("name") or "").strip().lower()
            if not NAME_RX.match(name) or name.startswith("_") or name == "forge":
                name = re.sub(r"[^a-z0-9_]", "_", name)[:31].strip("_") or "new_skill"
                if not NAME_RX.match(name):
                    name = "new_skill"
            if self._taken(name):
                name = f"{name}_2"[:31]
            d = self._write(name, spec)
            findings = review_code(spec.get("tools_py", ""), spec.get("test_py", ""), spec.get("network_hosts") or [])
            blocks = [f["msg"] for f in findings if f["level"] == "block"]
            result = {"contract": [], "passed": [], "failed": {}} if blocks else run_sandbox(d)
            tools = re.findall(r'"name"\s*:\s*"([a-z0-9_]+)"', spec.get("tools_py", ""))
            last = Proposal(name=name, request=request, description=spec.get("description", ""),
                            summary=spec.get("summary_for_user", ""), network_hosts=spec.get("network_hosts") or [],
                            tools=sorted(set(tools)), review=findings, tests_passed=len(result["passed"]),
                            tests_failed=len(result["failed"]) + len(result["contract"]), cost_eur=total_cost,
                            provider=provider)
            problems = blocks + result["contract"] + [f"{k}: {v}" for k, v in result["failed"].items()]
            if not problems:
                last.status = "ready"
                break
            last.status, last.reason = "rejected", "; ".join(problems)[:600]
            logger.warning(f"FORGE attempt {attempt + 1} for {name}: {last.reason}")
            messages = messages + [
                {"role": "assistant", "content": [{"type": "text", "text": f"Draft {attempt + 1} of skill {name}."}]},
                {"role": "user", "content": "The draft failed review or tests. Fix every problem and return the "
                                            "complete corrected skill:\n- " + "\n- ".join(problems)}]
        self.proposals[last.name] = last
        self._save_state()
        audit.log("forge_built", "forge", {"name": last.name, "status": last.status, "cost_eur": round(total_cost, 4),
                                            "tests_passed": last.tests_passed})
        return last

    # ------------------------------------------------------------ approve / discard
    def install(self, name: str) -> Proposal:
        p = self.proposals.get(name)
        src = self.quarantine / name
        if not p or not src.exists():
            raise LookupError(f"no drafted skill called {name}")
        if p.status != "ready":
            raise PermissionError(f"{name} didn't pass review and tests, so it can't be installed")
        dst = self.skills_dir / name
        if dst.exists():
            raise FileExistsError(f"a skill called {name} already exists")
        shutil.move(str(src), str(dst))
        md = (dst / "SKILL.md").read_text(encoding="utf-8")
        (dst / "SKILL.md").write_text(re.sub(r"(?m)^trusted:\s*false\s*$", "trusted: true", md), encoding="utf-8")
        p.status = "installed"
        self._save_state()
        audit.log("forge_installed", "forge", {"name": name})
        return p

    def discard(self, name: str) -> Proposal:
        p = self.proposals.get(name)
        src = self.quarantine / name
        if src.exists():
            shutil.rmtree(src)
        if not p:
            raise LookupError(f"no drafted skill called {name}")
        p.status = "discarded"
        self._save_state()
        audit.log("forge_discarded", "forge", {"name": name})
        return p

    def pending(self) -> list[Proposal]:
        return [p for p in self.proposals.values() if p.status == "ready"]


_forge: Forge | None = None


def get_forge() -> Forge:
    global _forge
    if _forge is None:
        from core.settings import get_settings
        _forge = Forge(get_settings().get("skills", {}).get("dir", "skills"))
    return _forge
