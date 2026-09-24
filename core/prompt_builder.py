"""
Prompt assembly, including EVA.md, the equivalent of CLAUDE.md in Claude Code.

EVA.md lives at the repo root. It's plain markdown you can edit in any editor:
who you are, how E.V.A. should behave, and standing instructions. It is read
fresh on every turn (a tiny file read), so edits take effect immediately with
no restart. When you say "from now on ...", E.V.A. appends a line to the
"Standing instructions" section herself.

Layering per turn (kept short on purpose, since a 3B model gets confused by
long system prompts):
    persona  ->  EVA.md  ->  relevant CORTEX facts  ->  matched SKILL.md bodies
             ->  response rules  ->  tool results for this turn
"""

from __future__ import annotations

from pathlib import Path

EVA_MD = Path("EVA.md")
MAX_EVA_MD_CHARS = 3000
SECTION = "## Standing instructions"


def load_eva_md() -> str:
    try:
        text = EVA_MD.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return ""
    return text[:MAX_EVA_MD_CHARS]


def add_standing_instruction(instruction: str) -> dict:
    """Append '- instruction' under the Standing instructions section (created if missing)."""
    instruction = " ".join(instruction.split()).strip().rstrip(".")
    if len(instruction) < 3:
        return {"status": "ignored"}
    line = f"- {instruction[0].upper()}{instruction[1:]}."
    text = EVA_MD.read_text(encoding="utf-8") if EVA_MD.exists() else "# EVA.md\n"
    if line.lower() in text.lower():
        return {"status": "duplicate", "instruction": line[2:]}
    if SECTION in text:
        head, _, tail = text.partition(SECTION)
        # insert at the end of the section (before the next "## " heading, if any)
        nxt = tail.find("\n## ")
        body, rest = (tail, "") if nxt == -1 else (tail[:nxt], tail[nxt:])
        text = head + SECTION + body.rstrip() + "\n" + line + "\n" + rest
    else:
        text = text.rstrip() + f"\n\n{SECTION}\n{line}\n"
    EVA_MD.write_text(text, encoding="utf-8")
    return {"status": "added", "instruction": line[2:]}


def build_answer_system(persona: str, style_rules: str, facts: list[dict],
                        skill_bodies: list[str], gathered: list[str]) -> str:
    parts = [persona.strip()]
    eva_md = load_eva_md()
    if eva_md:
        parts.append("## Your standing context (EVA.md)\n" + eva_md)
    if facts:
        parts.append("## What you remember about the user (use only if relevant)\n"
                     + "\n".join(f"- {f['text']}" for f in facts))
    if skill_bodies:
        parts.append("## Active skill instructions\n" + "\n\n".join(skill_bodies))
    parts.append(style_rules.strip())
    if gathered:
        parts.append("## Real data from tools this turn (use it, never invent)\n" + "\n".join(gathered))
    return "\n\n".join(parts)


def build_decision_system(tool_list_text: str, skill_bodies: list[str], gathered: list[str]) -> str:
    parts = [
        "You are the tool-router for E.V.A. Decide whether a tool is needed to answer "
        "the user's latest message. Choose exactly one tool from the list, or \"none\" to "
        "answer directly. Fill only the parameter fields the chosen tool needs. Do not "
        "answer the user here; only choose.",
        "Tools:\n" + tool_list_text,
    ]
    if skill_bodies:
        parts.append("Skill instructions that apply to this request:\n" + "\n\n".join(skill_bodies))
    if gathered:
        parts.append("Data already gathered (do NOT call the same tool again; if this "
                     "answers the user, choose \"none\"):\n" + "\n".join(gathered))
    return "\n\n".join(parts)
