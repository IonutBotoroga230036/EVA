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
import re
import base64
import json
import threading
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import Request, FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response
from loguru import logger

import core.netsec as netsec
from core import conversation
from core.brain import get_brain
from core.security.audit import audit
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


MUSIC_TOOLS = {"spotify_play", "set_mood"}       # no follow-up window after music starts: lyrics aren't commands


async def announce_forge(msg: dict) -> None:
    """A finished FORGE build: every open window says it, and its next "yes" installs a ready skill."""
    from core.forge_jobs import offer_install
    if not msg.get("say"):
        return
    for conn in list(CONNECTIONS):
        try:
            offer_install(conn.orch, msg)
            await conn.proactive(msg["say"], msg.get("widget"))
        except Exception as e:
            logger.warning(f"FORGE: could not tell a window ({e})")


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
    # v0.2.5 FORGE jobs: a build finishes in a worker thread; hop onto this loop before touching any window
    loop = asyncio.get_running_loop()

    def _on_forge_done(data: dict) -> None:
        if loop.is_closed():
            return
        asyncio.run_coroutine_threadsafe(announce_forge(data.get("message") or {}), loop)
    get_bus().subscribe("forge.done", _on_forge_done)

    # v0.3: the phone as E.V.A.'s hands, and actions later
    from core import device, later as later_mod
    from core.orchestrator_hybrid import ToolBelt
    device.LOOP = loop

    async def _run_later(tool: str, args: dict) -> dict:
        return await ToolBelt(get_registry(), None).aexecute(tool, args)

    async def _tell(text: str, session: str = "") -> None:
        here = next((c for c in list(CONNECTIONS) if session and c.session_id == session), None)
        if here:                                    # the window you asked from; otherwise every window
            await here.proactive(text)
            return
        if not await broadcast(text):
            from core import telegram_bridge
            await asyncio.to_thread(telegram_bridge.send_from_thread, text)
    later_mod.get_later().start(loop, _run_later, _tell)
    yield
    get_bus().unsubscribe("forge.done", _on_forge_done)
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
    if (netsec.https_enabled() and request.url.scheme == "http" and request.url.path not in netsec.PUBLIC_PATHS
            and not netsec.is_local(host, request.headers) and not netsec.proxied(request.headers)):
        # other devices use HTTPS (the mic needs a secure page); the pairing token travels along
        target = request.url.replace(scheme="https", port=netsec.https_port())
        return RedirectResponse(str(target), status_code=307)
    ok, reason, set_cookie = netsec.verdict(request.method, host, request.headers, request.query_params,
                                            request.cookies, path=request.url.path)
    if not ok:
        logger.warning(f"NETSEC: refused {request.method} {request.url.path} from {host}: {reason}")
        code = 403 if reason.startswith("cross-site") else 401
        return PlainTextResponse(f"E.V.A.: {reason}. Open the pairing link printed in her terminal.", status_code=code)
    if set_cookie and request.method == "GET":
        clean = request.url.remove_query_params("token")
        resp = RedirectResponse(clean.path + (f"?{clean.query}" if clean.query else ""), status_code=303)
        resp.set_cookie(netsec.COOKIE, request.query_params["token"], httponly=True, samesite="strict",
                        max_age=365 * 24 * 3600, secure=request.url.scheme == "https")
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


def _remote_status() -> dict:
    from core import tailnet
    return tailnet.state(netsec.port())


def _forge_jobs_status() -> dict:
    from core.forge_jobs import get_jobs
    jobs = get_jobs()
    run = jobs.running()
    return {"running": run.request if run else "", "minutes": round((time.time() - run.started) / 60) if run else 0,
            "waiting": len(jobs.waiting())}


WAKE_DIR = Path("data/wakeword/samples")         # v0.3 9b: your "Eva" clips for training the phone's wake word
WAKE_LABELS = ("eva", "hey_eva", "other")


def _wake_counts() -> dict:
    return {lbl: len(list((WAKE_DIR / lbl).glob("*.wav"))) if (WAKE_DIR / lbl).exists() else 0 for lbl in WAKE_LABELS}


@app.get("/wakeword")
async def wakeword_page():
    return FileResponse(Path(__file__).parent / "wakeword.html")


@app.get("/api/wakeword/status")
async def wakeword_status():
    return {"counts": _wake_counts()}


@app.post("/api/wakeword/sample")
async def wakeword_sample(request: Request, label: str = ""):
    """A 2-second 16 kHz WAV clip from the recorder page. Only WAV, only these labels, only small files."""
    if label not in WAKE_LABELS:
        return JSONResponse({"detail": "label must be eva, hey_eva or other"}, status_code=400)
    body = await request.body()
    if len(body) > 600_000 or len(body) < 1000 or body[:4] != b"RIFF" or body[8:12] != b"WAVE":
        return JSONResponse({"detail": "send a short WAV clip"}, status_code=400)
    folder = WAKE_DIR / label
    folder.mkdir(parents=True, exist_ok=True)
    n = len(list(folder.glob("*.wav"))) + 1
    (folder / f"{label}_{n:03d}.wav").write_bytes(body)
    return {"saved": f"{label}_{n:03d}.wav", "counts": _wake_counts()}


@app.get("/api/conversation")
async def conversation_get():
    return {"enabled": conversation.enabled()}


@app.post("/api/conversation")
async def conversation_set(body: dict):
    """{enabled: true|false}: the status panel's Conversation lane switch (v0.2.5 milestone 4)."""
    on = bool(body.get("enabled"))
    conversation.set_enabled(on)
    audit.log("conversation_lane", "status_panel", {"enabled": on})
    return {"enabled": on}


@app.get("/api/brain")
async def brain_overview():
    return await asyncio.to_thread(get_brain().overview)


@app.post("/api/brain")
async def brain_set(body: dict):
    """{mode} for the default, or {feature, mode} with mode default|local|auto|cloud (v0.2.5 milestone 7)."""
    from core.brain import FEATURES, MODES
    b = get_brain()
    mode, feature = str(body.get("mode", "")), str(body.get("feature", ""))
    if feature:
        if feature not in FEATURES or mode not in (*MODES, "default"):
            return JSONResponse({"error": "feature must be one of " + ", ".join(FEATURES)}, status_code=400)
        b.set_feature(feature, mode)
    else:
        if mode not in MODES:
            return JSONResponse({"error": "mode must be local, auto or cloud"}, status_code=400)
        b.set_mode(mode)
    audit.log("brain_mode_changed", "status_panel", {"feature": feature or "default", "mode": mode})
    return await asyncio.to_thread(b.overview)


@app.get("/eva-ca.crt")
async def eva_ca():
    """E.V.A.'s local CA certificate, for installing on a phone (public; the key never leaves this PC)."""
    from core import tls
    path = tls.ca_path()
    if not path.exists():
        return PlainTextResponse("No certificate yet: set server.listen: network and restart E.V.A.", status_code=404)
    return Response(path.read_bytes(), media_type="application/x-x509-ca-cert",
                    headers={"Content-Disposition": 'attachment; filename="eva-ca.crt"'})


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
        "brain": await asyncio.to_thread(get_brain().overview),
        "forge_jobs": _forge_jobs_status(),
        "conversation": {"enabled": conversation.enabled()},
        "remote": await asyncio.to_thread(_remote_status),
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
        self.music_on = False                # E.V.A. started music and nobody stopped it: lyrics aren't commands
        self.device_caps: tuple = ()         # v0.3: the Android app says what it can do (alarm, timer)
        self._device_waits: dict = {}
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
        last_final = ""
        try:
            async for event in self.orch.process_stream(text):
                await self.send(event)
                if event["type"] == "final":
                    logger.info(f"EVA: {event['text']}")
                    last_final = event["text"]
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
            tools = set(getattr(self.orch, "last_tools", ()) or ())
            if tools & MUSIC_TOOLS or ("media_control" in tools and re.search(r"\b(play|resume)\b", text, re.I)):
                self.music_on = True
            elif "media_control" in tools and re.search(r"\b(stop|pause|quiet)\b", text, re.I):
                self.music_on = False
            follow = not self.music_on             # while music plays, only "Eva, ..." (or a tap) starts a turn
            meta = {"type": "turn_meta", "turn": turn, "follow_up": follow}
            if getattr(self.orch, "last_lane", "") == "conversation":
                ms = conversation.follow_window_ms(last_final)
                if ms:
                    meta["listen_ms"] = ms                 # she asked you something: more time to think
            await self.send(meta)
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

    async def _check_mic(self, listener, delay: float = 3.0) -> None:
        """3 s into an open window, still nothing: say why. (Right after her own voice, Android's echo
        cancellation mutes the phone mic for a moment; checking at once gave false alarms.)"""
        await asyncio.sleep(delay)
        if not listener.armed() or listener.speaking or getattr(listener, "_utt", None) is not None:
            return
        problem = listener.mic_problem()
        if problem:
            logger.warning(f"ECHO: {problem} (session {self.session_id})")

    async def request_device(self, action: str, args: dict, timeout: float = 12.0) -> dict:
        """Ask the phone to do something; its answer, not our hope, decides what she says."""
        import uuid
        rid = uuid.uuid4().hex[:8]
        fut = asyncio.get_running_loop().create_future()
        self._device_waits[rid] = fut
        await self.send({"type": "device_action", "id": rid, "action": action, "args": args})
        try:
            return await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError:
            return {"ok": False, "error": "the phone didn't answer in time"}
        finally:
            self._device_waits.pop(rid, None)

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
                logger.info(f"ECHO: the window opened a {ms / 1000:.0f}s listening window" if ms > 0
                            else "ECHO: the window closed its listening window")
                if ms > 0 and hasattr(listener, "mic_problem"):
                    asyncio.create_task(self._check_mic(listener))
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
                elif kind == "device":
                    self.device_caps = tuple(str(c) for c in (data.get("caps") or [])[:20])
                    logger.info(f"DEVICE: {data.get('kind', 'device')} connected with {', '.join(self.device_caps)}")
                elif kind == "device_result":
                    fut = self._device_waits.get(str(data.get("id")))
                    if fut and not fut.done():
                        fut.set_result({"ok": bool(data.get("ok")), "error": str(data.get("error") or "")[:200]})
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
    if (ok and netsec.https_enabled() and websocket.url.scheme == "ws" and not netsec.is_local(host, websocket.headers)
            and not netsec.proxied(websocket.headers)):
        ok, reason = False, "other devices must use wss:// (HTTPS)"
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


async def _serve_both(cert: str, key: str) -> None:
    """HTTP on :8001 (this PC; phones only get the CA and a redirect) and HTTPS on :8443 for other devices.
    One app, one lifespan: the HTTPS server skips startup/shutdown so ORACLE and Telegram run once."""
    plain = uvicorn.Server(uvicorn.Config(app, host=netsec.bind_host(), port=netsec.port(), log_level="info"))
    secure = uvicorn.Server(uvicorn.Config(app, host="0.0.0.0", port=netsec.https_port(), log_level="info",
                                           ssl_certfile=cert, ssl_keyfile=key, lifespan="off"))
    tasks = [asyncio.create_task(plain.serve()), asyncio.create_task(secure.serve())]
    await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    plain.should_exit = secure.should_exit = True           # one stops (Ctrl+C), both stop
    await asyncio.gather(*tasks, return_exceptions=True)


if __name__ == "__main__":
    if netsec.https_enabled():
        from core import tls
        cert_file, key_file = tls.ensure([netsec.lan_ip()])
        print("\n" + netsec.startup_banner() + "\n")
        try:
            asyncio.run(_serve_both(cert_file, key_file))
        except KeyboardInterrupt:
            pass
    else:
        print("\n" + netsec.startup_banner() + "\n")
        uvicorn.run(app, host=netsec.bind_host(), port=netsec.port(), log_level="info")
