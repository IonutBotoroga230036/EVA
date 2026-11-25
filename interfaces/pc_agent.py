"""E.V.A.'s hands on your PC, as a device (v0.4).

Today the brain runs on this PC and does a task's work directly, so you don't need this. When the brain moves to the
cloud (or another machine), run this on your PC:

    python -m interfaces.pc_agent --server https://<brain>:8443 --token <your E.V.A. token>
    python -m interfaces.pc_agent                      (the brain on this PC: ws://localhost:8001)

It connects to /ws/pc like the phone connects to /ws, and serves the same calls as core/agent/pc.py: files in a
task's folder, the code sandbox, pip into E.V.A.'s own venv, winget installs (only after your yes, decided by the
brain), running programs on a task's files, copying finished files to Documents/E.V.A. It reconnects by itself.
The token can also come from the EVA_TOKEN environment variable. Nothing here decides anything: it only does
what the brain asks, inside the limits in core/agent/pc.py.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import socket

from loguru import logger

from core.agent import pc


def ws_url(server: str) -> str:
    s = (server or "http://localhost:8001").rstrip("/")
    s = "wss://" + s[len("https://"):] if s.startswith("https://") else s
    s = "ws://" + s[len("http://"):] if s.startswith("http://") else s
    return s + "/ws/pc"


async def handle(ws, raw: str) -> None:
    try:
        msg = json.loads(raw)
    except ValueError:
        return
    if msg.get("type") != "call":
        return
    method, kwargs = str(msg.get("method")), dict(msg.get("kwargs") or {})
    logger.info(f"PC AGENT: {method} {list(kwargs)}")
    out = await asyncio.to_thread(pc.serve, method, kwargs)
    await ws.send(json.dumps({"type": "result", "id": msg.get("id"), **out}))


async def run(server: str, token: str = "", ca: str = "") -> None:
    from websockets.asyncio.client import connect          # websockets 14.1 (requirements.txt)
    url = ws_url(server)
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    ssl_ctx = None
    if url.startswith("wss://"):
        import ssl
        ssl_ctx = ssl.create_default_context(cafile=ca or None)
    delay = 2
    while True:
        try:
            async with connect(url, additional_headers=headers, ssl=ssl_ctx, max_size=16 * 1024 * 1024) as ws:
                await ws.send(json.dumps({"type": "hello", "name": socket.gethostname(), "caps": list(pc.METHODS)}))
                logger.info(f"PC AGENT: connected to {url}")
                delay = 2
                async for raw in ws:
                    asyncio.create_task(handle(ws, raw))
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning(f"PC AGENT: {e.__class__.__name__}: {e}; trying again in {delay} s")
        await asyncio.sleep(delay)
        delay = min(delay * 2, 60)


def main() -> None:
    ap = argparse.ArgumentParser(description="E.V.A.'s hands on this PC")
    ap.add_argument("--server", default=os.environ.get("EVA_SERVER", "http://localhost:8001"))
    ap.add_argument("--token", default=os.environ.get("EVA_TOKEN", ""))
    ap.add_argument("--ca", default="", help="E.V.A.'s certificate (eva-ca.crt) for https servers")
    a = ap.parse_args()
    try:
        asyncio.run(run(a.server, a.token, a.ca))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
