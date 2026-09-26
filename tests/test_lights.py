"""AURA lights and moods, against fake strips (same methods as tinytuya.BulbDevice)."""

import json

import pytest

import core.lights as cl
from core.lights import Lights, find_mood, load_devices, parse_brightness, parse_color
from core.orchestrator_hybrid import ToolBelt, fast_path
from skills.registry import SkillRegistry


class FakeStrip:
    def __init__(self, dev, fail=False):
        self.dev, self.fail, self.log = dev, fail, []

    def _do(self, *a):
        if self.fail:
            raise ConnectionError("unreachable")
        self.log.append(a)

    def turn_on(self): self._do("on")
    def turn_off(self): self._do("off")
    def set_colour(self, r, g, b): self._do("colour", (r, g, b))
    def set_brightness_percentage(self, b): self._do("brightness", b)
    def set_white_percentage(self, b, t): self._do("white", b, t)


@pytest.fixture
def strips(monkeypatch, tmp_path):
    made = {}

    def factory(dev):
        made[dev["name"]] = FakeStrip(dev, fail=dev.get("fail", False))
        return made[dev["name"]]
    devs = [{"id": "a", "key": "k", "name": "Desk strip"}, {"id": "b", "key": "k", "name": "Bed strip"}]
    monkeypatch.setattr(cl, "_lights", Lights(devs, factory))
    monkeypatch.setattr(cl, "MOODS_PATH", tmp_path / "moods.json")
    return made


def fns():
    return SkillRegistry("skills").discover().functions()


@pytest.mark.parametrize("text,expected", [
    ("purple", {"rgb": cl.COLORS["purple"], "name": "purple"}), ("warm white", {"white": 0, "name": "warm white"}),
    ("#ff00aa", {"rgb": (255, 0, 170), "name": "#ff00aa"}), ("make it cool white", {"white": 100, "name": "cool white"}),
])
def test_parse_color(text, expected):
    assert parse_color(text) == expected


@pytest.mark.parametrize("text,pct", [("lights to 30 percent", 30), ("dim the lights", 25), ("full brightness", 100),
                                      ("40%", 40), ("purple", None)])
def test_parse_brightness(text, pct):
    assert parse_brightness(text) == pct


def test_color_and_brightness_reach_every_strip(strips):
    out = fns()["lights_set"](color="purple", brightness=40)
    assert out["say"] == "Purple at 40 percent, sir."
    for s in strips.values():
        assert s.log == [("on",), ("colour", cl.COLORS["purple"]), ("brightness", 40)]


def test_one_strip_by_name_and_white(strips):
    fns()["lights_set"](color="warm white", brightness=60, which="desk")
    assert strips["Desk strip"].log == [("on",), ("white", 60, 0)] and "Bed strip" not in strips


def test_unreachable_strip_is_reported_honestly(monkeypatch, tmp_path):
    devs = [{"id": "a", "key": "k", "name": "Desk", "fail": True}]
    monkeypatch.setattr(cl, "_lights", Lights(devs, lambda d: FakeStrip(d, fail=True)))
    assert "close it and try again" in fns()["lights_power"](on=False)["say"]


def test_not_set_up_says_how(monkeypatch):
    monkeypatch.setattr(cl, "_lights", Lights([]))
    monkeypatch.setattr(cl, "load_devices", lambda folder=None: [])
    assert "LIGHTS_SETUP" in fns()["lights_power"](on=True)["say"]


def test_im_home_i_feel_red_sets_lights_and_music(strips, monkeypatch):
    import core.tools_native as tn
    played = []
    monkeypatch.setattr(tn, "tool_spotify_play", lambda what="", **k: played.append(what) or {"result": "{}"})
    out = fns()["set_mood"](name="red")
    assert played == ["The Weeknd"]
    assert strips["Desk strip"].log[:2] == [("on",), ("colour", (255, 0, 0))]
    assert out["say"] == "Red mood: the lights are set and the music is on, sir."


def test_create_and_use_your_own_mood(strips, monkeypatch):
    import core.tools_native as tn
    monkeypatch.setattr(tn, "tool_spotify_play", lambda what="", **k: {"result": "{}"})
    assert "Saved the study mood" in fns()["create_mood"](name="study", color="cool white", brightness=80,
                                                          playlist="Deep Focus")["say"]
    assert find_mood("study")[1] == {"brightness": 80, "playlist": "Deep Focus", "white": 100}
    assert "study" in fns()["list_moods"]()["say"]


def test_any_colour_is_a_mood_on_the_fly():
    assert find_mood("crimson")[1]["color"] == "crimson"
    assert find_mood("tired") is None


@pytest.mark.parametrize("text,expected", [
    ("I'm home Eva, I feel red", ("set_mood", {"name": "red"})),
    ("I'm home", ("set_mood", {"name": "home"})),
    ("I feel blue", ("set_mood", {"name": "blue"})),
    ("set the mood to relax", ("set_mood", {"name": "relax"})),
    ("turn off the lights", ("lights_power", {"on": False})),
    ("turn on the bed lights", ("lights_power", {"on": True, "which": "bed"})),
    ("lights off", ("lights_power", {"on": False})),
    ("make the lights purple", ("lights_set", {"color": "purple"})),
    ("set the lights to 30 percent", ("lights_set", {"brightness": 30})),
    ("dim the lights", ("lights_set", {"brightness": 25})),
])
def test_fast_paths(text, expected):
    assert fast_path(text, ToolBelt(SkillRegistry("skills").discover())) == expected


@pytest.mark.parametrize("text", ["I feel tired", "what's my mood", "I feel like it's going to rain"])
def test_feelings_stay_conversation(text):
    got = fast_path(text, ToolBelt(SkillRegistry("skills").discover()))
    assert not got or got[0] != "set_mood"


def test_wizard_output_is_read_with_ips_and_only_lights(tmp_path):
    (tmp_path / "devices.json").write_text(json.dumps([
        {"id": "a", "key": "k1", "name": "Desk strip", "category": "dd", "version": "3.3"},
        {"id": "p", "key": "k2", "name": "Kettle plug", "category": "cz"},
        {"id": "x", "key": "", "name": "No key"}]), encoding="utf-8")
    (tmp_path / "snapshot.json").write_text(json.dumps({"devices": [{"id": "a", "ip": "192.168.1.50"}]}),
                                            encoding="utf-8")
    assert load_devices(tmp_path) == [{"id": "a", "key": "k1", "name": "Desk strip", "version": "3.3",
                                       "ip": "192.168.1.50"}]
