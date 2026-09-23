"""
Computer Control Skill - Lets E.V.A. interact with your desktop.
Opens URLs, launches apps, types text, takes screenshots.
"""

import subprocess
import webbrowser
import os
from datetime import datetime
from loguru import logger


KNOWN_SITES = {
    "youtube": "https://www.youtube.com",
    "facebook": "https://www.facebook.com",
    "twitter": "https://www.twitter.com",
    "x": "https://www.x.com",
    "github": "https://www.github.com",
    "linkedin": "https://www.linkedin.com",
    "reddit": "https://www.reddit.com",
    "instagram": "https://www.instagram.com",
    "google": "https://www.google.com",
    "gmail": "https://mail.google.com",
    "whatsapp": "https://web.whatsapp.com",
    "spotify": "https://open.spotify.com",
    "netflix": "https://www.netflix.com",
    "chatgpt": "https://chat.openai.com",
    "claude": "https://claude.ai",
    "notion": "https://www.notion.so",
    "calendar": "https://calendar.google.com",
    "google calendar": "https://calendar.google.com",
    "outlook": "https://outlook.live.com",
    "google drive": "https://drive.google.com",
    "maps": "https://maps.google.com",
}

KNOWN_APPS = {
    "notepad": "notepad.exe",
    "calculator": "calc.exe",
    "file explorer": "explorer.exe",
    "explorer": "explorer.exe",
    "terminal": "wt.exe",
    "command prompt": "cmd.exe",
    "cmd": "cmd.exe",
    "task manager": "taskmgr.exe",
    "settings": "ms-settings:",
    "vscode": "code",
    "vs code": "code",
    "visual studio code": "code",
    "chrome": "chrome",
    "firefox": "firefox",
    "edge": "msedge",
    "sticky notes": "explorer.exe shell:appsFolder\\Microsoft.MicrosoftStickyNotes_8wekyb3d8bbwe!App",
    "paint": "mspaint.exe",
    "snipping tool": "snippingtool",
    "word": "winword",
    "excel": "excel",
    "powerpoint": "powerpnt",
}


class ComputerControlSkill:
    def __init__(self):
        self.name = "computer_control"
        try:
            import pyautogui
            self.has_pyautogui = True
        except ImportError:
            self.has_pyautogui = False

    async def execute(self, user_input: str, eva) -> str:
        lower = user_input.lower()

        # 1. Direct site match (skip LLM entirely)
        for site_name, url in KNOWN_SITES.items():
            if site_name in lower and any(w in lower for w in ["open", "go to", "visit", "show", "launch"]):
                webbrowser.open(url)
                return f"Opening {site_name.title()} for you, sir."

        # 2. Direct app match
        for app_name, cmd in KNOWN_APPS.items():
            if app_name in lower and any(w in lower for w in ["open", "launch", "start", "run"]):
                return self._open_application(app_name, cmd)

        # 3. Screenshot
        if "screenshot" in lower or "screen capture" in lower:
            return self._take_screenshot()

        # 4. Volume
        if "volume" in lower:
            if "up" in lower or "louder" in lower:
                return self._adjust_volume("up")
            elif "down" in lower or "quieter" in lower or "lower" in lower:
                return self._adjust_volume("down")
            elif "mute" in lower:
                return self._adjust_volume("mute")

        # 5. URL detection (anything with a dot that looks like a URL)
        words = user_input.split()
        for w in words:
            if "." in w and any(w.endswith(tld) for tld in [".com", ".org", ".net", ".io", ".ai", ".nl", ".dev"]):
                url = w if w.startswith("http") else "https://" + w
                webbrowser.open(url)
                return f"Opening {url} for you, sir."

        # 6. Google search as fallback for "search for X"
        if any(w in lower for w in ["search", "google", "look up", "find"]):
            query = lower
            for w in ["search for", "search", "google", "look up", "find", "eva", "eva,"]:
                query = query.replace(w, "")
            query = query.strip()
            if query:
                url = f"https://www.google.com/search?q={query.replace(' ', '+')}"
                webbrowser.open(url)
                return f"Searching Google for '{query}', sir."

        # 7. Fallback: use LLM to classify
        try:
            import json
            prompt = (
                "You are a command parser. Given the user's request, determine the desktop action.\n"
                'Respond with ONLY valid JSON:\n'
                '{"type": "open_url|open_app|search_web|type_text|screenshot", "target": "value"}\n\n'
                f"User request: {user_input}\nJSON:"
            )
            result = await eva._local_inference(prompt, model=eva.config["inference"]["local"]["router_model"])
            result = result.strip()
            if result.startswith("```"):
                result = result.split("\n", 1)[1].rsplit("```", 1)[0]
            action = json.loads(result)

            if action.get("type") == "open_url":
                url = action["target"]
                if not url.startswith("http"):
                    url = "https://" + url
                webbrowser.open(url)
                return f"Opening {url} for you, sir."
            elif action.get("type") == "open_app":
                return self._open_application(action["target"], action["target"])
            elif action.get("type") == "search_web":
                url = f"https://www.google.com/search?q={action['target'].replace(' ', '+')}"
                webbrowser.open(url)
                return f"Searching for '{action['target']}', sir."
        except Exception:
            pass

        return "I'm not sure what to open, sir. Try saying 'open YouTube' or 'open VS Code'."

    def _open_application(self, display_name: str, cmd: str = None) -> str:
        lower = display_name.lower().strip()
        executable = KNOWN_APPS.get(lower, cmd or lower)
        try:
            if executable.startswith("ms-") or executable.startswith("explorer.exe shell:"):
                os.system(f'start "" "{executable}"')
            else:
                subprocess.Popen(executable, shell=True)
            return f"Opening {display_name} for you, sir."
        except Exception as e:
            return f"Couldn't open {display_name}, sir: {e}"

    def _take_screenshot(self) -> str:
        if not self.has_pyautogui:
            return "I need pyautogui for screenshots, sir. Run: pip install pyautogui"
        import pyautogui
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = os.path.join("data", "screenshots", f"screenshot_{timestamp}.png")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        screenshot = pyautogui.screenshot()
        screenshot.save(path)
        return f"Screenshot saved as screenshot_{timestamp}.png, sir."

    def _adjust_volume(self, direction: str) -> str:
        if not self.has_pyautogui:
            return "I need pyautogui for volume control, sir."
        import pyautogui
        if "up" in direction:
            pyautogui.press("volumeup", presses=5)
            return "Volume increased, sir."
        elif "down" in direction:
            pyautogui.press("volumedown", presses=5)
            return "Volume decreased, sir."
        elif "mute" in direction:
            pyautogui.press("volumemute")
            return "Audio muted, sir."
        return "Specify up, down, or mute, sir."