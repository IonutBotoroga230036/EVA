"""Projects (v0.6): bigger than a task, they last weeks. The desk screen, the GPS app, a trip.

A project keeps what you decide together: the goal, decisions, ideas, open questions (and their answers), research
from tasks, a parts list with prices and links, and its tasks. One project is "active": the mind sees its brief in
every turn, so "how do I connect the display to the Pi?" is answered with everything decided before.

    "start a project: a transparent screen for my desk"   -> project_start
    "let's go with the Pepper's ghost version"             -> project_note(kind="decision")
    "add a Raspberry Pi 5 to the parts, about 80 euros"    -> project_add_part
    "what's still open on the desk screen?"                -> project_show (a card: decisions, questions, parts)
    "put the parts list in a file"                         -> project_export (Documents/E.V.A./projects/<name>)

Stored in data/projects/<id>.json, plain JSON you can read.
"""

from __future__ import annotations

import csv
import json
import re
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

ROOT = Path("data/projects")
KINDS = ("decision", "idea", "question", "answer", "research", "note")
PART_STATUS = ("idea", "chosen", "ordered", "have")


@dataclass
class Project:
    id: str
    name: str
    goal: str = ""
    status: str = "active"
    created: float = field(default_factory=time.time)
    updated: float = field(default_factory=time.time)
    notes: list = field(default_factory=list)       # {"ts", "kind", "text", "open"(questions), "answer"}
    parts: list = field(default_factory=list)       # {"name", "qty", "price_eur", "link", "why", "status"}
    links: list = field(default_factory=list)       # {"title", "url"}
    tasks: list = field(default_factory=list)       # task ids

    def open_questions(self) -> list[tuple[int, dict]]:
        return [(i, n) for i, n in enumerate(self.notes, 1) if n["kind"] == "question" and n.get("open", True)]

    def total_eur(self) -> float:
        return round(sum(float(p.get("price_eur") or 0) * int(p.get("qty") or 1) for p in self.parts), 2)

    def brief(self, limit: int = 1200) -> str:
        """For the mind and for tasks: what this project is and what's been decided."""
        dec = [n["text"] for n in self.notes if n["kind"] == "decision"][-6:]
        qs = [n["text"] for _, n in self.open_questions()][-4:]
        parts = ", ".join(f"{p['name']}" + (f" ({p['status']})" if p.get("status") else "") for p in self.parts[:12])
        lines = [f"Project '{self.name}': {self.goal}".strip()]
        if dec:
            lines.append("Decided: " + "; ".join(dec))
        if qs:
            lines.append("Open questions: " + "; ".join(qs))
        if parts:
            lines.append(f"Parts ({len(self.parts)}, about {self.total_eur():g} euros): {parts}")
        research = [n["text"] for n in self.notes if n["kind"] == "research"][-2:]
        if research:
            lines.append("Research so far: " + " | ".join(r[:300] for r in research))
        return "\n".join(lines)[:limit]


class Store:
    def __init__(self, root: Path = ROOT):
        self.root = Path(root)
        self._lock = threading.RLock()

    def _path(self, pid: str) -> Path:
        return self.root / f"{pid}.json"

    def save(self, p: Project) -> None:
        with self._lock:
            p.updated = time.time()
            self.root.mkdir(parents=True, exist_ok=True)
            tmp = self._path(p.id).with_suffix(".tmp")
            tmp.write_text(json.dumps(asdict(p), indent=1, ensure_ascii=False), encoding="utf-8")
            from core.fsutil import replace as _replace
            _replace(tmp, self._path(p.id))

    def get(self, pid: str) -> Optional[Project]:
        try:
            raw = json.loads(self._path(pid).read_text(encoding="utf-8"))
            return Project(**{k: v for k, v in raw.items() if k in Project.__dataclass_fields__})
        except Exception:
            return None

    def all(self) -> list[Project]:
        if not self.root.exists():
            return []
        out = [self.get(f.stem) for f in self.root.glob("*.json") if f.stem != "active"]
        return sorted([p for p in out if p], key=lambda p: p.updated)

    def new(self, name: str, goal: str = "") -> Project:
        p = Project(id=uuid.uuid4().hex[:8], name=_clean(name)[:60] or "project", goal=" ".join(goal.split())[:600])
        self.save(p)
        self.set_active(p.id)
        return p

    def active(self) -> Optional[Project]:
        try:
            pid = json.loads((self.root / "active.json").read_text(encoding="utf-8")).get("id", "")
            p = self.get(pid)
            return p if p and p.status == "active" else None
        except Exception:
            return None

    def set_active(self, pid: str) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "active.json").write_text(json.dumps({"id": pid}), encoding="utf-8")

    def find(self, words: str = "") -> Optional[Project]:
        """By words of its name or goal; no words: the active one, else the newest."""
        words = (words or "").strip().lower()
        projects = self.all()
        if not words:
            return self.active() or (projects[-1] if projects else None)
        want = set(re.findall(r"[a-z0-9]+", words)) - {"the", "my", "project", "a", "an"}
        best, score = None, 0
        for p in projects:
            have = set(re.findall(r"[a-z0-9]+", f"{p.name} {p.goal}".lower()))
            s = len(want & have)
            if s > score or (s == score and s and best and p.updated > best.updated):
                best, score = p, s
        return best if score else None


def _clean(text: str) -> str:
    return " ".join(re.sub(r"^(?:the|my|a|an)\s+", "", (text or "").strip(), flags=re.I).split()).strip(" .")


_store: Optional[Store] = None


def get_store() -> Store:
    global _store
    if _store is None:
        _store = Store()
    return _store


def reset_store(root: Optional[Path] = None) -> None:
    global _store
    _store = Store(root) if root else None


# ---------------------------------------------------------------- tools
def _r(data: dict, say: str = "", widget: Optional[dict] = None, **extra) -> dict:
    out = {"result": json.dumps(data, ensure_ascii=False), **extra}
    if say:
        out["say"] = say
        out["exact"] = True          # Nov 25: her own line; the small local model rephrased it into "I've initiated..."
    if widget:
        out["widget"] = widget
    return out


_listed: list[str] = []          # the order of the last "which projects?", for "delete the first three"


def _pick(project: str) -> Optional[Project]:
    project = (project or "").strip()
    if re.fullmatch(r"[a-f0-9]{8}", project):                 # an id (the mind passes them along)
        return get_store().get(project)
    n = _number(project)
    if n and 1 <= n <= len(_listed):
        return get_store().get(_listed[n - 1])
    return get_store().find(project)


_ORD = {"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6, "last": -1}


def _number(text: str) -> int:
    t = (text or "").strip().lower()
    if t.isdigit():
        return int(t)
    for w, n in _ORD.items():
        if re.search(rf"\b{w}\b", t):
            return len(_listed) if n == -1 else n
    return 0


def widget(p: Project) -> dict:
    return {"kind": "project", "id": p.id, "name": p.name, "goal": p.goal, "status": p.status,
            "decisions": [n["text"] for n in p.notes if n["kind"] == "decision"][-10:],
            "questions": [{"n": i, "text": n["text"]} for i, n in p.open_questions()][-8:],
            "ideas": [n["text"] for n in p.notes if n["kind"] == "idea"][-6:],
            "research": [n["text"][:400] for n in p.notes if n["kind"] == "research"][-3:],
            "parts": p.parts[:40], "total_eur": p.total_eur(), "links": p.links[-8:], "tasks": len(p.tasks)}


def tool_project_start(name: str = "", goal: str = "", **_):
    name = _clean(name)
    if len(name) < 2:
        return _r({"error": "what should the project be called?"}, "What should we call the project, sir?")
    old = get_store().find(name)
    if old and old.name.lower() == name.lower():
        get_store().set_active(old.id)
        return _r({"project": old.id, "existed": True}, f"We already have the {old.name} project, sir. It's the "
                  f"active one again.", widget(old))
    p = get_store().new(name, goal)
    # Nov 25: "nothing was really going on": a project now starts with an offer to do the first real work
    goal_text = goal or name
    research = {"tool": "task_start", "desc": f"research how to build the {p.name} and draft a first parts list",
                "args": {"goal": f"For the project '{p.name}' ({goal_text}): research the ways to build it, compare "
                                 f"them (cost, difficulty, how it looks), recommend one, and draft a parts list with "
                                 f"prices and shop links in the Netherlands. Put decisions-to-make as questions, "
                                 f"and the parts on the project.", "project": p.id}}
    return _r({"project": p.id, "name": p.name}, f"Started the {p.name} project, sir. Shall I research how to build "
              f"it and draft a first parts list?", widget(p), confirm_next=research)


def tool_project_note(text: str = "", kind: str = "note", project: str = "", **_):
    p = _pick(project)
    if not p:
        return _r({"error": "no project yet"}, "There's no project yet, sir. Shall I start one?")
    text = " ".join((text or "").split())[:1500]
    if not text:
        return _r({"error": "nothing to note"})
    kind = kind if kind in KINDS else "note"
    entry = {"ts": time.time(), "kind": kind, "text": text}
    if kind == "question":
        entry["open"] = True
    p.notes.append(entry)
    get_store().save(p)
    words = {"decision": "Noted the decision", "idea": "Added the idea", "question": "Added the question",
             "research": "Saved the research", "answer": "Noted", "note": "Noted"}[kind]
    return _r({"project": p.id, "kind": kind, "n": len(p.notes)}, f"{words} to the {p.name}, sir.")


def tool_project_answer(question: int = 0, answer: str = "", project: str = "", **_):
    p = _pick(project)
    if not p:
        return _r({"error": "no project"})
    open_qs = p.open_questions()
    hit = next((n for i, n in open_qs if i == int(question or 0)), None) or (open_qs[-1][1] if open_qs else None)
    if not hit:
        return _r({"error": "no open questions"}, f"There are no open questions on the {p.name}, sir.")
    hit["open"], hit["answer"] = False, " ".join((answer or "").split())[:1000]
    p.notes.append({"ts": time.time(), "kind": "answer", "text": f"{hit['text']} -> {hit['answer']}"})
    get_store().save(p)
    left = len(p.open_questions())
    return _r({"project": p.id, "open_left": left}, f"Answered, sir. {left} open question{'s' if left != 1 else ''} "
              f"left on the {p.name}.")


def tool_project_add_part(name: str = "", qty: int = 1, price_eur: float = 0.0, link: str = "", why: str = "",
                          status: str = "", project: str = "", **_):
    p = _pick(project)
    if not p:
        return _r({"error": "no project"}, "There's no project yet, sir. Shall I start one?")
    name = " ".join((name or "").split())[:120]
    if not name:
        return _r({"error": "which part?"})
    part = next((x for x in p.parts if x["name"].lower() == name.lower()), None)
    new = part is None
    part = part or {"name": name, "qty": 1, "price_eur": None, "link": "", "why": "", "status": "idea"}
    if qty:
        part["qty"] = max(1, int(qty))
    if price_eur:
        part["price_eur"] = round(float(price_eur), 2)
    if link and link.startswith(("http://", "https://")):
        part["link"] = link[:500]
    if why:
        part["why"] = why[:300]
    if status in PART_STATUS:
        part["status"] = status
    if new:
        p.parts.append(part)
    get_store().save(p)
    total = p.total_eur()
    return _r({"project": p.id, "part": part, "parts": len(p.parts), "total_eur": total},
              f"{'Added' if new else 'Updated'} {name} on the {p.name} parts list, sir. About {total:g} euros so far.")


def tool_project_show(project: str = "", **_):
    p = _pick(project)
    if not p:
        return _r({"error": "no project"}, "There's no project yet, sir.")
    qs = len(p.open_questions())
    dec = sum(1 for n in p.notes if n["kind"] == "decision")
    say = (f"The {p.name}: {dec} decision{'s' if dec != 1 else ''}, {qs} open question{'s' if qs != 1 else ''}, "
           f"{len(p.parts)} part{'s' if len(p.parts) != 1 else ''}" +
           (f" at about {p.total_eur():g} euros" if p.total_eur() else "") + ". It's in the window, sir.")
    return _r({"project": p.id, "brief": p.brief()}, say, widget(p))


def tool_project_list(**_):
    ps = get_store().all()
    if not ps:
        return _r({"projects": []}, "No projects yet, sir.")
    act = get_store().active()
    _listed[:] = [p.id for p in ps]
    names = "; ".join(f"{i}, {p.name}" + (" (the active one)" if act and p.id == act.id else "")
                      for i, p in enumerate(ps, 1))
    return _r({"projects": [{"n": i, "id": p.id, "name": p.name, "status": p.status} for i, p in enumerate(ps, 1)]},
              f"Your projects, sir: {names}.")


def tool_project_delete(projects: Optional[list] = None, project: str = "", **_):
    """Delete projects by name or by their number from the last list ('the first three'). Always asks first."""
    want = [str(x) for x in (projects or [])] + ([project] if project else [])
    hits, seen = [], set()
    for w in want:
        p = _pick(w)
        if p and p.id not in seen:
            hits.append(p)
            seen.add(p.id)
    if not hits:
        return _r({"error": "no such project"}, "I couldn't tell which projects you mean, sir.")
    for p in hits:
        try:
            get_store()._path(p.id).unlink()
        except FileNotFoundError:
            pass
    _listed[:] = [x for x in _listed if x not in seen]
    names = ", ".join(p.name for p in hits)
    return _r({"deleted": [p.id for p in hits]}, f"Deleted, sir: {names}.")


def describe_delete(args: dict) -> str:
    want = [str(x) for x in (args.get("projects") or [])] + ([args["project"]] if args.get("project") else [])
    names = []
    for w in want:
        p = _pick(w)
        if p:
            names.append(p.name)
    return f"delete {'the project' if len(names) == 1 else 'these projects'}: {', '.join(names) or ', '.join(want)}"


def tool_project_switch(project: str = "", **_):
    p = _pick(project)
    if not p:
        return _r({"error": "no such project"}, "I don't have a project like that, sir.")
    if p.status != "active":
        p.status = "active"
        get_store().save(p)
    get_store().set_active(p.id)
    return _r({"project": p.id}, f"We're on the {p.name} now, sir.", widget(p))


def tool_project_export(project: str = "", **_):
    """The parts list as a spreadsheet (CSV, opens in Excel) and the project as a note, in Documents/E.V.A."""
    p = _pick(project)
    if not p:
        return _r({"error": "no project"})
    from core.agent.pc import _cfg as pc_cfg
    out = Path(str(pc_cfg().get("output_dir") or "~/Documents/E.V.A.")).expanduser() / "projects" / \
        re.sub(r"[^\w\s-]", "", p.name).strip()[:60]
    out.mkdir(parents=True, exist_ok=True)
    with open(out / "parts.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["Part", "Qty", "Price (EUR)", "Status", "Why", "Link"])
        for x in p.parts:
            w.writerow([x["name"], x.get("qty", 1), x.get("price_eur") or "", x.get("status", ""), x.get("why", ""),
                        x.get("link", "")])
        w.writerow(["Total", "", p.total_eur(), "", "", ""])
    md = [f"# {p.name}", "", p.goal, ""]
    for title, kind in (("Decisions", "decision"), ("Ideas", "idea"), ("Research", "research")):
        rows = [n["text"] for n in p.notes if n["kind"] == kind]
        if rows:
            md += [f"## {title}", ""] + [f"- {r}" for r in rows] + [""]
    qs = p.open_questions()
    if qs:
        md += ["## Open questions", ""] + [f"- {n['text']}" for _, n in qs] + [""]
    (out / "project.md").write_text("\n".join(md), encoding="utf-8")
    return _r({"folder": str(out), "files": ["parts.csv", "project.md"]},
              f"The {p.name} is in your E.V.A. folder, sir: the parts list as a spreadsheet and a summary.")


SCHEMAS = [
    {"type": "function", "function": {
        "name": "project_start",
        "description": "Start a project: something bigger than one task that lasts days or weeks (a desk screen, an "
                       "app, a trip). It becomes the active project; decisions, questions and parts are kept there.",
        "parameters": {"type": "object", "properties": {"name": {"type": "string"}, "goal": {"type": "string"}},
                       "required": ["name"]}}},
    {"type": "function", "function": {
        "name": "project_note",
        "description": "Keep something in a project: kind decision (what you agreed), idea, question (open, to "
                       "answer later), research (what was found), or note. Default: the active project.",
        "parameters": {"type": "object", "properties": {
            "text": {"type": "string"}, "kind": {"type": "string", "enum": list(KINDS)},
            "project": {"type": "string"}}, "required": ["text"]}}},
    {"type": "function", "function": {
        "name": "project_answer",
        "description": "Answer one of a project's open questions (its number from project_show; 0 = the latest).",
        "parameters": {"type": "object", "properties": {"question": {"type": "integer"}, "answer": {"type": "string"},
                                                        "project": {"type": "string"}}, "required": ["answer"]}}},
    {"type": "function", "function": {
        "name": "project_add_part",
        "description": "Add or update a part on a project's parts list: name, quantity, price in euros, a shop link, "
                       "why it's needed, status (idea, chosen, ordered, have).",
        "parameters": {"type": "object", "properties": {
            "name": {"type": "string"}, "qty": {"type": "integer"}, "price_eur": {"type": "number"},
            "link": {"type": "string"}, "why": {"type": "string"},
            "status": {"type": "string", "enum": list(PART_STATUS)}, "project": {"type": "string"}},
            "required": ["name"]}}},
    {"type": "function", "function": {
        "name": "project_show",
        "description": "Show a project in the window: goal, decisions, open questions, parts with the total, "
                       "research. 'What's still open on the desk screen?'",
        "parameters": {"type": "object", "properties": {"project": {"type": "string"}}}}},
    {"type": "function", "function": {
        "name": "project_list", "description": "Which projects there are, and which one is active.",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "project_switch", "description": "Make another project the active one ('let's work on the GPS app').",
        "parameters": {"type": "object", "properties": {"project": {"type": "string"}}, "required": ["project"]}}},
    {"type": "function", "function": {
        "name": "project_delete",
        "description": "Delete projects, by name or by their number in the last list ('delete the first three "
                       "projects' -> projects ['1','2','3']). Asks him first.",
        "parameters": {"type": "object", "properties": {"projects": {"type": "array", "items": {"type": "string"}},
                                                        "project": {"type": "string"}}}}},
    {"type": "function", "function": {
        "name": "project_export",
        "description": "Save a project to his E.V.A. folder: the parts list as a spreadsheet and a summary note.",
        "parameters": {"type": "object", "properties": {"project": {"type": "string"}}}}},
]
FUNCTIONS = {"project_delete": tool_project_delete, "project_start": tool_project_start, "project_note": tool_project_note,
             "project_answer": tool_project_answer, "project_add_part": tool_project_add_part,
             "project_show": tool_project_show, "project_list": tool_project_list,
             "project_switch": tool_project_switch, "project_export": tool_project_export}
GUARDS = {"project_start": r"\bproject\w*\b", "project_switch": r"\b(project|work on|switch|go back)\w*\b",
          "project_add_part": r"\b(part|parts|add|buy|need|component|list)\w*\b",
          "project_export": r"\b(file|export|save|spreadsheet|excel|csv)\w*\b",
          "project_delete": r"\b(delete|remove|get rid|clear|drop)\w*\b"}
ACTIONS = {"project_delete"}
CONFIRM = {"project_delete": describe_delete}
