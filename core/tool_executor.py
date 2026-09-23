"""
Tool Executor - Gives the LLM access to real tools.
Instead of keyword matching, the LLM decides which tool to call.
Tools return real data. The LLM formulates the response.
"""

import json
import webbrowser
import subprocess
import os
from datetime import datetime
from pathlib import Path
from loguru import logger

try:
    from duckduckgo_search import DDGS
    SEARCH_OK = True
except ImportError:
    SEARCH_OK = False

try:
    import pyautogui
    PYAUTOGUI_OK = True
except ImportError:
    PYAUTOGUI_OK = False


# === TOOL DEFINITIONS (what the LLM sees) ===

TOOLS_DESCRIPTION = """You have access to these tools. To use one, respond with a JSON block:
{"tool": "tool_name", "args": {"param": "value"}}

If you don't need a tool, just respond normally.

AVAILABLE TOOLS:

get_datetime()
  Returns the current date and time. Use when asked about today's date, current time, day of the week.

web_search(query: str)
  Searches the internet via DuckDuckGo. Use for weather, news, prices, current events, anything requiring up-to-date info.
  Always use this instead of guessing at current information.

open_website(name: str)
  Opens a website in the browser. name can be a URL or a known site like "youtube", "facebook", "gmail", "linkedin", etc.

open_app(name: str)
  Opens a desktop application. name can be "vscode", "notepad", "calculator", "terminal", "sticky notes", "file explorer", etc.

take_screenshot()
  Takes a screenshot of the screen and saves it with a timestamp.

google_search(query: str)
  Opens Google in the browser with the search query. Use when the user wants to browse results themselves.

IMPORTANT RULES:
- If you need CURRENT information (weather, news, time, prices, scores), you MUST use a tool. NEVER guess or make up current data.
- If asked about the time or date, use get_datetime. Do not guess.
- If asked about weather, use web_search. Do not guess.
- You can chain tools: first get_datetime for today's date, then web_search for weather.
- After receiving tool results, formulate a natural response using the real data.
"""

# === KNOWN SITES & APPS ===

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
    "tiktok": "https://www.tiktok.com",
    "amazon": "https://www.amazon.com",
    "twitch": "https://www.twitch.tv",
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


# === TOOL IMPLEMENTATIONS ===

def tool_get_datetime(**kwargs) -> str:
    now = datetime.now()
    return json.dumps({
        "time": now.strftime("%H:%M:%S"),
        "date": now.strftime("%A, %B %d, %Y"),
        "timestamp": now.isoformat(),
        "timezone": "CET/CEST (Netherlands)",
    })


def tool_web_search(query: str = "", **kwargs) -> str:
    if not SEARCH_OK:
        return json.dumps({"error": "duckduckgo-search not installed"})
    try:
        results = []
        with DDGS() as ddgs:
            for r in ddgs.text(query, max_results=5):
                results.append({
                    "title": r.get("title", ""),
                    "snippet": r.get("body", ""),
                    "url": r.get("href", ""),
                })
        return json.dumps({"query": query, "results": results})
    except Exception as e:
        return json.dumps({"error": str(e)})


def tool_open_website(name: str = "", **kwargs) -> str:
    lower = name.lower().strip()
    url = KNOWN_SITES.get(lower)
    if not url:
        if "." in name:
            url = name if name.startswith("http") else "https://" + name
        else:
            url = f"https://www.{lower}.com"
    webbrowser.open(url)
    return json.dumps({"opened": url})


def tool_open_app(name: str = "", **kwargs) -> str:
    lower = name.lower().strip()
    executable = KNOWN_APPS.get(lower, lower)
    try:
        if executable.startswith("ms-") or "shell:appsFolder" in executable:
            os.system(f'start "" "{executable}"')
        else:
            subprocess.Popen(executable, shell=True)
        return json.dumps({"opened": name})
    except Exception as e:
        return json.dumps({"error": str(e)})


def tool_take_screenshot(**kwargs) -> str:
    if not PYAUTOGUI_OK:
        return json.dumps({"error": "pyautogui not installed"})
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join("data", "screenshots", f"screenshot_{timestamp}.png")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    pyautogui.screenshot().save(path)
    return json.dumps({"saved": path, "timestamp": timestamp})


def tool_google_search(query: str = "", **kwargs) -> str:
    url = f"https://www.google.com/search?q={query.replace(' ', '+')}"
    webbrowser.open(url)
    return json.dumps({"searched": query, "url": url})


# === TOOL REGISTRY ===

TOOLS = {
    "get_datetime": tool_get_datetime,
    "web_search": tool_web_search,
    "open_website": tool_open_website,
    "open_app": tool_open_app,
    "take_screenshot": tool_take_screenshot,
    "google_search": tool_google_search,
}


def execute_tool(tool_name: str, args: dict) -> str:
    """Execute a tool by name with given arguments."""
    if tool_name not in TOOLS:
        return json.dumps({"error": f"Unknown tool: {tool_name}"})
    try:
        result = TOOLS[tool_name](**args)
        logger.info(f"TOOL: {tool_name}({args}) -> success")
        return result
    except Exception as e:
        logger.error(f"TOOL: {tool_name} failed: {e}")
        return json.dumps({"error": str(e)})


def extract_tool_call(llm_response: str) -> tuple[dict | None, str]:
    """
    Parse the LLM response for a tool call.
    Returns (tool_call_dict, remaining_text).
    If no tool call found, returns (None, original_text).
    """
    text = llm_response.strip()

    # Try to find JSON in the response
    # Look for {"tool": ...} pattern
    start = text.find('{"tool"')
    if start == -1:
        start = text.find("{'tool'")
    if start == -1:
        return None, text

    # Find the matching closing brace
    depth = 0
    end = start
    for i in range(start, len(text)):
        if text[i] == '{':
            depth += 1
        elif text[i] == '}':
            depth -= 1
            if depth == 0:
                end = i + 1
                break

    try:
        json_str = text[start:end]
        # Handle single quotes
        json_str = json_str.replace("'", '"')
        tool_call = json.loads(json_str)
        remaining = text[:start] + text[end:]
        return tool_call, remaining.strip()
    except json.JSONDecodeError:
        return None, text