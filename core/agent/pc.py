"""The PC as a device (v0.4): everything a task does on your computer goes through this one interface.

Today the brain and the PC are the same machine, so LocalPC does the work in-process. When the brain moves to the
cloud, run `python -m interfaces.pc_agent` on the PC: it connects to the brain like the phone does (/ws/pc) and
serves these same calls, and RemotePC forwards them. Nothing in the agent changes.

Every call is about one task's workspace (data/tasks/<id>/work on the PC):
- files never leave it except through export_file (to your output folder, after your yes)
- code runs in a sandbox: its own process, no network, no starting programs, writes and deletes only inside the
  workspace (a Python audit hook, which code can't remove), a time limit, packages from E.V.A.'s own venv
  (data/agent/venv), never yours. Not a wall against deliberately hostile code (ctypes can reach the OS):
  that's what a Docker sandbox is for, next.
- programs run with the workspace as their folder; paths outside it are refused
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

from loguru import logger

AGENT_DIR = Path("data/agent")
VENV = AGENT_DIR / "venv"
PROGRAMS = AGENT_DIR / "programs.json"
MAX_WRITE = 5 * 1024 * 1024
DEFAULT_PROGRAMS = ["openscad", "prusa-slicer", "blender"]     # agent.programs: may run on a task's files
# a program that runs other code would get round the sandbox: never through run_program
NEVER_RUN = {"cmd", "powershell", "pwsh", "bash", "sh", "zsh", "wsl", "python", "python3", "py", "pythonw", "node",
             "deno", "wscript", "cscript", "mshta", "rundll32", "regsvr32", "reg", "regedit", "schtasks", "sc",
             "msiexec", "winget", "choco", "scoop", "pip", "pip3", "curl", "wget", "certutil", "bitsadmin", "ssh",
             "scp", "ftp", "explorer", "taskkill", "del", "rm", "format", "diskpart", "bcdedit", "netsh", "icacls",
             "takeown", "runas", "git", "perl", "ruby", "php", "java", "javaw", "lua", "osascript"}
METHODS = ("list_files", "read_file", "write_file", "run_python", "pip_install", "find_program", "install_program",
           "run_program", "export_file", "info")

SANDBOX = r'''
import os, sys, runpy
ROOT = os.path.normcase(os.path.abspath(os.getcwd()))
_W = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_TRUNC
def _inside(p):
    try:
        return os.path.normcase(os.path.abspath(os.fsdecode(p))).startswith(ROOT)
    except Exception:
        return False
_BLOCK = ("subprocess.Popen", "os.system", "os.exec", "os.spawn", "os.posix_spawn", "os.startfile", "os.fork",
          "socket.connect", "socket.getaddrinfo", "socket.gethostbyname", "winreg.", "_winapi.CreateProcess",
          "pty.spawn", "os.kill")
_PATHS = ("os.remove", "os.unlink", "os.rename", "os.replace", "os.rmdir", "os.mkdir", "os.chmod", "os.chown",
          "os.symlink", "os.link", "os.truncate", "shutil.rmtree", "shutil.move", "shutil.copyfile", "os.utime")
def _guard(event, args):
    if event.startswith(_BLOCK):
        raise PermissionError(f"the task sandbox doesn't allow {event} (no programs, no network)")
    if event == "open" and args:
        path, mode, flags = (list(args) + [None, None, 0])[:3]
        writing = (isinstance(mode, str) and any(c in mode for c in "wax+")) or (mode is None and (flags or 0) & _W)
        if writing and isinstance(path, (str, bytes, os.PathLike)) and not _inside(path):
            raise PermissionError("writes outside the task folder are blocked")
    elif event.startswith(_PATHS):
        for a in args[:2]:
            if isinstance(a, (str, bytes, os.PathLike)) and not _inside(a):
                raise PermissionError(f"{event} outside the task folder is blocked")
sys.addaudithook(_guard)
sys.path.insert(0, ROOT)
sys.argv = [sys.argv[1]]
runpy.run_path(sys.argv[0], run_name="__main__")
'''


class PCError(RuntimeError):
    pass


def _cfg() -> dict:
    try:
        from core.settings import get_settings
        return get_settings().get("agent") or {}
    except Exception:
        return {}


def _workspace(tid: str) -> Path:
    from core.agent.tasks import get_store
    if not re.fullmatch(r"[a-f0-9]{8}", tid or ""):
        raise PCError("bad task id")
    return get_store().workspace(tid).resolve()


def _inside(ws: Path, rel: str) -> Path:
    p = (ws / (rel or "").lstrip("/\\")).resolve()
    if p != ws and ws not in p.parents:
        raise PCError(f"{rel} is outside the task folder")
    return p


def _files(ws: Path) -> list[dict]:
    out = []
    for p in sorted(ws.rglob("*")):
        if p.is_file() and not any(part.startswith("_eva_") for part in p.relative_to(ws).parts):
            out.append({"path": p.relative_to(ws).as_posix(), "bytes": p.stat().st_size})
        if len(out) >= 300:
            break
    return out


def _snapshot(ws: Path) -> dict:
    return {f["path"]: f["bytes"] for f in _files(ws)}


def _changed(before: dict, ws: Path) -> list[str]:
    now = _snapshot(ws)
    return [k for k, v in now.items() if before.get(k) != v]


def _python() -> str:
    exe = VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    return str(exe) if exe.exists() else sys.executable


def _run(cmd: list[str], cwd: Path, timeout: int, env: Optional[dict] = None) -> dict:
    try:
        p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=timeout, env=env)
        return {"exit": p.returncode, "stdout": p.stdout[-6000:], "stderr": p.stderr[-3000:]}
    except subprocess.TimeoutExpired:
        return {"exit": -1, "stdout": "", "stderr": f"stopped after {timeout} s"}
    except OSError as e:
        return {"exit": -1, "stdout": "", "stderr": str(e)}


def _known_programs() -> dict:
    try:
        return json.loads(PROGRAMS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


class LocalPC:
    """The work itself, on this machine."""
    where = "this PC"

    def info(self) -> dict:
        return {"os": sys.platform, "python": sys.version.split()[0], "venv": VENV.exists(),
                "winget": bool(shutil.which("winget")), "where": self.where}

    def list_files(self, task: str) -> dict:
        return {"files": _files(_workspace(task))}

    def read_file(self, task: str, path: str, max_chars: int = 20000) -> dict:
        p = _inside(_workspace(task), path)
        if not p.is_file():
            raise PCError(f"{path} doesn't exist in the task folder")
        data = p.read_bytes()
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            return {"path": path, "bytes": len(data), "binary": True}
        return {"path": path, "bytes": len(data), "text": text[:max_chars], "cut": len(text) > max_chars}

    def write_file(self, task: str, path: str, content: str = "", base64_content: str = "") -> dict:
        p = _inside(_workspace(task), path)
        data = base64.b64decode(base64_content) if base64_content else (content or "").encode("utf-8")
        if len(data) > MAX_WRITE:
            raise PCError("files are limited to 5 MB")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        return {"path": p.relative_to(_workspace(task)).as_posix(), "bytes": len(data)}

    def run_python(self, task: str, code: str, timeout: int = 120) -> dict:
        ws = _workspace(task)
        n = int(time.time() * 1000) % 10_000_000
        script, runner = ws / f"_eva_run_{n}.py", ws / "_eva_sandbox.py"
        script.write_text(code, encoding="utf-8")
        runner.write_text(SANDBOX, encoding="utf-8")
        before = _snapshot(ws)
        env = {k: v for k, v in os.environ.items() if k.upper() in ("PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP",
                                                                     "HOME", "USERPROFILE", "LANG")}
        tmp = ws / "_eva_tmp"
        tmp.mkdir(exist_ok=True)
        env.update({"PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1", "MPLBACKEND": "Agg",
                    "TEMP": str(tmp), "TMP": str(tmp), "TMPDIR": str(tmp), "MPLCONFIGDIR": str(tmp)})
        out = _run([_python(), "-I", runner.name, script.name], ws, max(5, min(int(timeout), 900)), env)
        for f in (script, runner):
            try:
                f.unlink()
            except OSError:
                pass
        out["new_files"] = _changed(before, ws)
        return out

    def pip_install(self, packages: list) -> dict:
        pk = [str(p).strip() for p in packages or [] if str(p).strip()]
        if not pk or len(pk) > 10 or not all(re.fullmatch(r"[A-Za-z0-9_.\-\[\],=<>~!]{1,80}", p) for p in pk):
            raise PCError("give 1 to 10 package names like numpy-stl or trimesh==4.5.0")
        AGENT_DIR.mkdir(parents=True, exist_ok=True)
        if not VENV.exists():
            made = _run([sys.executable, "-m", "venv", str(VENV)], AGENT_DIR.parent, 300)
            if made["exit"] != 0:
                return {"ok": False, "output": made["stderr"]}
        out = _run([_python(), "-m", "pip", "install", "--disable-pip-version-check", "-q", *pk], AGENT_DIR, 900)
        return {"ok": out["exit"] == 0, "output": (out["stdout"] + out["stderr"])[-2000:], "packages": pk}

    def find_program(self, name: str) -> dict:
        n = re.sub(r"[^\w.+-]", "", name or "")
        if not n:
            return {"found": False}
        known = _known_programs().get(n.lower())
        if known and Path(known).exists():
            return {"found": True, "path": known}
        hit = shutil.which(n) or shutil.which(n + ".exe")
        if not hit and os.name == "nt":
            for base in filter(None, (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)"),
                                      os.environ.get("LOCALAPPDATA") and str(Path(os.environ["LOCALAPPDATA"]) / "Programs"))):
                for pattern in (f"*/{n}.exe", f"*/*/{n}.exe", f"*/bin/{n}.exe"):
                    found = sorted(Path(base).glob(pattern))
                    if found:
                        hit = str(found[0])
                        break
                if hit:
                    break
        return {"found": bool(hit), "path": hit or ""}

    def install_program(self, winget_id: str, exe: str = "") -> dict:
        if not re.fullmatch(r"[A-Za-z0-9][\w.+-]{1,80}", winget_id or ""):
            raise PCError("give a winget package id like OpenSCAD.OpenSCAD")
        if not shutil.which("winget"):
            return {"ok": False, "output": "winget isn't available on this PC (Windows 10/11 'App Installer')"}
        out = _run(["winget", "install", "-e", "--id", winget_id, "--silent", "--accept-package-agreements",
                    "--accept-source-agreements", "--disable-interactivity"], AGENT_DIR.parent, 1800)
        ok = out["exit"] == 0 or "already installed" in (out["stdout"] + out["stderr"]).lower()
        found = self.find_program(exe or winget_id.split(".")[-1].lower()) if ok else {"found": False}
        if found.get("found"):
            known = _known_programs()
            known[(exe or winget_id.split(".")[-1]).lower()] = found["path"]
            AGENT_DIR.mkdir(parents=True, exist_ok=True)
            PROGRAMS.write_text(json.dumps(known, indent=1), encoding="utf-8")
        return {"ok": ok, "output": (out["stdout"] + out["stderr"])[-2000:], "path": found.get("path", "")}

    def run_program(self, task: str, program: str, args: list, timeout: int = 300) -> dict:
        ws = _workspace(task)
        if Path(str(program)).stem.lower() in NEVER_RUN:
            raise PCError(f"{program} can run other code, so tasks never run it (use run_python for code)")
        found = self.find_program(program)
        if not found["found"]:
            raise PCError(f"{program} isn't installed (install_program first)")
        clean = []
        for a in args or []:
            a = str(a)
            if ".." in a.replace("\\", "/").split("/") or re.match(r"^(?:[A-Za-z]:[\\/]|/|\\\\)", a):
                raise PCError(f"argument {a!r} points outside the task folder")
            clean.append(a)
        before = _snapshot(ws)
        out = _run([found["path"], *clean], ws, max(5, min(int(timeout), 1800)))
        out["new_files"] = _changed(before, ws)
        return out

    def export_file(self, task: str, path: str, folder: str = "") -> dict:
        ws = _workspace(task)
        src = _inside(ws, path)
        if not src.is_file():
            raise PCError(f"{path} doesn't exist in the task folder")
        root = Path(os.path.expanduser(_cfg().get("output_dir") or "~/Documents/E.V.A.")).resolve()
        sub = re.sub(r"[^\w\- ]", "", folder or "").strip()[:60] or task
        dest_dir = root / sub
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / src.name
        shutil.copy2(src, dest)
        return {"copied_to": str(dest)}


class RemotePC:
    """The same calls, sent to `python -m interfaces.pc_agent` running on the PC (brain elsewhere)."""
    def __init__(self, link):
        self.link = link
        self.where = f"your PC ({getattr(link, 'name', 'remote')})"

    def _call(self, method: str, **kwargs) -> dict:
        loop = LINK.get("loop")
        if loop is None:
            raise PCError("the PC link has no event loop")
        fut = asyncio.run_coroutine_threadsafe(self.link.request(method, kwargs), loop)
        out = fut.result(int(kwargs.get("timeout", 300)) + 60)
        if not out.get("ok"):
            raise PCError(out.get("error") or "the PC refused")
        return out.get("data") or {}

    def __getattr__(self, method: str):
        if method not in METHODS:
            raise AttributeError(method)
        return lambda *a, **k: self._call(method, **_named(method, a, k))


def _named(method: str, args: tuple, kwargs: dict) -> dict:
    import inspect
    params = [p for p in inspect.signature(getattr(LocalPC, method)).parameters if p != "self"]
    return {**dict(zip(params, args)), **kwargs}


LINK: dict = {"conn": None, "loop": None}


def get_pc():
    """The PC link when `pc_agent` is connected (or agent.pc: remote), otherwise this machine."""
    mode = str(_cfg().get("pc", "auto")).lower()
    conn = LINK.get("conn")
    if conn is not None and mode in ("auto", "remote"):
        return RemotePC(conn)
    if mode == "remote":
        raise PCError("agent.pc is 'remote' but the PC agent isn't connected (python -m interfaces.pc_agent)")
    return LocalPC()


def serve(method: str, kwargs: dict) -> dict:
    """The PC side of a call (used by interfaces/pc_agent.py): {ok, data | error}."""
    if method not in METHODS:
        return {"ok": False, "error": f"unknown call {method}"}
    try:
        return {"ok": True, "data": getattr(LocalPC(), method)(**kwargs)}
    except (PCError, TypeError, ValueError, OSError) as e:
        return {"ok": False, "error": str(e)[:400]}
    except Exception as e:                                   # a crash never takes the PC agent down
        logger.exception(f"PC: {method} crashed")
        return {"ok": False, "error": f"{e.__class__.__name__}: {e}"[:400]}
