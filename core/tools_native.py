"""
Built-in tools for E.V.A.

Each tool returns {"result": <JSON string for the model>, "widget": <optional UI card>,
"say": <optional exact spoken confirmation>}. When a turn used only actions that
returned "say", the orchestrator speaks those lines verbatim and skips the LLM:
faster, and she can never misreport what she did ("Volume set to 0" after a pause).

GUARDS: an action only runs if the user's words plausibly ask for it. This stops
a small model from opening a website because a sentence was cut off.
ACK_PHRASES give the "phone call" feel: a short line spoken the instant a tool is
chosen, before it runs. ACTION_TOOLS change the world (volume, media, apps); after a
successful action the orchestrator stops the tool loop instead of chaining more calls.

Descriptions are written for a 3B model: say exactly when to use the tool and
when NOT to, because the description is the only thing it knows about it.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
import webbrowser
from datetime import datetime
from urllib.parse import quote_plus, urlparse

from loguru import logger

from core.weather import fill_from_words, get_weather_report, spoken as weather_spoken

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

ACTION_TOOLS = {"spotify_play", "media_control", "set_volume", "open_app", "open_website", "send_to_phone"}

# Information tools whose "say" line IS the answer (exact data, no LLM rephrasing).
EXACT_TOOLS = {"get_weather", "get_datetime", "calculate", "budget_status", "set_brain_mode", "convert_currency",
               "crypto_price"}

# Before a tool runs, the user's own words can fill or correct its arguments.
def _fill_spotify(args: dict, text: str) -> dict:
    out = dict(args)
    if out.get("device") and not re.search(r"\b(phone|mobile|laptop|computer|pc|desktop|speaker|tv|on (?:my|the))\b",
                                           text or "", re.I):
        out.pop("device")                            # a device you never mentioned
    return out


ARG_FILLERS = {"get_weather": fill_from_words, "spotify_play": _fill_spotify}

# The user's message must match before an action runs (case-insensitive search).
GUARDS = {
    "open_app": r"\b(open|launch|start|run|fire up|pull up)\b",
    "open_website": r"\b(open|go to|visit|pull up|show me|load|launch|browse)\b",
    "spotify_play": r"\b(play|music|song|songs|spotify|playlist|album|listen|artist|put on)\b",
    "set_volume": r"\b(volume|louder|quieter|loud|quiet|sound|hear|mute|unmute|max)\b",
    "media_control": r"\b(pause|stop|play|resume|skip|next|previous|back|mute|unmute|track|song|music)\b",
    "get_weather": r"\b(weather|temperature|rain|raining|snow|sunny|sun|cold|hot|warm|forecast|wind|degrees|"
                   r"umbrella|jacket|outside|storm|cloudy|freezing)\b",
    "calculate": r"\d|\b(square root|percent|plus|minus|times|divided)\b",
    "budget_status": r"\b(budget|spent|spend|spending|cost|costs|credit|credits|money)\b",
    "set_brain_mode": r"\b(local|offline|cloud|online|claude|auto|automatic)\b",
    "send_to_phone": r"\b(telegram|phone|text me|message me|send me)\b",
    "convert_currency": r"\b(euros?|eur|dollars?|usd|pounds?|gbp|lei|ron|yen|francs?|currency|exchange|convert)\b|[€$£]",
    "crypto_price": r"\b(bitcoin|btc|ethereum|eth|crypto|solana|sol|dogecoin|doge|cardano|ada|xrp|ripple)\b",
}

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
        "name": "convert_currency",
        "description": "Convert money between currencies at today's ECB rate, e.g. 'what's that in euros'.",
        "parameters": {"type": "object", "properties": {
            "amount": {"type": "number"}, "from_currency": {"type": "string", "description": "e.g. USD"},
            "to_currency": {"type": "string", "description": "e.g. EUR"}}, "required": ["amount", "from_currency"]}}},
    {"type": "function", "function": {
        "name": "crypto_price",
        "description": "Current price of a cryptocurrency (bitcoin, ethereum, solana...).",
        "parameters": {"type": "object", "properties": {
            "coin": {"type": "string"}, "currency": {"type": "string", "description": "Default EUR."}},
            "required": ["coin"]}}},
    {"type": "function", "function": {
        "name": "send_to_phone",
        "description": "Send the user a message on their phone (Telegram).",
        "parameters": {"type": "object", "properties": {
            "text": {"type": "string", "description": "The message, in the user's words."}}, "required": ["text"]}}},
    {"type": "function", "function": {
        "name": "set_brain_mode",
        "description": "Switch where heavy thinking and skill-building run: cloud (Claude), local (private, free), or auto.",
        "parameters": {"type": "object", "properties": {
            "mode": {"type": "string", "enum": ["auto", "cloud", "local"]}}, "required": ["mode"]}}},
    {"type": "function", "function": {
        "name": "budget_status",
        "description": "Report how much of the cloud budget (Claude API) has been spent today and this month.",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "calculate",
        "description": "Calculate a math expression exactly: arithmetic, percentages, powers, square roots.",
        "parameters": {"type": "object", "properties": {
            "expression": {"type": "string", "description": "The math as the user said it, e.g. '15% of 80'."}},
            "required": ["expression"]}}},
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
            "what": {"type": "string", "description": "What to play, e.g. 'jazz', 'The Weeknd', 'my chill playlist', 'liked songs'."},
            "device": {"type": "string", "description": "Only if the user names one: 'phone', 'computer', a speaker name."}},
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
        "description": "Open a desktop application (vscode, notepad, spotify, calculator, chrome, terminal, explorer).",
        "parameters": {"type": "object", "properties": {
            "app": {"type": "string", "description": "The application the user named, e.g. 'vscode'."}},
            "required": ["app"]}}},
    {"type": "function", "function": {
        "name": "open_website",
        "description": "Open a website in the browser when the user asks to open or go to a site.",
        "parameters": {"type": "object", "properties": {
            "site": {"type": "string", "description": "The site the user named, e.g. 'youtube', 'github.com', or a URL."}},
            "required": ["site"]}}},
]


# ------------------------------------------------------------------ tools
def tool_get_datetime(**_):
    now = datetime.now()
    return {"result": json.dumps({"time": now.strftime("%H:%M"), "date": now.strftime("%A, %B %d, %Y")}),
            "widget": {"kind": "clock", "time": now.strftime("%H:%M"), "date": now.strftime("%A, %d %B")},
            "say": f"It's {now:%H:%M} on {now:%A} the {now.day}{_ordinal(now.day)}, sir."}


def _ordinal(n: int) -> str:
    return "th" if 11 <= n % 100 <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")


# ---------------------------------------------------------------- calculator (safe: AST, no eval)
import ast
import math
import operator as _op

_BIN = {ast.Add: _op.add, ast.Sub: _op.sub, ast.Mult: _op.mul, ast.Div: _op.truediv, ast.Pow: _op.pow,
        ast.Mod: _op.mod, ast.FloorDiv: _op.floordiv}
_FUN = {"sqrt": math.sqrt, "abs": abs, "round": round, "sin": math.sin, "cos": math.cos, "tan": math.tan,
        "log": math.log10, "ln": math.log, "exp": math.exp}
_CONST = {"pi": math.pi, "e": math.e}


def _normalize_math(text: str) -> str:
    t = (text or "").lower().strip().rstrip("?.!=")
    t = re.sub(r"^(what'?s|what is|how much is|calculate|compute|work out|eva,?)\s+", "", t)
    t = re.sub(r"(\d(?:[\d.]*))\s*%\s*of\s*", r"\1/100*", t)                 # 15% of 80
    t = re.sub(r"(\d(?:[\d.]*))\s*percent\s*of\s*", r"\1/100*", t)
    t = re.sub(r"square root of\s*([\d.]+)", r"sqrt(\1)", t)
    for word, sym in (("plus", "+"), ("minus", "-"), ("times", "*"), ("multiplied by", "*"),
                      ("divided by", "/"), ("over", "/"), ("to the power of", "**"), ("squared", "**2")):
        t = t.replace(word, sym)
    t = t.replace("×", "*").replace("x", "*").replace("÷", "/").replace("^", "**")
    t = re.sub(r"(?<=\d),(?=\d{3}\b)", "", t)                                       # 1,000 -> 1000
    return t.replace(",", ".")


def safe_eval(expr: str) -> float:
    def ev(n):
        if isinstance(n, ast.Expression):
            return ev(n.body)
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)):
            return n.value
        if isinstance(n, ast.BinOp) and type(n.op) in _BIN:
            left, right = ev(n.left), ev(n.right)
            if isinstance(n.op, ast.Pow) and abs(right) > 100:
                raise ValueError("exponent too large")
            return _BIN[type(n.op)](left, right)
        if isinstance(n, ast.UnaryOp) and isinstance(n.op, (ast.UAdd, ast.USub)):
            return ev(n.operand) if isinstance(n.op, ast.UAdd) else -ev(n.operand)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in _FUN and len(n.args) <= 2:
            return _FUN[n.func.id](*[ev(a) for a in n.args])
        if isinstance(n, ast.Name) and n.id in _CONST:
            return _CONST[n.id]
        raise ValueError("unsupported expression")
    if len(expr) > 200:
        raise ValueError("expression too long")
    return ev(ast.parse(expr, mode="eval"))


def _fmt(v: float) -> str:
    """Plain digits, no group separators: '1024' is read as a number, '1 024' digit by digit."""
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return f"{v:.6g}" if isinstance(v, float) else str(v)


def tool_calculate(expression: str = "", **_):
    expr = _normalize_math(expression)
    try:
        value = safe_eval(expr)
    except ZeroDivisionError:
        return {"result": json.dumps({"error": "division by zero"}), "say": "That's a division by zero, sir."}
    except Exception as e:
        return {"result": json.dumps({"error": f"I couldn't calculate {expression!r} ({e})"})}
    shown = _fmt(value)
    return {"result": json.dumps({"expression": expr, "value": value}),
            "widget": {"kind": "note", "title": "Calculation", "text": f"{expression.strip()} = {shown}"},
            "say": f"That's {shown}, sir."}


def tool_get_weather(city: str = "", day: str = "", hour: str = "", **_):
    try:
        data = get_weather_report((city or HOME_CITY).strip(), day or None, hour or None)
    except Exception as e:
        logger.error(f"weather failed: {e}")
        data = {"error": str(e)}
    if "error" in data:
        return {"result": json.dumps(data), "say": weather_spoken(data)}
    return {"result": json.dumps(data), "widget": {"kind": "weather", **data}, "say": weather_spoken(data)}


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


def tool_spotify_play(what: str = "", device: str = "", **_):
    what = (what or "").strip()
    from core import spotify as sp
    if sp.connected():
        try:
            if re.fullmatch(r"(?:it|this|that|the music|music|this song|the song|resume|continue)?", what, re.I):
                if device:                                   # "play it on my computer": move, don't search
                    name = sp.get_spotify().transfer(device)
                    return {"result": json.dumps({"moved_to": name}), "say": f"Moved the music to {name}, sir."}
                sp.get_spotify().control("resume")
                return {"result": json.dumps({"resumed": True}), "say": "Resuming, sir."}
            out = sp.get_spotify().play(what, device)
            where = f" on {out['device']}" if device else ""
            return {"result": json.dumps(out), "widget": {"kind": "nowplaying", "what": out["label"]},
                    "say": f"Playing {out['label']}{where}, sir."}
        except sp.PremiumRequired:
            logger.warning("SPOTIFY: Premium required for control; falling back to opening Spotify")
        except LookupError as e:
            return {"result": json.dumps({"error": str(e)}), "say": f"{str(e)[0].upper()}{str(e)[1:]}, sir."}
        except sp.NeedSpotifyAuth:
            return {"result": json.dumps({"error": "not signed in"}), "say": sp.auth_message()}
        except Exception as e:
            logger.warning(f"SPOTIFY: API play failed ({e}); falling back")
    what = what or "liked songs"
    try:
        webbrowser.open(f"spotify:search:{what.replace(' ', '%20')}")
        if PYAUTOGUI_OK:
            time.sleep(2.0)
            pyautogui.press("playpause")
        return {"result": json.dumps({"status": "playing on Spotify", "requested": what}),
                "widget": {"kind": "nowplaying", "what": what}, "say": f"Playing {what} on Spotify, sir."}
    except Exception as e:
        return {"result": json.dumps({"error": str(e)})}


def tool_media_control(action: str = "playpause", **_):
    keymap = {"playpause": "playpause", "next": "nexttrack", "previous": "prevtrack",
              "volup": "volumeup", "voldown": "volumedown", "mute": "volumemute"}
    from core import spotify as sp
    if sp.connected() and action in ("playpause", "next", "previous"):
        try:
            sp.get_spotify().control({"playpause": "toggle"}.get(action, action))
            says = {"playpause": "Done, sir.", "next": "Skipping ahead, sir.", "previous": "Going back a track, sir."}
            return {"result": json.dumps({"done": action, "via": "spotify"}), "say": says[action]}
        except Exception as e:
            logger.info(f"SPOTIFY: control via API failed ({e}); using media keys")
    if not PYAUTOGUI_OK:
        return {"result": json.dumps({"error": "media keys unavailable (pyautogui not installed)"})}
    try:
        pyautogui.press(keymap.get(action, "playpause"))
        says = {"playpause": "Done, sir.", "next": "Skipping ahead, sir.", "previous": "Going back a track, sir.",
                "volup": "A little louder, sir.", "voldown": "A little quieter, sir.", "mute": "Mute toggled, sir."}
        return {"result": json.dumps({"done": action}), "say": says.get(action, "Done, sir.")}
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
        return {"result": json.dumps({"volume_set": level, "muted": level == 0}),
                "say": "Muted, sir." if level == 0 else f"Volume at {level}, sir."}
    except Exception as e:
        logger.error(f"set_volume failed: {e}")
        return {"result": json.dumps({"error": f"volume control failed: {e}"})}


_APPS = {"notepad": "notepad.exe", "calculator": "calc.exe", "calc": "calc.exe", "vscode": "code",
         "vs code": "code", "visual studio code": "code", "spotify": "spotify:", "chrome": "chrome",
         "edge": "msedge", "explorer": "explorer.exe", "file explorer": "explorer.exe", "files": "explorer.exe",
         "terminal": "wt.exe", "powershell": "powershell.exe", "settings": "ms-settings:",
         "obsidian": "obsidian:", "discord": "discord:", "task manager": "taskmgr.exe", "paint": "mspaint.exe"}
_SITES = {"youtube": "https://youtube.com", "gmail": "https://mail.google.com", "google": "https://google.com",
          "github": "https://github.com", "calendar": "https://calendar.google.com", "maps": "https://maps.google.com",
          "google maps": "https://maps.google.com", "drive": "https://drive.google.com", "spotify": "https://open.spotify.com",
          "whatsapp": "https://web.whatsapp.com", "linkedin": "https://linkedin.com", "reddit": "https://reddit.com",
          "netflix": "https://netflix.com", "x": "https://x.com", "twitter": "https://x.com",
          "instagram": "https://instagram.com", "facebook": "https://facebook.com", "claude": "https://claude.ai",
          "outlook": "https://outlook.live.com", "notion": "https://notion.so", "wikipedia": "https://wikipedia.org",
          "amazon": "https://amazon.nl", "bol": "https://bol.com", "brightspace": "https://brightspace.ru.nl"}
_HOST = re.compile(r"^(?=.{4,253}$)([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,}$")
_SHELL_META = re.compile(r"[&|;<>^%$`\"'()]")
_TOOL_NAMES = {"open_app", "open_website", "spotify_play", "media_control", "set_volume", "web_search",
               "get_weather", "get_datetime"}


def resolve_site(site: str) -> tuple[str | None, str]:
    """-> (url, how). Known names, real URLs and real hostnames only; anything else goes
    through DuckDuckGo's first result instead of guessing www.<name>.com."""
    s = (site or "").strip().strip(".").lower()
    s = re.sub(r"^(the |my )", "", s)
    s = re.sub(r"\s+(website|site|page|web ?site)$", "", s).strip()
    if not s or s in _TOOL_NAMES or len(s) > 200:
        return None, "invalid"
    if s in _SITES:
        return _SITES[s], "known"
    if s.startswith(("http://", "https://")):
        host = (urlparse(s).hostname or "")
        return (site.strip(), "url") if _HOST.match(host) else (None, "invalid")
    bare = s.removeprefix("www.")
    if _HOST.match(bare):
        return f"https://{bare}", "domain"
    return f"https://duckduckgo.com/?q=%5C{quote_plus(site.strip())}", "search"   # "\" = go to first result


def tool_open_app(app: str = "", **_):
    name = (app or "").strip().lower()
    if not name or name in _TOOL_NAMES or _SHELL_META.search(name):
        return {"result": json.dumps({"error": f"no valid app name was given ({app!r})"})}
    target = _APPS.get(name) or shutil.which(name)
    if not target:
        return {"result": json.dumps({"error": f"I don't know an app called {app}"})}
    try:
        if target.endswith(":") and hasattr(os, "startfile"):
            os.startfile(target)                        # URI protocols like spotify: or ms-settings:
        elif target.endswith(":"):
            webbrowser.open(target)
        else:
            subprocess.Popen([target], shell=False)     # never shell=True with model-provided text
        return {"result": json.dumps({"opened": app}), "say": f"Opening {app}, sir."}
    except Exception as e:
        return {"result": json.dumps({"error": str(e)})}


def tool_open_website(site: str = "", **_):
    url, how = resolve_site(site)
    if not url:
        return {"result": json.dumps({"error": f"that isn't a website I can open ({site!r})"})}
    webbrowser.open(url)
    shown = site.strip() if how == "search" else (urlparse(url).hostname or url).removeprefix("www.")
    say = f"Opening the top result for {shown}, sir." if how == "search" else f"Opening {shown}, sir."
    return {"result": json.dumps({"opened": url, "how": how}), "say": say}


_CUR = {"euro": "EUR", "euros": "EUR", "eur": "EUR", "€": "EUR", "dollar": "USD", "dollars": "USD", "usd": "USD", "$": "USD",
        "pound": "GBP", "pounds": "GBP", "gbp": "GBP", "£": "GBP", "lei": "RON", "leu": "RON", "ron": "RON",
        "yen": "JPY", "jpy": "JPY", "franc": "CHF", "francs": "CHF", "chf": "CHF", "zloty": "PLN", "pln": "PLN"}
_COINS = {"bitcoin": "bitcoin", "btc": "bitcoin", "ethereum": "ethereum", "eth": "ethereum", "solana": "solana",
          "sol": "solana", "dogecoin": "dogecoin", "doge": "dogecoin", "cardano": "cardano", "ada": "cardano",
          "xrp": "ripple", "ripple": "ripple"}


def _code(c: str, default: str = "EUR") -> str:
    c = (c or "").strip().lower()
    return _CUR.get(c, c.upper() if re.fullmatch(r"[a-z]{3}", c) else default)


def _money(v: float) -> str:
    return f"{v:,.0f}" if v >= 1000 else f"{v:,.2f}"


def tool_convert_currency(amount: float = 0, from_currency: str = "USD", to_currency: str = "EUR", **_):
    import httpx
    src, dst = _code(from_currency, "USD"), _code(to_currency, "EUR")
    try:
        amount = float(str(amount).replace(",", ""))
        r = httpx.get("https://api.frankfurter.app/latest", params={"amount": amount, "from": src, "to": dst}, timeout=10)
        value = r.json()["rates"][dst]
    except Exception as e:
        return {"result": json.dumps({"error": f"no exchange rate ({e})"}), "say": "I couldn't get today's exchange rate, sir."}
    return {"result": json.dumps({"amount": amount, "from": src, "to": dst, "value": value, "source": "ECB via Frankfurter"}),
            "say": f"{_money(amount)} {src} is about {_money(value)} {dst} at today's rate, sir."}


def tool_crypto_price(coin: str = "bitcoin", currency: str = "EUR", **_):
    import httpx
    cid = _COINS.get((coin or "").strip().lower(), (coin or "bitcoin").strip().lower())
    cur = _code(currency, "EUR").lower()
    try:
        r = httpx.get("https://api.coingecko.com/api/v3/simple/price", params={"ids": cid, "vs_currencies": cur}, timeout=10)
        value = r.json()[cid][cur]
    except Exception as e:
        return {"result": json.dumps({"error": f"no price for {coin} ({e})"}), "say": f"I couldn't get a price for {coin}, sir."}
    names = {"EUR": "euros", "USD": "dollars", "GBP": "pounds", "RON": "lei"}
    return {"result": json.dumps({"coin": cid, "currency": cur.upper(), "price": value, "source": "CoinGecko"}),
            "say": f"{cid.capitalize()} is at {_money(value)} {names.get(cur.upper(), cur.upper())} right now, sir."}


def tool_send_to_phone(text: str = "", **_):
    from core.telegram_bridge import active, send_from_thread
    if not active():
        return {"result": json.dumps({"error": "Telegram not paired"}),
                "say": "Telegram isn't connected yet, sir. See the Telegram section in docs/CAPABILITIES.md."}
    text = (text or "").strip()
    if not text:
        return {"result": json.dumps({"error": "no text"}), "say": "What should the message say, sir?"}
    ok = send_from_thread(text)
    return ({"result": json.dumps({"sent": text}), "say": "Sent to your phone, sir."} if ok else
            {"result": json.dumps({"error": "send failed"}), "say": "Telegram didn't take the message, sir."})


def tool_set_brain_mode(mode: str = "auto", **_):
    from core.brain import get_brain
    b = get_brain()
    m = b.set_mode(mode)
    where = {"cloud": "Claude, in the cloud", "local": "local models only, nothing leaves this machine",
             "auto": "Claude when a key and budget are available, otherwise local"}[m]
    note = ""
    if m == "cloud" and not b.cloud_ready():
        note = " Note: Claude isn't available right now, so those jobs will fail until it is."
    return {"result": json.dumps({"mode": m}), "say": f"Done, sir. Heavy thinking now uses {where}.{note}"}


def tool_budget_status(**_):
    from core.budget import get_budget
    s = get_budget().today_summary()
    return {"result": json.dumps(s),
            "say": (f"Today I've spent {s['spent']:.2f} of your {s['limit']:.2f} euro daily cloud budget, and "
                    f"{s['month_spent']:.2f} of {s['month_limit']:.0f} euros this month, sir. Local work is free.")}


REGISTRY = {
    "get_datetime": tool_get_datetime,
    "get_weather": tool_get_weather,
    "calculate": tool_calculate,
    "budget_status": tool_budget_status,
    "set_brain_mode": tool_set_brain_mode,
    "send_to_phone": tool_send_to_phone,
    "convert_currency": tool_convert_currency,
    "crypto_price": tool_crypto_price,
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
