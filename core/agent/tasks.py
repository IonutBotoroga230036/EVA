"""Tasks (v0.4): a long job with a goal, a plan, a folder of its own, and a memory that survives restarts.

data/tasks/<id>/task.json      the task: goal, status, plan, log, consent log, cost, what it's waiting for
data/tasks/<id>/messages.json  the agent's conversation with Claude, so it resumes exactly where it stopped
data/tasks/<id>/work/          its workspace: every file it makes lives here until you let it out

status: queued | running | waiting (for your answer or yes) | paused | done | failed | cancelled
"""

from __future__ import annotations

import json
import re
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

ROOT = Path("data/tasks")
ACTIVE = ("queued", "running", "waiting", "paused")


@dataclass
class Task:
    id: str
    goal: str
    title: str = ""
    status: str = "queued"
    created: float = field(default_factory=time.time)
    updated: float = field(default_factory=time.time)
    plan: list = field(default_factory=list)          # [{"step": "...", "done": bool, "result": "..."}]
    log: list = field(default_factory=list)           # [{"ts", "what"}]: what happened, for "how's it going?"
    consents: list = field(default_factory=list)      # [{"ts", "kind", "what", "decision"}]
    grants: dict = field(default_factory=dict)        # kinds you said "yes, for this task" to
    waiting: dict = field(default_factory=dict)       # {"kind": "question"|"consent", "text", "tool_use_id", ...}
    cost_eur: float = 0.0
    budget_eur: float = 1.0
    steps: int = 0
    summary: str = ""
    files: list = field(default_factory=list)         # files it delivered (outside the workspace)
    error: str = ""
    system: str = ""            # Nov 25: the instructions, written once at the start and resent unchanged
    drop_thinking: bool = False  # earlier thinking no longer matches (trimmed or changed): the API drops it

    def note(self, what: str) -> None:
        self.log.append({"ts": time.time(), "what": what[:300]})
        self.log = self.log[-200:]
        self.updated = time.time()


def short_title(goal: str) -> str:
    """'design me a guitar stand I can 3D print' -> 'guitar stand'."""
    g = re.sub(r"^(?:hey\s+)?(?:eva[,.]?\s*)?(?:can you |could you |please |i need you to |i want you to )*", "",
               goal.strip(), flags=re.I)
    g = re.sub(r"^(?:design|build|make|create|write|plan|research|find|draw|model|figure out|help me with)"
               r"(?:\s+(?:me|for me|us))?\s+(?:a |an |the |some |my )?", "", g, flags=re.I)
    g = re.split(r"\s(?:so (?:that )?|that i can|which|for my|to print|i can|that can)\b|[,.;]", g, maxsplit=1,
                 flags=re.I)[0]
    words = g.split()[:5]
    return " ".join(words).strip(" ,.") or "task"


class Store:
    def __init__(self, root: Path = ROOT):
        self.root = Path(root)
        self._lock = threading.RLock()

    def folder(self, tid: str) -> Path:
        return self.root / tid

    def workspace(self, tid: str) -> Path:
        d = self.folder(tid) / "work"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def new(self, goal: str, budget: float = 1.0) -> Task:
        t = Task(id=uuid.uuid4().hex[:8], goal=" ".join(goal.split())[:1000], title=short_title(goal),
                 budget_eur=float(budget))
        t.note("task created")
        self.save(t)
        self.workspace(t.id)
        return t

    def save(self, t: Task) -> None:
        with self._lock:
            t.updated = time.time()
            d = self.folder(t.id)
            d.mkdir(parents=True, exist_ok=True)
            tmp = d / "task.json.tmp"
            tmp.write_text(json.dumps(asdict(t), indent=1), encoding="utf-8")
            tmp.replace(d / "task.json")

    def get(self, tid: str) -> Optional[Task]:
        p = self.folder(tid) / "task.json"
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return Task(**{k: v for k, v in raw.items() if k in Task.__dataclass_fields__})

    def all(self) -> list[Task]:
        if not self.root.exists():
            return []
        out = [self.get(p.name) for p in self.root.iterdir() if (p / "task.json").exists()]
        return sorted([t for t in out if t], key=lambda t: t.created)

    def active(self) -> list[Task]:
        return [t for t in self.all() if t.status in ACTIVE]

    def find(self, words: str = "") -> Optional[Task]:
        """The task you mean: by words in its title or goal, else the one that changed last (active first)."""
        tasks = self.all()
        if not tasks:
            return None
        w = [x for x in re.findall(r"[a-z0-9]+", (words or "").lower()) if len(x) > 2 and x not in (
            "the", "task", "how", "going", "with", "what", "about", "status", "progress", "is", "my", "that")]
        if w:
            scored = [(sum(x in f"{t.title} {t.goal}".lower() for x in w), t.updated, t) for t in tasks]
            best = max(scored, key=lambda s: (s[0], s[1]))
            if best[0] > 0:
                return best[2]
        live = [t for t in tasks if t.status in ACTIVE]
        return max(live or tasks, key=lambda t: t.updated)

    # the agent's conversation with Claude
    def messages(self, tid: str) -> list:
        try:
            return json.loads((self.folder(tid) / "messages.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []

    def save_messages(self, tid: str, msgs: list) -> None:
        with self._lock:
            p = self.folder(tid) / "messages.json"
            tmp = p.with_suffix(".tmp")
            tmp.write_text(json.dumps(msgs), encoding="utf-8")
            tmp.replace(p)


_store: Optional[Store] = None


def get_store() -> Store:
    global _store
    if _store is None:
        _store = Store()
    return _store
