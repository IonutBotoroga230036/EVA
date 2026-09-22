"""
E.V.A. Web Server - FastAPI with WebSocket and TTS audio serving.
"""

import asyncio
import json
from datetime import datetime
from pathlib import Path
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from loguru import logger

from core.orchestrator import Eva
from voice.tts import synthesize, cleanup_old_audio

app = FastAPI(title="E.V.A. API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

eva: Eva | None = None
connected_clients: list[WebSocket] = []


@app.on_event("startup")
async def startup():
    global eva
    eva = Eva()
    eva.pulse.start_listening()
    await cleanup_old_audio()
    logger.info("E.V.A. Web Server online")


@app.on_event("shutdown")
async def shutdown():
    if eva:
        eva.pulse.stop()


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    connected_clients.append(websocket)
    logger.info(f"Client connected. Total: {len(connected_clients)}")

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

                # Generate audio
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
        connected_clients.remove(websocket)
        logger.info(f"Client disconnected. Total: {len(connected_clients)}")


@app.get("/api/audio/{filename}")
async def serve_audio(filename: str):
    """Serve generated audio files."""
    path = Path(f"./data/audio/{filename}")
    if not path.exists() or not path.suffix == ".mp3":
        return {"error": "not found"}
    return FileResponse(path, media_type="audio/mpeg")


@app.get("/api/health")
async def health():
    return {
        "status": "online",
        "persona": eva.persona["name"] if eva else "offline",
        "timestamp": datetime.now().isoformat(),
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("interfaces.web.server:app", host="0.0.0.0", port=8000, reload=True)