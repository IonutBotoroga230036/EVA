"""
ECHO - Text-to-Speech via edge-tts.
Generates audio from text and returns the file path.
When deployed on Linux server, this swaps to Piper with no API changes.
"""

import asyncio
import hashlib
import os
from pathlib import Path
from loguru import logger

try:
    import edge_tts
    TTS_AVAILABLE = True
except ImportError:
    TTS_AVAILABLE = False
    logger.warning("ECHO: edge-tts not installed. TTS disabled.")


VOICE_MAP = {
    "eva": "en-US-AriaNeural",
    "kira": "en-US-GuyNeural",
}

AUDIO_DIR = Path("./data/audio")
AUDIO_DIR.mkdir(parents=True, exist_ok=True)


async def synthesize(text: str, persona: str = "eva") -> str | None:
    """Convert text to speech. Returns path to audio file."""
    if not TTS_AVAILABLE:
        return None

    voice = VOICE_MAP.get(persona, VOICE_MAP["eva"])

    # Cache by content hash so we don't regenerate identical responses
    text_hash = hashlib.md5(f"{voice}:{text}".encode()).hexdigest()[:12]
    output_path = AUDIO_DIR / f"{text_hash}.mp3"

    if output_path.exists():
        return str(output_path)

    try:
        communicate = edge_tts.Communicate(text, voice)
        await communicate.save(str(output_path))
        logger.debug(f"ECHO: Generated audio ({len(text)} chars) -> {output_path.name}")
        return str(output_path)
    except Exception as e:
        logger.error(f"ECHO: TTS failed: {e}")
        return None


async def cleanup_old_audio(max_files: int = 200):
    """Remove oldest audio files if cache gets too large."""
    files = sorted(AUDIO_DIR.glob("*.mp3"), key=os.path.getmtime)
    if len(files) > max_files:
        for f in files[: len(files) - max_files]:
            f.unlink()
        logger.info(f"ECHO: Cleaned {len(files) - max_files} old audio files")