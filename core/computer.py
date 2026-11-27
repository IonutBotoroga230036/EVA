"""Using your computer (v0.7): Claude's computer toolset (computer_toolset_20260801), run here with pyautogui.

She sees the screen (screenshots, scaled to at most 1366 wide so it's cheap and inside the API's limits) and moves
the mouse, clicks, types and presses keys. Always as a background task you said yes to:

    "Eva, turn on Bluetooth on my PC"  -> "Just to confirm, sir: use your mouse and keyboard to turn on Bluetooth.
                                         I'll stop if you say stop or push the mouse into a corner. Go ahead?"

Settings ("Using your computer"): off, watch (screenshots only, she can't click), ask (each task asks first, then
she acts). Safety, whatever is on screen (it's data, never instructions):
- pyautogui's fail-safe: shove the mouse into a corner of the screen and every action stops at once
- "stop" / "cancel the task" ends it between two actions
- she never types card or account numbers, and the task's instructions forbid passwords, payments, sending
  messages, deleting files and changing accounts without asking you in the task (ask_user)
- a short pause after each action, so you can see what she does
"""

from __future__ import annotations

import base64
import re
import time
from io import BytesIO
from typing import Optional

from loguru import logger

ENTRY = {"type": "computer_toolset_20260801"}
MAX_W, MAX_H = 1366, 800
LOOK_ONLY = {"screenshot", "zoom", "cursor_position", "wait"}
_CARD = re.compile(r"(?:\d[ -]?){13,19}")
KEYS = {"return": "enter", "enter": "enter", "escape": "esc", "esc": "esc", "tab": "tab", "backspace": "backspace",
        "delete": "delete", "up": "up", "down": "down", "left": "left", "right": "right", "page_up": "pageup",
        "pageup": "pageup", "page_down": "pagedown", "pagedown": "pagedown", "home": "home", "end": "end",
        "space": "space", "ctrl": "ctrl", "control": "ctrl", "alt": "alt", "shift": "shift", "super": "win",
        "cmd": "win", "meta": "win", "win": "win", "windows": "win", "print": "printscreen", "insert": "insert"}


class ComputerError(Exception):
    pass


def cfg() -> dict:
    try:
        from core.settings import get_settings
        return {"mode": "ask", **(get_settings().get("computer") or {})}
    except Exception:
        return {"mode": "ask"}


def mode() -> str:
    m = str(cfg().get("mode", "ask"))
    return m if m in ("off", "watch", "ask", "auto") else "ask"


def acts_alone(args: dict | None = None) -> bool:
    """v0.8: 'Act without asking' in the settings: use_computer starts at once (the corner stop and the
    never-list stay)."""
    return mode() == "auto"


def _page_for(goal: str) -> str:
    g = (goal or "").lower()
    if not re.search(r"\b(settings?|turn (on|off)|switch (on|off)|enable|disable|connect|disconnect|pair)\b", g):
        return ""
    return next((k for k in sorted(SETTINGS_PAGES, key=len, reverse=True) if re.search(rf"\b{re.escape(k)}\b", g)), "")


def _gui():
    import pyautogui
    pyautogui.FAILSAFE = True                 # mouse into a corner -> FailSafeException, everything stops
    pyautogui.PAUSE = 0.25
    return pyautogui


def _keys(text: str) -> list[str]:
    out = []
    for part in [p for p in re.split(r"\s*\+\s*", str(text).strip()) if p]:
        k = KEYS.get(part.lower(), part.lower())
        if re.fullmatch(r"f\d{1,2}", k) or len(k) == 1 or k in KEYS.values():
            out.append(k)
        else:
            out.append(k)
    return out


class Computer:
    def __init__(self, watch_only: bool = False):
        self.watch_only = watch_only
        self.scale = 1.0
        self.last_png: bytes = b""

    def _shot(self):
        from PIL import ImageGrab
        img = ImageGrab.grab(all_screens=False)
        self.scale = min(1.0, MAX_W / img.width, MAX_H / img.height)
        if self.scale < 1.0:
            img = img.resize((int(img.width * self.scale), int(img.height * self.scale)))
        return img

    def _xy(self, c) -> tuple[int, int]:
        if not c or len(c) != 2:
            raise ComputerError("coordinate must be [x, y]")
        return int(float(c[0]) / self.scale), int(float(c[1]) / self.scale)

    def _png(self, img) -> list:
        buf = BytesIO()
        img.save(buf, "PNG")
        self.last_png = buf.getvalue()
        return [{"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                             "data": base64.b64encode(self.last_png).decode()}}]

    def run(self, name: str, inp: dict) -> list:
        if self.watch_only and name not in LOOK_ONLY:
            raise ComputerError("Watch only: I can look at the screen but not click or type. Computer use can be set "
                                "to 'ask' in the settings.")
        g = _gui() if name not in ("screenshot", "zoom", "wait") else None
        if name == "screenshot":
            return self._png(self._shot())
        if name == "zoom":
            from PIL import ImageGrab
            full = ImageGrab.grab(all_screens=False)
            x0, y0, x1, y1 = [float(v) / self.scale for v in inp.get("region") or [0, 0, full.width, full.height]]
            crop = full.crop((int(x0), int(y0), int(x1), int(y1)))
            f = min(4.0, MAX_W / max(1, crop.width), MAX_H / max(1, crop.height))
            return self._png(crop.resize((max(1, int(crop.width * f)), max(1, int(crop.height * f)))))
        if name == "cursor_position":
            x, y = g.position()
            return [{"type": "text", "text": f"X={int(x * self.scale)}, Y={int(y * self.scale)}"}]
        if name == "wait":
            time.sleep(max(0.0, min(float(inp.get("duration") or 1), 10.0)))
            return [{"type": "text", "text": "OK"}]
        mods = _keys(inp.get("text", "")) if name.endswith("click") and inp.get("text") else []
        if name in ("left_click", "right_click", "middle_click", "double_click", "triple_click"):
            if inp.get("coordinate"):
                g.moveTo(*self._xy(inp["coordinate"]), duration=0.2)
            button = {"right_click": "right", "middle_click": "middle"}.get(name, "left")
            clicks = {"double_click": 2, "triple_click": 3}.get(name, 1)
            for m in mods:
                g.keyDown(m)
            try:
                g.click(button=button, clicks=clicks, interval=0.08)
            finally:
                for m in reversed(mods):
                    g.keyUp(m)
        elif name == "mouse_move":
            g.moveTo(*self._xy(inp.get("coordinate")), duration=0.2)
        elif name == "left_click_drag":
            g.moveTo(*self._xy(inp.get("start_coordinate")), duration=0.2)
            g.dragTo(*self._xy(inp.get("coordinate")), duration=0.4, button="left")
        elif name == "left_mouse_down":
            g.mouseDown()
        elif name == "left_mouse_up":
            g.mouseUp()
        elif name == "scroll":
            if inp.get("coordinate"):
                g.moveTo(*self._xy(inp["coordinate"]), duration=0.15)
            amount = max(1, min(int(inp.get("scroll_amount") or 3), 30))
            d = inp.get("scroll_direction", "down")
            if d in ("up", "down"):
                g.scroll(120 * amount * (1 if d == "up" else -1))
            else:
                g.hscroll(120 * amount * (1 if d == "right" else -1))
        elif name == "type":
            text = str(inp.get("text") or "")
            if _CARD.search(text):
                raise ComputerError("that looks like a card or account number; I never type those")
            g.write(text, interval=0.02)
        elif name == "key":
            keys = _keys(inp.get("text", ""))
            for _ in range(max(1, min(int(inp.get("repeat") or 1), 30))):
                g.hotkey(*keys) if len(keys) > 1 else g.press(keys[0])
        elif name == "hold_key":
            keys = _keys(inp.get("text", ""))
            for k in keys:
                g.keyDown(k)
            time.sleep(max(0.0, min(float(inp.get("duration") or 0.5), 5.0)))
            for k in reversed(keys):
                g.keyUp(k)
        else:
            raise ComputerError(f"{name} isn't supported here")
        time.sleep(0.35)                         # let the screen settle (and you see what happened)
        return [{"type": "text", "text": "OK"}]


def result(use: dict, computer: Computer) -> dict:
    """A computer tool_use -> its tool_result (toolset_name echoed). The fail-safe stops the whole task."""
    try:
        content = computer.run(use["name"], use.get("input") or {})
        if use["name"] not in ("screenshot", "zoom", "cursor_position"):
            logger.info(f"COMPUTER: {use['name']} {str(use.get('input'))[:100]}")
        return {"type": "tool_result", "tool_use_id": use["id"], "toolset_name": "computer", "content": content}
    except ComputerError as e:
        return {"type": "tool_result", "tool_use_id": use["id"], "toolset_name": "computer", "is_error": True,
                "content": str(e)}
    except Exception as e:
        if e.__class__.__name__ == "FailSafeException":
            raise                                 # you pushed the mouse into a corner: the task stops here
        logger.warning(f"COMPUTER: {use['name']} failed: {e}")
        return {"type": "tool_result", "tool_use_id": use["id"], "toolset_name": "computer", "is_error": True,
                "content": f"{e.__class__.__name__}: {str(e)[:300]}"}


# ---------------------------------------------------------------- conversation: start a computer task, settings pages
SETTINGS_PAGES = {"bluetooth": "ms-settings:bluetooth", "wifi": "ms-settings:network-wifi", "wi-fi":
                  "ms-settings:network-wifi", "network": "ms-settings:network", "display": "ms-settings:display",
                  "sound": "ms-settings:sound", "notifications": "ms-settings:notifications", "power":
                  "ms-settings:powersleep", "battery": "ms-settings:batterysaver", "apps": "ms-settings:appsfeatures",
                  "default apps": "ms-settings:defaultapps", "privacy": "ms-settings:privacy", "updates":
                  "ms-settings:windowsupdate", "windows update": "ms-settings:windowsupdate", "personalization":
                  "ms-settings:personalization", "background": "ms-settings:personalization-background", "mouse":
                  "ms-settings:mousetouchpad", "keyboard": "ms-settings:keyboard", "printers": "ms-settings:printers",
                  "storage": "ms-settings:storagesense", "night light": "ms-settings:nightlight", "focus":
                  "ms-settings:quiethours", "vpn": "ms-settings:network-vpn", "hotspot": "ms-settings:network-mobilehotspot",
                  "camera": "ms-settings:privacy-webcam", "microphone": "ms-settings:privacy-microphone",
                  "date": "ms-settings:dateandtime", "language": "ms-settings:regionlanguage"}


def tool_open_settings(page: str = "", **_):
    import json
    import os
    p = (page or "").lower().strip()
    uri = next((v for k, v in sorted(SETTINGS_PAGES.items(), key=lambda kv: -len(kv[0])) if k in p), "ms-settings:")
    starter = getattr(os, "startfile", None)
    if starter is None:
        return {"result": json.dumps({"error": "Windows settings open on Windows"})}
    starter(uri)
    name = next((k for k, v in SETTINGS_PAGES.items() if v == uri and k in p), "") or "the settings"
    return {"result": json.dumps({"opened": uri}), "say": f"Opening {name} settings on your PC, sir.", "exact": True}


def tool_use_computer(goal: str = "", **_):
    import json
    goal = " ".join((goal or "").split())
    m = mode()
    if m == "off":
        return {"result": json.dumps({"error": "computer use is off"}), "exact": True,
                "say": "Using your computer is switched off in the settings, sir."}
    if len(goal) < 6:
        return {"result": json.dumps({"error": "no goal"}), "say": "What should I do on your computer, sir?"}
    from core.agent.runner import get_manager
    from core.agent.tasks import name_task
    task_goal = goal
    page = _page_for(goal) if m != "watch" else ""
    if page and getattr(__import__("os"), "startfile", None):       # v0.8: open the page first, saves a few steps
        try:
            tool_open_settings(page)
            task_goal = f"{goal}\n(The Windows {page} settings page was just opened for you; start from it.)"
        except Exception:
            pass
    t = get_manager().start(task_goal, 0.0, name_task(goal), "",
                            toolsets=["computer:" + ("watch" if m == "watch" else "act")])
    look = "look at your screen" if m == "watch" else "use your mouse and keyboard"
    return {"result": json.dumps({"task": t.id, "title": t.title}), "exact": True,
            "say": f"On it, sir: I'll {look} to {goal[0].lower() + goal[1:]}. Say stop, or push the mouse into a "
                   f"corner, and I stop at once."}


def describe_use(args: dict) -> str:
    look = "look at your screen" if mode() == "watch" else "use your mouse and keyboard"
    return f"{look} to {args.get('goal', '')}. I'll stop if you say stop or push the mouse into a corner"


SCHEMAS = [
    {"type": "function", "function": {
        "name": "use_computer",
        "description": "Do something on his PC with the mouse and keyboard, seeing the screen, as a background "
                       "task: change a Windows setting ('turn on Bluetooth'), work in an app, follow on-screen "
                       "steps. Asks him first. Not for things another tool does directly.",
        "parameters": {"type": "object", "properties": {"goal": {"type": "string"}}, "required": ["goal"]}}},
    {"type": "function", "function": {
        "name": "open_settings",
        "description": "Open a Windows settings page on the PC: bluetooth, wifi, display, sound, notifications, "
                       "power, apps, default apps, privacy, updates, background, mouse, keyboard, printers, storage, "
                       "night light, vpn, camera, microphone.",
        "parameters": {"type": "object", "properties": {"page": {"type": "string"}}, "required": ["page"]}}},
]
FUNCTIONS = {"use_computer": tool_use_computer, "open_settings": tool_open_settings}
GUARDS = {"use_computer": r"\b(turn|switch|click|type|mouse|cursor|keyboard|computer|pc|screen|open|change|set)\w*\b",
          "open_settings": r"\b(settings?|bluetooth|wi-?fi|display|sound|notifications?|power|updates?|background)\b"}
ACTIONS = {"use_computer"}
CONFIRM = {"use_computer": describe_use}
CONFIRM_UNLESS = {"use_computer": acts_alone}      # v0.8: no question in 'Act without asking' mode
