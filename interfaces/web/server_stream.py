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

import sys

if __name__ == "__main__":
    # `python -m interfaces.web.server_stream` runs this file as __main__; without this, the first
    # `from interfaces.web.server_stream import ...` elsewhere loads a SECOND copy with its own empty state
    # (Nov 23: every phone alarm said "your phone app isn't open" while it was).
    sys.modules.setdefault("interfaces.web.server_stream", sys.modules[__name__])

import asyncio
import numpy as np
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
from voice.speech import SentenceChunker, TurnSpeaker, get_tts, speakable

UI_FILE = Path(__file__).parent / "eva.html"
STATIC = Path(__file__).parent / "static"
PORT = 8001
STARTED = time.time()
from core.device import CONNECTIONS  # noqa: E402  (one set of open windows, shared with core/device.py)
MAX_AUDIO_FRAME = 256 * 1024          # bytes; a larger binary frame is dropped, never buffered
MAX_PCM_COMMAND = 12 * 16000 * 2 * 4 // 3 + 16     # base64 of at most 12 s of 16 kHz PCM16


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
WAKE_LABELS = ("eva", "hey_eva", "end_eva", "other", "speech")
_detector: dict = {}                              # the trained model, reloaded when the file changes


def _wake_counts() -> dict:
    return {lbl: len(list((WAKE_DIR / lbl).glob("*.wav"))) if (WAKE_DIR / lbl).exists() else 0 for lbl in WAKE_LABELS}


@app.get("/wakeword")
async def wakeword_page():
    return FileResponse(Path(__file__).parent / "wakeword.html")


@app.get("/api/wakeword/status")
async def wakeword_status():
    from core.wakeword import features as wf
    info = json.loads(wf.INFO.read_text(encoding="utf-8")) if wf.INFO.exists() else {}
    return {"counts": _wake_counts(), "model": {"exists": wf.MODEL.exists(), **info}}


@app.post("/api/wakeword/test")
async def wakeword_test(request: Request):
    """Score a clip with the trained model: say "Eva" (should fire) or anything else (shouldn't)."""
    import io
    import wave
    from core.wakeword import features as wf
    if not wf.MODEL.exists():
        return JSONResponse({"detail": "train the model first: python -m core.wakeword.train"}, status_code=409)
    body = await request.body()
    if len(body) > 600_000 or body[:4] != b"RIFF":
        return JSONResponse({"detail": "send a short WAV clip"}, status_code=400)
    with wave.open(io.BytesIO(body), "rb") as w:
        audio = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2")
    mtime = wf.MODEL.stat().st_mtime
    if _detector.get("mtime") != mtime:
        _detector.update(mtime=mtime, d=await asyncio.to_thread(wf.Detector, wf.MODEL))
    return await asyncio.to_thread(_detector["d"].check, audio)


# v0.3 9b: the phone runs the same three models on-device. It downloads them from here (with your token), so a
# retrained model reaches the phone by itself; the two feature models are pinned (core/wakeword/features.py).
WAKE_FILES = {"melspectrogram.onnx": lambda wf: wf.MODELS / "melspectrogram.onnx",
              "embedding_model.onnx": lambda wf: wf.MODELS / "embedding_model.onnx",
              "eva.onnx": lambda wf: wf.MODEL}


def _stt_engine():
    from voice.stt import get_stt
    return get_stt()


def _romanian_hint(orch) -> None:
    """Whisper heard Romanian: she answers in Romanian (v0.3.2, 9d)."""
    try:
        if getattr(_stt_engine(), "last_language", None) == "ro":
            orch.reply_language = "ro"
    except Exception:
        pass


def heard_wake(text: str) -> str | None:
    """The PC's double-check of a phone wake (Nov 22: dictating near the phone woke her). The command after "Eva"
    ("" for "Eva" alone), or None when Whisper heard no "Eva" at all. The phone sends ~1.2 s from before the
    trigger, so a word or two before her name is allowed."""
    from voice.listen import ListenConfig
    t = (text or "").replace("E.V.A.", "Eva").replace("E.V.A", "Eva")
    words = list(re.finditer(r"[A-Za-z']+", t))
    wake = set(ListenConfig.wake_words)
    for i, m in enumerate(words[:4]):
        if m.group(0).lower() in wake:
            return t[m.end():].strip(" ,.!?")
    if words and words[-1].group(0).lower() in wake:
        return t[:words[-1].start()].strip(" ,.!?")
    return None


@app.post("/api/wakeword/verify")
async def wakeword_verify(request: Request):
    """The phone thinks it heard "Eva": raw PCM16 16 kHz mono from just before the trigger until you stopped.
    {"wake": false} means it wasn't for her and nothing happens on the phone."""
    body = await request.body()
    if not body or len(body) > 12 * 16000 * 2:
        return JSONResponse({"detail": "send at most 12 s of 16 kHz PCM16"}, status_code=400)
    engine = _stt_engine()
    if not engine:
        return JSONResponse({"detail": "speech-to-text isn't loaded"}, status_code=503)
    audio = np.frombuffer(body[:len(body) // 2 * 2], dtype="<i2").astype(np.float32) / 32768.0
    text = (await asyncio.to_thread(engine.transcribe, audio)) or ""
    cmd = heard_wake(text)
    logger.info(f"WAKE (phone): {'yes' if cmd is not None else 'no, ignored'}: {text!r}")
    from core import phone_prefs
    return {"wake": cmd is not None, "command": cmd or "", "heard": text, **phone_prefs.get()}


@app.get("/api/phone")
async def phone_get():
    from core import phone_prefs
    return phone_prefs.get()


@app.post("/api/phone")
async def phone_set(body: dict):
    """{stay_dark: true|false}: the status panel's Phone switch (same as saying "Eva, stay dark")."""
    from core import phone_prefs
    prefs = phone_prefs.set(**{k: bool(v) for k, v in body.items() if k in phone_prefs.DEFAULTS})
    audit.log("phone_settings", "status_panel", prefs)
    return prefs


@app.post("/api/phone/message")
async def phone_message(body: dict):
    """v0.3.2 (9c): a WhatsApp or Instagram notification from the phone app {app, sender, text, conversation,
    group}. Kept in memory only. When it matters she says so in every open window and answers {announce, audio}
    so the phone can say it too when its app isn't open. Quiet hours and do-not-disturb keep her quiet."""
    import base64
    from core import messages
    m = messages.get_inbox().add(app=str(body.get("app") or "")[:60], sender=str(body.get("sender") or "")[:80],
                                 text=str(body.get("text") or "")[:2000],
                                 conversation=str(body.get("conversation") or "")[:80], group=bool(body.get("group")))
    out = {"new": bool(m), "important": bool(m and m.important), "announce": "", "audio": ""}
    if not m or not messages.should_announce(m):
        return out
    oracle = get_oracle()
    if oracle.in_quiet_hours() or oracle.in_dnd():
        logger.info("MESSAGES: quiet hours or do-not-disturb; kept for 'any messages?'")
        return out
    line = messages.announcement(m)
    m.read = True                                     # said once; "any messages?" doesn't repeat it
    if messages.cfg().get("pc", True):
        await broadcast(line)
    tts = get_tts()
    wav = await asyncio.to_thread(tts.synth, line) if tts else None
    out.update(announce=line, audio=base64.b64encode(wav).decode() if wav else "")
    return out


class _DarkPhone:
    """Stands in for the phone app during a screen-off conversation, so alarms and timers still work: the actions
    go back to the phone in the answer and it runs them (it says so if it can't, on the next wake)."""
    device_caps = ("alarm", "timer", "reply", "volume")

    def __init__(self):
        self.actions: list[dict] = []

    async def request_device(self, action: str, args: dict, timeout: float = 12.0) -> dict:
        self.actions.append({"action": action, "args": args})
        return {"ok": True, "error": ""}


_dark_session: dict = {}
_END_TALK = re.compile(r"^(?:ok(?:ay)?[,.]? |thanks[,.]? |thank you[,.]? )?(?:that'?s all|that'?s it|nothing|never ?mind|"
                       r"stop|bye|goodbye|good night|no(?:,? thanks?)?)(?: for now)?[.!]*$", re.I)


@app.post("/api/phone/turn")
async def phone_turn(body: dict):
    """v0.3.1 "stay dark": a conversation with the screen off, run by the phone's wake service. {text} (already
    checked by /api/wakeword/verify) or {pcm} (a follow-up), plus {locked}. Answers {text, audio (WAV, base64),
    actions, listen}; listen=false ends the conversation."""
    import base64
    text = str(body.get("text") or "").strip()
    if not text and body.get("pcm"):
        b64 = str(body["pcm"])
        if len(b64) > MAX_PCM_COMMAND:
            return JSONResponse({"detail": "at most 12 s"}, status_code=400)
        engine = _stt_engine()
        audio = np.frombuffer(base64.b64decode(b64), dtype="<i2").astype(np.float32) / 32768.0
        text = ((await asyncio.to_thread(engine.transcribe, audio)) if engine and len(audio) > 4000 else "") or ""
        text = text.strip()
    tts = get_tts()

    async def speak(line: str, listen: bool, actions=()):
        wav = await asyncio.to_thread(tts.synth, line) if tts and line else None
        return {"text": line, "audio": base64.b64encode(wav).decode() if wav else "", "actions": list(actions),
                "listen": listen}

    if not text or len(re.sub(r"[^a-z]", "", text.lower())) < 2:
        return await speak("Yes, sir?", True) if body.get("text") is not None and not body.get("pcm") else \
            {"text": "", "audio": "", "actions": [], "listen": False}
    if _END_TALK.match(text):
        return await speak("Alright, sir.", False)
    orch = _dark_session.get("orch")
    if orch is None:
        orch = _dark_session["orch"] = HybridOrchestrator(session_id="phone-dark")
    orch.locked = bool(body.get("locked"))
    _romanian_hint(orch)
    phone = _DarkPhone()
    CONNECTIONS.add(phone)
    try:
        logger.info(f"USER (phone, screen off): {text}")
        final = ""
        async for ev in orch.process_stream(text):
            if ev.get("type") == "final":
                final = ev.get("text", "")
    finally:
        CONNECTIONS.discard(phone)
    logger.info(f"EVA (phone, screen off): {final}")
    return await speak(final, True, phone.actions)


@app.get("/api/wakeword/info")
async def wakeword_info():
    from core.wakeword import features as wf
    if not wf.MODEL.exists():
        return JSONResponse({"detail": "train the model first: python -m core.wakeword.train"}, status_code=409)
    info = json.loads(wf.INFO.read_text(encoding="utf-8")) if wf.INFO.exists() else {}
    return {"version": int(wf.MODEL.stat().st_mtime), "threshold": float(info.get("threshold", 0.5)),
            "phone_threshold": float(info.get("phone_threshold", info.get("threshold", 0.5))),
            "files": sorted(WAKE_FILES), "sample_rate": wf.SR, "frames": wf.FRAMES}


@app.get("/api/wakeword/files/{name}")
async def wakeword_file(name: str):
    from core.wakeword import features as wf
    if name not in WAKE_FILES:
        return JSONResponse({"detail": "unknown file"}, status_code=404)
    path = WAKE_FILES[name](wf)
    if name != "eva.onnx":
        wf.feature_model_dir()                  # the pinned pair only: a different pair breaks every trained model
    if not path.exists():
        return JSONResponse({"detail": "train the model first: python -m core.wakeword.train"}, status_code=409)
    return FileResponse(path, media_type="application/octet-stream")


# v0.3 9b: the overlay the phone shows over other apps and the lock screen. It is E.V.A.'s own page logic (the
# bridge script of eva.html, unchanged) driving the Claude Design wake visual (docs/design/eva_wake.html, Core).
OVERLAY_SHIM = """<script>
(() => {
  const W = window.EVA;                                       // the wake visual (Core)
  const MAP = { idle: 'wake', alert: 'wake', listening: 'listening', thinking: 'thinking', speaking: 'speaking',
                error: 'error', needs_unlock: 'needs_unlock' };
  const N = window.EvaOverlay || null;                        // the Android overlay activity
  let state = 'idle', lastBusy = Date.now(), started = Date.now(), turns = 0;
  window.EVA = {
    setState(s) { state = s; if (s !== 'idle' && s !== 'alert') { lastBusy = Date.now(); if (s === 'thinking') turns++; }
                  W.setState(MAP[s] || 'wake'); },
    setLevel(v) { W.setLevel(v || 0); },
    setCaption(t, spoken) { W.setCaption && W.setCaption(spoken ? '' : t, spoken ? t : ''); },
    setHint() {}, setStatus() {}, addTranscript() {}, closeAll() {},
    setConnection(live) { if (!live) W.setState('error'); },
    showCard(type, data) { if (type === 'confirm' && W.showConfirm) W.showConfirm(data); return 0; },
    showCards(list) { (list || []).forEach(c => window.EVA.showCard(c.type, c.data)); },
    dismiss() {}, clearCards() {}, expand() {}, openPanel() {}, openHistory() {}, alert() { return 0; }, rules: {}
  };
  // Done talking: back to whatever you were doing (or the lock screen) after a short quiet moment.
  setInterval(() => {
    const quiet = state === 'idle' && Date.now() - lastBusy > (turns ? 3500 : 15000);
    if (quiet && Date.now() - started > 4000) { W.setState('done'); setTimeout(() => N && N.close(), 600); }
  }, 500);
  addEventListener('eva:unlock-needed', () => { W.setState('needs_unlock'); N && N.unlock(); });
  addEventListener('load', () => {
    if (!N) return;
    dispatchEvent(new CustomEvent('eva:device-state', { detail: { locked: !!N.isLocked() } }));
    N.ready();                                                // the words you said right after "Eva" arrive as eva:pcm
  });
})();
</script>
"""


_overlay_cache: dict = {}


@app.get("/overlay")
async def overlay_page():
    from fastapi.responses import HTMLResponse
    page = Path(__file__).parent / "eva.html"
    key = page.stat().st_mtime
    if _overlay_cache.get("key") != key:
        _overlay_cache.update(key=key, html=build_overlay())
    # the phone's WebView may keep it for 5 minutes: the overlay shows faster after "Eva"
    return HTMLResponse(_overlay_cache["html"], headers={"Cache-Control": "private, max-age=300"})


def build_overlay() -> str:
    page = (Path(__file__).parent / "eva.html").read_text(encoding="utf-8")
    start = page.index("E.V.A. bridge")
    start = page.rindex("<script>", 0, start)
    bridge = page[start:page.index("</script>", start) + len("</script>")]
    design = (Path(__file__).resolve().parents[2] / "docs" / "design" / "eva_wake.html").read_text(encoding="utf-8")
    head_end = design.index("</body>")
    body = design.index(">", design.index("<body")) + 1
    boot = "<script>window.EVA_EMBED = true; /* variant=core is the design's default */</script>"
    return design[:body] + boot + design[body:head_end] + OVERLAY_SHIM + bridge + "\n</body></html>\n"


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
        "phone": __import__("core.phone_prefs", fromlist=["get"]).get(),
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
        self.connected_at = time.time()      # the newest phone window gets device actions
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
                    if event.get("replaced"):              # a guard swapped the answer: say the honest one instead
                        chunker.flush()
                        rest = event["text"]
                    else:
                        rest = chunker.flush() if streamed else event["text"]
                    await speaker.say(rest)
            tools = set(getattr(self.orch, "last_tools", ()) or ())
            if tools & MUSIC_TOOLS or ("media_control" in tools and re.search(r"\b(play|resume)\b", text, re.I)):
                self.music_on = True
            elif "media_control" in tools and re.search(r"\b(stop|pause|quiet)\b", text, re.I):
                self.music_on = False
            from core.tools_native import TALK_WINDOW_MS, talk_mode, touch_talk
            talking = talk_mode()
            touch_talk()
            follow = talking or not self.music_on  # while music plays, only "Eva, ..." (or a tap) starts a turn
            meta = {"type": "turn_meta", "turn": turn, "follow_up": follow}
            if talking:
                meta["listen_ms"] = TALK_WINDOW_MS          # "let's talk": keep listening until "that's all"
            elif getattr(self.orch, "last_lane", "") == "conversation":
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
        _romanian_hint(self.orch)
        await self.submit(text, voice=True, stt=True)

    async def on_wake(self) -> None:
        await self.send({"type": "wake", "text": WAKE_PHRASE})

    async def on_barge_in(self) -> None:
        await self.interrupt()

    async def on_device_state(self, locked: bool) -> None:
        was = getattr(self.orch, "locked", False)
        self.orch.locked = locked
        logger.info(f"DEVICE: phone {'locked: private things wait for an unlock' if locked else 'unlocked'}")
        waiting = getattr(self.orch, "after_unlock", None)
        if was and not locked and waiting and time.time() - waiting[1] < 120:
            self.orch.after_unlock = None
            await self.submit(waiting[0], voice=True, stt=True)       # unlocked for it: do what you asked

    async def on_pcm_command(self, b64: str) -> None:
        """PCM16 16 kHz mono (base64), recorded by the phone's wake service from "Eva" until you stopped talking."""
        import base64
        if not b64 or len(b64) > MAX_PCM_COMMAND:
            await self.on_wake()
            return
        listener = await self.ensure_listener()
        try:
            audio = np.frombuffer(base64.b64decode(b64), dtype="<i2").astype(np.float32) / 32768.0
            text = await asyncio.to_thread(listener.stt.transcribe, audio) if listener and len(audio) > 4000 else ""
        except Exception as e:
            logger.warning(f"ECHO: the phone's recording couldn't be read ({e})")
            text = ""
        from voice.listen import strip_wake
        cmd = strip_wake(text or "")
        cmd = text.strip() if cmd is None else cmd.strip()
        logger.info(f"ECHO: after the phone's wake word: {text!r}")
        if len(re.sub(r"[^a-z]", "", cmd.lower())) < 2:
            if listener:
                listener.arm(listener.cfg.command_window_secs)
            await self.on_wake()                                       # just "Eva": "Yes, sir?" and listen
            return
        await self.voice_command(cmd)

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
            if "barge" in data:                    # the phone in media mode: only "Eva" interrupts her
                listener.barge_mode = "wake" if data.get("barge") == "wake" else "voice"
                logger.info(f"ECHO: interrupting her: {'say Eva' if listener.barge_mode == 'wake' else 'just talk'}")
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
                    self.connected_at = time.time()
                    logger.info(f"DEVICE: {data.get('kind', 'device')} connected with {', '.join(self.device_caps)}")
                elif kind == "device_result":
                    fut = self._device_waits.get(str(data.get("id")))
                    if fut and not fut.done():
                        fut.set_result({"ok": bool(data.get("ok")), "error": str(data.get("error") or "")[:200]})
                elif kind in ("listen", "speaking", "audio_format"):
                    await self.on_control(data)
                elif kind == "device_state":               # v0.3 9b: the phone overlay, locked or not
                    await self.on_device_state(bool(data.get("locked")))
                elif kind == "pcm_command":                # v0.3 9b: what you said right after "Eva", screen off
                    if "text" in data:                      # already checked and transcribed by /api/wakeword/verify
                        cmd = str(data.get("text") or "").strip()
                        await (self.voice_command(cmd) if cmd else self.on_wake())
                    else:
                        await self.on_pcm_command(str(data.get("pcm") or ""))
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
