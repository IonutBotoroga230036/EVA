"""
tools_native.py  ->  core/tools_native.py

Native tool-calling for E.V.A. Uses the Ollama /api/chat `tools` schema so the
model returns a STRUCTURED tool_calls array instead of freeform JSON in prose.
This is what kills the hallucinated weather and the leaked chain-of-thought.

Each tool returns a dict: {"result": <str for the model>, "widget": <optional dict for the UI>}.
ACK_PHRASES give the "phone call" feel: E.V.A. says a short line the instant a
tool is chosen, then runs it, then answers. She never narrates the mechanics.
"""

from __future__ import annotations
import json
import os
import subprocess
import webbrowser
from datetime import datetime

import httpx
from loguru import logger

try:
    import pyautogui
    PYAUTOGUI_OK = True
except Exception:
    PYAUTOGUI_OK = False

try:
    from duckduckgo_search import DDGS
    SEARCH_OK = True
except Exception:
    SEARCH_OK = False


HOME_CITY = os.environ.get("EVA_HOME_CITY", "Breda")

# --- Short spoken acknowledgements, per tool. Keep them human and brief. -----
ACK_PHRASES = {
    "get_weather":    "Let me check, sir.",
    "web_search":     "Looking into that now, sir.",
    "get_calendar":   "One moment, sir. Pulling up your calendar.",
    "spotify_play":   "Right away, sir.",
    "media_control":  "Done, sir.",
    "open_app":       "Opening it now, sir.",
    "open_website":   "One moment, sir.",
    "get_datetime":   None,   # instant, no ack needed
    "_default":       "One moment, sir.",
}


# === TOOL SCHEMAS (Ollama native format) ====================================
TOOL_SCHEMAS = [
    {"type": "function", "function": {
        "name": "get_datetime",
        "description": "Get the current local date and time. Use for any question about the time, date, or day.",
        "parameters": {"type": "object", "properties": {}},
    }},
    {"type": "function", "function": {
        "name": "get_weather",
        "description": "Get real current weather and today's forecast for a city. Use for any weather question. Never guess weather.",
        "parameters": {"type": "object", "properties": {
            "city": {"type": "string", "description": f"City name. Defaults to {HOME_CITY} if the user did not name one."}
        }},
    }},
    {"type": "function", "function": {
        "name": "web_search",
        "description": "Search the web for current facts, news, prices, or anything you are unsure about. Prefer this over guessing.",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string", "description": "The search query."}
        }, "required": ["query"]},
    }},
    {"type": "function", "function": {
        "name": "spotify_play",
        "description": "Play music on Spotify: a genre, playlist name, artist, or the user's liked songs.",
        "parameters": {"type": "object", "properties": {
            "what": {"type": "string", "description": "What to play, e.g. 'jazz', 'Deep Focus', 'Miles Davis'."}
        }, "required": ["what"]},
    }},
    {"type": "function", "function": {
        "name": "media_control",
        "description": "Control media playback on this computer: play/pause, next, previous, volume up/down, mute.",
        "parameters": {"type": "object", "properties": {
            "action": {"type": "string", "enum": ["playpause", "next", "previous", "volup", "voldown", "mute"]}
        }, "required": ["action"]},
    }},
    {"type": "function", "function": {
        "name": "open_app",
        "description": "Open a desktop application by name (e.g. 'vscode', 'notepad', 'spotify', 'calculator').",
        "parameters": {"type": "object", "properties": {
            "name": {"type": "string"}
        }, "required": ["name"]},
    }},
    {"type": "function", "function": {
        "name": "open_website",
        "description": "Open a website in the browser. A known name ('youtube', 'gmail') or a URL.",
        "parameters": {"type": "object", "properties": {
            "name": {"type": "string"}
        }, "required": ["name"]},
    }},
]


# === IMPLEMENTATIONS ========================================================
_WEATHER_CODES = {
    0: "clear", 1: "mainly clear", 2: "partly cloudy", 3: "overcast",
    45: "fog", 48: "rime fog", 51: "light drizzle", 53: "drizzle", 55: "heavy drizzle",
    61: "light rain", 63: "rain", 65: "heavy rain", 71: "light snow", 73: "snow",
    75: "heavy snow", 80: "rain showers", 81: "rain showers", 82: "violent rain showers",
    95: "thunderstorm", 96: "thunderstorm with hail", 99: "severe thunderstorm",
}


def tool_get_datetime(**_):
    now = datetime.now()
    return {
        "result": json.dumps({"time": now.strftime("%H:%M"), "date": now.strftime("%A, %B %d, %Y")}),
        "widget": {"kind": "clock", "time": now.strftime("%H:%M"), "date": now.strftime("%A, %d %B")},
    }


def tool_get_weather(city: str = "", **_):
    city = (city or HOME_CITY).strip()
    try:
        with httpx.Client(timeout=15) as c:
            geo = c.get("https://geocoding-api.open-meteo.com/v1/search",
                        params={"name": city, "count": 1}).json()
            if not geo.get("results"):
                return {"result": json.dumps({"error": f"Could not find {city}"})}
            g = geo["results"][0]
            lat, lon = g["latitude"], g["longitude"]
            fc = c.get("https://api.open-meteo.com/v1/forecast", params={
                "latitude": lat, "longitude": lon,
                "current": "temperature_2m,precipitation,weather_code,wind_speed_10m",
                "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max",
                "timezone": "auto",
            }).json()
        cur = fc["current"]; day = fc["daily"]
        desc = _WEATHER_CODES.get(cur["weather_code"], "unknown")
        data = {
            "city": g["name"], "temp_c": round(cur["temperature_2m"]),
            "conditions": desc, "wind_kmh": round(cur["wind_speed_10m"]),
            "high_c": round(day["temperature_2m_max"][0]), "low_c": round(day["temperature_2m_min"][0]),
            "rain_chance_pct": day["precipitation_probability_max"][0],
        }
        return {
            "result": json.dumps(data),
            "widget": {"kind": "weather", **data},
        }
    except Exception as e:
        logger.error(f"weather failed: {e}")
        return {"result": json.dumps({"error": str(e)})}


def tool_web_search(query: str = "", **_):
    if not SEARCH_OK:
        return {"result": json.dumps({"error": "search not installed"})}
    try:
        out = []
        with DDGS() as d:
            for r in d.text(query, max_results=5):
                out.append({"title": r.get("title", ""), "snippet": r.get("body", ""), "url": r.get("href", "")})
        return {"result": json.dumps({"query": query, "results": out})}
    except Exception as e:
        return {"result": json.dumps({"error": str(e)})}


def tool_spotify_play(what: str = "", **_):
    # Opens Spotify via URI search and starts playback with the media key.
    # Full track-level control needs the Spotify Web API (spotipy) + a dev app;
    # this is the honest local-only version until that is wired.
    try:
        uri = f"spotify:search:{what.replace(' ', '%20')}"
        webbrowser.open(uri)
        if PYAUTOGUI_OK:
            import time
            time.sleep(2.0)
            pyautogui.press("playpause")
        return {
            "result": json.dumps({"playing": what, "note": "opened Spotify search; full control pending Web API"}),
            "widget": {"kind": "nowplaying", "what": what},
        }
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


_APPS = {"notepad": "notepad.exe", "calculator": "calc.exe", "vscode": "code", "vs code": "code",
         "spotify": "spotify", "chrome": "chrome", "explorer": "explorer.exe", "terminal": "wt.exe"}
_SITES = {"youtube": "https://youtube.com", "gmail": "https://mail.google.com",
          "github": "https://github.com", "calendar": "https://calendar.google.com",
          "spotify": "https://open.spotify.com", "whatsapp": "https://web.whatsapp.com"}


def tool_open_app(name: str = "", **_):
    exe = _APPS.get(name.lower().strip(), name)
    try:
        subprocess.Popen(exe, shell=True)
        return {"result": json.dumps({"opened": name})}
    except Exception as e:
        return {"result": json.dumps({"error": str(e)})}


def tool_open_website(name: str = "", **_):
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
    "open_app": tool_open_app,
    "open_website": tool_open_website,
}


def execute_tool(name: str, args: dict, retries: int = 1) -> dict:
    """Run a tool by name. Retries once on failure, then fails honestly."""
    fn = REGISTRY.get(name)
    if fn is None:
        # This is the "I don't have a skill for that" path (his rule #5).
        return {"result": json.dumps({"missing_skill": name}),
                "missing": True}
    last_err = None
    for attempt in range(retries + 1):
        try:
            out = fn(**(args or {}))
            logger.info(f"TOOL {name}({args}) ok")
            return out
        except Exception as e:
            last_err = e
            logger.warning(f"TOOL {name} attempt {attempt+1} failed: {e}")
    return {"result": json.dumps({"error": str(last_err)}), "failed": True}


def ack_for(name: str) -> str | None:
    return ACK_PHRASES.get(name, ACK_PHRASES["_default"])
