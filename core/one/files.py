"""Her own files on your PC (v1.1): where she put things, and running her own scripts.

Nov 27: she built a face recognition model with you, then couldn't find eva_face.py ("I didn't put any files
anywhere"), couldn't run its setup, and couldn't wire it in as a skill. Now:

    my_files("face")                 the files in her folder (v1.2: "E.V.A. Files" next to her code, one folder per task)
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

from loguru import logger

APPROVALS = Path("data/script_approvals.json")
TEXT = {".py", ".md", ".txt", ".json", ".yaml", ".yml", ".csv", ".scad", ".html", ".js", ".css", ".toml", ".ini",
        ".cfg", ".log", ".xml", ".svg"}


OLD_DEFAULT = "~/Documents/E.V.A."
FOLDER_NAME = "E.V.A. Files"


def code_root() -> Path:
    """Her code: the folder with core/, voice/, interfaces/ (D:/Project E.V.A/eva on his PC)."""
    return Path(__file__).resolve().parents[2]


def root() -> Path:
    """v1.2: her folder is next to her code, outside the repository (D:/Project E.V.A/E.V.A. Files), unless
    agent.output_dir names another one. The old default (Documents/E.V.A.) counts as unset: its files move here."""
    try:
        from core.agent.pc import _cfg
        base = str(_cfg().get("output_dir") or "").strip()
    except Exception:
        base = ""
    if not base or base.lower() == "auto" or base.replace("\\", "/").rstrip("/") == OLD_DEFAULT.rstrip("/"):
        return code_root().parent / FOLDER_NAME
    p = Path(os.path.expanduser(base))
    if not p.exists() and str(p).endswith("."):            # Windows drops a trailing dot: "E.V.A." is "E.V.A"
        alt = Path(str(p).rstrip("."))
        if alt.exists():
            return alt
    return p


def _old_roots() -> list[Path]:
    p = Path(os.path.expanduser(OLD_DEFAULT))
    return [x for x in dict.fromkeys([p, Path(str(p).rstrip("."))]) if x.exists() and x.is_dir()]


def migrate(old: Optional[list] = None, new: Optional[Path] = None) -> dict:
    """Move everything from the old folder (Documents/E.V.A.) into her folder, once, and point the paths written
    inside her scripts, notes, approvals and skills at the new place. Nothing is overwritten: a name that already
    exists in the new folder stays in the old one, and the old folder is removed only when it's empty."""
    import shutil
    olds = [Path(o) for o in (old if old is not None else _old_roots())]
    new = Path(new or root())
    moved, kept = [], []
    for o in olds:
        try:
            if o.resolve() == new.resolve() or not o.is_dir():
                continue
        except OSError:
            continue
        new.mkdir(parents=True, exist_ok=True)
        for item in sorted(o.iterdir()):
            dest = new / item.name
            if dest.exists():
                kept.append(str(item))
                continue
            try:
                shutil.move(str(item), str(dest))
                moved.append(item.name)
            except OSError as e:
                kept.append(f"{item} ({e})")
        try:
            if not any(o.iterdir()):
                o.rmdir()
        except OSError:
            pass
    if moved:
        fixed = repoint(olds, new)
        logger.info(f"FILES: moved {len(moved)} item(s) from {', '.join(map(str, olds))} to {new}; "
                    f"{fixed} file(s) now point there")
    if kept:
        logger.warning(f"FILES: left in the old folder (the name already exists in {new}): {kept[:5]}")
    return {"moved": moved, "kept": kept, "to": str(new)}


def _variants(p: Path) -> list[str]:
    s = str(p)
    forms = {s, s.rstrip("."), s.replace("\\", "/"), s.rstrip(".").replace("\\", "/")}
    forms |= {f.replace("/", "\\") for f in list(forms)}
    forms |= {f.replace("\\", "\\\\") for f in list(forms) if "\\" in f}      # as written inside JSON
    return sorted(forms, key=len, reverse=True)


def repoint(olds: list, new: Path) -> int:
    """Old folder paths written in her text files, approvals and pc skills -> the new folder."""
    n = 0
    pairs = []
    for o in olds:
        for v in _variants(Path(o)):
            if "\\\\" in v:
                pairs.append((v, str(new).replace("\\", "\\\\")))
            elif "/" in v and "\\" not in v:
                pairs.append((v, str(new).replace("\\", "/")))
            else:
                pairs.append((v, str(new)))
    targets = [p for p in Path(new).rglob("*") if p.is_file() and p.suffix.lower() in TEXT
               and p.stat().st_size < 2_000_000]
    targets += [APPROVALS] + list(Path("data/skills_made").glob("*/skill.json"))
    for p in targets:
        try:
            t = p.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        u = t
        for a, b in pairs:
            u = u.replace(a, b)
        if u != t:
            p.write_text(u, encoding="utf-8")
            n += 1
    return n


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


# import name -> the pip package (the ones that differ)
PIP_NAMES = {"cv2": "opencv-python", "PIL": "pillow", "sklearn": "scikit-learn", "yaml": "pyyaml", "skimage":
             "scikit-image", "bs4": "beautifulsoup4", "dotenv": "python-dotenv", "serial": "pyserial", "usb": "pyusb",
             "Crypto": "pycryptodome", "fitz": "pymupdf", "docx": "python-docx", "pptx": "python-pptx",
             "mediapipe": "mediapipe", "onnxruntime": "onnxruntime", "face_recognition": "face-recognition",
             "sounddevice": "sounddevice", "speech_recognition": "SpeechRecognition", "win32api": "pywin32"}
_PKG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]*(?:\[[A-Za-z0-9_,\-]+\])?(?:[=<>~!]=?[A-Za-z0-9_.*+\-]+)?$")


def install(packages: list) -> dict:
    """v1.2: pip install into HER environment (data/agent/venv; made if missing), never his own Python. Nov 28: a
    task typed 'pip install opencv-python numpy' into his terminal and upgraded numpy in his conda base."""
    pk = [str(p).strip() for p in packages or [] if str(p).strip()]
    if not pk or len(pk) > 10 or not all(len(p) <= 80 and _PKG.match(p) for p in pk):
        return {"ok": False, "error": "give 1 to 10 package names like opencv-python or numpy==1.26.4"}
    from core.agent.pc import AGENT_DIR, VENV
    AGENT_DIR.mkdir(parents=True, exist_ok=True)
    if not VENV.exists():
        r = subprocess.run([sys.executable, "-m", "venv", str(VENV)], capture_output=True, text=True, timeout=300,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if r.returncode != 0:
            return {"ok": False, "error": f"couldn't make her environment: {(r.stderr or '')[-400:]}"}
    try:
        r = subprocess.run([python(), "-m", "pip", "install", "--disable-pip-version-check", "-q", *pk],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=900,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "pip took longer than 15 minutes"}
    out = ((r.stdout or "") + (r.stderr or ""))[-1500:]
    logger.info(f"FILES: pip install {' '.join(pk)} into her environment: {'ok' if r.returncode == 0 else 'failed'}")
    return {"ok": r.returncode == 0, "packages": pk, "where": "her own environment (data/agent/venv)",
            **({"output": out} if r.returncode else {})}


def _last_error(stderr: str) -> str:
    lines = [x.strip() for x in (stderr or "").splitlines() if x.strip()]
    for ln in reversed(lines):
        if re.match(r"^[A-Za-z_][\w.]*(?:Error|Exception|Exit|Interrupt)\b", ln) or ln.startswith("error:"):
            return ln[:300]
    return lines[-1][:300] if lines else ""


def missing_module(stderr: str) -> str:
    m = re.search(r"No module named '([\w.]+)'", stderr or "")
    return m.group(1).split(".")[0] if m else ""


def run_script(path: str, args: Optional[list] = None, timeout: float = 120, stdin: str = "",
               auto_install: bool = True) -> dict:
    """Run one of her .py scripts (approved by the caller). -> {"ok", "exit", "error"?, "output", "json"?}.
    v1.2: a missing module is installed into her environment once and the script runs again."""
    p = inside(path)
    if not p or not p.is_file():
        return {"ok": False, "error": f"{path} isn't a file in your E.V.A. folder"}
    if p.suffix.lower() != ".py":
        return {"ok": False, "error": "only Python scripts (.py) run from here"}
    argv = [str(a) for a in (args or [])][:30]
    if any("\n" in a or len(a) > 500 for a in argv):
        return {"ok": False, "error": "arguments must be short, one line each"}
    approve(p)
    installed: list[str] = []
    for _attempt in range(3):
        t0 = time.time()
        env = {**os.environ, "EVA_FILES": str(root()), "PYTHONIOENCODING": "utf-8"}
        try:
            r = subprocess.run([python(), "-X", "utf8", str(p), *argv], cwd=str(p.parent), capture_output=True,
                               text=True, encoding="utf-8", errors="replace",
                               timeout=max(5, min(float(timeout or 120), 600)), input=stdin or None, env=env,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": f"it ran longer than {timeout:.0f} s and was stopped"}
        except OSError as e:
            return {"ok": False, "error": str(e)}
        mod = missing_module(r.stderr) if r.returncode != 0 else ""
        if mod and auto_install and mod not in installed and len(installed) < 3:
            pkg = PIP_NAMES.get(mod, mod.replace("_", "-"))
            got = install([pkg])
            installed.append(mod)
            if got.get("ok"):
                continue
            return {"ok": False, "exit": r.returncode, "error": f"it needs {pkg}, and installing it into your own "
                                                                f"environment failed", "pip": got.get("output", "")[-800:]}
        break
    out = (r.stdout or "")[-4000:]
    res = {"ok": r.returncode == 0, "exit": r.returncode, "seconds": round(time.time() - t0, 1)}
    if r.returncode != 0:
        res["error"] = _last_error(r.stderr) or f"exit code {r.returncode}"
    if installed:
        res["installed"] = [PIP_NAMES.get(m, m) for m in installed]
    res["output"] = out
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
    out = {"result": json.dumps(data, ensure_ascii=False, default=str),
           **{k: v for k, v in extra.items() if v is not None}}
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
    return _r({"script": str(p), **out})


def tool_install_package(packages=None, **_):
    pk = packages if isinstance(packages, list) else re.split(r"[\s,]+", str(packages or "").strip())
    out = install([x for x in pk if x])
    return _r(out, changes=[{"what": "her Python environment", "before": "", "after": "+ " + ", ".join(out["packages"])}]
              if out.get("ok") else None)


def describe_run(args: dict) -> str:
    return f"run {Path(str(args.get('path') or 'the script')).name} on your PC for the first time"


SCHEMAS = [
    {"type": "function", "function": {
        "name": "my_files", "description": "Find files you or your tasks made, in his E.V.A. folder "
                                           "(E.V.A. Files, next to your code; one folder per task): 'where is the face script?'",
        "parameters": {"type": "object", "properties": {"query": {"type": "string"}}}}},
    {"type": "function", "function": {
        "name": "read_my_file", "description": "Read a text file from his E.V.A. folder (a script, SKILL.md, notes).",
        "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}},
    {"type": "function", "function": {
        "name": "install_package", "description": "pip install Python packages into YOUR OWN environment "
                                                  "(data/agent/venv), the one run_my_script and your skills use. Never "
                                                  "his own Python. run_my_script already installs a missing module by "
                                                  "itself; use this for anything else a script needs.",
        "parameters": {"type": "object", "properties": {"packages": {"type": "array", "items": {"type": "string"}}},
                       "required": ["packages"]}}},
    {"type": "function", "function": {
        "name": "run_my_script", "description": "Run a Python script from his E.V.A. folder on his PC, with your own "
                                                "Python environment (camera, network and files work). A script's "
                                                "first run needs his yes; then it runs freely until it changes.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"}, "args": {"type": "array", "items": {"type": "string"}},
            "timeout": {"type": "number"}}, "required": ["path"]}}},
]
FUNCTIONS = {"my_files": tool_my_files, "read_my_file": tool_read_my_file, "run_my_script": tool_run_my_script,
             "install_package": tool_install_package}
ACTIONS = {"run_my_script", "install_package"}
