"""
Settings: config/settings.yaml (defaults, updated with each release) deep-merged with
config/settings.local.yaml (YOUR overrides, git-ignored, never overwritten by an update).

Put only what you change in settings.local.yaml, for example:

    obsidian:
      vault_path: "D:/Obsidian/Second Brain"
    voice:
      tts:
        voice_eva: "bf_emma"
        lang_code: "b"

Nested keys merge one by one, so the rest of each section keeps its defaults.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml
from loguru import logger

SETTINGS_PATH = Path("config/settings.yaml")
LOCAL_PATH = Path("config/settings.local.yaml")


def deep_merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for k, v in (override or {}).items():
        out[k] = deep_merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def _read(path: Path) -> dict:
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except FileNotFoundError:
        return {}
    except Exception as e:
        logger.error(f"settings: {path} is not valid YAML ({e}); ignoring it")
        return {}


@lru_cache(maxsize=1)
def get_settings() -> dict:
    base = _read(SETTINGS_PATH)
    if not base:
        logger.warning(f"settings: could not read {SETTINGS_PATH}; using defaults")
    local = _read(LOCAL_PATH)
    if local:
        logger.info(f"settings: your overrides from {LOCAL_PATH} are applied")
    return deep_merge(base, local)


def local_cfg() -> dict:
    """The inference.local block with v0.2 defaults filled in."""
    loc = dict(get_settings().get("inference", {}).get("local", {}))
    loc.setdefault("base_url", "http://localhost:11434")
    loc.setdefault("decision_model", "qwen2.5:3b-instruct")
    loc.setdefault("answer_model", loc["decision_model"])
    loc.setdefault("vision_model", "qwen2.5vl:3b")
    loc.setdefault("keep_alive", "30m")
    return loc
