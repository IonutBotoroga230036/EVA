"""Spotify skill: extras on top of the built-in spotify_play (which already uses the Web API)."""

import json

from core import spotify as sp

TOOLS = [
    {"type": "function", "function": {"name": "spotify_now_playing", "description": "What song is playing on Spotify right now.",
                                      "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "spotify_queue", "description": "Add a song to the Spotify queue.",
                                      "parameters": {"type": "object", "properties": {"what": {"type": "string"}},
                                                     "required": ["what"]}}},
    {"type": "function", "function": {"name": "spotify_volume", "description": "Set the volume of the Spotify device (0 to 100).",
                                      "parameters": {"type": "object", "properties": {"level": {"type": "integer"}},
                                                     "required": ["level"]}}},
]


def _guard(fn):
    try:
        return fn()
    except sp.NeedSpotifyAuth:
        return {"result": json.dumps({"error": "not signed in"}), "say": sp.auth_message(), "exact": True}
    except sp.PremiumRequired:
        return {"result": json.dumps({"error": "premium"}), "exact": True,
                "say": "Spotify only allows that with a Premium account, sir."}
    except LookupError as e:
        return {"result": json.dumps({"error": str(e)}), "exact": True, "say": f"{str(e)[0].upper()}{str(e)[1:]}, sir."}
    except Exception as e:
        return {"result": json.dumps({"error": str(e)}), "exact": True, "say": f"Spotify didn't answer, sir: {str(e)[:100]}."}


def spotify_now_playing(**_):
    def run():
        now = sp.get_spotify().now_playing()
        if not now["title"]:
            return {"result": json.dumps(now), "exact": True, "say": "Nothing is playing on Spotify, sir."}
        state = "Playing" if now["is_playing"] else "Paused on"
        return {"result": json.dumps(now), "exact": True, "widget": {"kind": "nowplaying", "what": f"{now['title']} · {now['artist']}"},
                "say": f"{state} {now['title']} by {now['artist']}" + (f" on {now['device']}" if now["device"] else "") + ", sir."}
    return _guard(run)


def spotify_queue(what: str = "", **_):
    return _guard(lambda: {"result": json.dumps({"queued": what}), "say": f"Queued {sp.get_spotify().queue(what)}, sir."})


def spotify_volume(level: int = 50, **_):
    def run():
        sp.get_spotify().volume(level)
        return {"result": json.dumps({"volume": level}), "say": f"Spotify volume at {level}, sir."}
    return _guard(run)


FUNCTIONS = {"spotify_now_playing": spotify_now_playing, "spotify_queue": spotify_queue, "spotify_volume": spotify_volume}
ACTIONS = ["spotify_queue", "spotify_volume"]
GUARDS = {"spotify_now_playing": r"\b(playing|song|track|this music|who sings|what is this)\b",
          "spotify_queue": r"\b(queue|next up|after this|play next)\b",
          "spotify_volume": r"\b(spotify|phone)\b.*\bvolume\b|\bvolume\b.*\b(spotify|phone)\b"}
