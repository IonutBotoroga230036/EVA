"""Is the local brain healthy? (v0.9.1) Where Ollama keeps a model (GPU or CPU) and how long each call took.

27 Nov 11:07: in local mode a decision took 120 s and then failed, an answer 245 s. A 3B model on a GPU answers a
decision in one or two seconds, so something else was going on: the model partly on the CPU (VRAM full: Whisper,
other apps), models swapping, or Ollama itself stuck. These lines in the log say which:

    LOCAL: qwen2.5:3b-instruct is 100% on the GPU (2.4 GB)          GET /api/ps, at start-up and after a slow call
    LOCAL: decision 1.6 s (load 0.0 s, 2950 prompt tokens in 0.9 s, 24 out)     from Ollama's own timings
"""

from __future__ import annotations

from typing import Optional

import httpx
from loguru import logger


def placement(base_url: str, timeout: float = 4.0) -> list[dict]:
    """The loaded models and how much of each sits in VRAM."""
    r = httpx.get(f"{base_url}/api/ps", timeout=timeout)
    out = []
    for m in (r.json() or {}).get("models", []) or []:
        size, vram = int(m.get("size") or 0), int(m.get("size_vram") or 0)
        out.append({"name": m.get("name") or m.get("model") or "?", "gb": round(size / 1e9, 1),
                    "gpu_pct": round(100 * vram / size) if size else 0})
    return out


def report(base_url: str) -> str:
    try:
        models = placement(base_url)
    except Exception as e:
        line = f"LOCAL: Ollama at {base_url} doesn't answer ({type(e).__name__}); is it running?"
        logger.warning(line)
        return line
    if not models:
        line = "LOCAL: no model loaded in Ollama right now"
        logger.info(line)
        return line
    parts = []
    for m in models:
        where = "on the GPU" if m["gpu_pct"] >= 99 else ("on the CPU" if m["gpu_pct"] == 0 else
                                                         f"{m['gpu_pct']}% on the GPU, the rest on the CPU")
        parts.append(f"{m['name']} is {'100% ' if m['gpu_pct'] >= 99 else ''}{where} ({m['gb']} GB)")
    line = "LOCAL: " + "; ".join(parts)
    slow = any(m["gpu_pct"] < 99 for m in models)
    (logger.warning if slow else logger.info)(line + ("  <- partly on the CPU: local answers will be slow (VRAM full? "
                                                      "close GPU-heavy apps, or use a smaller model)" if slow else ""))
    return line


def timing(kind: str, data: dict, wall_s: float) -> str:
    """Ollama's own numbers for one call (durations are in nanoseconds)."""
    ns = 1e9
    load = float(data.get("load_duration") or 0) / ns
    pe_n, pe_t = int(data.get("prompt_eval_count") or 0), float(data.get("prompt_eval_duration") or 0) / ns
    out_n = int(data.get("eval_count") or 0)
    line = (f"LOCAL: {kind} {wall_s:.1f} s (load {load:.1f} s, {pe_n} prompt tokens in {pe_t:.1f} s, "
            f"{out_n} out)")
    (logger.warning if wall_s > 8 else logger.info)(line)
    return line


SLOW: dict = {"ts": 0.0, "why": ""}           # the last time the local brain didn't answer in time


def mark_slow(why: str) -> None:
    import time
    SLOW["ts"], SLOW["why"] = time.time(), why


def recently_slow(within_s: float = 90.0) -> Optional[str]:
    import time
    return SLOW["why"] if SLOW["ts"] and time.time() - SLOW["ts"] < within_s else None
