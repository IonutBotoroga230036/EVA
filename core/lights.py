"""
AURA lights: your Tuya-based LED strips (LSC Smart Connect, Smart Life), controlled LOCALLY over Wi-Fi.

Setup once (docs/LIGHTS_SETUP.md): move the strips to the Smart Life app, get their local keys with
    cd data\\lights  &&  python -m tinytuya wizard
which writes devices.json (and snapshot.json with IP addresses) into data/lights/, git-ignored.

- No cloud per command: E.V.A. talks straight to each strip on your network (tinytuya).
- Several strips are switched in parallel, so the room changes at once.
- Tuya devices accept one connection at a time: close the Smart Life app if a command fails.
"""

from __future__ import annotations

import colorsys
import json
import re
from concurrent.futures import ThreadPoolExecutor
from difflib import SequenceMatcher
from pathlib import Path
from typing import Callable, Optional

from loguru import logger

DIR = Path("data/lights")

COLORS = {
    "red": (255, 0, 0), "crimson": (220, 20, 60), "orange": (255, 110, 0), "amber": (255, 160, 0),
    "yellow": (255, 220, 0), "gold": (255, 180, 20), "green": (0, 255, 0), "lime": (160, 255, 0),
    "teal": (0, 200, 170), "cyan": (0, 255, 255), "turquoise": (60, 225, 210), "blue": (0, 60, 255),
    "navy": (0, 20, 140), "purple": (140, 60, 255), "violet": (139, 92, 246), "lavender": (200, 170, 255),
    "magenta": (255, 0, 200), "pink": (255, 90, 170), "rose": (255, 60, 120),
}
WHITES = {"warm white": 0, "warm": 0, "soft white": 20, "white": 50, "neutral white": 50, "cool white": 100,
          "cold white": 100, "daylight": 100}


def parse_color(text: str) -> Optional[dict]:
    """'purple' -> {'rgb': (...)}; 'warm white' -> {'white': 0}; '#ff00aa' -> {'rgb': (255, 0, 170)}."""
    t = (text or "").lower().strip()
    m = re.search(r"#?\b([0-9a-f]{6})\b", t)
    if m:
        h = m.group(1)
        return {"rgb": (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)), "name": f"#{h}"}
    for name in sorted(WHITES, key=len, reverse=True):
        if re.search(rf"\b{name}\b", t):
            return {"white": WHITES[name], "name": name}
    for name in sorted(COLORS, key=len, reverse=True):
        if re.search(rf"\b{name}\b", t):
            return {"rgb": COLORS[name], "name": name}
    return None


def parse_brightness(text: str) -> Optional[int]:
    t = (text or "").lower()
    m = re.search(r"(\d{1,3})\s*(?:%|percent)", t) or re.search(r"\b(?:to|at)\s+(\d{1,3})\b", t)
    if m:
        return max(1, min(100, int(m.group(1))))
    for words, pct in ((("full", "max", "brightest"), 100), (("bright",), 80), (("half",), 50),
                       (("dim", "low", "soft"), 25), (("very dim", "night light", "lowest"), 5)):
        if any(re.search(rf"\b{w}\b", t) for w in words):
            return pct
    return None


class Light:
    """One strip. Wraps tinytuya.BulbDevice so tests can swap in a fake."""

    def __init__(self, dev: dict, factory: Optional[Callable] = None):
        self.name = dev.get("name") or dev["id"]
        self.dev = dev
        self._factory = factory
        self._d = None

    def _device(self):
        if self._d is None:
            if self._factory:
                self._d = self._factory(self.dev)
            else:
                import tinytuya
                self._d = tinytuya.BulbDevice(dev_id=self.dev["id"], address=self.dev.get("ip") or "Auto",
                                              local_key=self.dev["key"], version=float(self.dev.get("version") or 3.3))
                self._d.set_socketTimeout(4)
        return self._d

    def apply(self, on: Optional[bool] = None, rgb=None, white: Optional[int] = None,
              brightness: Optional[int] = None) -> None:
        d = self._device()
        if on is False:
            d.turn_off()
            return
        d.turn_on()
        if rgb is not None:
            # tinytuya sets colour at full value; brightness follows as a separate step
            d.set_colour(*rgb)
        elif white is not None:
            d.set_white_percentage(brightness or 80, white)
            return
        if brightness is not None:
            d.set_brightness_percentage(brightness)


class Lights:
    def __init__(self, devices: Optional[list[dict]] = None, factory: Optional[Callable] = None):
        self.lights = [Light(d, factory) for d in (devices if devices is not None else load_devices())]

    def pick(self, which: str = "") -> list[Light]:
        w = (which or "").lower().strip()
        if not w or w in ("all", "the lights", "lights", "everything", "room", "all lights"):
            return self.lights
        scored = sorted(self.lights, key=lambda l: SequenceMatcher(None, w, l.name.lower()).ratio(), reverse=True)
        best = [l for l in scored if w in l.name.lower() or SequenceMatcher(None, w, l.name.lower()).ratio() > 0.6]
        return best[:1] if best else []

    def apply(self, which: str = "", **kw) -> tuple[list[str], list[str]]:
        targets = self.pick(which)
        ok, failed = [], []

        def run(l):
            try:
                l.apply(**kw)
                return l.name, None
            except Exception as e:
                logger.warning(f"LIGHTS: {l.name} failed: {e}")
                return l.name, e
        with ThreadPoolExecutor(max_workers=max(1, len(targets))) as ex:
            for name, err in ex.map(run, targets):
                (failed if err else ok).append(name)
        return ok, failed


def load_devices(folder: Path = DIR) -> list[dict]:
    """devices.json from the tinytuya wizard, with IPs from snapshot.json when it exists."""
    try:
        devs = json.loads((folder / "devices.json").read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    ips = {}
    try:
        snap = json.loads((folder / "snapshot.json").read_text(encoding="utf-8"))
        ips = {d["id"]: d.get("ip") for d in snap.get("devices", [])}
    except Exception:
        pass
    out = []
    for d in devs:
        if not d.get("key"):
            continue
        category = str(d.get("category", "")).lower()
        if category and category not in ("dd", "dj", "dc", "fwd", "tgq", "xdd", "tyndj", "light", ""):
            continue                                         # only lights and strips
        out.append({"id": d["id"], "key": d["key"], "name": d.get("name") or d["id"],
                    "version": d.get("version") or 3.3, "ip": d.get("ip") or ips.get(d["id"])})
    return out


# ------------------------------------------------------------------ moods
MOODS_PATH = Path("data/moods.json")
DEFAULT_MOODS = {
    "home":   {"white": 10, "brightness": 70, "playlist": ""},
    "red":    {"color": "red", "brightness": 60, "playlist": "The Weeknd"},
    "blue":   {"color": "blue", "brightness": 45, "playlist": "blues"},
    "purple": {"color": "violet", "brightness": 50, "playlist": "chill"},
    "relax":  {"color": "amber", "brightness": 35, "playlist": "jazz"},
    "focus":  {"white": 90, "brightness": 90, "playlist": "Deep Focus"},
    "party":  {"color": "magenta", "brightness": 100, "playlist": "party"},
    "night":  {"white": 0, "brightness": 8, "playlist": ""},
}


def load_moods() -> dict:
    try:
        custom = json.loads(MOODS_PATH.read_text(encoding="utf-8"))
    except Exception:
        custom = {}
    return {**DEFAULT_MOODS, **custom}


def save_mood(name: str, mood: dict) -> None:
    try:
        custom = json.loads(MOODS_PATH.read_text(encoding="utf-8"))
    except Exception:
        custom = {}
    custom[name.lower().strip()] = mood
    MOODS_PATH.parent.mkdir(parents=True, exist_ok=True)
    MOODS_PATH.write_text(json.dumps(custom, indent=2), encoding="utf-8")


def find_mood(name: str) -> Optional[tuple[str, dict]]:
    moods = load_moods()
    n = (name or "").lower().strip()
    if n in moods:
        return n, moods[n]
    color = parse_color(n)                                    # "I feel crimson" -> a colour mood on the fly
    if color and "rgb" in color:
        return n, {"color": color["name"], "brightness": 60, "playlist": ""}
    best = max(moods, key=lambda k: SequenceMatcher(None, n, k).ratio(), default=None)
    if best and SequenceMatcher(None, n, best).ratio() > 0.75:
        return best, moods[best]
    return None


_lights: Optional[Lights] = None


def get_lights() -> Lights:
    global _lights
    if _lights is None or not _lights.lights:
        _lights = Lights()
    return _lights
