"""
E.V.A. Core Orchestrator v3 - Tool-calling architecture.
The LLM decides which tools to use. No keyword matching.
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
from core.tool_executor import TOOLS_DESCRIPTION, extract_tool_call, execute_tool


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

        self._cloud_available = vault.has("ANTHROPIC_API_KEY")
        if not self._cloud_available:
            logger.warning("No Anthropic API key found. Running in local-only mode.")

        # MIRROR
        self.mirror = mirror
        logger.info(f"MIRROR: Online. {len(mirror.list_skills())} skills registered.")

        # FORGE
        self.forge = forge
        logger.info("FORGE: Online. Skill builder ready.")

        # Load persona
        persona_name = self.config["personas"]["default"]
        persona_path = Path(f"personas/{persona_name}.yaml")
        with open(persona_path) as f:
            self.persona = yaml.safe_load(f)

        audit.log("system_start", "orchestrator", {"persona": persona_name})
        logger.info(f"E.V.A. initialized as {self.persona['name']}")

    def _get_claude(self) -> anthropic.Anthropic:
        if self._claude is None:
            self._claude = anthropic.Anthropic(api_key=vault.get("ANTHROPIC_API_KEY"))
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
                    return str(result)
        except httpx.TimeoutException:
            return "I'm still processing, sir. The local model is running slowly."
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
        self.budget.record_usage(model=model, input_tokens=usage.input_tokens, output_tokens=usage.output_tokens)
        return response.content[0].text

    def _is_self_query(self, user_input: str) -> bool:
        lower = user_input.lower()
        self_triggers = [
            "what can you do", "what are your capabilities",
            "how do you work", "how are you built", "your architecture",
            "what skills do you have", "tell me about yourself",
            "who are you", "what are you", "describe yourself",
            "what is mirror", "what is forge", "what is iris",
            "what is cortex", "what is pulse", "what is aegis",
        ]
        return any(trigger in lower for trigger in self_triggers)

    def _is_forge_command(self, user_input: str) -> bool:
        lower = user_input.lower()
        forge_triggers = [
            "forge approve", "forge build", "yes, build it",
            "yes build it", "build the skill", "forge activate",
        ]
        return any(trigger in lower for trigger in forge_triggers)

    async def _handle_self_query(self, user_input: str) -> str:
        lower = user_input.lower()
        if any(w in lower for w in ["architecture", "how are you built", "how do you work"]):
            context = self.mirror.get_architecture_description()
        else:
            context = self.mirror.get_capabilities_summary()
        prompt = (
            f"{self.persona['personality']}\n\n"
            f"The user asks about your capabilities. Here is your self-knowledge:\n\n"
            f"{context}\n\n"
            f"Answer concisely in your voice. Be accurate. Don't invent capabilities.\n\n"
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
        The core loop: give the LLM tools, let it decide what to call,
        execute the tools, and let the LLM formulate the final response.
        Supports up to 3 chained tool calls.
        """
        system = (
            f"{self.persona['personality']}\n\n"
            f"{TOOLS_DESCRIPTION}"
        )

        conversation = f"User: {user_input}\nAssistant:"
        tool_results = []

        # Allow up to 3 tool calls in sequence
        for turn in range(3):
            prompt = system
            if tool_results:
                prompt += "\n\nPrevious tool results:\n"
                for tr in tool_results:
                    prompt += f"Tool '{tr['tool']}' returned: {tr['result']}\n"
                prompt += "\nNow formulate your final response using the real data above. Do NOT make up any information. Use only the tool results.\n"

            prompt += f"\n{conversation}"

            llm_response = await self._local_inference(prompt)

            # Check if the LLM wants to call a tool
            tool_call, remaining_text = extract_tool_call(llm_response)

            if tool_call and "tool" in tool_call:
                tool_name = tool_call["tool"]
                tool_args = tool_call.get("args", {})
                logger.info(f"TOOL CALL: {tool_name}({tool_args})")

                result = execute_tool(tool_name, tool_args)
                tool_results.append({
                    "tool": tool_name,
                    "args": tool_args,
                    "result": result,
                })
                audit.log("tool_call", "orchestrator", {"tool": tool_name, "args": tool_args})
                continue
            else:
                # No tool call, this is the final response
                if remaining_text:
                    return remaining_text
                return llm_response

        # If we exhausted tool calls, formulate final response
        prompt = (
            f"{self.persona['personality']}\n\n"
            f"The user asked: {user_input}\n\n"
            f"Here are the results from tools you used:\n"
        )
        for tr in tool_results:
            prompt += f"Tool '{tr['tool']}' returned: {tr['result']}\n"
        prompt += "\nFormulate a concise, natural response using ONLY this real data. Never guess or make up information.\nAssistant:"

        return await self._local_inference(prompt)

    async def process(self, user_input: str) -> str:
        self.session.add_message("user", user_input)
        self.episodic.add_message(
            session_id=self.session.session_id,
            role="user",
            content=user_input,
            persona=self.session.persona,
        )

        # Self-queries bypass everything
        if self._is_self_query(user_input):
            audit.log("route", "MIRROR", {"type": "self_query"})
            response = await self._handle_self_query(user_input)

        # FORGE commands
        elif self._is_forge_command(user_input):
            audit.log("route", "FORGE", {"type": "forge_command"})
            response = await self._handle_forge_command(user_input)

        else:
            # Everything goes through the tool-augmented pipeline
            # The LLM decides whether it needs tools or can answer directly
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