"""
Local network safety (v0.2.5 milestone 2).

Default: E.V.A. listens on 127.0.0.1 only. Nothing else on the network can reach her.

    server:
      listen: "local"      # 127.0.0.1 only (default)
      listen: "network"    # 0.0.0.0: other devices may connect, but only with the remote token

Rules, for every HTTP request and every WebSocket:
  1. Origin check. A browser always sends Origin on WebSockets and on cross-site POSTs. If it's
     there and doesn't match the host she's served from, the request is refused. This stops any
     website you visit from talking to ws://localhost:8001 behind your back (cross-site WebSocket
     hijacking) or firing commands at the API. Native apps send no Origin and pass this check.
  2. This PC (127.0.0.1 / ::1) is trusted, unless the request came through a proxy
     (X-Forwarded-For and friends). A proxy on this PC, like Tailscale Serve later, counts as remote.
  3. Anything remote needs the token: "Authorization: Bearer <token>", ?token=<token> once
     (the browser then gets an HttpOnly cookie), or that cookie.

The token: EVA_REMOTE_TOKEN in the environment or config/secrets.env, else generated once and kept
in data/remote_token.txt (git-ignored). Compared in constant time.
"""

from __future__ import annotations

import hmac
import os
import secrets
import socket
from pathlib import Path
from typing import Mapping, Optional
from urllib.parse import urlsplit

from loguru import logger

LOOPBACK = {"127.0.0.1", "::1", "localhost"}
FORWARD_HEADERS = ("x-forwarded-for", "forwarded", "x-real-ip", "x-forwarded-host")
TOKEN_FILE = Path("data/remote_token.txt")
SECRETS_ENV = Path("config/secrets.env")
COOKIE = "eva_token"
UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def server_cfg() -> dict:
    from core.settings import get_settings
    return get_settings().get("server", {}) or {}


def network_mode() -> bool:
    return str(server_cfg().get("listen", "local")).lower() == "network"


def bind_host() -> str:
    return "0.0.0.0" if network_mode() else "127.0.0.1"


def port() -> int:
    return int(server_cfg().get("port", 8001))


def _from_secrets_env(key: str) -> Optional[str]:
    try:
        for line in SECRETS_ENV.read_text(encoding="utf-8").splitlines():
            k, sep, v = line.partition("=")
            if sep and k.strip() == key and v.strip():
                return v.strip().strip('"').strip("'")
    except OSError:
        pass
    return None


def load_token() -> str:
    """The remote token, created on first use."""
    tok = os.environ.get("EVA_REMOTE_TOKEN") or _from_secrets_env("EVA_REMOTE_TOKEN")
    if tok:
        return tok
    try:
        tok = TOKEN_FILE.read_text(encoding="utf-8").strip()
        if tok:
            return tok
    except OSError:
        pass
    tok = secrets.token_urlsafe(24)
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_text(tok + "\n", encoding="utf-8")
    logger.info(f"NETSEC: new remote token written to {TOKEN_FILE}")
    return tok


def token_ok(presented: Optional[str]) -> bool:
    if not presented:
        return False
    return hmac.compare_digest(presented.encode("utf-8"), load_token().encode("utf-8"))


def presented_token(headers: Mapping[str, str], query: Mapping[str, str], cookies: Mapping[str, str]) -> Optional[str]:
    auth = headers.get("authorization") or ""
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return query.get("token") or cookies.get(COOKIE) or None


def is_local(client_host: Optional[str], headers: Mapping[str, str]) -> bool:
    if (client_host or "") not in LOOPBACK:
        return False
    return not any(headers.get(h) for h in FORWARD_HEADERS)


def _hostport(value: str) -> str:
    return (value or "").strip().lower().rstrip("/")


def origin_ok(headers: Mapping[str, str]) -> bool:
    """No Origin (native app, curl) passes. A browser Origin must match the Host header or the allow list."""
    origin = headers.get("origin")
    if not origin:
        return True
    if origin == "null":
        return False
    allowed = {_hostport(o) for o in (server_cfg().get("allowed_origins") or [])}
    if _hostport(origin) in allowed:
        return True
    netloc = urlsplit(origin).netloc.lower()
    host = (headers.get("host") or "").lower()
    if netloc and netloc == host:
        return True
    # localhost and 127.0.0.1 are the same PC: allow either spelling against the other
    loop = {"localhost", "127.0.0.1", "[::1]"}
    o_name, _, o_port = netloc.rpartition(":") if ":" in netloc else (netloc, "", "")
    h_name, _, h_port = host.rpartition(":") if ":" in host else (host, "", "")
    return o_name in loop and h_name in loop and o_port == h_port


def verdict(method: str, client_host: Optional[str], headers: Mapping[str, str], query: Mapping[str, str],
            cookies: Mapping[str, str], websocket: bool = False) -> tuple[bool, str, bool]:
    """(allowed, reason, set_cookie). set_cookie: a valid ?token= arrived and the browser should keep it."""
    if (websocket or method.upper() in UNSAFE_METHODS) and not origin_ok(headers):
        return False, "cross-site request refused", False
    if is_local(client_host, headers):
        return True, "", False
    tok = presented_token(headers, query, cookies)
    if token_ok(tok):
        return True, "", bool(query.get("token")) and not websocket
    return False, "remote device without a valid token", False


def lan_ip() -> str:
    """This PC's address on the home network (no packet is sent)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"


def startup_banner() -> str:
    if not network_mode():
        return (f"  E.V.A. -> http://localhost:{port()}   (this PC only; set server.listen: network "
                f"in config/settings.local.yaml for other devices)")
    return (f"  E.V.A. -> http://localhost:{port()}\n"
            f"  Other devices on your network: open this once, it pairs the browser:\n"
            f"    http://{lan_ip()}:{port()}/?token={load_token()}\n"
            f"  Apps: send the header  Authorization: Bearer <token>  (token in {TOKEN_FILE} or EVA_REMOTE_TOKEN)")
