"""
E.V.A. streaming server (v0.2).

Run from the REPO ROOT (D:\\Project E.V.A\\eva):

    python -m interfaces.web.server_stream

Then open http://localhost:8001. By default she listens on this PC only (v0.2.5 network safety):
set server.listen: network in config/settings.local.yaml to allow other devices, which then need the
remote token (the terminal prints a pairing link). Rules: core/netsec.py.

WebSocket protocol (server -> browser):
    hello       {"tts": "kokoro" | "browser"}         once, on connect
    phrase      {"key": "wake", "audio": b64}          cached "Yes, sir?" in her real voice
    turn_start  {"turn": n}                            before every reply
    ack / token / widget / final                       text events, as before
    audio       {"turn": n, "seq": k, "audio": b64}    one WAV per sentence, in order
    audio_end   {"turn": n, "count": N}                all audio for turn n has been sent

    heard       {"text": "...", "stt": bool}           the transcript after vocabulary correction;
                                                       stt=true when E.V.A. heard it herself (v0.2.5)

  v0.2.5 server-side listening (full spec: docs/VOICE_PROTOCOL.md):
    stt         {"engine": "server"|"browser", ...}    once, after hello: stream the mic, or use browser STT
    listen      {"state": "speech"|"pause"|"end"|"noise"|"timeout"}   what the ears are doing
    wake        {"text": "Yes, sir?"}                  she heard her name alone: answer and listen
    barge_in    {"stage": "duck"|"stop"|"resume"}      the user talks over her

Browser -> server:
    {"type": "message", "text": "...", "voice": bool}  a new request (cancels any reply in progress);
                                                       voice=true applies vocabulary correction
    {"type": "stop"}                                   barge-in: stop talking now
    binary frames                                      mic audio: PCM16 little-endian, mono, 16 kHz
    {"type": "audio_format", "rate": 48000}            only if the client can't send 16 kHz
    {"type": "listen", "wake": bool, "arm": ms}        wake-word mode on/off; accept the next utterance
                                                       (arm: 0 closes the window)
    {"type": "speaking", "on": bool}                   her voice started / stopped playing (for barge-in)

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
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response
from loguru import logger

import core.netsec as netsec
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
from voice.listen import Resampler, build_listener, pipeline_status
from voice.speech import SentenceChunker, TurnSpeaker, get_tts

UI_FILE = Path(__file__).parent / "eva.html"
STATIC = Path(__file__).parent / "static"
PORT = 8001
STARTED = time.time()
CONNECTIONS: set["Connection"] = set()
MAX_AUDIO_FRAME = 256 * 1024          # bytes; a larger binary frame is dropped, never buffered


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
    import os as _os
    if _os.environ.get("EVA_TESTING"):
        return                                  # the test suite never touches your real Ollama
    import httpx as _h
    from core.settings import local_cfg
    cfg = local_cfg()
    try:
        _h.post(f"{cfg['base_url']}/api/generate", json={"model": cfg["decision_model"],
                                                           "keep_alive": cfg["keep_alive"]}, timeout=120)
        logger.info(f"LLM warm: {cfg['decision_model']} loaded")
    except Exception as e:
        logger.warning(f"LLM warm-up skipped ({e})")


def _warm_stt() -> None:
    """Load Whisper at startup when server listening is on (the first run downloads the model once)."""
    import os as _os
    if _os.environ.get("EVA_TESTING"):
        return                                  # the test suite never loads a real model
    if pipeline_status().get("engine") != "server":
        return
    from voice.stt import get_stt
    engine = get_stt()
    if engine and engine.warm():
        logger.info(f"ECHO: listening ready ({pipeline_status().get('detail')})")


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
    threading.Thread(target=_warm_stt, daemon=True, name="whisper-warmup").start()
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
                f"voice: {'Kokoro' if tts else 'browser'}, "
                f"listening: {pipeline_status().get('engine')}")
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
_extra_origins = list(netsec.server_cfg().get("allowed_origins") or [])
if _extra_origins:                         # no wildcard: only origins you list in settings (e.g. a dev server)
    app.add_middleware(CORSMiddleware, allow_origins=_extra_origins, allow_methods=["*"], allow_headers=["*"],
                       allow_credentials=True)


@app.middleware("http")
async def network_guard(request, call_next):
    """This PC is trusted; remote devices need the token; cross-site writes are refused. See core/netsec.py."""
    host = request.client.host if request.client else None
    ok, reason, set_cookie = netsec.verdict(request.method, host, request.headers, request.query_params,
                                            request.cookies)
    if not ok:
        logger.warning(f"NETSEC: refused {request.method} {request.url.path} from {host}: {reason}")
        code = 403 if reason.startswith("cross-site") else 401
        return PlainTextResponse(f"E.V.A.: {reason}. Open the pairing link printed in her terminal.", status_code=code)
    if set_cookie and request.method == "GET":
        clean = request.url.remove_query_params("token")
        resp = RedirectResponse(clean.path + (f"?{clean.query}" if clean.query else ""), status_code=303)
        resp.set_cookie(netsec.COOKIE, request.query_params["token"], httponly=True, samesite="strict",
                        max_age=365 * 24 * 3600)
        logger.info(f"NETSEC: paired a browser at {host}")
        return resp
    return await call_next(request)


from interfaces.web.routines_api import router as routines_router  # noqa: E402  (v0.2.5 Routines panel)
app.include_router(routines_router)


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
        "stt": await asyncio.to_thread(pipeline_status),
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
        self.stt: dict = {"engine": "browser"}
        self.listener = None                 # built on the first audio frame or listen message
        self._listener_lock = asyncio.Lock()
        self._resample = Resampler(16000)
        self._audio_errors = 0

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
        try:
            self.stt = await asyncio.to_thread(pipeline_status)
        except Exception as e:                    # listening is optional; the browser can always listen
            self.stt = {"engine": "browser", "reason": str(e)}
        await self.send({"type": "stt", **self.stt})

    async def reply(self, text: str, turn: int) -> None:
        await self.send({"type": "turn_start", "turn": turn})
        speaker = TurnSpeaker(self.tts, self.send, turn) if self.tts else None
        chunker, streamed = SentenceChunker(), False
        try:
            async for event in self.orch.process_stream(text):
                await self.send(event)
                if event["type"] == "final":
                    logger.info(f"EVA: {event['text']}")
                    if self.listener:
                        self.listener.said(event["text"])
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
        if self.listener:
            self.listener.said(text)
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

    async def submit(self, text: str, voice: bool = False, stt: bool = False) -> None:
        """Start a turn. Cancels any reply in progress. voice: fix names; stt: she heard it herself."""
        text = (text or "").strip()
        if not text:
            return
        await self.interrupt()
        self.turn += 1
        if voice:
            fixed, changes = vocab_correct(text, vocabulary_text())
            if changes:
                logger.info(f"VOCAB: {' | '.join(f'{a!r} -> {b!r}' for a, b in changes)}")
                text = fixed
                if not stt:
                    await self.send({"type": "heard", "text": text})
        if stt:
            await self.send({"type": "heard", "text": text, "stt": True})
        logger.info(f"USER{' (voice)' if stt else ''}: {text}")
        self.current = asyncio.create_task(self.reply(text, self.turn))

    # ------------------------------------------------------------ server-side listening (v0.2.5)
    async def ensure_listener(self):
        if self.listener or self.stt.get("engine") != "server":
            return self.listener
        async with self._listener_lock:
            if self.listener is None:
                try:
                    self.listener = await asyncio.to_thread(
                        build_listener, on_event=self.send, on_command=self.voice_command,
                        on_wake=self.on_wake, on_barge_in=self.on_barge_in)
                    logger.info(f"ECHO: listening for session {self.session_id}")
                except Exception as e:
                    logger.error(f"ECHO: listening could not start ({e}); browser speech recognition instead")
                    self.stt = {"engine": "browser", "reason": f"listening could not start: {e}"}
                    await self.send({"type": "stt", **self.stt})
        return self.listener

    async def voice_command(self, text: str) -> None:
        await self.submit(text, voice=True, stt=True)

    async def on_wake(self) -> None:
        await self.send({"type": "wake", "text": WAKE_PHRASE})

    async def on_barge_in(self) -> None:
        await self.interrupt()

    async def on_audio(self, data: bytes) -> None:
        if len(data) > MAX_AUDIO_FRAME:
            logger.warning(f"ECHO: dropped an oversized audio frame ({len(data)} bytes)")
            return
        listener = await self.ensure_listener()
        if not listener:
            return
        try:
            pcm = self._resample(data)
            if pcm:                               # a resampler may hold back its first few ms
                await listener.feed(pcm)
        except Exception as e:                    # bad audio must never drop the connection
            self._audio_errors += 1
            if self._audio_errors <= 3:
                logger.exception(f"ECHO: audio frame failed: {e}")

    async def on_control(self, data: dict) -> None:
        kind = data.get("type")
        if kind == "audio_format":
            rate = int(data.get("rate") or 16000)
            if 8000 <= rate <= 192000:
                self._resample = Resampler(rate)
            return
        listener = await self.ensure_listener()
        if not listener:
            return
        if kind == "speaking":
            await listener.set_speaking(bool(data.get("on")))
        elif kind == "listen":
            if "wake" in data:
                listener.set_wake(bool(data.get("wake")))
            if "arm" in data:
                ms = float(data.get("arm") or 0)
                listener.arm(ms / 1000) if ms > 0 else listener.disarm()

    async def run(self) -> None:
        await self.hello()
        CONNECTIONS.add(self)
        asyncio.create_task(get_oracle().on_client_connected())         # reminders that fired while away
        try:
            while True:
                msg = await self.ws.receive()
                if msg.get("type") == "websocket.disconnect":
                    raise WebSocketDisconnect(msg.get("code", 1000))
                if msg.get("bytes") is not None:
                    await self.on_audio(msg["bytes"])
                    continue
                try:
                    data = json.loads(msg.get("text") or "")
                except ValueError:
                    continue
                if not isinstance(data, dict):
                    continue
                kind = data.get("type")
                if kind == "stop":
                    await self.interrupt()
                elif kind == "message":
                    await self.submit(data.get("text") or "", voice=bool(data.get("voice")))
                elif kind in ("listen", "speaking", "audio_format"):
                    await self.on_control(data)
        finally:
            CONNECTIONS.discard(self)
            await self.interrupt()
            if self.listener:
                await self.listener.close()


@app.get("/api/weather")
async def weather_debug(city: str = "", day: str = "", hour: str = ""):
    """Exactly what E.V.A. would get, e.g. /api/weather?city=Tilburg&day=tomorrow&hour=6"""
    from core.tools_native import HOME_CITY
    from core.weather import get_weather_report
    return JSONResponse(await asyncio.to_thread(get_weather_report, city or HOME_CITY, day or None, hour or None))


@app.websocket("/ws")
async def ws(websocket: WebSocket):
    host = websocket.client.host if websocket.client else None
    ok, reason, _ = netsec.verdict("GET", host, websocket.headers, websocket.query_params, websocket.cookies,
                                   websocket=True)
    if not ok:
        logger.warning(f"NETSEC: refused WebSocket from {host}: {reason}")
        await websocket.close(code=1008)                   # before accept: the handshake gets a 403
        return
    await websocket.accept()
    conn = Connection(websocket)
    logger.info(f"client connected (session {conn.session_id})")
    try:
        await conn.run()
    except WebSocketDisconnect:
        logger.info(f"client disconnected (session {conn.session_id})")


if __name__ == "__main__":
    print("\n" + netsec.startup_banner() + "\n")
    uvicorn.run(app, host=netsec.bind_host(), port=netsec.port(), log_level="info")
