"""
Shopping list (v0.2.5 milestone 5), kept as an Obsidian note so it's on every device the vault syncs to.

    <vault>/Shopping list.md
    # Shopping list
    - [ ] milk
    - [x] eggs          (ticked off; "clear the ticked items" removes these)

Used by the shopping skill ("add milk and eggs to my shopping list") and by the Routines panel.
Everything stays inside the vault (skills/obsidian/tools.py decides where the vault is).
"""

from __future__ import annotations

import re
import threading
from pathlib import Path
from typing import Optional

TITLE = "Shopping list"
_LINE = re.compile(r"^\s*[-*]\s*\[( |x|X)\]\s*(.+?)\s*$")
_LOCK = threading.Lock()
_FILLER = re.compile(r"^(?:some|a|an|the|more|few|couple of|bit of)\s+", re.I)


def path() -> Path:
    from skills.obsidian.tools import vault_root
    return vault_root() / f"{TITLE}.md"


def items() -> list[dict]:
    try:
        text = path().read_text(encoding="utf-8")
    except OSError:
        return []
    out = []
    for line in text.splitlines():
        m = _LINE.match(line)
        if m:
            out.append({"text": m.group(2), "done": m.group(1).lower() == "x"})
    return out


def _write(rows: list[dict]) -> None:
    p = path()
    p.parent.mkdir(parents=True, exist_ok=True)
    body = "\n".join(f"- [{'x' if r['done'] else ' '}] {r['text']}" for r in rows)
    p.write_text(f"# {TITLE}\n\n{body}\n" if body else f"# {TITLE}\n", encoding="utf-8")


def split_items(text: str) -> list[str]:
    """'milk, eggs and some bread' -> ['milk', 'eggs', 'bread']."""
    parts = re.split(r"\s*(?:,|;|\band\b|&|\bplus\b)\s*", text or "", flags=re.I)
    out = []
    for p in parts:
        p = _FILLER.sub("", p.strip(" .!?")).strip()
        if p and len(p) <= 80:
            out.append(p)
    return out


def _find(rows: list[dict], name: str) -> Optional[int]:
    n = name.lower().strip()
    for i, r in enumerate(rows):
        if r["text"].lower() == n:
            return i
    for i, r in enumerate(rows):
        if n and (n in r["text"].lower() or r["text"].lower() in n):
            return i
    return None


def add(names: list[str]) -> tuple[list[str], list[str]]:
    """(added, already_there). An item that was ticked off becomes open again."""
    with _LOCK:
        rows, added, already = items(), [], []
        for name in names:
            i = next((k for k, r in enumerate(rows) if r["text"].lower() == name.lower()), None)
            if i is None:
                rows.append({"text": name, "done": False})
                added.append(name)
            elif rows[i]["done"]:
                rows[i]["done"] = False
                added.append(rows[i]["text"])
            else:
                already.append(rows[i]["text"])
        _write(rows)
        return added, already


def remove(name: str) -> Optional[str]:
    with _LOCK:
        rows = items()
        i = _find(rows, name)
        if i is None:
            return None
        gone = rows.pop(i)["text"]
        _write(rows)
        return gone


def set_done(name: str, done: bool = True) -> Optional[str]:
    with _LOCK:
        rows = items()
        i = _find(rows, name)
        if i is None:
            return None
        rows[i]["done"] = bool(done)
        _write(rows)
        return rows[i]["text"]


def clear(done_only: bool = True) -> int:
    with _LOCK:
        rows = items()
        keep = [r for r in rows if not r["done"]] if done_only else []
        _write(keep)
        return len(rows) - len(keep)


def spoken(rows: list[dict]) -> str:
    open_ = [r["text"] for r in rows if not r["done"]]
    if not open_:
        return "Your shopping list is empty, sir."
    if len(open_) == 1:
        return f"Just one thing on your shopping list, sir: {open_[0]}."
    return f"On your shopping list, sir: {', '.join(open_[:-1])} and {open_[-1]}."
