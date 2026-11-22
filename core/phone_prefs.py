"""Phone settings that live on the PC, so the voice ('Eva, stay dark') and the status panel change the same thing,
and the phone picks them up with every wake (the /api/wakeword/verify answer carries them). v0.3.1, Nov 22."""

from __future__ import annotations

import json
from pathlib import Path

PATH = Path("data/phone.json")
DEFAULTS = {"stay_dark": False}       # stay dark: with the screen off, she answers by voice only, screen stays off


def get() -> dict:
    try:
        return {**DEFAULTS, **json.loads(PATH.read_text(encoding="utf-8"))}
    except (OSError, ValueError):
        return dict(DEFAULTS)


def set(**changes) -> dict:          # noqa: A001  (phone_prefs.set reads naturally)
    prefs = {**get(), **{k: v for k, v in changes.items() if k in DEFAULTS}}
    PATH.parent.mkdir(parents=True, exist_ok=True)
    PATH.write_text(json.dumps(prefs, indent=2), encoding="utf-8")
    return prefs
