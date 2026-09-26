"""Lights skill: voice wrappers over core/lights.py, plus moods (lights + music)."""

import json

from core.lights import (find_mood, get_lights, load_moods, parse_brightness, parse_color, save_mood)

TOOLS = [
    {"type": "function", "function": {
        "name": "lights_set",
        "description": "Set the LED strips' colour and/or brightness.",
        "parameters": {"type": "object", "properties": {
            "color": {"type": "string", "description": "A colour name ('purple', 'warm white') or hex; empty keeps it."},
            "brightness": {"type": "integer", "description": "1 to 100; empty keeps it."},
            "which": {"type": "string", "description": "A strip name if the user named one; empty = all."}}}}},
    {"type": "function", "function": {
        "name": "lights_power",
        "description": "Turn the LED strips on or off.",
        "parameters": {"type": "object", "properties": {
            "on": {"type": "boolean"}, "which": {"type": "string"}}, "required": ["on"]}}},
    {"type": "function", "function": {
        "name": "set_mood",
        "description": "Set a mood: lights plus music, e.g. 'I'm home, I feel red', 'set the mood to relax'.",
        "parameters": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}}},
    {"type": "function", "function": {
        "name": "create_mood",
        "description": "Create or change a mood: a colour or white, a brightness, and optionally a playlist.",
        "parameters": {"type": "object", "properties": {
            "name": {"type": "string"}, "color": {"type": "string"}, "brightness": {"type": "integer"},
            "playlist": {"type": "string"}}, "required": ["name"]}}},
    {"type": "function", "function": {
        "name": "list_moods", "description": "List the available moods.",
        "parameters": {"type": "object", "properties": {}}}},
]


def _not_set_up():
    return {"result": json.dumps({"error": "no lights set up"}), "exact": True,
            "say": "Your lights aren't connected yet, sir. Follow docs/LIGHTS_SETUP.md and I'll take it from there."}


def _result(ok, failed, what):
    if not ok and not failed:
        return {"result": json.dumps({"error": "no matching light"}), "say": "I couldn't find that light, sir."}
    if not ok:
        return {"result": json.dumps({"error": "unreachable", "failed": failed}),
                "say": "The lights didn't respond, sir. If the Smart Life app is open, close it and try again."}
    extra = f" {', '.join(failed)} didn't respond." if failed else ""
    return {"result": json.dumps({"done": what, "lights": ok, "failed": failed}), "say": f"{what}, sir.{extra}"}


def lights_set(color: str = "", brightness: int = 0, which: str = "", **_):
    lights = get_lights()
    if not lights.lights:
        return _not_set_up()
    c = parse_color(color) if color else None
    b = int(brightness) if brightness else None
    if not c and b is None:
        return {"result": json.dumps({"error": "nothing to set"}), "say": "What colour or brightness, sir?"}
    ok, failed = lights.apply(which, rgb=c.get("rgb") if c else None, white=c.get("white") if c else None, brightness=b)
    what = " at ".join(x for x in ((c["name"].capitalize() if c else ""), (f"{b} percent" if b else "")) if x)
    return _result(ok, failed, what or "Done")


def lights_power(on: bool = True, which: str = "", **_):
    lights = get_lights()
    if not lights.lights:
        return _not_set_up()
    ok, failed = lights.apply(which, on=bool(on))
    return _result(ok, failed, "Lights on" if on else "Lights off")


def set_mood(name: str = "home", **_):
    found = find_mood(name or "home")
    if not found:
        names = ", ".join(sorted(load_moods()))
        return {"result": json.dumps({"error": "unknown mood"}), "exact": True,
                "say": f"I don't have a mood called {name}, sir. I know {names}."}
    key, mood = found
    parts, lights = [], get_lights()
    if lights.lights:
        c = parse_color(mood.get("color", "")) if mood.get("color") else None
        ok, failed = lights.apply("", rgb=c.get("rgb") if c else None,
                                  white=mood.get("white") if not c else None, brightness=mood.get("brightness"))
        parts.append("the lights are set" if ok else "the lights didn't respond")
    else:
        parts.append("your lights aren't connected yet")
    if mood.get("playlist"):
        from core.tools_native import tool_spotify_play
        played = tool_spotify_play(what=mood["playlist"])
        parts.append("the music is on" if "error" not in played.get("result", "") else "the music didn't start")
    greeting = "Welcome home, sir. " if key == "home" else ""
    say = f"{greeting}{key.capitalize()} mood: {' and '.join(parts)}, sir."
    return {"result": json.dumps({"mood": key, **mood}), "say": say,
            "widget": {"kind": "note", "title": f"Mood · {key}", "text": ", ".join(
                x for x in (mood.get("color") or ("white" if "white" in mood else ""), f"{mood.get('brightness', '')}%",
                            mood.get("playlist", "")) if x)}}


def create_mood(name: str = "", color: str = "", brightness: int = 0, playlist: str = "", **_):
    if not name.strip():
        return {"result": json.dumps({"error": "no name"}), "say": "What should the mood be called, sir?"}
    c = parse_color(color) if color else None
    mood = {"brightness": int(brightness) if brightness else 60, "playlist": playlist.strip()}
    if c and "rgb" in c:
        mood["color"] = c["name"]
    elif c:
        mood["white"] = c["white"]
    else:
        mood["white"] = 30
    save_mood(name, mood)
    music = f" with {playlist}" if playlist else ""
    return {"result": json.dumps({"saved": name, **mood}),
            "say": f"Saved the {name} mood, sir: {c['name'] if c else 'soft white'} at {mood['brightness']} percent{music}."}


def list_moods(**_):
    names = sorted(load_moods())
    return {"result": json.dumps({"moods": names}), "exact": True, "say": "Your moods, sir: " + ", ".join(names) + "."}


def _fill(args: dict, text: str) -> dict:
    """Your words decide colour and brightness when the model leaves them out."""
    out = dict(args)
    c = parse_color(text)
    if c and not out.get("color"):
        out["color"] = c["name"]
    b = parse_brightness(text)
    if b and not out.get("brightness"):
        out["brightness"] = b
    return out


FUNCTIONS = {"lights_set": lights_set, "lights_power": lights_power, "set_mood": set_mood,
             "create_mood": create_mood, "list_moods": list_moods}
ACTIONS = ["lights_set", "lights_power", "set_mood", "create_mood"]
GUARDS = {"lights_set": r"\b(lights?|leds?|strips?|lamp|dim|bright|brighter|darker|colou?r)\b",
          "lights_power": r"\b(lights?|leds?|strips?|lamp)\b",
          "set_mood": r"\b(mood|i'?m home|i feel|feeling|vibe)\b",
          "create_mood": r"\b(create|make|add|new|save|change)\b.*\bmood\b",
          "list_moods": r"\bmoods?\b"}
FILLERS = {"lights_set": _fill}
