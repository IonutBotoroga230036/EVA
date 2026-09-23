"""
E.V.A. Web Server - Tool-calling with single-response TTS.
"""

import json
from datetime import datetime
from pathlib import Path
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from loguru import logger

from core.orchestrator import Eva
from voice.tts import synthesize

app = FastAPI(title="E.V.A. API", version="0.2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

eva: Eva | None = None


@app.on_event("startup")
async def startup():
    global eva
    eva = Eva()
    eva.pulse.start_listening()
    logger.info("E.V.A. Web Server online")


@app.on_event("shutdown")
async def shutdown():
    if eva:
        eva.pulse.stop()


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    logger.info("Client connected")

    await websocket.send_text(json.dumps({
        "type": "system_init",
        "data": {
            "persona": eva.persona["name"],
            "greeting": eva.persona["greeting"],
            "budget": eva.budget.today_summary(),
            "version": eva.config["system"]["version"],
        },
        "timestamp": datetime.now().isoformat(),
    }))

    try:
        while True:
            raw = await websocket.receive_text()
            msg = json.loads(raw)

            if msg.get("type") == "chat":
                user_text = msg["content"]

                await websocket.send_text(json.dumps({
                    "type": "typing",
                    "data": {"active": True},
                    "timestamp": datetime.now().isoformat(),
                }))

                response = await eva.process(user_text)

                persona_key = eva.config["personas"]["default"]
                audio_path = await synthesize(response, persona_key)
                audio_url = None
                if audio_path:
                    filename = Path(audio_path).name
                    audio_url = f"/api/audio/{filename}"

                await websocket.send_text(json.dumps({
                    "type": "chat_response",
                    "data": {
                        "content": response,
                        "persona": eva.persona["name"],
                        "budget": eva.budget.today_summary(),
                        "audio_url": audio_url,
                    },
                    "timestamp": datetime.now().isoformat(),
                }))

            elif msg.get("type") == "get_status":
                await websocket.send_text(json.dumps({
                    "type": "status_update",
                    "data": {
                        "budget": eva.budget.today_summary(),
                        "session_messages": len(eva.session.messages),
                        "persona": eva.persona["name"],
                    },
                    "timestamp": datetime.now().isoformat(),
                }))

    except WebSocketDisconnect:
        logger.info("Client disconnected")


@app.get("/api/audio/{filename}")
async def serve_audio(filename: str):
    path = Path(f"./data/audio/{filename}")
    if not path.exists():
        return {"error": "not found"}
    media = "audio/wav" if path.suffix == ".wav" else "audio/mpeg"
    return FileResponse(path, media_type=media)


@app.get("/api/health")
async def health():
    return {
        "status": "online",
        "persona": eva.persona["name"] if eva else "offline",
        "timestamp": datetime.now().isoformat(),
    }