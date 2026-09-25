"""
Vision skill: capture the primary screen and ask a local multimodal model.
Pattern adapted from im4peace/Jarvis see_screen.py (MIT). Uses a SEPARATE
vision model because sending images to the text model fails inside Ollama.

VRAM note (6 GB card): the vision model loads only when called and unloads
right after (keep_alive 0), so the everyday text model stays resident and fast.
"""

import base64
import io
import json
import re
from datetime import datetime
from pathlib import Path

import httpx
from loguru import logger

from core.security.audit import audit
from core.settings import local_cfg

SHOT_DIR = Path("data/screenshots")
MAX_EDGE = 1280
PROMPT = ("You are looking at a screenshot of the user's computer screen. {q} "
          "Be concise and concrete. Quote short visible text exactly when it matters.")

TOOLS = [{"type": "function", "function": {
    "name": "see_screen",
    "description": "Take a screenshot and answer a question about what is visible on the user's screen.",
    "parameters": {"type": "object", "properties": {
        "question": {"type": "string", "description": "What the user wants to know about the screen."}}}}}]


def see_screen(question: str = "", **_):
    try:
        import pyautogui
        img = pyautogui.screenshot()
    except Exception as e:
        return {"result": json.dumps({"error": f"screenshot failed: {e}"})}
    img.thumbnail((MAX_EDGE, MAX_EDGE))
    SHOT_DIR.mkdir(parents=True, exist_ok=True)
    path = SHOT_DIR / f"screen_{datetime.now():%Y%m%d_%H%M%S}.png"
    img.save(path)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode()

    cfg = local_cfg()
    q = question.strip() or "Describe what is on the screen."
    try:
        r = httpx.post(f"{cfg['base_url']}/api/chat", timeout=180, json={
            "model": cfg["vision_model"], "stream": False, "keep_alive": 0,
            "options": {"temperature": 0.2},
            "messages": [{"role": "user", "content": PROMPT.format(q=q), "images": [b64]}],
        })
        r.raise_for_status()
        answer = r.json()["message"]["content"].strip()
    except Exception as e:
        logger.error(f"see_screen failed: {e}")
        return {"result": json.dumps({"error": f"vision model unavailable ({e}). "
                                              f"Run: ollama pull {cfg['vision_model']}"})}
    audit.log("see_screen", "vision", {"screenshot": str(path)})
    text = " ".join(answer.split())
    text = re.sub(r"^(the (screenshot|image|screen) (shows|displays|contains)|in the (screenshot|image),?)\s*",
                  "I can see ", text, flags=re.I)
    sentences = re.split(r"(?<=[.!?])\s+", text)
    say = " ".join(sentences[:2])[:320].rstrip(".") + ", sir."
    return {"result": json.dumps({"you_just_looked_at_the_screen": True, "what_you_see": answer}),
            "widget": {"kind": "vision", "title": "On your screen", "text": answer[:280]},
            "say": say, "exact": True}


FUNCTIONS = {"see_screen": see_screen}
ACKS = {"see_screen": "Taking a look, sir."}
