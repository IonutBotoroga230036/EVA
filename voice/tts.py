"""
ECHO - Text-to-Speech via Kokoro (local).
Fully local, no internet needed after first model download.
"""

import hashlib
import os
import asyncio
from pathlib import Path
from loguru import logger

AUDIO_DIR = Path("./data/audio")
AUDIO_DIR.mkdir(parents=True, exist_ok=True)

VOICE_MAP = {
    "eva": "af_heart",
    "kira": "am_adam",
}

_pipeline = None


def _get_pipeline():
    global _pipeline
    if _pipeline is None:
        try:
            from kokoro import KPipeline
            _pipeline = KPipeline(lang_code="a")
            logger.info("ECHO: Kokoro TTS pipeline loaded")
        except Exception as e:
            logger.error(f"ECHO: Failed to load Kokoro: {e}")
            return None
    return _pipeline


async def synthesize(text: str, persona: str = "eva") -> str | None:
    """Convert text to speech. Returns path to audio file."""
    voice = VOICE_MAP.get(persona, VOICE_MAP["eva"])

    text_hash = hashlib.md5(f"{voice}:{text}".encode()).hexdigest()[:12]
    output_path = AUDIO_DIR / f"{text_hash}.wav"

    if output_path.exists():
        return str(output_path)

    pipe = _get_pipeline()
    if pipe is not None:
        try:
            import soundfile as sf

            def generate():
                for _, _, audio in pipe(text, voice=voice, speed=1.0):
                    return audio
                return None

            audio = await asyncio.to_thread(generate)
            if audio is not None:
                sf.write(str(output_path), audio, 24000)
                logger.debug(f"ECHO: Kokoro ({voice}) -> {output_path.name}")
                return str(output_path)
        except Exception as e:
            logger.warning(f"ECHO: Kokoro failed ({e}), trying fallback")

    # Fallback to edge-tts
    try:
        import edge_tts
        mp3_path = output_path.with_suffix(".mp3")
        edge_voice = "en-US-AriaNeural" if persona == "eva" else "en-US-GuyNeural"
        communicate = edge_tts.Communicate(text, edge_voice)
        await communicate.save(str(mp3_path))
        return str(mp3_path)
    except Exception as e:
        logger.error(f"ECHO: All TTS failed: {e}")
        return None


async def cleanup_old_audio(max_files: int = 200):
    files = sorted(
        list(AUDIO_DIR.glob("*.wav")) + list(AUDIO_DIR.glob("*.mp3")),
        key=os.path.getmtime,
    )
    if len(files) > max_files:
        for f in files[: len(files) - max_files]:
            f.unlink()