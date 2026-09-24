"""
Settings loader. Reads config/settings.yaml once and caches it.
Every module asks here instead of parsing YAML itself, so paths and model
names live in exactly one place.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml
from loguru import logger

SETTINGS_PATH = Path("config/settings.yaml")


@lru_cache(maxsize=1)
def get_settings() -> dict:
    try:
        return yaml.safe_load(SETTINGS_PATH.read_text(encoding="utf-8")) or {}
    except Exception as e:
        logger.warning(f"settings: could not read {SETTINGS_PATH} ({e}); using defaults")
        return {}


def local_cfg() -> dict:
    """The inference.local block with v0.2 defaults filled in."""
    loc = dict(get_settings().get("inference", {}).get("local", {}))
    loc.setdefault("base_url", "http://localhost:11434")
    loc.setdefault("decision_model", "qwen2.5:3b-instruct")
    loc.setdefault("answer_model", loc["decision_model"])
    loc.setdefault("vision_model", "qwen2.5vl:3b")
    loc.setdefault("keep_alive", "30m")
    return loc
