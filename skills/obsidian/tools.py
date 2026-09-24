"""
Obsidian skill: a structured second brain as plain markdown files.

Local only, no network. Every path is resolved INSIDE the vault; titles are
sanitized for Windows (no ../ escapes, no reserved names like CON or NUL).

Vault layout (created on first use):
    Daily/2026-09-25.md      quick notes, one timestamped line each
    Inbox/  Projects/  People/  Ideas/
Each new note gets YAML frontmatter (created, tags) so Obsidian can index it.

Vault location: env EVA_VAULT, else obsidian.vault_path in config/settings.yaml,
else ./data/vault (git-ignored).
"""

import json
import os
import re
from datetime import datetime
from pathlib import Path

from core.settings import get_settings

FOLDERS = ["Inbox", "Projects", "People", "Ideas", "Daily"]
SKIP_DIRS = {".obsidian", ".trash", ".git"}
MAX_READ = 2500               # characters of a note handed to the model
MAX_FILE = 300_000            # skip huge files when searching
_BAD = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}
_WORDS = re.compile(r"[a-z0-9]+")
_STOP = {"the", "a", "an", "about", "my", "note", "notes", "on", "of", "for", "to", "and", "what", "did", "i"}


def vault_root() -> Path:
    cfg = get_settings().get("obsidian", {})
    root = Path(os.environ.get("EVA_VAULT") or cfg.get("vault_path") or "./data/vault").expanduser()
    root.mkdir(parents=True, exist_ok=True)
    return root.resolve()


def _daily_folder() -> str:
    return get_settings().get("obsidian", {}).get("daily_folder", "Daily")


def safe_title(title: str) -> str:
    t = _BAD.sub(" ", title or "")
    t = " ".join(t.split()).strip(" .")[:100]
    if not t:
        t = "Untitled"
    if t.lower() in _RESERVED:
        t = f"Note {t}"
    return t


def _inside(root: Path, p: Path) -> Path:
    p = p.resolve()
    if p != root and root not in p.parents:
        raise PermissionError("path escapes the vault")
    return p


def _note_path(root: Path, title: str, folder: str) -> Path:
    folder = folder if folder in FOLDERS else "Inbox"
    return _inside(root, root / folder / f"{safe_title(title)}.md")


def _all_notes(root: Path):
    for p in root.rglob("*.md"):
        if not SKIP_DIRS.intersection(p.relative_to(root).parts):
            yield p


def _find(root: Path, title: str) -> Path | None:
    want = safe_title(title).lower()
    notes = list(_all_notes(root))
    for p in notes:                                  # exact title first
        if p.stem.lower() == want:
            return p
    words = set(_WORDS.findall(want)) - _STOP
    best, score = None, 0
    for p in notes:                                  # then the title sharing most words
        s = len(words & set(_WORDS.findall(p.stem.lower())))
        if s > score:
            best, score = p, s
    return best


def _frontmatter(title: str) -> str:
    return f"---\ncreated: {datetime.now():%Y-%m-%d %H:%M}\ntags: [eva]\n---\n# {title}\n\n"


def _ok(data: dict, widget_title: str, text: str) -> dict:
    return {"result": json.dumps(data), "widget": {"kind": "note", "title": widget_title, "text": text[:240]}}


def _err(msg: str) -> dict:
    return {"result": json.dumps({"error": msg})}


# ---------------------------------------------------------------- tools
def obsidian_quick_note(text: str = "", **_):
    text = " ".join((text or "").split()).strip()
    if not text:
        return _err("nothing to note")
    root = vault_root()
    now = datetime.now()
    day = _inside(root, root / _daily_folder() / f"{now:%Y-%m-%d}.md")
    day.parent.mkdir(parents=True, exist_ok=True)
    if not day.exists():
        day.write_text(_frontmatter(f"{now:%A %d %B %Y}").replace("tags: [eva]", "tags: [daily, eva]"),
                       encoding="utf-8")
    with day.open("a", encoding="utf-8") as f:
        f.write(f"- {now:%H:%M} {text}\n")
    return _ok({"status": "noted", "note": f"{_daily_folder()}/{day.name}", "text": text},
               f"Noted · {now:%d %b %H:%M}", text)


def obsidian_write(title: str = "", content: str = "", folder: str = "Inbox", **_):
    if not (title or "").strip():
        return _err("a note needs a title")
    if not (content or "").strip():
        return _err("nothing to write")
    root = vault_root()
    existing = _find(root, title)
    same = existing is not None and existing.stem.lower() == safe_title(title).lower()
    path = existing if same else _note_path(root, title, folder)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        with path.open("a", encoding="utf-8") as f:
            f.write(f"\n{content.strip()}\n")
        status = "appended"
    else:
        path.write_text(_frontmatter(path.stem) + content.strip() + "\n", encoding="utf-8")
        status = "created"
    rel = path.relative_to(root).as_posix()
    return _ok({"status": status, "note": rel}, f"{status.capitalize()} · {path.stem}", content)


def obsidian_search(query: str = "", **_):
    words = set(_WORDS.findall((query or "").lower())) - _STOP
    if not words:
        return _err("say what to search for")
    root = vault_root()
    hits = []
    for p in _all_notes(root):
        try:
            if p.stat().st_size > MAX_FILE:
                continue
            body = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        low = body.lower()
        score = 3 * len(words & set(_WORDS.findall(p.stem.lower()))) + sum(low.count(w) for w in words)
        if score:
            lines = [ln for ln in body.splitlines() if ln.strip() and not ln.startswith(("---", "created:", "tags:"))]
            content = [ln for ln in lines if not ln.lstrip().startswith("#")]      # body lines beat the heading
            match = next((ln for ln in content if any(w in ln.lower() for w in words)), None)
            if match is None:                      # only the title matched: show what the note says
                match = content[0] if content else p.stem
            line = match.strip("-# ").strip()
            hits.append((score, p.relative_to(root).as_posix(), line[:200]))
    hits.sort(reverse=True)
    if not hits:
        return {"result": json.dumps({"query": query, "results": [], "note": "no matching notes"})}
    return {"result": json.dumps({"query": query, "results": [{"note": n, "snippet": s} for _, n, s in hits[:5]]})}


def obsidian_read(title: str = "", **_):
    if not (title or "").strip():
        return _err("which note?")
    root = vault_root()
    p = _find(root, title)
    if not p:
        return _err(f"no note called {title}")
    body = p.read_text(encoding="utf-8", errors="ignore")
    body = re.sub(r"^---\n.*?\n---\n", "", body, flags=re.S)       # frontmatter is noise for the model
    return {"result": json.dumps({"note": p.relative_to(root).as_posix(), "content": body[:MAX_READ],
                                  "truncated": len(body) > MAX_READ})}


TOOLS = [
    {"type": "function", "function": {
        "name": "obsidian_quick_note",
        "description": "Add a quick note to today's daily note when the user says 'note that', 'jot down', 'write down'.",
        "parameters": {"type": "object", "properties": {
            "text": {"type": "string", "description": "What to note, in the user's words."}}, "required": ["text"]}}},
    {"type": "function", "function": {
        "name": "obsidian_write",
        "description": "Create a named note, or append to it if it exists, in the Obsidian vault.",
        "parameters": {"type": "object", "properties": {
            "title": {"type": "string", "description": "Short note title, e.g. 'ZippZapp branding'."},
            "content": {"type": "string"},
            "folder": {"type": "string", "enum": ["Inbox", "Projects", "People", "Ideas"]}},
            "required": ["title", "content"]}}},
    {"type": "function", "function": {
        "name": "obsidian_search",
        "description": "Search the user's notes by keywords when they ask what they noted or wrote about something.",
        "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}}},
    {"type": "function", "function": {
        "name": "obsidian_read",
        "description": "Read one note by its title.",
        "parameters": {"type": "object", "properties": {"title": {"type": "string"}}, "required": ["title"]}}},
]
FUNCTIONS = {"obsidian_quick_note": obsidian_quick_note, "obsidian_write": obsidian_write,
             "obsidian_search": obsidian_search, "obsidian_read": obsidian_read}
ACKS = {"obsidian_search": "Checking your notes, sir.", "obsidian_read": "Opening it, sir."}
ACTIONS = ["obsidian_quick_note", "obsidian_write"]
