"""
E.V.A. Core Orchestrator - The central nervous system.
Now with MIRROR (self-awareness) and FORGE (self-extension).
"""

import yaml
import anthropic
import httpx
from pathlib import Path
from loguru import logger

from core.router import Router, TaskCategory
from core.budget import BudgetTracker
from core.memory.episodic import EpisodicMemory
from core.memory.semantic import SemanticMemory
from core.memory.context import SessionContext
from core.security.vault import vault
from core.security.audit import audit
from core.events.bus import PulseEventBus
from core.mirror import mirror
from core.forge import forge


class BudgetExhaustedError(Exception):
    pass


class Eva:
    def __init__(self, config_path: str = "config/settings.yaml"):
        with open(config_path) as f:
            self.config = yaml.safe_load(f)

        vault.load()
        logger.info("Initializing E.V.A. subsystems...")

        self.router = Router(
            ollama_url=self.config["inference"]["local"]["base_url"],
            model=self.config["inference"]["local"]["router_model"],
        )
        self.budget = BudgetTracker(
            daily_limit_euros=self.config["budget"]["daily_limit_euros"],
            warning_threshold=self.config["budget"]["warning_threshold"],
            track_file=self.config["budget"]["track_file"],
        )
        self.episodic = EpisodicMemory(db_path=self.config["memory"]["sqlite_path"])
        self.semantic = SemanticMemory(persist_dir=self.config["memory"]["chromadb_path"])
        self.pulse = PulseEventBus(
            redis_host=self.config["redis"]["host"],
            redis_port=self.config["redis"]["port"],
        )
        self.session = SessionContext()
        self._skills: dict[str, object] = {}
        self._claude: anthropic.Anthropic | None = None

        # Cloud availability check
        self._cloud_available = vault.has("ANTHROPIC_API_KEY")
        if not self._cloud_available:
            logger.warning("No Anthropic API key found. Running in local-only mode.")

        # MIRROR - Self-awareness
        self.mirror = mirror
        logger.info(f"MIRROR: Online. {len(mirror.list_skills())} skills registered.")

        # FORGE - Self-extension
        self.forge = forge
        logger.info("FORGE: Online. Skill builder ready.")

        persona_name = self.config["personas"]["default"]
        persona_path = Path(f"personas/{persona_name}.yaml")
        with open(persona_path) as f:
            self.persona = yaml.safe_load(f)

        audit.log("system_start", "orchestrator", {"persona": persona_name})
        logger.info(f"E.V.A. initialized as {self.persona['name']}")

    def register_skill(self, name: str, skill_instance: object) -> None:
        self._skills[name] = skill_instance
        self.router.register_skills(list(self._skills.keys()))

    def _get_claude(self) -> anthropic.Anthropic:
        if self._claude is None:
            self._claude = anthropic.Anthropic(api_key=vault.get("ANTHROPIC_API_KEY"))
            audit.log("api_init", "AEGIS", {"provider": "anthropic"}, sensitive=True)
        return self._claude

    async def _local_inference(self, prompt: str, model: str | None = None) -> str:
        model = model or self.config["inference"]["local"]["general_model"]
        try:
            async with httpx.AsyncClient(timeout=120) as client:
                response = await client.post(
                    f"{self.config['inference']['local']['base_url']}/api/generate",
                    json={"model": model, "prompt": prompt, "stream": False},
                )
                result = response.json()
                if "response" in result:
                    return result["response"]
                elif "message" in result:
                    return result["message"].get("content", str(result))
                else:
                    logger.warning(f"Unexpected Ollama response: {list(result.keys())}")
                    return str(result)
        except httpx.TimeoutException:
            return "I'm still thinking, sir. The local model is running slowly. Please try again in a moment."
        except Exception as e:
            logger.error(f"Local inference error: {e}")
            return f"My apologies, sir. Local inference failed: {e}"

    def _cloud_inference(self, messages: list[dict], model: str | None = None) -> str:
        if not self.budget.can_spend():
            raise BudgetExhaustedError("Daily API budget exhausted")

        model = model or self.config["inference"]["cloud"]["model_heavy"]
        client = self._get_claude()
        response = client.messages.create(
            model=model,
            max_tokens=2048,
            system=self.persona["personality"],
            messages=messages,
        )
        usage = response.usage
        self.budget.record_usage(
            model=model,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
        )
        audit.log("api_call", "VAULT", {
            "model": model,
            "tokens_in": usage.input_tokens,
            "tokens_out": usage.output_tokens,
        })
        return response.content[0].text

    def _is_self_query(self, user_input: str) -> bool:
        """Detect if the user is asking about E.V.A. herself."""
        lower = user_input.lower()
        self_triggers = [
            "what can you do", "what are your capabilities",
            "how do you work", "how are you built", "your architecture",
            "what skills do you have", "what are your skills",
            "tell me about yourself", "who are you", "what are you",
            "describe yourself", "your subsystems", "your modules",
            "what are your limits", "your limitations",
            "explain your system", "how were you made",
            "what is mirror", "what is forge", "what is iris",
            "what is cortex", "what is pulse", "what is aegis",
            "what is vault", "what is echo", "what is oracle",
            "what is tempo", "what is herald", "what is aura",
            "what is scribe", "what is muse",
        ]
        return any(trigger in lower for trigger in self_triggers)

    def _is_forge_command(self, user_input: str) -> bool:
        """Detect forge-related commands."""
        lower = user_input.lower()
        forge_triggers = [
            "forge approve", "forge build", "yes, build it",
            "yes build it", "go ahead and build", "build the skill",
            "forge activate", "approve the skill",
        ]
        return any(trigger in lower for trigger in forge_triggers)

    async def _handle_self_query(self, user_input: str) -> str:
        """Answer questions about E.V.A.'s own capabilities and architecture."""
        lower = user_input.lower()

        if any(w in lower for w in ["architecture", "how are you built", "how were you made", "how do you work", "explain your system"]):
            context = self.mirror.get_architecture_description()
        else:
            context = self.mirror.get_capabilities_summary()

        # Use LLM to generate a natural response based on the self-knowledge
        prompt = (
            f"{self.persona['personality']}\n\n"
            f"The user is asking about your own capabilities or architecture. "
            f"Here is your accurate self-knowledge:\n\n"
            f"{context}\n\n"
            f"Answer the user's question naturally using this information. "
            f"Be accurate. Do not invent capabilities you don't have. "
            f"If something is planned but not built yet, say so.\n\n"
            f"User: {user_input}\n"
            f"Assistant:"
        )
        return await self._local_inference(prompt)

    async def _handle_forge_command(self, user_input: str) -> str:
        """Handle FORGE-related commands (approve, build, activate)."""
        lower = user_input.lower()

        if "activate" in lower:
            # Extract skill name: "forge activate cad_design"
            parts = lower.split("activate", 1)
            if len(parts) > 1 and parts[1].strip():
                skill_name = parts[1].strip()
                return self.forge.activate_skill(skill_name, self)
            return "Which skill should I activate, sir? Please specify the name."

        # Approve and build the latest pending request
        latest = self.forge.get_latest_pending()
        if latest:
            return await self.forge.build_skill(latest.id, self)
        else:
            latest_any = self.forge.get_latest_request()
            if latest_any and latest_any.status == "done":
                return (
                    f"The last skill I built ('{latest_any.analysis.get('skill_name', 'unknown')}') "
                    f"is already complete, sir. Say 'forge activate {latest_any.analysis.get('skill_name')}' "
                    f"to register it."
                )
            return "There's no pending skill request to approve, sir. Ask me to do something I can't do yet, and I'll propose a new skill."

    async def process(self, user_input: str) -> str:
        self.session.add_message("user", user_input)
        self.episodic.add_message(
            session_id=self.session.session_id,
            role="user",
            content=user_input,
            persona=self.session.persona,
        )

        # Check for self-queries first (bypass IRIS for these)
        if self._is_self_query(user_input):
            audit.log("route", "MIRROR", {"type": "self_query", "input_preview": user_input[:100]})
            response = await self._handle_self_query(user_input)

        # Check for FORGE commands
        elif self._is_forge_command(user_input):
            audit.log("route", "FORGE", {"type": "forge_command", "input_preview": user_input[:100]})
            response = await self._handle_forge_command(user_input)

        else:
            # Normal IRIS routing
            classification = await self.router.classify(user_input)
            category = classification["category"]
            target_skill = classification.get("skill")

            audit.log("route", "IRIS", {
                "category": category,
                "skill": target_skill,
                "input_preview": user_input[:100],
            })
            self.pulse.publish("input", {
                "event": "user_input",
                "category": category,
                "skill": target_skill,
            })

            try:
                if category == TaskCategory.TRIVIAL:
                    response = await self._local_inference(
                        f"{self.persona['personality']}\n\nUser: {user_input}\nAssistant:"
                    )
                elif category == TaskCategory.ROUTINE:
                    if target_skill and target_skill in self._skills:
                        response = await self._skills[target_skill].execute(user_input, self)
                    else:
                        response = await self._local_inference(
                            f"{self.persona['personality']}\n\nUser: {user_input}\nAssistant:"
                        )
                elif category == TaskCategory.COMPLEX:
                    if self._cloud_available:
                        messages = self.session.get_recent_messages(limit=10)
                        try:
                            response = self._cloud_inference(messages)
                        except BudgetExhaustedError:
                            response = await self._local_inference(
                                f"{self.persona['personality']}\n\nUser: {user_input}\nAssistant:"
                            )
                    else:
                        response = await self._local_inference(
                            f"{self.persona['personality']}\n\nUser: {user_input}\nAssistant:"
                        )
                elif category == TaskCategory.SENSITIVE:
                    response = await self._local_inference(
                        f"{self.persona['personality']}\n\n[SENSITIVE MODE: Local only.]\n\nUser: {user_input}\nAssistant:"
                    )
                elif category == TaskCategory.SKILL_MISSING:
                    # FORGE takes over
                    response = await self.forge.propose_skill(user_input, self)
                else:
                    response = await self._local_inference(
                        f"{self.persona['personality']}\n\nUser: {user_input}\nAssistant:"
                    )
            except Exception as e:
                logger.error(f"Processing error: {e}")
                audit.log("error", "orchestrator", {"error": str(e)})
                response = f"My apologies, sir. Something went wrong: {e}"

        # Store response
        self.session.add_message("assistant", response)
        self.episodic.add_message(
            session_id=self.session.session_id,
            role="assistant",
            content=response,
            persona=self.session.persona,
        )
        self.pulse.publish("output", {
            "event": "response",
            "length": len(response),
        })
        return response