"""Her hands on your PC in conversation (v0.5, the mind): what's installed, what opens a file, install an app, open
what a task made.

    "how can I see the files?"   -> check_installed(file_type="stl"): nothing opens STL files
                                 -> "Shall I install PrusaSlicer to open them, or show it here?"
    "yes, install it"            -> install_app (always asks first: it changes your PC)
    "open it"                    -> open_file: the task's STL in its default app, or in the program you name

Only files from a task's own folder or your E.V.A. output folder, only document-like types (never anything that
runs), programs only by name from PATH / Program Files / what she installed, never a shell.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

OPENABLE = {".stl", ".3mf", ".obj", ".step", ".stp", ".scad", ".gcode", ".png", ".jpg", ".jpeg", ".svg", ".gif",
            ".pdf", ".md", ".txt", ".csv", ".xlsx", ".docx", ".pptx", ".html", ".json", ".mp4", ".mp3", ".wav"}


def _r(data: dict, say: str = "") -> dict:
    out = {"result": json.dumps(data, ensure_ascii=False)}
    if say:
        out["say"] = say
    return out


def default_app(ext: str) -> str:
    """What Windows opens a file type with ('' when nothing, or not on Windows)."""
    ext = "." + ext.lower().lstrip(".")
    if sys.platform != "win32":
        return ""
    try:
        import winreg
    except ImportError:
        return ""

    def read(root, path, name=""):
        try:
            with winreg.OpenKey(root, path) as k:
                return str(winreg.QueryValueEx(k, name)[0] or "")
        except OSError:
            return ""
    prog = read(winreg.HKEY_CURRENT_USER,
                rf"Software\Microsoft\Windows\CurrentVersion\Explorer\FileExts\{ext}\UserChoice", "ProgId")
    prog = prog or read(winreg.HKEY_CLASSES_ROOT, ext)
    if not prog:
        return ""
    cmd = read(winreg.HKEY_CLASSES_ROOT, rf"{prog}\shell\open\command")
    m = re.match(r'\s*"([^"]+)"|\s*(\S+)', cmd or "")
    exe = (m.group(1) or m.group(2)) if m else ""
    return Path(exe).stem if exe else prog


def tool_check_installed(program: str = "", file_type: str = "", **_):
    from core.agent.pc import get_pc
    out: dict = {}
    if program:
        found = get_pc().find_program(program)
        out["program"] = {"name": program, "installed": bool(found.get("found")), "path": found.get("path", "")}
    if file_type:
        app = default_app(file_type)
        out["file_type"] = {"type": file_type.lower().lstrip("."), "opens_with": app or None}
    if not out:
        return _r({"error": "say which program or file type"})
    return _r(out)


def tool_install_app(winget_id: str = "", name: str = "", **_):
    from core.agent.pc import PCError, get_pc
    try:
        out = get_pc().install_program(winget_id, "")
    except PCError as e:
        return _r({"error": str(e)}, f"I couldn't install that, sir: {e}")
    label = name or winget_id
    if out.get("ok"):
        return _r({"installed": label, "path": out.get("path", "")}, f"{label} is installed, sir.")
    return _r({"error": (out.get("output") or "")[-300:]}, f"The install of {label} didn't work, sir.")


def describe_install(args: dict) -> str:
    return f"install {args.get('name') or args.get('winget_id')} on your PC with winget ({args.get('winget_id')})"


def _task_file(which: str, file: str):
    """(task, path or folder) from a task's delivered files or its work folder."""
    from core.agent.tasks import get_store
    store = get_store()
    t = store.find(which) if which else None
    if t is None:
        done = [x for x in store.all() if x.status == "done"]
        t = max(done, key=lambda x: x.updated) if done else store.find("")
    if t is None:
        return None, None
    if not file:
        return t, (Path(t.files[0]).parent if t.files else store.workspace(t.id))
    want = Path(file).name.lower()
    for f in t.files:
        if Path(f).name.lower() == want:
            return t, Path(f)
    ws = store.workspace(t.id)
    for p in ws.rglob("*"):
        if p.is_file() and p.name.lower() == want and not any(x.startswith("_eva_") for x in p.relative_to(ws).parts):
            return t, p
    return t, None


CODE = {".py", ".js", ".ts", ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".ps1", ".bat", ".cmd", ".sh",
        ".css", ".java", ".c", ".cpp", ".h", ".rs", ".go", ".ipynb"}       # v1.1: opened in an editor, never run


def editor() -> str | None:
    """VS Code if it's there (Nov 27: 'code' wasn't found by name), else Notepad."""
    import shutil
    for c in ("code", "code.cmd"):
        found = shutil.which(c)
        if found:
            return found
    for base in (os.environ.get("LOCALAPPDATA", ""), os.environ.get("ProgramFiles", ""),
                 os.environ.get("ProgramFiles(x86)", "")):
        exe = Path(base) / ("Programs/Microsoft VS Code/Code.exe" if base == os.environ.get("LOCALAPPDATA") else
                            "Microsoft VS Code/Code.exe")
        if base and exe.exists():
            return str(exe)
    return shutil.which("notepad") or ("notepad.exe" if sys.platform == "win32" else None)


def tool_open_file(file: str = "", task: str = "", program: str = "", **_):
    from core.agent.pc import NEVER_RUN, LocalPC
    t, path = _task_file(task, file)
    if (path is None or not path.exists()) and file:          # v1.1: anything in his E.V.A. folder, by name
        from core.one import files as my
        inside = my.inside(file)
        path = inside if inside and inside.exists() else (my.by_name(file) or path)
    if t is None and path is None:
        return _r({"error": "no tasks"}, "I haven't made any files yet, sir.")
    if path is None or not path.exists():
        where = f"the {t.title}" if t else "your E.V.A. folder"
        return _r({"error": f"{file} isn't among {where}'s files"}, f"I can't find {file} in {where}, sir.")
    if path.is_file() and path.suffix.lower() in CODE:        # code is opened to read, in an editor, never run
        ed = editor()
        if not ed:
            return _r({"error": "no editor found"})
        subprocess.Popen([ed, str(path)], cwd=str(path.parent), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        name = "VS Code" if "code" in Path(ed).name.lower() else Path(ed).stem
        return _r({"opened": str(path), "with": name}, f"Opening {path.name} in {name}, sir.")
    if path.is_file() and path.suffix.lower() not in OPENABLE:
        return _r({"error": f"{path.suffix} files aren't opened from here"})
    if program:
        if Path(program).stem.lower() in NEVER_RUN:
            return _r({"error": f"{program} isn't something I open files with"})
        found = LocalPC().find_program(program)
        if not found.get("found"):
            return _r({"error": f"{program} isn't installed", "installed": False},
                      f"{program} isn't installed, sir.")
        subprocess.Popen([found["path"], str(path)], cwd=str(path.parent if path.is_file() else path),
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return _r({"opened": str(path), "with": program}, f"Opening {path.name} in {program}, sir.")
    starter = getattr(os, "startfile", None)
    if starter is None:
        return _r({"error": "opening files works on Windows"})
    if path.is_file() and not default_app(path.suffix):
        return _r({"error": f"nothing on the PC opens {path.suffix} files", "opens_with": None},
                  f"Nothing on your PC opens {path.suffix} files yet, sir.")
    starter(str(path))
    return _r({"opened": str(path)}, f"Opening {path.name}, sir." if path.is_file() else "Opening the folder, sir.")


SCHEMAS = [
    {"type": "function", "function": {
        "name": "check_installed",
        "description": "Is a program installed on the PC, and/or which app opens a file type (stl, pdf, 3mf...). "
                       "Use it before suggesting how to open or edit something.",
        "parameters": {"type": "object", "properties": {"program": {"type": "string"},
                                                        "file_type": {"type": "string"}}}}},
    {"type": "function", "function": {
        "name": "install_app",
        "description": "Install a program on the PC with winget, after the user's yes. Give the exact winget id "
                       "(e.g. Prusa3D.PrusaSlicer, UltiMaker.Cura, OpenSCAD.OpenSCAD, Blender.Blender).",
        "parameters": {"type": "object", "properties": {"winget_id": {"type": "string"}, "name": {"type": "string"}},
                       "required": ["winget_id"]}}},
    {"type": "function", "function": {
        "name": "open_file",
        "description": "Open a file a task made (or its folder, when file is empty) on the PC: in its default app, "
                       "or in a named installed program ('open the stand in PrusaSlicer').",
        "parameters": {"type": "object", "properties": {
            "file": {"type": "string", "description": "File name, e.g. stand_base.stl; empty = the folder"},
            "task": {"type": "string", "description": "Words naming the task; empty = the latest"},
            "program": {"type": "string", "description": "Optional program to open it with"}}}}},
]
FUNCTIONS = {"check_installed": tool_check_installed, "install_app": tool_install_app, "open_file": tool_open_file}
GUARDS = {"install_app": r"\binstall\w*\b", "open_file": r"\b(open|launch|view|show|see|look)\w*\b",
          "check_installed": r"\b(installed|install|have|got|open|program|app)\w*\b"}
ACTIONS = {"install_app", "open_file"}
CONFIRM = {"install_app": describe_install}
