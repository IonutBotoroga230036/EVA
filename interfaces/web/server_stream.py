"""
E.V.A. streaming server (v0.2).

Run from the REPO ROOT (D:\\Project E.V.A\\eva):

    python -m interfaces.web.server_stream

Then open http://localhost:8001 (or http://<your-pc-ip>:8001 on your phone).

Every WebSocket connection gets its own session id, but memory is shared:
CORTEX persists turns and facts to data/cortex.db, and a new session resumes
the last few turns if you talked within the continuity window (24h default).

Events sent to the browser: ack, token, widget, final (unchanged).
GET /api/status returns memory stats, loaded skills, and recent PULSE events.
"""

from __future__ import annotations

import json
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from loguru import logger

from core.events.bus import get_bus
from core.memory.cortex import get_cortex
from core.orchestrator_hybrid import HybridOrchestrator
from skills.registry import get_registry

UI_FILE = Path(__file__).parent / "eva.html"
PORT = 8001
STARTED = time.time()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    bus = get_bus()
    bus.start_listening()
    cortex = get_cortex()          # opens data/cortex.db, logs fact/turn counts
    registry = get_registry()      # discovers skills/*/SKILL.md
    logger.info(f"E.V.A. online: {cortex.stats()['facts']} facts, {len(registry.enabled())} skills")
    yield
    bus.stop()
    cortex.close()


app = FastAPI(title="E.V.A.", version="0.2.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.get("/")
async def index():
    return HTMLResponse(UI_FILE.read_text(encoding="utf-8"))


@app.get("/api/status")
async def status():
    return JSONResponse({
        "uptime_s": round(time.time() - STARTED),
        "memory": get_cortex().stats(),
        "skills": get_registry().status(),
        "recent_events": get_bus().recent(20),
    })


@app.websocket("/ws")
async def ws(websocket: WebSocket):
    await websocket.accept()
    session_id = uuid.uuid4().hex[:12]
    orch = HybridOrchestrator(session_id=session_id)
    logger.info(f"client connected (session {session_id})")
    try:
        while True:
            data = json.loads(await websocket.receive_text())
            if data.get("type") != "message":
                continue
            text = (data.get("text") or "").strip()
            if not text:
                continue
            logger.info(f"USER: {text}")
            try:
                async for event in orch.process_stream(text):
                    await websocket.send_text(json.dumps(event))
            except WebSocketDisconnect:
                raise
            except Exception as e:     # one bad turn must never kill the connection
                logger.exception(f"turn failed: {e}")
                await websocket.send_text(json.dumps(
                    {"type": "final", "text": "Something went wrong on my side, sir. It's logged."}))
    except WebSocketDisconnect:
        logger.info(f"client disconnected (session {session_id})")


if __name__ == "__main__":
    print(f"\n  E.V.A. -> http://localhost:{PORT}\n")
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")
