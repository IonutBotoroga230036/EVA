"""
MIRROR - E.V.A. Self-Awareness Module
Maintains a live manifest of E.V.A.'s own architecture, capabilities,
active skills, and limitations. Any subsystem can query MIRROR to understand
what E.V.A. can and cannot do.

When the user asks "what can you do?" or "how do you work?", MIRROR provides
the answer. When FORGE needs to know what skills already exist before building
a new one, MIRROR provides the registry.
"""

import yaml
import json
from pathlib import Path
from datetime import datetime
from loguru import logger


class SkillManifest:
    """Describes a single registered skill."""
    def __init__(self, name: str, description: str, triggers: list[str],
                 version: str = "0.1.0", author: str = "system",
                 created_by_forge: bool = False):
        self.name = name
        self.description = description
        self.triggers = triggers
        self.version = version
        self.author = author
        self.created_by_forge = created_by_forge
        self.registered_at = datetime.now().isoformat()
        self.call_count = 0
        self.last_used = None
        self.errors = 0

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "triggers": self.triggers,
            "version": self.version,
            "author": self.author,
            "created_by_forge": self.created_by_forge,
            "registered_at": self.registered_at,
            "call_count": self.call_count,
            "last_used": self.last_used,
            "errors": self.errors,
        }

    def record_use(self, success: bool = True):
        self.call_count += 1
        self.last_used = datetime.now().isoformat()
        if not success:
            self.errors += 1


class Mirror:
    """
    E.V.A.'s self-model. She can introspect on:
    - Who she is (identity, persona, design philosophy)
    - What subsystems she has (IRIS, CORTEX, FORGE, etc.)
    - What skills are registered and active
    - What her current limits are
    - How she was built (architecture overview)
    """

    def __init__(self, config_path: str = "config/settings.yaml"):
        with open(config_path) as f:
            self.config = yaml.safe_load(f)

        self._skills: dict[str, SkillManifest] = {}
        self._subsystems: dict[str, dict] = {}
        self._init_identity()
        self._init_subsystems()
        self._manifest_path = Path("./data/mirror_state.json")
        self._load_state()
        logger.info(f"MIRROR: Self-model initialized. {len(self._skills)} skills, {len(self._subsystems)} subsystems.")

    def _init_identity(self):
        """Core identity that E.V.A. knows about herself."""
        self.identity = {
            "name": "E.V.A.",
            "full_name": "Extensible Virtual Agent",
            "project_codename": "ADAM",
            "version": self.config["system"]["version"],
            "creator": "Ionut-Alberto Botoroga",
            "purpose": (
                "A local-first, privacy-centric personal AI assistant that orchestrates "
                "local and cloud language models, manages memory, automates tasks, and "
                "extends its own capabilities through the FORGE self-building system."
            ),
            "design_principles": [
                "Local-first, cloud-assisted: everything that can run locally, does.",
                "Modular: every capability is a plugin (skill). The core is just an orchestrator.",
                "Self-extending: when I encounter a task I cannot do, FORGE builds a new skill.",
                "Budget-capped: API spend is tracked daily with hard limits and automatic fallback.",
                "Zero data leakage: secrets encrypted, skills sandboxed, sensitive data stays local.",
                "Voice-first: voice is the primary interface, not text.",
            ],
            "personas": {
                "eva": "Formal, JARVIS-style. Addresses user as 'sir'. Composed, precise, dry wit.",
                "kira": "Collegial, technically sharp. Uses user's name. Challenges ideas constructively.",
            },
        }

    def _init_subsystems(self):
        """Register the known subsystems so E.V.A. can describe her own architecture."""
        subsystems = [
            {
                "name": "IRIS",
                "full_name": "Input Routing & Intent Sorting",
                "role": "Classifies every input into TRIVIAL/ROUTINE/COMPLEX/SENSITIVE/SKILL_MISSING and routes to the right handler.",
                "status": "active",
                "tech": "Local LLM (Ollama) for classification",
            },
            {
                "name": "CORTEX",
                "full_name": "Contextual Organized Retrieval & Temporal Experience",
                "role": "Three-tier memory: episodic (SQLite conversations), semantic (ChromaDB vector search), and session context.",
                "status": "active",
                "tech": "SQLite + ChromaDB + all-MiniLM-L6-v2 embeddings",
            },
            {
                "name": "VAULT",
                "full_name": "Virtual Allocation & Usage Limiting Tracker",
                "role": "Tracks daily API spend per model, enforces budget caps, circuit-breaker to local-only mode.",
                "status": "active",
                "tech": "JSON-based daily tracking with per-model cost calculation",
            },
            {
                "name": "AEGIS",
                "full_name": "Authenticated Encryption, Governance & Isolation System",
                "role": "Secret management (encrypted vault), tamper-evident audit logging, Docker sandboxing for skills.",
                "status": "active",
                "tech": "dotenv secrets + hash-chained JSONL audit log",
            },
            {
                "name": "PULSE",
                "full_name": "Pipeline for Unified Lightweight Signal Exchange",
                "role": "Inter-module event bus. Any subsystem can publish/subscribe to events.",
                "status": "active",
                "tech": "Redis pub/sub",
            },
            {
                "name": "ECHO",
                "full_name": "Engine for Conversational Hearing & Output",
                "role": "Voice I/O: speech-to-text (browser Web Speech API / Whisper) and text-to-speech (edge-tts / Piper).",
                "status": "partial",
                "tech": "Browser STT + edge-tts. Will upgrade to local Whisper + Piper on GPU server.",
            },
            {
                "name": "MIRROR",
                "full_name": "Meta-Introspective Representation & Reasoning on Resources",
                "role": "Self-awareness. I know my own capabilities, skills, architecture, and limits.",
                "status": "active",
                "tech": "In-memory skill registry + architecture manifest",
            },
            {
                "name": "FORGE",
                "full_name": "Fabrication & Orchestration of Runtime-Generated Extensions",
                "role": "Self-extending skill builder. When I lack a capability, FORGE searches for tools, writes adapter code, tests it, and asks for approval.",
                "status": "skeleton",
                "tech": "LLM-driven code generation + Docker sandbox testing + approval workflow",
            },
            {
                "name": "TEMPO",
                "full_name": "Task & Event Management, Planning & Optimization",
                "role": "Calendar sync (Google + Outlook), weekly planning by voice, proactive scheduling.",
                "status": "planned",
                "tech": "Google Calendar API + Microsoft Graph API",
            },
            {
                "name": "SCRIBE",
                "full_name": "Smart Composition & Response Intelligence for Business Exchanges",
                "role": "Drafts emails, messages, LinkedIn posts in the user's voice.",
                "status": "planned",
                "tech": "LLM with user style examples",
            },
            {
                "name": "HERALD",
                "full_name": "Handoff Engine for Realtime Answering, Logging & Dispatch",
                "role": "Phone gateway. Answers missed calls, sends SMS, makes outbound calls.",
                "status": "planned",
                "tech": "Twilio Voice + SMS API",
            },
            {
                "name": "ORACLE",
                "full_name": "Observational Reasoning & Anticipatory Contextual Logic Engine",
                "role": "Proactive intelligence. Surfaces suggestions before the user asks.",
                "status": "planned",
                "tech": "Scheduled CORTEX scans + calendar analysis + pattern detection",
            },
            {
                "name": "AURA",
                "full_name": "Automated Utility & Room Awareness",
                "role": "Smart home and IoT control via Home Assistant.",
                "status": "planned",
                "tech": "Home Assistant REST API + ESPHome devices",
            },
            {
                "name": "MUSE",
                "full_name": "Memory Upload through Speech Extraction",
                "role": "Optional always-on ambient recording and transcription for networking events.",
                "status": "planned",
                "tech": "Whisper continuous transcription + entity extraction",
            },
        ]
        for s in subsystems:
            self._subsystems[s["name"]] = s

    def register_skill(self, name: str, description: str, triggers: list[str],
                       author: str = "system", created_by_forge: bool = False) -> SkillManifest:
        """Register a new skill in E.V.A.'s self-model."""
        manifest = SkillManifest(
            name=name,
            description=description,
            triggers=triggers,
            author=author,
            created_by_forge=created_by_forge,
        )
        self._skills[name] = manifest
        self._save_state()
        logger.info(f"MIRROR: Registered skill '{name}' (triggers: {triggers})")
        return manifest

    def unregister_skill(self, name: str) -> bool:
        if name in self._skills:
            del self._skills[name]
            self._save_state()
            return True
        return False

    def get_skill(self, name: str) -> SkillManifest | None:
        return self._skills.get(name)

    def list_skills(self) -> list[dict]:
        return [s.to_dict() for s in self._skills.values()]

    def list_active_subsystems(self) -> list[dict]:
        return [s for s in self._subsystems.values() if s["status"] == "active"]

    def list_all_subsystems(self) -> list[dict]:
        return list(self._subsystems.values())

    def get_capabilities_summary(self) -> str:
        """Generate a natural-language summary of what E.V.A. can do right now.
        This is what gets injected into the LLM context when the user asks
        'what can you do?' or similar questions."""

        active = [s for s in self._subsystems.values() if s["status"] == "active"]
        partial = [s for s in self._subsystems.values() if s["status"] == "partial"]
        planned = [s for s in self._subsystems.values() if s["status"] == "planned"]
        skills = self.list_skills()

        lines = []
        lines.append(f"I am {self.identity['name']} ({self.identity['full_name']}), version {self.identity['version']}.")
        lines.append(f"Project codename: {self.identity['project_codename']}.")
        lines.append(f"Created by: {self.identity['creator']}.")
        lines.append("")
        lines.append(f"Purpose: {self.identity['purpose']}")
        lines.append("")

        lines.append("ACTIVE SUBSYSTEMS:")
        for s in active:
            lines.append(f"  {s['name']} ({s['full_name']}): {s['role']}")
        if partial:
            lines.append("")
            lines.append("PARTIALLY ACTIVE:")
            for s in partial:
                lines.append(f"  {s['name']} ({s['full_name']}): {s['role']}")

        if skills:
            lines.append("")
            lines.append("REGISTERED SKILLS:")
            for sk in skills:
                forge_tag = " [built by FORGE]" if sk["created_by_forge"] else ""
                lines.append(f"  {sk['name']}: {sk['description']}{forge_tag}")
                lines.append(f"    Triggers: {', '.join(sk['triggers'])}")
                lines.append(f"    Used {sk['call_count']} times, {sk['errors']} errors")
        else:
            lines.append("")
            lines.append("REGISTERED SKILLS: None yet. Skills will be registered as they are built.")

        if planned:
            lines.append("")
            lines.append("PLANNED (not yet built):")
            for s in planned:
                lines.append(f"  {s['name']}: {s['role']}")

        lines.append("")
        lines.append("CURRENT LIMITATIONS:")
        lines.append("  - No Claude API key configured; running in local-only mode.")
        lines.append("  - Voice TTS depends on internet (edge-tts). Will be local with Piper on GPU server.")
        lines.append("  - FORGE is skeleton-only; cannot yet self-build new skills.")
        lines.append("  - No calendar integration yet (TEMPO planned).")
        lines.append("  - No phone/SMS gateway yet (HERALD planned).")

        return "\n".join(lines)

    def get_architecture_description(self) -> str:
        """For when the user asks 'how are you built?' or 'explain your architecture'."""
        return f"""
I am built as a modular orchestration system with these layers:

INTERFACE LAYER: Web UI (React + TypeScript), CLI, and voice (ECHO).
The user talks to me through any of these. Voice uses browser speech recognition
for input and edge-tts for output. When the GPU server is ready, this switches
to local Whisper + Piper.

CORE ORCHESTRATOR: The central nervous system. Receives input, sends it to
IRIS for classification, executes via the appropriate handler, and returns
the response. Manages the session and coordinates all subsystems.

IRIS (Router): Every input is classified as TRIVIAL (local LLM), ROUTINE
(skill execution), COMPLEX (cloud API), SENSITIVE (forced local), or
SKILL_MISSING (triggers FORGE). This saves ~80% of API costs by handling
simple tasks locally.

CORTEX (Memory): Three tiers:
  - Episodic: SQLite database of all conversations and interactions.
  - Semantic: ChromaDB vector store for long-term knowledge, people, projects.
  - Session: In-memory context for the current conversation.

MIRROR (Self-Awareness): That's me describing myself right now. I maintain
a live registry of all skills, subsystems, and capabilities so I can
accurately answer questions about what I can and cannot do.

FORGE (Self-Extension): When I encounter a task no skill can handle, FORGE
searches for libraries/tools, generates adapter code, tests it in a Docker
sandbox, and asks the user to approve before installing. Currently skeleton.

VAULT (Budget): Tracks every cloud API call's cost in EUR. Daily cap with
circuit breaker: when budget is exhausted, I fall back to local-only mode.

AEGIS (Security): Encrypted secrets vault, hash-chained audit log of every
action, Docker sandboxing for skill execution.

PULSE (Event Bus): Redis pub/sub for inter-module communication. Any
subsystem can publish or subscribe to events.

INFERENCE LAYER: Two tiers:
  - Local: Ollama running Llama 3.2 3B (router) and Llama 3.1 8B (general).
  - Cloud: Claude API (Haiku for mid-tier, Sonnet for complex). Only called
    when local models cannot handle the task and budget allows.

All code is Python 3.12 on the backend, React/TypeScript on the frontend.
The system runs on {self.identity['creator']}'s hardware and will be deployed
to a dedicated server with an RTX 3060 12GB GPU for 24/7 operation.
"""

    def update_subsystem_status(self, name: str, status: str) -> None:
        if name in self._subsystems:
            self._subsystems[name]["status"] = status
            logger.info(f"MIRROR: {name} status -> {status}")

    def _save_state(self):
        """Persist skill registry to disk."""
        state = {
            "skills": {name: s.to_dict() for name, s in self._skills.items()},
            "saved_at": datetime.now().isoformat(),
        }
        self._manifest_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._manifest_path, "w") as f:
            json.dump(state, f, indent=2)

    def _load_state(self):
        """Load persisted skill registry."""
        if self._manifest_path.exists():
            try:
                with open(self._manifest_path) as f:
                    state = json.load(f)
                for name, data in state.get("skills", {}).items():
                    manifest = SkillManifest(
                        name=data["name"],
                        description=data["description"],
                        triggers=data["triggers"],
                        version=data.get("version", "0.1.0"),
                        author=data.get("author", "system"),
                        created_by_forge=data.get("created_by_forge", False),
                    )
                    manifest.call_count = data.get("call_count", 0)
                    manifest.last_used = data.get("last_used")
                    manifest.errors = data.get("errors", 0)
                    manifest.registered_at = data.get("registered_at", datetime.now().isoformat())
                    self._skills[name] = manifest
                logger.info(f"MIRROR: Loaded {len(self._skills)} skills from disk")
            except Exception as e:
                logger.warning(f"MIRROR: Could not load state: {e}")


# Singleton
mirror = Mirror()
