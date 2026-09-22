"""
ECHO - Text-to-Speech via Kokoro (local) or edge-tts (fallback).
Kokoro runs as a Docker container on localhost:8880 with an
OpenAI-compatible API. Fully local, no data leaves your machine.
"""

import hashlib
import os
from pathlib import Path
from loguru import logger
import httpx

AUDIO_DIR = Path("./data/audio")
AUDIO_DIR.mkdir(parents=True, exist_ok=True)

VOICE_MAP = {
    "eva": "af_heart",
    "kira": "am_adam",
}

KOKORO_URL = "http://localhost:8880/v1/audio/speech"


async def synthesize(text: str, persona: str = "eva") -> str | None:
    """Convert text to speech via Kokoro. Returns path to audio file."""
    voice = VOICE_MAP.get(persona, VOICE_MAP["eva"])

    # Cache by content hash
    text_hash = hashlib.md5(f"{voice}:{text}".encode()).hexdigest()[:12]
    output_path = AUDIO_DIR / f"{text_hash}.mp3"

    if output_path.exists():
        return str(output_path)

    # Try Kokoro first (local, high quality)
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                KOKORO_URL,
                json={
                    "model": "kokoro",
                    "input": text,
                    "voice": voice,
                    "response_format": "mp3",
                },
            )
            if response.status_code == 200:
                output_path.write_bytes(response.content)
                logger.debug(f"ECHO: Kokoro generated audio ({len(text)} chars) -> {output_path.name}")
                return str(output_path)
            else:
                logger.warning(f"ECHO: Kokoro returned {response.status_code}")
    except Exception as e:
        logger.warning(f"ECHO: Kokoro unavailable ({e}), trying edge-tts fallback")

    # Fallback to edge-tts
    try:
        import edge_tts
        edge_voice = "en-US-AriaNeural" if persona == "eva" else "en-US-GuyNeural"
        communicate = edge_tts.Communicate(text, edge_voice)
        await communicate.save(str(output_path))
        logger.debug(f"ECHO: edge-tts generated audio -> {output_path.name}")
        return str(output_path)
    except Exception as e:
        logger.error(f"ECHO: All TTS failed: {e}")
        return None


async def cleanup_old_audio(max_files: int = 200):
    """Remove oldest audio files if cache gets too large."""
    files = sorted(AUDIO_DIR.glob("*.mp3"), key=os.path.getmtime)
    if len(files) > max_files:
        for f in files[: len(files) - max_files]:
            f.unlink()
        logger.info(f"ECHO: Cleaned {len(files) - max_files} old audio files")