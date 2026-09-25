"""
E.V.A. streaming server (v0.2).

Run from the REPO ROOT (D:\\Project E.V.A\\eva):

    python -m interfaces.web.server_stream

Then open http://localhost:8001 (or http://<your-pc-ip>:8001 on your phone).

WebSocket protocol (server -> browser):
    hello       {"tts": "kokoro" | "browser"}         once, on connect
    phrase      {"key": "wake", "audio": b64}          cached "Yes, sir?" in her real voice
    turn_start  {"turn": n}                            before every reply
    ack / token / widget / final                       text events, as before
    audio       {"turn": n, "seq": k, "audio": b64}    one WAV per sentence, in order
    audio_end   {"turn": n, "count": N}                all audio for turn n has been sent

    heard       {"text": "..."}                        the transcript after vocabulary correction

Browser -> server:
    {"type": "message", "text": "...", "voice": bool}  a new request (cancels any reply in progress);
                                                       voice=true applies vocabulary correction
    {"type": "stop"}                                   barge-in: stop talking now

Also served: /manifest.webmanifest, /sw.js and icons, so Chrome or Edge can install
E.V.A. as a desktop app (address bar -> "Install E.V.A.").

Each reply runs as its own task, so a new message or a stop cancels it mid-sentence.
"""

from __future__ import annotations

import asyncio
import base64
import json
import threading
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from loguru import logger

from core.events.bus import get_bus
from core.mcp_client import get_mcp
from core.prompt_builder import vocabulary_text
from core.settings import get_settings
from core.vocab import correct as vocab_correct
from core.memory.cortex import close_cortex, get_cortex
from core.oracle import get_oracle
import core.telegram_bridge as telegram_bridge
from core.telegram_bridge import TelegramBridge, active as telegram_active, set_active
from core.orchestrator_hybrid import HybridOrchestrator
from skills.registry import get_registry
from voice.speech import SentenceChunker, TurnSpeaker, get_tts

UI_FILE = Path(__file__).parent / "eva.html"
STATIC = Path(__file__).parent / "static"
PORT = 8001
STARTED = time.time()
CONNECTIONS: set["Connection"] = set()


async def broadcast(text: str, widget: dict | None = None) -> bool:
    """ORACLE's voice: speak a proactive message in every open E.V.A. window."""
    delivered = False
    for conn in list(CONNECTIONS):
        try:
            await conn.proactive(text, widget)
            delivered = True
        except Exception as e:
            logger.warning(f"proactive delivery failed for {conn.session_id}: {e}")
    return delivered
WAKE_PHRASE = "Yes, sir?"


def _warm_llm() -> None:
    """Load the decision model into VRAM at startup, so the first reply isn't a cold start."""
    import httpx as _h
    from core.settings import local_cfg
    cfg = local_cfg()
    try:
        _h.post(f"{cfg['base_url']}/api/generate", json={"model": cfg["decision_model"],
                                                           "keep_alive": cfg["keep_alive"]}, timeout=120)
        logger.info(f"LLM warm: {cfg['decision_model']} loaded")
    except Exception as e:
        logger.warning(f"LLM warm-up skipped ({e})")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    bus = get_bus()
    bus.start_listening()
    cortex = get_cortex()                 # starts the embedding warm-up in the background
    registry = get_registry()
    tts = get_tts()
    if tts:                               # load Kokoro off the startup path
        threading.Thread(target=tts.warm, daemon=True, name="kokoro-warmup").start()
    threading.Thread(target=_warm_llm, daemon=True, name="llm-warmup").start()
    mcp = get_mcp()
    await mcp.start(get_settings().get("mcp", {}).get("servers", []) or [])
    oracle = get_oracle()
    tg_cfg = get_settings().get("telegram", {})
    token = telegram_bridge.load_token() if tg_cfg.get("enabled", True) else None
    telegram = TelegramBridge(token, set(tg_cfg.get("allowed_user_ids", []) or [])) if token else None
    tg_task = asyncio.create_task(telegram.run(), name="telegram") if telegram else None
    set_active(telegram, asyncio.get_running_loop())
    mode = str(tg_cfg.get("proactive", "always"))              # always | when_away | never

    async def deliver(text, widget=None):
        on_screen = await broadcast(text, widget)
        on_phone = False
        if telegram and (mode == "always" or (mode == "when_away" and not on_screen)):
            on_phone = await telegram.notify(text, widget)
        return on_screen or on_phone
    oracle.deliver = deliver
    oracle_task = asyncio.create_task(oracle.run(), name="oracle")
    logger.info(f"E.V.A. online: {cortex.stats()['facts']} facts, {len(registry.enabled())} skills, "
                f"{sum(s.connected for s in mcp.servers.values())} MCP servers, "
                f"voice: {'Kokoro' if tts else 'browser'}")
    yield
    oracle.stop()
    oracle_task.cancel()
    if telegram:
        telegram.stop()
        tg_task.cancel()
    await mcp.stop()
    bus.stop()
    close_cortex()


app = FastAPI(title="E.V.A.", version="0.2.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.get("/")
async def index():
    return HTMLResponse(UI_FILE.read_text(encoding="utf-8"))


MANIFEST = {
    "name": "E.V.A.", "short_name": "E.V.A.", "start_url": "/", "scope": "/", "display": "standalone",
    "background_color": "#08060f", "theme_color": "#08060f", "description": "Your local AI assistant",
    "icons": [{"src": "/icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any"},
              {"src": "/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any maskable"}],
}
SERVICE_WORKER = """// Installability only. No caching: the UI must always be the one the server serves.
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', e => e.waitUntil(self.clients.claim()));
self.addEventListener('fetch', () => {});
"""


@app.get("/manifest.webmanifest")
async def manifest():
    return JSONResponse(MANIFEST, media_type="application/manifest+json")


@app.get("/sw.js")
async def service_worker():
    return Response(SERVICE_WORKER, media_type="application/javascript")


@app.get("/icon-{size}.png")
async def icon(size: int):
    path = STATIC / f"icon-{size}.png"
    return FileResponse(path) if path.exists() else Response(status_code=404)


@app.get("/favicon.ico")
async def favicon():
    return FileResponse(STATIC / "icon-192.png", media_type="image/png")


@app.get("/classic")
async def classic():
    """The previous interface, kept as a fallback while the new one settles in."""
    path = Path(__file__).parent / "eva_classic.html"
    return HTMLResponse(path.read_text(encoding="utf-8")) if path.exists() else Response(status_code=404)


@app.get("/api/status")
async def status():
    from core.brain import get_brain
    from core.budget import get_budget
    from core.forge_engine import get_forge
    from core.google_api import connected as google_connected
    return JSONResponse({
        "brain_mode": get_brain().mode(),
        "budget": get_budget().today_summary(),
        "google": await asyncio.to_thread(google_connected),
        "telegram": bool(telegram_active()),
        "forge_drafts": len(get_forge().pending()),
        "uptime_s": round(time.time() - STARTED),
        "memory": get_cortex().stats(),
        "skills": get_registry().status(),
        "voice": "kokoro" if get_tts() else "browser",
        "mcp": get_mcp().status(),
        "recent_events": get_bus().recent(20),
    })


class Connection:
    """One browser tab: its orchestrator, its send lock, and the reply in progress."""

    def __init__(self, websocket: WebSocket):
        self.ws = websocket
        self.session_id = uuid.uuid4().hex[:12]
        self.orch = HybridOrchestrator(session_id=self.session_id)
        self.tts = get_tts()
        self.turn = 0
        self.current: asyncio.Task | None = None
        self._send_lock = asyncio.Lock()     # token and audio sends come from two tasks

    async def send(self, msg: dict) -> None:
        async with self._send_lock:
            await self.ws.send_text(json.dumps(msg))

    async def hello(self) -> None:
        await self.send({"type": "hello", "tts": "kokoro" if self.tts else "browser"})
        if self.tts:
            wav = await asyncio.to_thread(self.tts.phrase, WAKE_PHRASE)
            if wav:
                await self.send({"type": "phrase", "key": "wake", "text": WAKE_PHRASE,
                                 "audio": base64.b64encode(wav).decode()})

    async def reply(self, text: str, turn: int) -> None:
        await self.send({"type": "turn_start", "turn": turn})
        speaker = TurnSpeaker(self.tts, self.send, turn) if self.tts else None
        chunker, streamed = SentenceChunker(), False
        try:
            async for event in self.orch.process_stream(text):
                await self.send(event)
                if event["type"] == "final":
                    logger.info(f"EVA: {event['text']}")
                if not speaker:
                    continue
                kind = event["type"]
                if kind == "ack":
                    await speaker.say(event["text"])
                elif kind == "token":
                    streamed = True
                    for sentence in chunker.feed(event["text"]):
                        await speaker.say(sentence)
                elif kind == "final":
                    rest = chunker.flush() if streamed else event["text"]
                    await speaker.say(rest)
            if speaker:
                await speaker.finish()
        except asyncio.CancelledError:
            if speaker:
                speaker.cancel()
            raise
        except WebSocketDisconnect:
            raise
        except Exception as e:                    # one bad turn must never kill the connection
            logger.exception(f"turn failed: {e}")
            await self.send({"type": "final", "text": "Something went wrong on my side, sir. It's logged."})
            if speaker:
                speaker.cancel()
                await self.send({"type": "audio_end", "turn": turn, "count": speaker.sent})

    async def proactive(self, text: str, widget: dict | None = None) -> None:
        """A turn E.V.A. starts herself. Waits briefly if she's mid-reply, then speaks like any answer."""
        if self.current and not self.current.done():
            try:
                await asyncio.wait_for(asyncio.shield(self.current), timeout=30)
            except Exception:
                pass
        self.turn += 1
        turn = self.turn
        await self.send({"type": "turn_start", "turn": turn})
        if widget:
            await self.send({"type": "widget", "data": widget, "proactive": True})
        await self.send({"type": "final", "text": text, "proactive": True})
        self.orch.history.append({"role": "assistant", "content": text})     # so "snooze it" has context
        if self.tts:
            speaker = TurnSpeaker(self.tts, self.send, turn)
            await speaker.say(text)
            await speaker.finish()
        logger.info(f"EVA (proactive): {text}")

    async def interrupt(self) -> None:
        if self.current and not self.current.done():
            self.current.cancel()
            try:
                await self.current
            except (asyncio.CancelledError, Exception):
                pass
            logger.info("barge-in: reply cancelled")

    async def run(self) -> None:
        await self.hello()
        CONNECTIONS.add(self)
        asyncio.create_task(get_oracle().on_client_connected())         # reminders that fired while away
        try:
            while True:
                data = json.loads(await self.ws.receive_text())
                kind = data.get("type")
                if kind == "stop":
                    await self.interrupt()
                elif kind == "message":
                    text = (data.get("text") or "").strip()
                    if not text:
                        continue
                    await self.interrupt()
                    self.turn += 1
                    if data.get("voice"):
                        fixed, changes = vocab_correct(text, vocabulary_text())
                        if changes:
                            logger.info(f"VOCAB: {' | '.join(f'{a!r} -> {b!r}' for a, b in changes)}")
                            text = fixed
                            await self.send({"type": "heard", "text": text})
                    logger.info(f"USER: {text}")
                    self.current = asyncio.create_task(self.reply(text, self.turn))
        finally:
            CONNECTIONS.discard(self)
            await self.interrupt()


@app.get("/api/weather")
async def weather_debug(city: str = "", day: str = "", hour: str = ""):
    """Exactly what E.V.A. would get, e.g. /api/weather?city=Tilburg&day=tomorrow&hour=6"""
    from core.tools_native import HOME_CITY
    from core.weather import get_weather_report
    return JSONResponse(await asyncio.to_thread(get_weather_report, city or HOME_CITY, day or None, hour or None))


@app.websocket("/ws")
async def ws(websocket: WebSocket):
    await websocket.accept()
    conn = Connection(websocket)
    logger.info(f"client connected (session {conn.session_id})")
    try:
        await conn.run()
    except WebSocketDisconnect:
        logger.info(f"client disconnected (session {conn.session_id})")


if __name__ == "__main__":
    print(f"\n  E.V.A. -> http://localhost:{PORT}\n")
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")
