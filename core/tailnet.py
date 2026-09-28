"""
Private remote access with Tailscale (v0.3 milestone 8).

Your phone reaches E.V.A. from anywhere through your tailnet: no open ports, a real HTTPS certificate on
<pc>.<tailnet>.ts.net, and E.V.A. can stay on listen: local (invisible on your Wi-Fi). Set up once:

    1. Tailscale on the PC and the phone, same account; in the admin console enable MagicDNS and HTTPS.
    2. On the PC:   tailscale serve --bg --https=443 http://127.0.0.1:8001
    3. The app (or the phone's browser): https://<pc>.<tailnet>.ts.net  with the remote token.

Requests through Tailscale Serve arrive from 127.0.0.1 WITH forwarding headers, so core/netsec.py treats
them as remote: the token is still required. This module only reads Tailscale's state for the banner and
the status panel; it never changes your Tailscale configuration.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import time
from typing import Optional

_cache: dict = {"t": 0.0, "v": None}


def _run(args: list[str]) -> Optional[dict]:
    exe = shutil.which("tailscale")
    if not exe:
        return None
    try:
        out = subprocess.run([exe, *args], capture_output=True, text=True, encoding="utf-8", errors="replace",
                             timeout=4)
        return json.loads(out.stdout) if out.returncode == 0 and out.stdout.strip() else None
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return None


def parse(status: Optional[dict], serve: Optional[dict], port: int) -> dict:
    """Pure: Tailscale's JSON -> what E.V.A. needs to know. (Tested without Tailscale installed.)"""
    if not status:
        return {"installed": False, "running": False, "url": "", "serving": False}
    me = status.get("Self") or {}
    dns = (me.get("DNSName") or "").rstrip(".")
    running = status.get("BackendState") == "Running"
    serving = False
    for host, cfg in ((serve or {}).get("Web") or {}).items():
        for handler in (cfg.get("Handlers") or {}).values():
            proxy = str(handler.get("Proxy") or "")
            if proxy.rstrip("/").endswith(f":{port}"):
                serving = True
    return {"installed": True, "running": running, "dns": dns,
            "ip": (me.get("TailscaleIPs") or [""])[0],
            "url": f"https://{dns}" if dns and serving else "", "serving": serving}


def state(port: int = 8001, max_age: float = 60) -> dict:
    now = time.time()
    if _cache["v"] is not None and now - _cache["t"] < max_age:
        return _cache["v"]
    v = parse(_run(["status", "--json"]), _run(["serve", "status", "--json"]), port)
    _cache.update(t=now, v=v)
    return v


def banner_line(port: int = 8001) -> str:
    s = state(port)
    if s.get("url"):
        return f"  Anywhere (Tailscale): {s['url']}   (the phone app or browser needs the remote token)"
    if s.get("running") and not s.get("serving"):
        return (f"  Tailscale is on. For access from anywhere run once:  "
                f"tailscale serve --bg --https=443 http://127.0.0.1:{port}")
    return ""
