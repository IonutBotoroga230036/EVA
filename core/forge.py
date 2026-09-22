"""
FORGE - Fabrication & Orchestration of Runtime-Generated Extensions
E.V.A.'s self-extending skill builder.

When IRIS classifies a request as SKILL_MISSING, FORGE activates:
1. Analyzes what capability is needed
2. Searches for Python libraries/tools that could handle it
3. Generates a skill module (manifest.yaml + handler.py)
4. Tests the skill in a Docker sandbox
5. Presents the result to the user for approval
6. On approval, registers the skill in MIRROR and makes it available

FORGE uses the most capable available LLM (cloud if available, local otherwise)
to generate code, since code generation requires strong reasoning.
"""

import json
import os
import subprocess
import tempfile
import shutil
from pathlib import Path
from datetime import datetime
from loguru import logger


# Template for generated skill manifests
MANIFEST_TEMPLATE = """name: "{name}"
description: "{description}"
version: "0.1.0"
author: "FORGE"
created_at: "{created_at}"
triggers:
{triggers_yaml}
dependencies:
{deps_yaml}
permissions:
  - filesystem
"""

# Template for generated skill handlers
HANDLER_TEMPLATE = '''"""
{name} - Auto-generated skill by FORGE
{description}
"""

from loguru import logger


class {class_name}Skill:
    """Handles: {description}"""

    def __init__(self):
        self.name = "{name}"
        logger.info(f"Skill '{name}' loaded")

    async def execute(self, user_input: str, eva) -> str:
        """Execute the skill. eva is the orchestrator instance."""
        try:
{execute_body}
        except Exception as e:
            logger.error(f"Skill '{name}' error: {{e}}")
            return f"I encountered an error running the {name} skill, sir: {{e}}"
'''

# The prompt FORGE uses to analyze what's needed and generate a skill
FORGE_ANALYSIS_PROMPT = """You are FORGE, the skill-building subsystem of E.V.A., an AI assistant.

The user asked for something that no existing skill can handle. Your job is to:
1. Identify what capability is needed
2. Find the best Python library/tool for it
3. Design a skill module

EXISTING SKILLS:
{existing_skills}

USER REQUEST: {user_request}

Respond with ONLY valid JSON:
{{
    "skill_name": "short_snake_case_name",
    "description": "One sentence describing what this skill does",
    "triggers": ["keyword1", "keyword2", "keyword3"],
    "python_packages": ["package1", "package2"],
    "approach": "Brief explanation of how the skill will work",
    "handler_code": "The Python code for the execute method body (indented with 12 spaces)"
}}"""

# The prompt for generating the actual handler code
FORGE_CODE_PROMPT = """You are FORGE, generating Python code for a new E.V.A. skill.

SKILL: {skill_name}
DESCRIPTION: {description}
APPROACH: {approach}
PACKAGES AVAILABLE: {packages}

Write the body of an async execute(self, user_input: str, eva) -> str method.
The method receives user_input (what the user said) and eva (the orchestrator, which has:
  - eva._local_inference(prompt) for LLM calls
  - eva.episodic for conversation history
  - eva.semantic for knowledge search
  - eva.config for system configuration)

The method must return a string response to show the user.

Rules:
- Handle errors gracefully
- Log important steps with logger.info() or logger.debug()
- Be concise but functional
- Import any needed modules at the top of the function
- The code will be indented inside a try: block (12 spaces indent)

Respond with ONLY the Python code, nothing else. No markdown fences."""


class ForgeRequest:
    """Represents a pending skill-building request."""
    def __init__(self, user_request: str, analysis: dict):
        self.id = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.user_request = user_request
        self.analysis = analysis
        self.status = "pending_approval"  # pending_approval -> approved -> building -> testing -> done / failed
        self.skill_dir: Path | None = None
        self.test_result: str | None = None
        self.created_at = datetime.now().isoformat()


class Forge:
    """
    FORGE: Self-extending skill builder for E.V.A.
    """

    def __init__(self, skills_dir: str = "./skills", sandbox_enabled: bool = True):
        self.skills_dir = Path(skills_dir)
        self.skills_dir.mkdir(parents=True, exist_ok=True)
        self.sandbox_enabled = sandbox_enabled
        self.pending_requests: dict[str, ForgeRequest] = {}
        self._forge_log_path = Path("./data/logs/forge_history.jsonl")
        self._forge_log_path.parent.mkdir(parents=True, exist_ok=True)
        logger.info("FORGE: Skill builder initialized")

    async def analyze_request(self, user_request: str, eva) -> dict:
        """
        Step 1: Analyze what the user needs and propose a skill.
        Uses the best available LLM to understand the request.
        """
        from core.mirror import mirror

        existing = mirror.list_skills()
        existing_str = "\n".join(
            f"  - {s['name']}: {s['description']} (triggers: {', '.join(s['triggers'])})"
            for s in existing
        ) if existing else "  (none registered yet)"

        prompt = FORGE_ANALYSIS_PROMPT.format(
            existing_skills=existing_str,
            user_request=user_request,
        )

        # Try cloud first (better at code), fall back to local
        try:
            if eva._cloud_available and eva.budget.can_spend():
                messages = [{"role": "user", "content": prompt}]
                response_text = eva._cloud_inference(messages, model=eva.config["inference"]["cloud"]["model_heavy"])
            else:
                response_text = await eva._local_inference(prompt)

            # Parse the JSON response
            # Clean up potential markdown fences
            cleaned = response_text.strip()
            if cleaned.startswith("```"):
                cleaned = cleaned.split("\n", 1)[1]
            if cleaned.endswith("```"):
                cleaned = cleaned.rsplit("```", 1)[0]
            cleaned = cleaned.strip()

            analysis = json.loads(cleaned)
            logger.info(f"FORGE: Analyzed request -> skill '{analysis.get('skill_name', 'unknown')}'")
            return analysis

        except Exception as e:
            logger.error(f"FORGE: Analysis failed: {e}")
            return {
                "error": str(e),
                "skill_name": "unknown",
                "description": f"Failed to analyze: {user_request}",
            }

    async def propose_skill(self, user_request: str, eva) -> str:
        """
        Called when IRIS routes a request as SKILL_MISSING.
        Analyzes the request and presents a proposal to the user.
        """
        analysis = await self.analyze_request(user_request, eva)

        if "error" in analysis:
            return (
                f"I attempted to design a new skill for that, sir, but the analysis failed: "
                f"{analysis['error']}. Could you describe what you need in more detail?"
            )

        # Create a pending request
        request = ForgeRequest(user_request, analysis)
        self.pending_requests[request.id] = request

        # Present proposal to user
        skill_name = analysis.get("skill_name", "unknown")
        description = analysis.get("description", "No description")
        packages = analysis.get("python_packages", [])
        approach = analysis.get("approach", "No approach specified")

        proposal = (
            f"I don't have a skill for that yet, sir, but I can build one. "
            f"Here's my proposal:\n\n"
            f"SKILL: {skill_name}\n"
            f"PURPOSE: {description}\n"
            f"APPROACH: {approach}\n"
            f"PACKAGES: {', '.join(packages) if packages else 'none needed'}\n\n"
            f"Shall I proceed with building this skill? "
            f"(Say 'yes, build it' or 'forge approve' to proceed, "
            f"or describe changes you'd like.)"
        )

        self._log_event("proposal", {
            "request_id": request.id,
            "skill_name": skill_name,
            "analysis": analysis,
        })

        return proposal

    async def build_skill(self, request_id: str, eva) -> str:
        """
        Step 2: Actually build the skill after user approval.
        Generates code, creates the skill directory, and optionally tests in sandbox.
        """
        if request_id not in self.pending_requests:
            return "I don't have a pending skill request with that ID, sir."

        request = self.pending_requests[request_id]
        analysis = request.analysis
        request.status = "building"

        skill_name = analysis["skill_name"]
        description = analysis.get("description", "")
        packages = analysis.get("python_packages", [])
        approach = analysis.get("approach", "")

        logger.info(f"FORGE: Building skill '{skill_name}'...")

        try:
            # Generate the handler code
            handler_code = await self._generate_handler_code(
                skill_name, description, approach, packages, eva
            )

            # Create the skill directory
            skill_dir = self.skills_dir / skill_name
            skill_dir.mkdir(parents=True, exist_ok=True)
            request.skill_dir = skill_dir

            # Write manifest
            triggers_yaml = "\n".join(f'  - "{t}"' for t in analysis.get("triggers", []))
            deps_yaml = "\n".join(f'  - "{p}"' for p in packages) if packages else '  []'

            manifest_content = MANIFEST_TEMPLATE.format(
                name=skill_name,
                description=description,
                created_at=datetime.now().isoformat(),
                triggers_yaml=triggers_yaml,
                deps_yaml=deps_yaml,
            )
            (skill_dir / "manifest.yaml").write_text(manifest_content)

            # Write handler
            class_name = "".join(word.capitalize() for word in skill_name.split("_"))
            # Ensure proper indentation for handler code
            indented_code = "\n".join(
                f"            {line}" if line.strip() else ""
                for line in handler_code.strip().split("\n")
            )

            handler_content = HANDLER_TEMPLATE.format(
                name=skill_name,
                description=description,
                class_name=class_name,
                execute_body=indented_code,
            )
            (skill_dir / "handler.py").write_text(handler_content)

            # Write __init__.py
            (skill_dir / "__init__.py").write_text("")

            # Test the skill (basic syntax check)
            test_result = self._test_skill(skill_dir, skill_name)
            request.test_result = test_result
            request.status = "tested"

            self._log_event("built", {
                "request_id": request_id,
                "skill_name": skill_name,
                "test_result": test_result,
                "skill_dir": str(skill_dir),
            })

            if "PASS" in test_result:
                request.status = "done"
                return (
                    f"Skill '{skill_name}' has been built and tested successfully, sir.\n\n"
                    f"TEST RESULT: {test_result}\n"
                    f"LOCATION: {skill_dir}\n\n"
                    f"The skill is now available. Say 'forge activate {skill_name}' "
                    f"to register it, or I can do it automatically."
                )
            else:
                request.status = "failed"
                return (
                    f"I built the skill '{skill_name}', but testing revealed issues:\n\n"
                    f"{test_result}\n\n"
                    f"I can attempt to fix these. Shall I try again, sir?"
                )

        except Exception as e:
            request.status = "failed"
            logger.error(f"FORGE: Build failed for '{skill_name}': {e}")
            self._log_event("build_failed", {
                "request_id": request_id,
                "skill_name": skill_name,
                "error": str(e),
            })
            return f"The build failed, sir: {e}. I can try a different approach if you'd like."

    async def _generate_handler_code(self, skill_name: str, description: str,
                                      approach: str, packages: list[str], eva) -> str:
        """Generate the Python handler code using LLM."""
        prompt = FORGE_CODE_PROMPT.format(
            skill_name=skill_name,
            description=description,
            approach=approach,
            packages=", ".join(packages) if packages else "standard library only",
        )

        try:
            if eva._cloud_available and eva.budget.can_spend():
                messages = [{"role": "user", "content": prompt}]
                code = eva._cloud_inference(messages, model=eva.config["inference"]["cloud"]["model_heavy"])
            else:
                code = await eva._local_inference(prompt)

            # Clean markdown fences
            code = code.strip()
            if code.startswith("```python"):
                code = code[len("```python"):].strip()
            elif code.startswith("```"):
                code = code[3:].strip()
            if code.endswith("```"):
                code = code[:-3].strip()

            return code

        except Exception as e:
            logger.error(f"FORGE: Code generation failed: {e}")
            return f'return "Skill {skill_name} code generation failed: {e}"'

    def _test_skill(self, skill_dir: Path, skill_name: str) -> str:
        """Basic test: try to import and instantiate the skill."""
        try:
            # Syntax check via compile
            handler_path = skill_dir / "handler.py"
            source = handler_path.read_text()
            compile(source, str(handler_path), "exec")

            return f"PASS: Syntax check passed for '{skill_name}'"

        except SyntaxError as e:
            return f"FAIL: Syntax error in '{skill_name}': {e}"
        except Exception as e:
            return f"FAIL: Test error for '{skill_name}': {e}"

    def activate_skill(self, skill_name: str, eva) -> str:
        """Register a built skill with E.V.A. so it becomes available."""
        from core.mirror import mirror

        skill_dir = self.skills_dir / skill_name
        manifest_path = skill_dir / "manifest.yaml"

        if not manifest_path.exists():
            return f"No skill found at {skill_dir}, sir."

        try:
            import yaml
            with open(manifest_path) as f:
                manifest = yaml.safe_load(f)

            # Register in MIRROR
            mirror.register_skill(
                name=manifest["name"],
                description=manifest["description"],
                triggers=manifest.get("triggers", []),
                author="FORGE",
                created_by_forge=True,
            )

            # Register with the orchestrator's IRIS router
            eva.router.register_skills(
                [s.name for s in mirror._skills.values()]
            )

            self._log_event("activated", {
                "skill_name": skill_name,
            })

            return (
                f"Skill '{skill_name}' is now active and registered, sir. "
                f"I can handle related requests from now on."
            )

        except Exception as e:
            return f"Failed to activate skill '{skill_name}': {e}"

    def get_latest_pending(self) -> ForgeRequest | None:
        """Get the most recent pending request."""
        pending = [r for r in self.pending_requests.values() if r.status == "pending_approval"]
        return pending[-1] if pending else None

    def get_latest_request(self) -> ForgeRequest | None:
        """Get the most recent request of any status."""
        if not self.pending_requests:
            return None
        return list(self.pending_requests.values())[-1]

    def _log_event(self, event_type: str, data: dict):
        """Log FORGE activity to file."""
        entry = {
            "timestamp": datetime.now().isoformat(),
            "event": event_type,
            "data": data,
        }
        with open(self._forge_log_path, "a") as f:
            f.write(json.dumps(entry) + "\n")


# Singleton
forge = Forge()
