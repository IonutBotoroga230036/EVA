"""Shared test doubles: a deterministic fake embedder and skill folder builder."""

import hashlib
import re
from pathlib import Path

import numpy as np

_W = re.compile(r"[a-z0-9]+")
_PREFIX = re.compile(r"^search_(document|query):\s*")


class FakeEmbedder:
    """Bag-of-words hashed into 256 dims. Similar wording -> high cosine."""

    def __call__(self, texts):
        out = []
        for t in texts:
            v = np.zeros(256, dtype=np.float32)
            for w in _W.findall(_PREFIX.sub("", t.lower())):
                v[int(hashlib.md5(w.encode()).hexdigest(), 16) % 256] += 1.0
            out.append(v)
        return out


def make_skill(root: Path, name: str, description: str, *, trusted=True, tools_py: str | None = None,
               triggers=None, body="Do the thing.") -> Path:
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    trig = f"triggers: {triggers}\n" if triggers else ""
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\nenabled: true\n"
        f"trusted: {'true' if trusted else 'false'}\n{trig}---\n{body}\n", encoding="utf-8")
    if tools_py:
        (d / "tools.py").write_text(tools_py, encoding="utf-8")
    return d


ECHO_TOOLS = '''
import json
TOOLS = [{"type": "function", "function": {"name": "echo_tool",
          "description": "Echo a word back.",
          "parameters": {"type": "object", "properties": {
              "word": {"type": "string"},
              "action": {"type": "string", "enum": ["a", "b"]}}}}}]
CALLS = []
def echo_tool(word="", **_):
    CALLS.append(word)
    if word == "boom":
        return {"result": json.dumps({"error": "exploded"})}
    return {"result": json.dumps({"echo": word}), "widget": {"kind": "echo", "title": "Echo", "text": word}}
FUNCTIONS = {"echo_tool": echo_tool}
ACKS = {"echo_tool": "Echoing, sir."}
'''
