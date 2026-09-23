"""
OpenJarvis Bridge - Lets E.V.A. use OpenJarvis's engine,
connectors, and skills while maintaining her own personality.

E.V.A. is the face. OpenJarvis is the engine under the hood.
"""

import subprocess
import json
import asyncio
from loguru import logger


class JarvisBridge:
    """Bridge between E.V.A.'s orchestrator and OpenJarvis."""

    def __init__(self):
        self._available = self._check_available()
        if self._available:
            logger.info("BRIDGE: OpenJarvis detected and available")
        else:
            logger.warning("BRIDGE: OpenJarvis not found. Running without it.")

    def _check_available(self) -> bool:
        try:
            result = subprocess.run(
                ["jarvis", "--version"],
                capture_output=True, text=True, timeout=10
            )
            return result.returncode == 0
        except Exception:
            return False

    @property
    def available(self) -> bool:
        return self._available

    async def ask(self, query: str, model: str | None = None) -> str:
        """Send a query through OpenJarvis and return the response."""
        if not self._available:
            return None

        try:
            cmd = ["jarvis", "ask", "--no-banner", query]
            if model:
                cmd.extend(["--model", model])

            result = await asyncio.to_thread(
                subprocess.run,
                cmd,
                capture_output=True,
                text=True,
                timeout=120,
            )

            if result.returncode == 0:
                response = result.stdout.strip()
                # Remove the OpenJarvis banner if present
                lines = response.split("\n")
                clean_lines = []
                skip = False
                for line in lines:
                    if "OpenJarvis" in line or "Personal AI" in line or line.strip().startswith("/"):
                        skip = True
                        continue
                    if skip and line.strip() == "":
                        skip = False
                        continue
                    if not skip:
                        clean_lines.append(line)
                response = "\n".join(clean_lines).strip()

                if response:
                    logger.debug(f"BRIDGE: Got response ({len(response)} chars)")
                    return response
                else:
                    return None
            else:
                logger.warning(f"BRIDGE: jarvis ask failed: {result.stderr}")
                return None

        except subprocess.TimeoutExpired:
            logger.warning("BRIDGE: OpenJarvis timed out")
            return None
        except Exception as e:
            logger.error(f"BRIDGE: Error: {e}")
            return None

    async def ask_with_skill(self, query: str, skill_name: str) -> str:
        """Run a query using a specific OpenJarvis skill."""
        if not self._available:
            return None

        try:
            result = await asyncio.to_thread(
                subprocess.run,
                ["jarvis", "skill", "run", skill_name, "--input", query],
                capture_output=True,
                text=True,
                timeout=120,
            )
            if result.returncode == 0:
                return result.stdout.strip()
            return None
        except Exception as e:
            logger.error(f"BRIDGE: Skill '{skill_name}' error: {e}")
            return None

    async def get_weather(self, location: str = "Breda") -> str:
        """Get weather through OpenJarvis weather connector."""
        return await self.ask(f"What is the current weather in {location}?")

    async def get_news(self) -> str:
        """Get news through OpenJarvis."""
        return await self.ask("Summarize today's top tech news headlines")

    async def search_arxiv(self, query: str) -> str:
        """Search academic papers."""
        return await self.ask_with_skill(query, "arxiv")

    def list_skills(self) -> list[str]:
        """Get list of installed OpenJarvis skills."""
        try:
            result = subprocess.run(
                ["jarvis", "skill", "list", "--json"],
                capture_output=True, text=True, timeout=30
            )
            if result.returncode == 0:
                try:
                    data = json.loads(result.stdout)
                    return [s.get("name", "") for s in data]
                except json.JSONDecodeError:
                    # Parse from text output
                    lines = result.stdout.strip().split("\n")
                    return [l.split()[0] for l in lines if l.strip() and not l.startswith("+")]
            return []
        except Exception:
            return []

    def list_connectors(self) -> dict:
        """Get status of OpenJarvis connectors."""
        try:
            result = subprocess.run(
                ["jarvis", "connect", "--list", "--json"],
                capture_output=True, text=True, timeout=30
            )
            if result.returncode == 0:
                try:
                    return json.loads(result.stdout)
                except json.JSONDecodeError:
                    return {}
            return {}
        except Exception:
            return {}


# Singleton
bridge = JarvisBridge()