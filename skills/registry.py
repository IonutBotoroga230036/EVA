"""
Skill registry: SKILL.md-style skills, like Claude Code's.

A skill is a folder under skills/ containing:

    SKILL.md    required. YAML frontmatter + markdown instructions.
    tools.py    optional. Defines TOOLS (Ollama tool schemas), FUNCTIONS
                (name -> callable) and optionally ACKS (name -> spoken ack).

Frontmatter fields:
    name         unique id
    description  one line; this is ALL the model sees until the skill is matched
    enabled      default true
    trusted      must be true for tools.py to be imported (AEGIS gate). Hand-written
                 skills set it; FORGE-generated skills stay false until you approve.
    triggers     optional phrases for keyword matching when embeddings are down
    always       optional; true injects the body on every turn (keep these tiny)
    permissions  declared network/filesystem needs (documentation for now,
                 enforced once the FORGE sandbox lands)

Progressive disclosure: descriptions are indexed at startup; a skill's body is
added to the prompt only when the request matches it (cosine over the same
embedding already computed for memory recall, so matching costs nothing extra).
"""

from __future__ import annotations

import importlib.util
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

import numpy as np
import yaml
from loguru import logger

_FRONT = re.compile(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", re.S)
_WORDS = re.compile(r"[a-z0-9]+")
_STOP = {"the", "a", "an", "to", "and", "or", "of", "for", "in", "on", "my", "me", "is",
         "it", "what", "can", "you", "please", "with", "this", "that", "eva", "i"}


@dataclass
class Skill:
    name: str
    description: str
    body: str
    path: Path
    enabled: bool = True
    trusted: bool = False
    always: bool = False
    triggers: list[str] = field(default_factory=list)
    permissions: dict = field(default_factory=dict)
    tools: list[dict] = field(default_factory=list)
    functions: dict[str, Callable] = field(default_factory=dict)
    acks: dict[str, str] = field(default_factory=dict)
    actions: set[str] = field(default_factory=set)
    guards: dict[str, str] = field(default_factory=dict)
    confirm: dict[str, str] = field(default_factory=dict)
    fillers: dict[str, Callable] = field(default_factory=dict)
    vec: Optional[np.ndarray] = None


def parse_skill_md(path: Path) -> tuple[dict, str]:
    text = path.read_text(encoding="utf-8")
    m = _FRONT.match(text)
    if not m:
        raise ValueError(f"{path} has no YAML frontmatter")
    meta = yaml.safe_load(m.group(1)) or {}
    return meta, m.group(2).strip()


class SkillRegistry:
    def __init__(self, skills_dir: str | Path = "skills", embedder=None,
                 match_threshold: float = 0.62, max_body_chars: int = 1500):
        self.dir = Path(skills_dir)
        self.embed = embedder
        self.match_threshold = match_threshold
        self.max_body_chars = max_body_chars
        self.skills: dict[str, Skill] = {}

    # ---------------------------------------------------------------- loading
    def discover(self) -> "SkillRegistry":
        self.skills.clear()
        if not self.dir.exists():
            return self
        for md in sorted(self.dir.glob("*/SKILL.md")):
            try:
                meta, body = parse_skill_md(md)
                sk = Skill(
                    name=str(meta.get("name") or md.parent.name),
                    description=str(meta.get("description", "")).strip(),
                    body=body, path=md.parent,
                    enabled=bool(meta.get("enabled", True)),
                    trusted=bool(meta.get("trusted", False)),
                    always=bool(meta.get("always", False)),
                    triggers=[t.lower() for t in meta.get("triggers", []) or []],
                    permissions=meta.get("permissions", {}) or {},
                )
                if sk.enabled:
                    self._load_tools(sk)
                self.skills[sk.name] = sk
            except Exception as e:
                logger.error(f"SKILLS: failed to load {md}: {e}")
        self._embed_descriptions()
        logger.info(f"SKILLS: {len(self.enabled())} enabled "
                    f"({', '.join(s.name for s in self.enabled()) or 'none'})")
        return self

    def _load_tools(self, sk: Skill) -> None:
        tools_py = sk.path / "tools.py"
        if not tools_py.exists():
            return
        if not sk.trusted:
            logger.warning(f"SKILLS: '{sk.name}' has tools.py but is not trusted; tools not loaded")
            return
        spec = importlib.util.spec_from_file_location(f"eva_skill_{sk.name}", tools_py)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        sk.tools = list(getattr(mod, "TOOLS", []))
        sk.functions = dict(getattr(mod, "FUNCTIONS", {}))
        sk.acks = dict(getattr(mod, "ACKS", {}))
        sk.actions = set(getattr(mod, "ACTIONS", []))
        sk.guards = dict(getattr(mod, "GUARDS", {}))
        sk.confirm = dict(getattr(mod, "CONFIRM", {}))
        sk.fillers = dict(getattr(mod, "FILLERS", {}))
        declared = {t["function"]["name"] for t in sk.tools}
        missing = declared - set(sk.functions)
        if missing:
            logger.error(f"SKILLS: '{sk.name}' declares tools without functions: {missing}")
            sk.tools = [t for t in sk.tools if t["function"]["name"] not in missing]

    def _embed_descriptions(self) -> None:
        if not self.embed:
            return
        todo = [s for s in self.enabled() if s.description]
        embed = getattr(self.embed, "embed_blocking", self.embed)    # startup work may wait longer
        vecs = embed([f"search_document: {s.name}: {s.description}" for s in todo]) if todo else None
        if not vecs:
            return
        for s, v in zip(todo, vecs):
            v = np.asarray(v, dtype=np.float32)
            n = float(np.linalg.norm(v))
            s.vec = v / n if n else None

    # ---------------------------------------------------------------- queries
    def enabled(self) -> list[Skill]:
        return [s for s in self.skills.values() if s.enabled]

    def tool_schemas(self) -> list[dict]:
        return [t for s in self.enabled() for t in s.tools]

    def functions(self) -> dict[str, Callable]:
        return {k: v for s in self.enabled() for k, v in s.functions.items()}

    def acks(self) -> dict[str, str]:
        return {k: v for s in self.enabled() for k, v in s.acks.items()}

    def guards(self) -> dict[str, str]:
        return {k: v for s in self.enabled() for k, v in s.guards.items()}

    def fillers(self) -> dict[str, Callable]:
        return {k: v for s in self.enabled() for k, v in s.fillers.items()}

    def confirmations(self) -> dict[str, str]:
        return {k: v for s in self.enabled() for k, v in s.confirm.items()}

    def actions(self) -> set[str]:
        """Tools that change something; a successful one ends the tool loop."""
        return {a for s in self.enabled() for a in s.actions}

    def match(self, query: str, query_vec: Optional[np.ndarray] = None, k: int = 2) -> list[Skill]:
        """Skills whose instructions should be loaded for this request."""
        picked = [s for s in self.enabled() if s.always]
        q = query.lower()
        scored = []
        for s in self.enabled():
            if s.always:
                continue
            if query_vec is not None and s.vec is not None:
                score = float(np.dot(query_vec, s.vec))
                hit = score >= self.match_threshold
            else:
                words = set(_WORDS.findall(q)) - _STOP
                desc = set(_WORDS.findall(f"{s.name} {s.description}".lower())) - _STOP
                overlap = len(words & desc)
                score = overlap + (5 if any(t in q for t in s.triggers) else 0)
                hit = score >= 2
            if any(t in q for t in s.triggers):   # explicit trigger always wins
                hit, score = True, max(score, 99)
            if hit:
                scored.append((score, s))
        scored.sort(key=lambda x: x[0], reverse=True)
        return picked + [s for _, s in scored[:k]]

    def bodies(self, skills: list[Skill]) -> list[str]:
        return [f"### Skill: {s.name}\n{s.body[: self.max_body_chars]}" for s in skills if s.body]

    def index_text(self) -> str:
        """One line per skill; for 'what can you do' questions and the status page."""
        return "\n".join(f"- {s.name}: {s.description}" for s in self.enabled())

    def status(self) -> list[dict]:
        return [{"name": s.name, "description": s.description, "enabled": s.enabled,
                 "trusted": s.trusted, "tools": [t["function"]["name"] for t in s.tools],
                 "permissions": s.permissions} for s in self.skills.values()]


_registry: SkillRegistry | None = None


def get_registry() -> SkillRegistry:
    global _registry
    if _registry is None:
        from core.memory.cortex import get_cortex
        from core.settings import get_settings
        cfg = get_settings().get("skills", {})
        embedder = get_cortex().embed
        _registry = SkillRegistry(cfg.get("dir", "skills"), embedder=embedder,
                                  match_threshold=float(cfg.get("match_threshold", 0.62))).discover()
        if hasattr(embedder, "on_ready"):          # embed descriptions once the model is warm
            embedder.on_ready(_registry._embed_descriptions)
    return _registry
