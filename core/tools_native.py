"""
Built-in tools for E.V.A.

Each tool returns {"result": <JSON string for the model>, "widget": <optional UI card>}.
ACK_PHRASES give the "phone call" feel: a short line spoken the instant a tool is
chosen, before it runs. ACTION_TOOLS change the world (volume, media, apps); after a
successful action the orchestrator stops the tool loop instead of chaining more calls.

Descriptions are written for a 3B model: say exactly when to use the tool and
when NOT to, because the description is the only thing it knows about it.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
import webbrowser
from datetime import datetime

from loguru import logger

from core.weather import get_weather_report

try:
    import pyautogui
    PYAUTOGUI_OK = True
except Exception:
    PYAUTOGUI_OK = False

try:
    from ddgs import DDGS
    SEARCH_OK = True
except Exception:
    try:
        from duckduckgo_search import DDGS
        SEARCH_OK = True
    except Exception:
        SEARCH_OK = False

HOME_CITY = os.environ.get("EVA_HOME_CITY", "Breda")

ACK_PHRASES = {
    "get_weather": "Let me check, sir.",
    "web_search": "Looking into that now, sir.",
    "spotify_play": "Right away, sir.",
    "open_app": "Opening it now, sir.",
    "open_website": "One moment, sir.",
    "media_control": None,        # instant; the confirmation is the answer
    "set_volume": None,
    "get_datetime": None,
    "_default": "One moment, sir.",
}

ACTION_TOOLS = {"spotify_play", "media_control", "set_volume", "open_app", "open_website"}

TOOL_SCHEMAS = [
    {"type": "function", "function": {
        "name": "get_datetime",
        "description": "Get the current local date and time. Use for any question about the time, date, or day.",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "get_weather",
        "description": ("Current weather or a forecast up to 16 days ahead for any city or country. "
                        "Use for ANY weather question, including tomorrow, a weekday, or a specific time. "
                        "Never guess weather and never use web_search for weather."),
        "parameters": {"type": "object", "properties": {
            "city": {"type": "string", "description": f"City or country. Leave empty for {HOME_CITY}, or the city "
                                                      "discussed earlier in the conversation."},
            "day": {"type": "string", "description": "'today', 'tomorrow', a weekday like 'friday', "
                                                     "'in 3 days', or a date like '2026-09-30'. Empty means now."},
            "hour": {"type": "string", "description": "Optional time, copied EXACTLY as the user said it "
                                                      "('6', '6pm', '18:00', 'evening'). Never convert it."},
        }}}},
    {"type": "function", "function": {
        "name": "web_search",
        "description": ("Search the web for current facts about the world: news, prices, people, events. "
                        "Do NOT use for greetings, small talk, weather, or questions about yourself."),
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string", "description": "A short, specific search query."}},
            "required": ["query"]}}},
    {"type": "function", "function": {
        "name": "spotify_play",
        "description": "Start music on Spotify: a genre, playlist, artist, or 'liked songs' if unspecified.",
        "parameters": {"type": "object", "properties": {
            "what": {"type": "string", "description": "What to play, e.g. 'jazz', 'Deep Focus', 'liked songs'."}},
            "required": ["what"]}}},
    {"type": "function", "function": {
        "name": "media_control",
        "description": ("Press a media key: playpause (also used to stop or pause music), next, previous. "
                        "Use 'mute' ONLY when the user literally says mute or unmute."),
        "parameters": {"type": "object", "properties": {
            "action": {"type": "string", "enum": ["playpause", "next", "previous", "volup", "voldown", "mute"]}},
            "required": ["action"]}}},
    {"type": "function", "function": {
        "name": "set_volume",
        "description": ("Set the system volume to an exact level 0 to 100, and unmute. If the user complains "
                        "the volume is at 0 or they can't hear, they want it RAISED, not set to 0."),
        "parameters": {"type": "object", "properties": {
            "level": {"type": "integer", "description": "Target volume, 0 to 100."}},
            "required": ["level"]}}},
    {"type": "function", "function": {
        "name": "open_app",
        "description": "Open a desktop application by name (vscode, notepad, spotify, calculator, chrome, terminal).",
        "parameters": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}}},
    {"type": "function", "function": {
        "name": "open_website",
        "description": "Open a website in the browser when the user asks to open or go to a site (youtube, gmail, a URL).",
        "parameters": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}}},
]


# ------------------------------------------------------------------ tools
def tool_get_datetime(**_):
    now = datetime.now()
    return {"result": json.dumps({"time": now.strftime("%H:%M"), "date": now.strftime("%A, %B %d, %Y")}),
            "widget": {"kind": "clock", "time": now.strftime("%H:%M"), "date": now.strftime("%A, %d %B")}}


def tool_get_weather(city: str = "", day: str = "", hour: str = "", **_):
    try:
        data = get_weather_report((city or HOME_CITY).strip(), day or None, hour or None)
    except Exception as e:
        logger.error(f"weather failed: {e}")
        return {"result": json.dumps({"error": str(e)})}
    if "error" in data:
        return {"result": json.dumps(data)}
    return {"result": json.dumps(data), "widget": {"kind": "weather", **data}}


def _search(query: str) -> list[dict]:
    with DDGS() as d:
        return [{"title": r.get("title", ""), "snippet": r.get("body", ""), "url": r.get("href", "")}
                for r in d.text(query, max_results=5)]


def tool_web_search(query: str = "", **_):
    query = (query or "").strip()
    if not query:
        return {"result": json.dumps({"error": "no search query was given"})}
    if not SEARCH_OK:
        return {"result": json.dumps({"error": "search package not installed (pip install ddgs)"})}
    try:
        results = _search(query)
        if not results:                              # empty results happen; retry once, reworded
            time.sleep(0.5)
            results = _search(f"{query} latest")
        if not results:
            return {"result": json.dumps({"query": query, "results": [],
                                          "note": "no results found; say so, don't guess"})}
        return {"result": json.dumps({"query": query, "results": results})}
    except Exception as e:
        return {"result": json.dumps({"error": str(e)})}


def tool_spotify_play(what: str = "", **_):
    what = (what or "liked songs").strip()
    try:
        webbrowser.open(f"spotify:search:{what.replace(' ', '%20')}")
        if PYAUTOGUI_OK:
            time.sleep(2.0)
            pyautogui.press("playpause")
        return {"result": json.dumps({"status": "playing on Spotify", "requested": what}),
                "widget": {"kind": "nowplaying", "what": what}}
    except Exception as e:
        return {"result": json.dumps({"error": str(e)})}


def tool_media_control(action: str = "playpause", **_):
    keymap = {"playpause": "playpause", "next": "nexttrack", "previous": "prevtrack",
              "volup": "volumeup", "voldown": "volumedown", "mute": "volumemute"}
    if not PYAUTOGUI_OK:
        return {"result": json.dumps({"error": "media keys unavailable (pyautogui not installed)"})}
    try:
        pyautogui.press(keymap.get(action, "playpause"))
        return {"result": json.dumps({"done": action})}
    except Exception as e:
        return {"result": json.dumps({"error": str(e)})}


def tool_set_volume(level: int = 50, **_):
    try:
        level = max(0, min(100, int(level)))
    except Exception:
        level = 50
    try:
        from comtypes import CoInitialize, CoUninitialize
        from pycaw.pycaw import AudioUtilities
        CoInitialize()
        try:
            device = AudioUtilities.GetSpeakers()
            try:
                ev = device.EndpointVolume                          # modern pycaw
            except AttributeError:                                  # legacy pycaw
                from ctypes import POINTER, cast
                from comtypes import CLSCTX_ALL
                from pycaw.pycaw import IAudioEndpointVolume
                ev = cast(device.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None),
                          POINTER(IAudioEndpointVolume))
            ev.SetMasterVolumeLevelScalar(level / 100.0, None)
            if level > 0:
                ev.SetMute(0, None)       # a muted endpoint stays silent at any level
        finally:
            CoUninitialize()
        return {"result": json.dumps({"volume_set": level, "muted": level == 0})}
    except Exception as e:
        logger.error(f"set_volume failed: {e}")
        return {"result": json.dumps({"error": f"volume control failed: {e}"})}


_APPS = {"notepad": "notepad.exe", "calculator": "calc.exe", "vscode": "code", "vs code": "code",
         "spotify": "spotify", "chrome": "chrome", "explorer": "explorer.exe", "terminal": "wt.exe"}
_SITES = {"youtube": "https://youtube.com", "gmail": "https://mail.google.com",
          "github": "https://github.com", "calendar": "https://calendar.google.com",
          "spotify": "https://open.spotify.com", "whatsapp": "https://web.whatsapp.com"}


def tool_open_app(name: str = "", **_):
    if not name.strip():
        return {"result": json.dumps({"error": "no app name was given"})}
    exe = _APPS.get(name.lower().strip(), name)
    try:
        subprocess.Popen(exe, shell=True)
        return {"result": json.dumps({"opened": name})}
    except Exception as e:
        return {"result": json.dumps({"error": str(e)})}


def tool_open_website(name: str = "", **_):
    if not name.strip():
        return {"result": json.dumps({"error": "no website was given"})}
    low = name.lower().strip()
    url = _SITES.get(low) or (name if name.startswith("http") else
                              (f"https://{name}" if "." in name else f"https://www.{low}.com"))
    webbrowser.open(url)
    return {"result": json.dumps({"opened": url})}


REGISTRY = {
    "get_datetime": tool_get_datetime,
    "get_weather": tool_get_weather,
    "web_search": tool_web_search,
    "spotify_play": tool_spotify_play,
    "media_control": tool_media_control,
    "set_volume": tool_set_volume,
    "open_app": tool_open_app,
    "open_website": tool_open_website,
}


def snip(result: str | None, n: int = 220) -> str:
    """Short, single-line view of a tool result for the terminal log."""
    r = " ".join((result or "").split())
    return r if len(r) <= n else r[:n] + "..."


def execute_tool(name: str, args: dict, retries: int = 1) -> dict:
    """Run a built-in tool. Retries once on an exception, then fails honestly."""
    fn = REGISTRY.get(name)
    if fn is None:
        return {"result": json.dumps({"missing_skill": name}), "missing": True}
    last_err = None
    for attempt in range(retries + 1):
        try:
            out = fn(**(args or {}))
            if '"error"' in (out.get("result") or ""):
                logger.warning(f"TOOL {name}({args}) returned an error: {out.get('result')}")
            else:
                logger.info(f"TOOL {name}({args}) ok -> {snip(out.get('result'))}")
            return out
        except Exception as e:
            last_err = e
            logger.warning(f"TOOL {name} attempt {attempt + 1} failed: {e}")
    return {"result": json.dumps({"error": str(last_err)}), "failed": True}


def ack_for(name: str) -> str | None:
    return ACK_PHRASES.get(name, ACK_PHRASES["_default"])
