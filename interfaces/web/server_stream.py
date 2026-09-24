"""
server_stream.py  ->  interfaces/web/server_stream.py

The streaming server for E.V.A. v0.2. Serves the UI and runs the new
StreamOrchestrator over a WebSocket, forwarding every event to the browser:

    ack    -> a short line E.V.A. speaks the instant she picks a tool
    token  -> streamed answer text (appears live in the caption)
    widget -> data to pop a widget on the core screen
    final  -> the complete answer (browser speaks this)

Run from the REPO ROOT (D:\\Project E.V.A\\eva):

    python -m interfaces.web.server_stream

Then open  http://localhost:8001  in your browser.
Requires Ollama running with the model in core/orchestrator_stream.py pulled.
"""

from __future__ import annotations
import json
from pathlib import Path

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from loguru import logger

from core.orchestrator_hybrid import HybridOrchestrator as StreamOrchestrator

app = FastAPI(title="E.V.A. Stream", version="0.2.0")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"],
    allow_methods=["*"], allow_headers=["*"],
)

UI_FILE = Path(__file__).parent / "eva.html"


@app.get("/")
async def index():
    return HTMLResponse(UI_FILE.read_text(encoding="utf-8"))


@app.websocket("/ws")
async def ws(websocket: WebSocket):
    await websocket.accept()
    orch = StreamOrchestrator()          # one brain per connection = per-session memory
    logger.info("client connected")
    try:
        while True:
            raw = await websocket.receive_text()
            data = json.loads(raw)
            if data.get("type") != "message":
                continue
            text = (data.get("text") or "").strip()
            if not text:
                continue
            logger.info(f"USER: {text}")
            async for event in orch.process_stream(text):
                await websocket.send_text(json.dumps(event))
    except WebSocketDisconnect:
        logger.info("client disconnected")
    except Exception as e:
        logger.error(f"ws error: {e}")


if __name__ == "__main__":
    print("\n  E.V.A. -> http://localhost:8001\n")
    uvicorn.run(app, host="0.0.0.0", port=8001, log_level="info")
