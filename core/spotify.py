"""
Spotify Web API: full control of your Spotify on any device (this PC, your phone, a speaker).

Setup (see docs/SPOTIFY_SETUP.md): create an app at developer.spotify.com, add the redirect URI
http://127.0.0.1:8888/callback, put its Client ID in config/settings.local.yaml under spotify.client_id,
then run  python -m core.spotify  (or just ask her to play something; she opens the sign-in page).

- PKCE sign-in: no client secret exists anywhere. Token in data/spotify/token.json (git-ignored), refreshed.
- Playback control needs Spotify Premium; without it she says so and falls back to opening Spotify.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import secrets
import threading
import time
import webbrowser
from difflib import SequenceMatcher
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, urlencode, urlparse

import httpx
from loguru import logger

AUTH_URL, TOKEN_URL, API = "https://accounts.spotify.com/authorize", "https://accounts.spotify.com/api/token", "https://api.spotify.com/v1"
REDIRECT = "http://127.0.0.1:8888/callback"
SCOPES = "user-read-playback-state user-modify-playback-state user-read-currently-playing playlist-read-private user-library-read"
TOKEN = Path("data/spotify/token.json")


class NeedSpotifyAuth(RuntimeError):
    pass


class PremiumRequired(RuntimeError):
    pass


def client_id() -> str:
    from core.settings import get_settings
    return os.environ.get("SPOTIFY_CLIENT_ID") or str(get_settings().get("spotify", {}).get("client_id", "") or "")


# ------------------------------------------------------------ sign-in (PKCE)
def authorize_interactive(timeout: float = 180) -> dict:
    cid = client_id()
    if not cid:
        raise NeedSpotifyAuth("no Spotify client_id in settings")
    verifier = secrets.token_urlsafe(64)[:96]
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state, got = secrets.token_urlsafe(16), {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            q = parse_qs(urlparse(self.path).query)
            if q.get("state", [""])[0] == state:
                got.update({k: v[0] for k, v in q.items()})
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write("<h2>E.V.A. is connected to Spotify. You can close this tab.</h2>".encode())

        def log_message(self, *a):
            pass
    server = HTTPServer(("127.0.0.1", 8888), Handler)
    server.timeout = 1
    webbrowser.open(AUTH_URL + "?" + urlencode({"client_id": cid, "response_type": "code", "redirect_uri": REDIRECT,
                                                "scope": SCOPES, "state": state, "code_challenge_method": "S256",
                                                "code_challenge": challenge}))
    end = time.time() + timeout
    while "code" not in got and "error" not in got and time.time() < end:
        server.handle_request()
    server.server_close()
    if "code" not in got:
        raise NeedSpotifyAuth(got.get("error", "sign-in timed out"))
    r = httpx.post(TOKEN_URL, data={"grant_type": "authorization_code", "code": got["code"], "redirect_uri": REDIRECT,
                                    "client_id": cid, "code_verifier": verifier}, timeout=20)
    r.raise_for_status()
    tok = r.json()
    tok["expires_at"] = time.time() + tok.get("expires_in", 3600) - 60
    TOKEN.parent.mkdir(parents=True, exist_ok=True)
    TOKEN.write_text(json.dumps(tok), encoding="utf-8")
    global _client
    _client = None
    logger.info("SPOTIFY: signed in")
    return tok


_auth_lock = threading.Lock()


def start_auth_background() -> bool:
    if not _auth_lock.acquire(blocking=False):
        return False

    def run():
        try:
            authorize_interactive()
        except Exception as e:
            logger.error(f"SPOTIFY: sign-in failed: {e}")
        finally:
            _auth_lock.release()
    threading.Thread(target=run, daemon=True, name="spotify-auth").start()
    return True


def auth_message() -> str:
    if not client_id():
        return "Spotify isn't set up yet, sir. Follow docs/SPOTIFY_SETUP.md, then ask me again."
    return ("I need to connect to your Spotify first, sir. I've opened the sign-in page; approve it, then ask again."
            if start_auth_background() else "The Spotify sign-in page is already open, sir.")


# ------------------------------------------------------------ client
class Spotify:
    def __init__(self, token: dict, http: Optional[httpx.Client] = None):
        self.tok = token
        self.http = http or httpx.Client(timeout=15)

    def _refresh(self) -> None:
        r = self.http.post(TOKEN_URL, data={"grant_type": "refresh_token", "refresh_token": self.tok["refresh_token"],
                                            "client_id": client_id()})
        if r.status_code >= 400:
            raise NeedSpotifyAuth("Spotify sign-in expired")
        new = r.json()
        self.tok.update(new)
        self.tok["expires_at"] = time.time() + new.get("expires_in", 3600) - 60
        TOKEN.parent.mkdir(parents=True, exist_ok=True)
        TOKEN.write_text(json.dumps(self.tok), encoding="utf-8")

    def req(self, method: str, path: str, **kw) -> dict:
        if time.time() > self.tok.get("expires_at", 0):
            self._refresh()
        for attempt in range(2):
            r = self.http.request(method, API + path, headers={"Authorization": f"Bearer {self.tok['access_token']}"}, **kw)
            if r.status_code == 401 and attempt == 0:
                self._refresh()
                continue
            break
        if r.status_code == 403 and "PREMIUM" in r.text.upper():
            raise PremiumRequired("playback control needs Spotify Premium")
        if r.status_code == 404 and "NO_ACTIVE_DEVICE" in r.text.upper():
            raise LookupError("no active Spotify device")
        if r.status_code >= 400:
            raise RuntimeError(f"Spotify error {r.status_code}: {r.text[:160]}")
        return r.json() if r.content and r.headers.get("content-type", "").startswith("application/json") else {}

    # ---------------- devices
    def devices(self) -> list[dict]:
        return self.req("GET", "/me/player/devices").get("devices", [])

    def pick_device(self, hint: str = "") -> Optional[dict]:
        devs = self.devices()
        if not devs:
            return None
        h = (hint or "").lower()
        kinds = {"phone": "smartphone", "mobile": "smartphone", "computer": "computer", "laptop": "computer",
                 "pc": "computer", "desktop": "computer", "speaker": "speaker", "tv": "tv"}
        for word, kind in kinds.items():
            if word in h:
                match = [d for d in devs if d.get("type", "").lower() == kind]
                if match:
                    return match[0]
        if h:
            named = max(devs, key=lambda d: SequenceMatcher(None, h, d.get("name", "").lower()).ratio())
            if SequenceMatcher(None, h, named.get("name", "").lower()).ratio() > 0.5:
                return named
        return next((d for d in devs if d.get("is_active")), None) or \
            next((d for d in devs if d.get("type", "").lower() == "computer"), devs[0])

    # ---------------- search
    def resolve(self, what: str) -> dict:
        q = re.sub(r"^(?:some|me|a bit of|a little|the song|the track|music by|songs by)\s+", "", (what or "").strip(), flags=re.I)
        low = q.lower()
        if not q or re.fullmatch(r"(my )?(liked songs|likes|favou?rites|my music|music|something)", low):
            items = self.req("GET", "/me/tracks", params={"limit": 50}).get("items", [])
            return {"uris": [i["track"]["uri"] for i in items if i.get("track")], "label": "your liked songs"}
        m = re.match(r"^(?:my )?(.+?) playlist$", low)
        if m or low.startswith("my "):
            name = (m.group(1) if m else low[3:]).strip()
            mine = self.req("GET", "/me/playlists", params={"limit": 50}).get("items", [])
            best = max(mine, key=lambda p: SequenceMatcher(None, name, p["name"].lower()).ratio(), default=None)
            if best and SequenceMatcher(None, name, best["name"].lower()).ratio() > 0.6:
                return {"context_uri": best["uri"], "label": f"your {best['name']} playlist"}
        kind = "playlist" if "playlist" in low else "album" if "album" in low else None
        term = re.sub(r"\b(playlist|album)\b", "", q, flags=re.I).strip()
        res = self.req("GET", "/search", params={"q": term, "type": kind or "artist,track,playlist", "limit": 3})
        artists = [a for a in (res.get("artists") or {}).get("items", []) if a]
        tracks = [t for t in (res.get("tracks") or {}).get("items", []) if t]
        lists = [p for p in (res.get("playlists") or {}).get("items", []) if p]
        albums = [a for a in (res.get("albums") or {}).get("items", []) if a]
        if kind == "album" and albums:
            return {"context_uri": albums[0]["uri"], "label": f"{albums[0]['name']} by {albums[0]['artists'][0]['name']}"}
        if kind != "playlist" and artists and SequenceMatcher(None, term.lower(), artists[0]["name"].lower()).ratio() > 0.75:
            return {"context_uri": artists[0]["uri"], "label": artists[0]["name"]}
        if kind != "playlist" and tracks and (" by " in low or SequenceMatcher(None, term.lower(), tracks[0]["name"].lower()).ratio() > 0.8):
            t = tracks[0]
            return {"uris": [t["uri"]], "label": f"{t['name']} by {t['artists'][0]['name']}"}
        if lists:
            return {"context_uri": lists[0]["uri"], "label": f"the {lists[0]['name']} playlist"}
        if artists:
            return {"context_uri": artists[0]["uri"], "label": artists[0]["name"]}
        raise LookupError(f"nothing on Spotify matches {what}")

    # ---------------- playback
    def _device_params(self, hint: str) -> tuple[dict, str]:
        dev = self.pick_device(hint)
        if not dev:
            raise LookupError("no Spotify device is open: start Spotify on the PC or your phone first")
        return {"device_id": dev["id"]}, dev.get("name", "your device")

    def play(self, what: str, device: str = "") -> dict:
        target = self.resolve(what)
        params, name = self._device_params(device)
        body = {k: v for k, v in target.items() if k in ("context_uri", "uris")}
        self.req("PUT", "/me/player/play", params=params, json=body)
        return {"label": target["label"], "device": name}

    def control(self, action: str, device: str = "") -> str:
        params, name = self._device_params(device) if device else ({}, "")
        if action in ("pause", "stop"):
            self.req("PUT", "/me/player/pause", params=params)
        elif action in ("resume", "play"):
            self.req("PUT", "/me/player/play", params=params)
        elif action == "next":
            self.req("POST", "/me/player/next", params=params)
        elif action == "previous":
            self.req("POST", "/me/player/previous", params=params)
        elif action == "toggle":
            now = self.now_playing()
            return self.control("pause" if now.get("is_playing") else "resume", device)
        return action

    def volume(self, level: int) -> None:
        self.req("PUT", "/me/player/volume", params={"volume_percent": max(0, min(100, int(level)))})

    def queue(self, what: str) -> str:
        res = self.req("GET", "/search", params={"q": what, "type": "track", "limit": 1})
        items = (res.get("tracks") or {}).get("items", [])
        if not items:
            raise LookupError(f"no song matches {what}")
        self.req("POST", "/me/player/queue", params={"uri": items[0]["uri"]})
        return f"{items[0]['name']} by {items[0]['artists'][0]['name']}"

    def now_playing(self) -> dict:
        st = self.req("GET", "/me/player")
        item = st.get("item") or {}
        return {"is_playing": bool(st.get("is_playing")), "title": item.get("name", ""),
                "artist": ", ".join(a["name"] for a in item.get("artists", [])), "device": (st.get("device") or {}).get("name", "")}


_client: Optional[Spotify] = None


def get_spotify() -> Spotify:
    global _client
    if _client is None:
        if not TOKEN.exists():
            raise NeedSpotifyAuth("not signed in to Spotify")
        _client = Spotify(json.loads(TOKEN.read_text(encoding="utf-8")))
    return _client


def connected() -> bool:
    return TOKEN.exists() and bool(client_id())


if __name__ == "__main__":
    if not client_id():
        print("Add spotify.client_id to config/settings.local.yaml first (see docs/SPOTIFY_SETUP.md).")
    else:
        authorize_interactive()
        print("Signed in. E.V.A. can now control your Spotify.")
