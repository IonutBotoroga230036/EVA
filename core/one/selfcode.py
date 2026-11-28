"""Her own code (v1.2): she can read it, and change a copy of it, with his yes before anything is real.

Nov 28: "give her access to her own source code so she can see it, install things and modify things." No Docker:
she needs this PC (camera, files, his apps), and a container would hide them. Instead, three rungs:

    code_find("listening window")       search her code (read-only, private files never shown)
    code_read("voice/listen.py", 280)   read part of a file
    my_console(level="warning")         her own log lines, in memory (core/console.py)

    code_change(path, old, new, why)    edit HER WORKSHOP: a git worktree next to the repo
                                        (D:/Project E.V.A/eva-workshop, branch eva/workshop). The running E.V.A. is
                                        untouched.
    code_test("tests/test_v12.py")      run the tests in the workshop
    code_diff()                         what she changed there
    code_apply()                        his yes, then the change is applied to the real code (uncommitted, so
                                        `git diff` shows it and he commits and pushes it himself). Takes effect when
                                        he restarts her.
    code_reset()                        throw the workshop's changes away

Never readable or writable: config/settings.local.yaml, config/secrets.env, EVA.local.md, any .env or key file,
data/, .git/, the virtual environments.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

from loguru import logger

from core.one.files import code_root

SOURCE = {".py", ".yaml", ".yml", ".md", ".html", ".js", ".css", ".txt", ".toml", ".ini", ".cfg", ".json", ".ps1",
          ".bat", ".sh"}
PRIVATE_FILES = {"config/settings.local.yaml", "config/secrets.env", "eva.local.md"}
SKIP_DIRS = {".git", ".venv", "venv", "env", "data", "node_modules", "__pycache__", ".pytest_cache", "models",
             "logs", ".idea", ".vscode", "dist", "build"}
_SECRETISH = re.compile(r"(?i)(^\.env|secret|\.key$|\.pem$|credentials|token)")
BRANCH = "eva/workshop"


def workshop() -> Path:
    return code_root().parent / "eva-workshop"


def private(rel: str) -> bool:
    r = rel.replace("\\", "/").lower()
    while r.startswith("./"):
        r = r[2:]
    r = r.lstrip("/")
    parts = r.split("/")
    return (r in PRIVATE_FILES or any(p in SKIP_DIRS for p in parts[:-1]) or bool(_SECRETISH.search(parts[-1]))
            or r.endswith(".local.yaml") or r.endswith(".local.md"))


def _resolve(base: Path, path: str) -> Optional[Path]:
    p = (base / str(path).replace("\\", "/").lstrip("/")).resolve()
    try:
        rel = p.relative_to(base.resolve()).as_posix()
    except ValueError:
        return None
    return None if private(rel) else p


def _files(base: Path):
    for dirpath, dirs, names in os.walk(base):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]
        for n in names:
            p = Path(dirpath) / n
            rel = p.relative_to(base).as_posix()
            if p.suffix.lower() in SOURCE and not private(rel):
                yield p, rel


def find(query: str, limit: int = 60, base: Optional[Path] = None) -> list[str]:
    base = base or code_root()
    q = (query or "").strip()
    if not q:
        return []
    rx = re.compile(q[3:], re.I) if q.startswith("re:") else re.compile(re.escape(q), re.I)
    hits = []
    for p, rel in _files(base):
        if rx.search(rel):
            hits.append(f"{rel}: (the file name)")
        try:
            if p.stat().st_size > 1_500_000:
                continue
            for i, line in enumerate(p.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                if rx.search(line):
                    hits.append(f"{rel}:{i}: {line.strip()[:160]}")
                    if len(hits) >= limit:
                        return hits
        except OSError:
            continue
    return hits


def read(path: str, start: int = 1, lines: int = 200, base: Optional[Path] = None) -> dict:
    base = base or code_root()
    p = _resolve(base, path)
    if not p:
        return {"error": f"{path} is private or outside your code"}
    if not p.is_file():
        near = [h for h in find(Path(str(path)).name, 8, base) if h.endswith("(the file name)")]
        return {"error": f"{path} doesn't exist", "maybe": near}
    all_lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    a = max(1, int(start or 1))
    b = min(len(all_lines), a + max(1, min(int(lines or 200), 600)) - 1)
    body = "\n".join(f"{i:>5}  {all_lines[i - 1]}" for i in range(a, b + 1))
    return {"path": p.relative_to(base).as_posix(), "lines": f"{a}-{b} of {len(all_lines)}", "text": body}


# ---------------------------------------------------------------- the workshop
def _git(args: list, cwd: Path, timeout: int = 120, input_: Optional[str] = None) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=timeout, input=input_,
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def ensure_workshop() -> dict:
    """The workshop exists and starts from the code as it is now (his uncommitted changes included)."""
    ws, live = workshop(), code_root()
    if not shutil.which("git"):
        return {"error": "git isn't installed on this PC"}
    if (ws / ".git").exists():
        return {"ok": True, "workshop": str(ws)}
    r = _git(["worktree", "add", "-f", "-B", BRANCH, str(ws), "HEAD"], live)
    if r.returncode != 0:
        return {"error": f"couldn't make the workshop: {(r.stderr or r.stdout)[-400:]}"}
    diff = _git(["diff", "--binary", "HEAD"], live).stdout
    if diff.strip():
        _git(["apply", "--whitespace=nowarn", "-"], ws, input_=diff)
    _git(["add", "-A"], ws)
    _git(["-c", "user.name=E.V.A.", "-c", "user.email=eva@localhost", "commit", "-q", "-m",
          "workshop base: the code as it was", "--allow-empty"], ws)
    logger.info(f"CODE: made her workshop at {ws}")
    return {"ok": True, "workshop": str(ws), "new": True}


def change(path: str, old: str, new: str, why: str = "") -> dict:
    w = ensure_workshop()
    if w.get("error"):
        return w
    ws = workshop()
    p = _resolve(ws, path)
    if not p:
        return {"error": f"{path} is private or outside your code"}
    if p.suffix.lower() not in SOURCE:
        return {"error": "only source files (code, settings, docs) can be changed"}
    if not p.exists():
        if old:
            return {"error": f"{path} doesn't exist; to make a new file, leave old empty"}
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(new, encoding="utf-8")
        return {"ok": True, "made": p.relative_to(ws).as_posix(), "why": why}
    text = p.read_text(encoding="utf-8")
    if not old:
        return {"error": "old must be the exact text to replace (copy it from code_read)"}
    n = text.count(old)
    if n != 1:
        return {"error": f"the old text is in the file {n} times; it must be exactly once (add a line around it)"}
    p.write_text(text.replace(old, new, 1), encoding="utf-8")
    logger.info(f"CODE: changed {p.relative_to(ws).as_posix()} in her workshop ({why[:80]})")
    return {"ok": True, "changed": p.relative_to(ws).as_posix(), "why": why}


def diff() -> dict:
    ws = workshop()
    if not (ws / ".git").exists():
        return {"diff": "", "files": []}
    _git(["add", "-A", "--", ".", ":(exclude)**/__pycache__/**", ":(exclude)**/*.pyc", ":(exclude).pytest_cache/**"], ws)
    d = _git(["diff", "--cached", "HEAD"], ws).stdout
    files = [x for x in _git(["diff", "--cached", "--name-only", "HEAD"], ws).stdout.splitlines() if x]
    return {"diff": d[-20000:], "files": files}


def test(target: str = "", timeout: int = 600) -> dict:
    w = ensure_workshop()
    if w.get("error"):
        return w
    ws = workshop()
    args = [a for a in re.split(r"\s+", (target or "").strip()) if a][:10]
    if any(a.startswith("-") and a not in ("-k", "-x", "-q") for a in args):
        return {"error": "give test files or -k words"}
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1"}
    try:
        r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider", *args],
                           cwd=str(ws), capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=max(30, min(int(timeout), 1800)), env=env,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"the tests ran longer than {timeout} s"}
    out = (r.stdout + r.stderr)[-5000:]
    last = next((ln for ln in reversed(out.splitlines()) if ln.strip()), "")
    return {"ok": r.returncode == 0, "summary": last[:300], "output": out}


def apply() -> dict:
    """The workshop's changes onto the real code, as uncommitted changes. Needs his yes (risk: ask)."""
    d = diff()
    if not d.get("files"):
        return {"error": "the workshop has no changes"}
    ws, live = workshop(), code_root()
    bad = [f for f in d["files"] if private(f)]
    if bad:
        return {"error": f"won't touch private files: {bad}"}
    patch = _git(["diff", "--cached", "--binary", "HEAD"], ws).stdout
    chk = _git(["apply", "--check", "--whitespace=nowarn", "-"], live, input_=patch)
    if chk.returncode != 0:
        return {"error": f"the real code changed since the workshop was made; code_reset and redo it "
                         f"({(chk.stderr or '')[-300:]})"}
    r = _git(["apply", "--whitespace=nowarn", "-"], live, input_=patch)
    if r.returncode != 0:
        return {"error": (r.stderr or "git apply failed")[-400:]}
    _git(["-c", "user.name=E.V.A.", "-c", "user.email=eva@localhost", "commit", "-q", "-m", "applied to her code"], ws)
    logger.info(f"CODE: applied her workshop changes to the real code: {', '.join(d['files'])}")
    return {"ok": True, "applied": d["files"], "next": "he restarts E.V.A. for it to take effect; git diff shows it, "
                                                       "and he commits and pushes it himself"}


def reset() -> dict:
    ws, live = workshop(), code_root()
    if (ws / ".git").exists():
        r = _git(["worktree", "remove", "--force", str(ws)], live)
        if r.returncode != 0 and ws.exists():
            shutil.rmtree(ws, ignore_errors=True)
        _git(["worktree", "prune"], live)
    return {"ok": True, "note": "the workshop starts fresh from the real code next time"}


# ---------------------------------------------------------------- her tools
def _r(data: dict) -> dict:
    return {"result": json.dumps(data, ensure_ascii=False, default=str)}


def tool_code_find(query: str = "", **_):
    hits = find(query)
    return _r({"root": str(code_root()), "hits": hits} if hits else {"hits": [], "note": "nothing; try one word"})


def tool_code_read(path: str = "", start: int = 1, lines: int = 200, **_):
    return _r(read(path, start, lines))


def tool_code_change(path: str = "", old: str = "", new: str = "", why: str = "", **_):
    out = change(path, old, new, why)
    return {**_r(out), **({"changes": [{"what": f"workshop: {out.get('changed') or out.get('made')}", "before": "",
                                         "after": why[:120]}]} if out.get("ok") else {})}


def tool_code_diff(**_):
    return _r(diff())


def tool_code_test(target: str = "", **_):
    return _r(test(target))


def tool_code_apply(**_):
    return _r(apply())


def tool_code_reset(**_):
    return _r(reset())


def describe_apply(_args: dict) -> str:
    files = diff().get("files") or []
    return f"apply my workshop changes to my real code ({', '.join(files[:4]) or 'nothing yet'}); you'd restart me after"


SCHEMAS = [
    {"type": "function", "function": {
        "name": "code_find", "description": "Search YOUR OWN source code (E.V.A.'s code on his PC): a word, a "
                                            "function name, a log message, or 're:<regex>'. Read-only.",
        "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}}},
    {"type": "function", "function": {
        "name": "code_read", "description": "Read part of one of your source files (with line numbers).",
        "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "start": {"type": "integer"},
                                                        "lines": {"type": "integer"}}, "required": ["path"]}}},
    {"type": "function", "function": {
        "name": "code_change", "description": "Change one of your source files IN YOUR WORKSHOP (a copy of your code; "
                                              "the running you is untouched): replace old (exact text, once) with "
                                              "new. Empty old makes a new file. Then code_test, then code_apply.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"}, "old": {"type": "string"}, "new": {"type": "string"},
            "why": {"type": "string"}}, "required": ["path", "new", "why"]}}},
    {"type": "function", "function": {
        "name": "code_diff", "description": "What you changed in your workshop so far.",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "code_test", "description": "Run your tests in the workshop (pytest): test files or -k words; empty "
                                            "runs them all (slow).",
        "parameters": {"type": "object", "properties": {"target": {"type": "string"}}}}},
    {"type": "function", "function": {
        "name": "code_apply", "description": "Apply your workshop changes to your real code (needs his yes; he "
                                             "restarts you after, and commits it himself).",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "code_reset", "description": "Throw away your workshop changes and start fresh from the real code.",
        "parameters": {"type": "object", "properties": {}}}},
]
FUNCTIONS = {"code_find": tool_code_find, "code_read": tool_code_read, "code_change": tool_code_change,
             "code_diff": tool_code_diff, "code_test": tool_code_test, "code_apply": tool_code_apply,
             "code_reset": tool_code_reset}
ACTIONS = {"code_change", "code_apply", "code_reset", "code_test"}
