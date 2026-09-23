"""
E.V.A. Core Orchestrator - Tool-calling architecture, single-response.
The LLM decides which tools to use. No keyword matching.
"""

import yaml
import anthropic
import httpx
from pathlib import Path
from loguru import logger

from core.budget import BudgetTracker
from core.memory.episodic import EpisodicMemory
from core.memory.semantic import SemanticMemory
from core.memory.context import SessionContext
from core.security.vault import vault
from core.security.audit import audit
from core.events.bus import PulseEventBus
from core.mirror import mirror
from core.forge import forge
from core.tool_executor import TOOLS_DESCRIPTION, extract_tool_call, execute_tool


class BudgetExhaustedError(Exception):
    pass


class Eva:
    def __init__(self, config_path: str = "config/settings.yaml"):
        with open(config_path) as f:
            self.config = yaml.safe_load(f)

        vault.load()
        logger.info("Initializing E.V.A. subsystems...")

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
        self._claude: anthropic.Anthropic | None = None

        self._cloud_available = vault.has("ANTHROPIC_API_KEY")
        if not self._cloud_available:
            logger.warning("No Anthropic API key found. Running in local-only mode.")

        # MIRROR - Self-awareness
        self.mirror = mirror
        logger.info(f"MIRROR: Online. {len(mirror.list_skills())} skills registered.")

        # FORGE - Self-extension
        self.forge = forge
        logger.info("FORGE: Online. Skill builder ready.")

        # Load persona
        persona_name = self.config["personas"]["default"]
        persona_path = Path(f"personas/{persona_name}.yaml")
        with open(persona_path) as f:
            self.persona = yaml.safe_load(f)

        audit.log("system_start", "orchestrator", {"persona": persona_name})
        logger.info(f"E.V.A. initialized as {self.persona['name']}")

    async def _local_inference(self, prompt: str, model: str | None = None) -> str:
        model = model or self.config["inference"]["local"]["general_model"]
        try:
            async with httpx.AsyncClient(timeout=120) as client:
                response = await client.post(
                    f"{self.config['inference']['local']['base_url']}/api/generate",
                    json={"model": model, "prompt": prompt, "stream": False},
                )
                result = response.json()
                return result.get("response", result.get("message", {}).get("content", str(result)))
        except httpx.TimeoutException:
            return "I'm still processing, sir. Please try again in a moment."
        except Exception as e:
            logger.error(f"Local inference error: {e}")
            return f"My apologies, sir. Inference failed: {e}"

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
        return response.content[0].text

    def _get_claude(self) -> anthropic.Anthropic:
        if self._claude is None:
            self._claude = anthropic.Anthropic(api_key=vault.get("ANTHROPIC_API_KEY"))
        return self._claude

    def _is_self_query(self, user_input: str) -> bool:
        lower = user_input.lower()
        triggers = [
            "what can you do", "your capabilities", "how do you work",
            "how are you built", "your architecture", "what skills",
            "tell me about yourself", "who are you", "what are you",
            "what is mirror", "what is forge", "what is iris",
            "what is cortex", "what is pulse", "what is aegis",
        ]
        return any(t in lower for t in triggers)

    def _is_forge_command(self, user_input: str) -> bool:
        lower = user_input.lower()
        triggers = [
            "forge approve", "forge build", "yes, build it",
            "yes build it", "build the skill", "forge activate",
        ]
        return any(t in lower for t in triggers)

    async def _handle_self_query(self, user_input: str) -> str:
        lower = user_input.lower()
        if any(w in lower for w in ["architecture", "how are you built", "how do you work"]):
            context = self.mirror.get_architecture_description()
        else:
            context = self.mirror.get_capabilities_summary()
        prompt = (
            f"{self.persona['personality']}\n\n"
            f"User asks about you. Your self-knowledge:\n{context}\n\n"
            f"Answer concisely. Don't invent capabilities.\n"
            f"User: {user_input}\nAssistant:"
        )
        return await self._local_inference(prompt)

    async def _handle_forge_command(self, user_input: str) -> str:
        lower = user_input.lower()
        if "activate" in lower:
            parts = lower.split("activate", 1)
            if len(parts) > 1 and parts[1].strip():
                return self.forge.activate_skill(parts[1].strip(), self)
            return "Which skill should I activate, sir?"
        latest = self.forge.get_latest_pending()
        if latest:
            return await self.forge.build_skill(latest.id, self)
        return "No pending skill request, sir."

    async def _tool_augmented_response(self, user_input: str) -> str:
        """
        Give the LLM tools, let it decide what to call,
        execute tools, formulate response with real data.
        """
        system = f"{self.persona['personality']}\n\n{TOOLS_DESCRIPTION}"

        # First pass: does the LLM want a tool?
        first_prompt = f"{system}\n\nUser: {user_input}\nAssistant:"
        first_response = await self._local_inference(first_prompt)

        tool_call, remaining = extract_tool_call(first_response)

        if tool_call and "tool" in tool_call:
            tool_name = tool_call["tool"]
            tool_args = tool_call.get("args", {})
            logger.info(f"TOOL CALL: {tool_name}({tool_args})")

            # Execute tool
            result = execute_tool(tool_name, tool_args)
            audit.log("tool_call", "orchestrator", {"tool": tool_name})

            # Check if a second tool call is needed
            # (e.g., first get_datetime, then web_search)
            second_prompt = (
                f"{system}\n\n"
                f"The user asked: {user_input}\n\n"
                f"You already called '{tool_name}' and got:\n{result}\n\n"
                f"Do you need another tool? If yes, respond with a JSON tool call. "
                f"If no, formulate a concise response using ONLY the real data above. "
                f"Never guess or invent information.\nAssistant:"
            )
            second_response = await self._local_inference(second_prompt)

            second_tool, second_remaining = extract_tool_call(second_response)

            if second_tool and "tool" in second_tool:
                tool_name_2 = second_tool["tool"]
                tool_args_2 = second_tool.get("args", {})
                logger.info(f"TOOL CALL 2: {tool_name_2}({tool_args_2})")

                result_2 = execute_tool(tool_name_2, tool_args_2)
                audit.log("tool_call", "orchestrator", {"tool": tool_name_2})

                # Final formulation with both results
                final_prompt = (
                    f"{self.persona['personality']}\n\n"
                    f"The user asked: {user_input}\n\n"
                    f"Tool results:\n"
                    f"1. {tool_name}({tool_args}) returned: {result}\n"
                    f"2. {tool_name_2}({tool_args_2}) returned: {result_2}\n\n"
                    f"Give a concise, natural response using ONLY this data.\nAssistant:"
                )
                return await self._local_inference(final_prompt)
            else:
                # Second response is the final answer
                if second_remaining and len(second_remaining) > 10:
                    return second_remaining
                return second_response

        else:
            # No tool needed, use the first response directly
            if remaining and len(remaining) > 10:
                return remaining
            return first_response

    async def process(self, user_input: str) -> str:
        """Main processing pipeline."""
        self.session.add_message("user", user_input)
        self.episodic.add_message(
            session_id=self.session.session_id,
            role="user",
            content=user_input,
            persona=self.session.persona,
        )

        # Self-queries
        if self._is_self_query(user_input):
            audit.log("route", "MIRROR", {"type": "self_query"})
            response = await self._handle_self_query(user_input)

        # FORGE commands
        elif self._is_forge_command(user_input):
            audit.log("route", "FORGE", {"type": "forge_command"})
            response = await self._handle_forge_command(user_input)

        # Everything else: tool-augmented pipeline
        else:
            audit.log("route", "orchestrator", {"type": "tool_augmented"})
            response = await self._tool_augmented_response(user_input)

        # Store response
        self.session.add_message("assistant", response)
        self.episodic.add_message(
            session_id=self.session.session_id,
            role="assistant",
            content=response,
            persona=self.session.persona,
        )
        self.pulse.publish("output", {"event": "response", "length": len(response)})
        return response