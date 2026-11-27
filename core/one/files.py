"""Her own files on your PC (v1.1): where she put things, and running her own scripts.

Nov 27: she built a face recognition model with you, then couldn't find eva_face.py ("I didn't put any files
anywhere"), couldn't run its setup, and couldn't wire it in as a skill. Now:

    my_files("face")                 the files in your E.V.A. folder (Documents/E.V.A., one folder per task)
    read_my_file(".../SKILL.md")     read one (text, up to 20,000 characters)
    run_my_script(".../setup.py")    run a Python script from there on your PC: her own Python environment
                                     (data/agent/venv, where tasks install packages, never yours), network
                                     allowed, 2 minutes by default. The first run of a script asks you once;
                                     after that it runs freely until the file changes.

Only .py files inside the E.V.A. folder. Approvals: data/script_approvals.json (path -> sha1 of the file).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

APPROVALS = Path("data/script_approvals.json")
TEXT = {".py", ".md", ".txt", ".json", ".yaml", ".yml", ".csv", ".scad", ".html", ".js", ".css", ".toml", ".ini",
        ".cfg", ".log", ".xml", ".svg"}


def root() -> Path:
    try:
        from core.agent.pc import _cfg
        base = _cfg().get("output_dir") or "~/Documents/E.V.A."
    except Exception:
        base = "~/Documents/E.V.A."
    p = Path(os.path.expanduser(base))
    if not p.exists() and str(p).endswith("."):            # Windows drops a trailing dot: "E.V.A." is "E.V.A"
        alt = Path(str(p).rstrip("."))
        if alt.exists():
            return alt
    return p


def inside(path: str | Path) -> Optional[Path]:
    """The real path if it's inside the E.V.A. folder (relative paths are taken from there), else None."""
    r = root()
    p = Path(os.path.expanduser(str(path)))
    if not p.is_absolute():
        p = r / p
    try:
        rp, rr = p.resolve(), r.resolve()
    except OSError:
        return None
    try:
        rp.relative_to(rr)
    except ValueError:
        return None
    return rp


def recent_folders(n: int = 5) -> list[str]:
    r = root()
    if not r.exists():
        return []
    dirs = [d for d in r.iterdir() if d.is_dir() and not d.name.startswith(".")]
    dirs.sort(key=lambda d: d.stat().st_mtime, reverse=True)
    out = []
    for d in dirs[:n]:
        names = sorted(p.name for p in d.iterdir() if p.is_file())[:6]
        out.append(f"{d.name} ({', '.join(names) or 'empty'})")
    return out


def find(query: str = "", limit: int = 30) -> list[dict]:
    r = root()
    if not r.exists():
        return []
    words = [w for w in re.findall(r"[a-z0-9]+", (query or "").lower()) if len(w) > 2 and w not in ("the", "and")]
    hits = []
    for p in r.rglob("*"):
        try:
            rel = p.relative_to(r)
        except ValueError:
            continue
        if len(rel.parts) > 4 or any(x.startswith((".", "__pycache__", "_eva_")) for x in rel.parts) or not p.is_file():
            continue
        hay = str(rel).lower().replace("_", " ")
        score = sum(1 for w in words if w in hay)
        if words and not score:
            continue
        st = p.stat()
        hits.append((score, st.st_mtime, {"path": str(p), "folder": rel.parts[0] if len(rel.parts) > 1 else "",
                                          "name": p.name, "kb": round(st.st_size / 1024, 1),
                                          "changed": time.strftime("%Y-%m-%d %H:%M", time.localtime(st.st_mtime))}))
    hits.sort(key=lambda x: (x[0], x[1]), reverse=True)
    return [h[2] for h in hits[:limit]]


def _sha(p: Path) -> str:
    return hashlib.sha1(p.read_bytes()).hexdigest()


def _approvals() -> dict:
    try:
        return json.loads(APPROVALS.read_text(encoding="utf-8"))
    except Exception:
        return {}


def approved(path: str | Path) -> bool:
    p = inside(path)
    return bool(p and p.is_file() and _approvals().get(str(p)) == _sha(p))


def approve(p: Path) -> None:
    d = _approvals()
    d[str(p)] = _sha(p)
    APPROVALS.parent.mkdir(parents=True, exist_ok=True)
    APPROVALS.write_text(json.dumps(d, indent=1), encoding="utf-8")


def python() -> str:
    try:
        from core.agent.pc import VENV
        exe = VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        if exe.exists():
            return str(exe)
    except Exception:
        pass
    return sys.executable


def run_script(path: str, args: Optional[list] = None, timeout: float = 120, stdin: str = "") -> dict:
    """Run one of her .py scripts (approved by the caller). -> {"ok", "exit", "output", "json"?}."""
    p = inside(path)
    if not p or not p.is_file():
        return {"ok": False, "error": f"{path} isn't a file in your E.V.A. folder"}
    if p.suffix.lower() != ".py":
        return {"ok": False, "error": "only Python scripts (.py) run from here"}
    argv = [str(a) for a in (args or [])][:30]
    if any("\n" in a or len(a) > 500 for a in argv):
        return {"ok": False, "error": "arguments must be short, one line each"}
    approve(p)
    t0 = time.time()
    try:
        r = subprocess.run([python(), "-X", "utf8", str(p), *argv], cwd=str(p.parent), capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=max(5, min(float(timeout or 120), 600)),
                           input=stdin or None,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"it ran longer than {timeout:.0f} s and was stopped"}
    except OSError as e:
        return {"ok": False, "error": str(e)}
    out = (r.stdout or "")[-4000:]
    res = {"ok": r.returncode == 0, "exit": r.returncode, "output": out, "seconds": round(time.time() - t0, 1)}
    if r.stderr and r.returncode != 0:
        res["errors"] = r.stderr[-2000:]
    for line in reversed([x for x in out.splitlines() if x.strip()][-5:]):
        try:
            res["json"] = json.loads(line)
            break
        except ValueError:
            continue
    return res


# ---------------------------------------------------------------- her tools
def _r(data: dict, say: str = "", **extra) -> dict:
    out = {"result": json.dumps(data, ensure_ascii=False, default=str), **extra}
    if say:
        out["say"] = say
    return out


def tool_my_files(query: str = "", **_):
    hits = find(query)
    return _r({"folder": str(root()), "files": hits} if hits else
              {"folder": str(root()), "files": [], "note": "nothing matches; try fewer words"})


def by_name(name: str, ext: str = "") -> Optional[Path]:
    """The newest file in the E.V.A. folder with exactly this name."""
    want = Path(str(name)).name.lower()
    for h in find(want, 200):
        if h["name"].lower() == want and (not ext or want.endswith(ext)):
            return Path(h["path"])
    return None


def tool_read_my_file(path: str = "", **_):
    p = inside(path)
    if not p or not p.is_file():
        p = by_name(path)
    if not p or not p.is_file():
        return _r({"error": f"{path} isn't in your E.V.A. folder"})
    if p.suffix.lower() not in TEXT:
        return _r({"error": f"{p.suffix} files aren't text", "path": str(p)})
    return _r({"path": str(p), "text": p.read_text(encoding="utf-8", errors="replace")[:20000]})


def tool_run_my_script(path: str = "", args: Optional[list] = None, timeout: float = 120, **_):
    p = inside(path)
    if not p or not p.is_file():
        p = by_name(path, ".py")
    if not p:
        return _r({"error": f"{path} isn't a script in your E.V.A. folder"})
    out = run_script(str(p), args if isinstance(args, list) else [str(args)] if args else [], timeout)
    say = "" if out.get("ok") else f"The script stopped with an error, sir."
    return _r({"script": str(p), **out}, say)


def describe_run(args: dict) -> str:
    return f"run {Path(str(args.get('path') or 'the script')).name} on your PC for the first time"


SCHEMAS = [
    {"type": "function", "function": {
        "name": "my_files", "description": "Find files you or your tasks made, in his E.V.A. folder "
                                           "(Documents/E.V.A., one folder per task): 'where is the face script?'",
        "parameters": {"type": "object", "properties": {"query": {"type": "string"}}}}},
    {"type": "function", "function": {
        "name": "read_my_file", "description": "Read a text file from his E.V.A. folder (a script, SKILL.md, notes).",
        "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}},
    {"type": "function", "function": {
        "name": "run_my_script", "description": "Run a Python script from his E.V.A. folder on his PC, with your own "
                                                "Python environment (camera, network and files work). A script's "
                                                "first run needs his yes; then it runs freely until it changes.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"}, "args": {"type": "array", "items": {"type": "string"}},
            "timeout": {"type": "number"}}, "required": ["path"]}}},
]
FUNCTIONS = {"my_files": tool_my_files, "read_my_file": tool_read_my_file, "run_my_script": tool_run_my_script}
ACTIONS = {"run_my_script"}
