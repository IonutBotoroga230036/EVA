"""Spotify Web API, currency and crypto, calendar shift, FORGE guards, placeholder cities."""

import json
from datetime import datetime, timedelta

import httpx
import pytest

import core.spotify as sp
from core.orchestrator_hybrid import ToolBelt, fast_path
from skills.registry import SkillRegistry


class FakeSpotifyAPI:
    def __init__(self, premium=True, devices=None):
        self.premium, self.calls = premium, []
        self.devices = devices if devices is not None else [
            {"id": "pc", "name": "LEGION", "type": "Computer", "is_active": True},
            {"id": "ph", "name": "Pixel", "type": "Smartphone", "is_active": False}]

    def handler(self, req: httpx.Request):
        path, q = req.url.path.replace("/v1", ""), dict(req.url.params)
        self.calls.append((req.method, path, q, json.loads(req.content) if req.content else None))
        if path == "/me/player/devices":
            return httpx.Response(200, json={"devices": self.devices})
        if path == "/search":
            return httpx.Response(200, json={
                "artists": {"items": [{"name": "The Weeknd", "uri": "spotify:artist:weeknd"}]},
                "tracks": {"items": [{"name": "Blinding Lights", "uri": "spotify:track:bl", "artists": [{"name": "The Weeknd"}]}]},
                "playlists": {"items": [{"name": "Jazz Classics", "uri": "spotify:playlist:jazz"}]}})
        if path == "/me/playlists":
            return httpx.Response(200, json={"items": [{"name": "Chill Evenings", "uri": "spotify:playlist:mine"}]})
        if path.startswith("/me/player/") and not self.premium:
            return httpx.Response(403, json={"error": {"reason": "PREMIUM_REQUIRED"}})
        if path == "/me/player" and req.method == "GET":
            return httpx.Response(200, json={"is_playing": True, "item": {"name": "Blinding Lights", "artists": [{"name": "The Weeknd"}]},
                                             "device": {"name": "Pixel"}})
        return httpx.Response(204)


def client(api):
    return sp.Spotify({"access_token": "a", "refresh_token": "r", "expires_at": 9e12},
                      http=httpx.Client(transport=httpx.MockTransport(api.handler)))


def test_play_the_weekend_on_my_phone():
    api = FakeSpotifyAPI()
    out = client(api).play("the weekend", device="phone")
    assert out == {"label": "The Weeknd", "device": "Pixel"}                    # speech "the weekend" still finds him
    method, path, q, body = api.calls[-1]
    assert (method, path, q["device_id"], body) == ("PUT", "/me/player/play", "ph", {"context_uri": "spotify:artist:weeknd"})


def test_my_playlist_and_genre_resolution():
    api = FakeSpotifyAPI()
    assert client(api).resolve("my chill evenings playlist")["label"] == "your Chill Evenings playlist"
    assert client(api).resolve("jazz playlist")["context_uri"] == "spotify:playlist:jazz"


def test_premium_and_no_device_are_honest():
    with pytest.raises(sp.PremiumRequired):
        client(FakeSpotifyAPI(premium=False)).play("jazz")
    with pytest.raises(LookupError, match="no Spotify device"):
        client(FakeSpotifyAPI(devices=[])).play("jazz")


def test_now_playing_skill(monkeypatch):
    monkeypatch.setattr(sp, "get_spotify", lambda: client(FakeSpotifyAPI()))
    out = SkillRegistry("skills").discover().functions()["spotify_now_playing"]()
    assert out["say"] == "Playing Blinding Lights by The Weeknd on Pixel, sir."


def test_builtin_play_uses_the_api_when_signed_in(monkeypatch):
    import core.tools_native as tn
    monkeypatch.setattr(sp, "connected", lambda: True)
    monkeypatch.setattr(sp, "get_spotify", lambda: client(FakeSpotifyAPI()))
    assert tn.tool_spotify_play(what="The Weeknd", device="phone")["say"] == "Playing The Weeknd on Pixel, sir."


@pytest.mark.parametrize("text,expected", [
    ("play The Weeknd on my phone", ("spotify_play", {"what": "The Weeknd", "device": "phone"})),
    ("what's playing", ("spotify_now_playing", {})),
    ("add Blinding Lights to the queue", ("spotify_queue", {"what": "Blinding Lights"})),
    ("what's the price of bitcoin", ("crypto_price", {"coin": "bitcoin", "currency": "EUR"})),
    ("84687 dollars in euros", ("convert_currency", {"amount": 84687.0, "from_currency": "dollars", "to_currency": "euros"})),
    ("can you move everything 1 hour later", ("calendar_shift", {"minutes": 60, "day": "today"})),
    ("push my schedule back by 30 minutes", ("calendar_shift", {"minutes": 30, "day": "today"})),
])
def test_fast_paths(text, expected):
    assert fast_path(text, ToolBelt(SkillRegistry("skills").discover())) == expected


def test_currency_and_crypto_are_exact(monkeypatch):
    import core.tools_native as tn

    class R:
        def __init__(self, data): self.data = data
        def json(self): return self.data
    monkeypatch.setattr(httpx, "get", lambda url, **k: R({"rates": {"EUR": 72456.12}}) if "frankfurter" in url
                        else R({"bitcoin": {"eur": 72123.5}}))
    assert tn.tool_convert_currency(amount=84687, from_currency="dollars", to_currency="euros")["say"] == \
        "84,687 USD is about 72,456 EUR at today's rate, sir."
    assert tn.tool_crypto_price(coin="btc")["say"] == "Bitcoin is at 72,124 euros right now, sir."


def test_move_everything_later(tmp_path):
    from core.tempo import shift_events
    from tests.fakes_google import FakeGoogle, ev
    now = datetime.now().replace(hour=16, minute=5, second=0, microsecond=0)
    g = FakeGoogle(events=[ev("1", "Meet W/ Muaad", now.replace(hour=15), now.replace(hour=17, minute=30)),
                           ev("2", "Meet W/ Everyone", now.replace(hour=18, minute=30), now.replace(hour=19)),
                           ev("3", "Go To Hostel Roots", now.replace(hour=18, minute=45), now.replace(hour=19, minute=30))])
    g.moved = []
    g.move_event = lambda i, s, e: g.moved.append((i, s.strftime("%H:%M")))
    out = shift_events(g, 60, "today", now=now)
    assert g.moved == [("2", "19:30"), ("3", "19:45")]                        # the current meeting stays put
    assert out["say"] == "Moved 2 events 1 hour later, sir. Meet W/ Everyone is now at 19:30."
    belt = ToolBelt(SkillRegistry("skills").discover())
    assert belt.needs_confirm("calendar_shift")


def test_forge_built_skills_get_guards(tmp_path):
    from tests.helpers import make_skill
    d = make_skill(tmp_path, "km_to_miles", "Convert kilometres and miles", triggers=["km to miles"],
                   tools_py='import json\nTOOLS=[{"type":"function","function":{"name":"miles_to_km","description":"x",'
                            '"parameters":{"type":"object","properties":{}}}}]\n'
                            'FUNCTIONS={"miles_to_km": lambda **_: {"result": json.dumps({})}}\n')
    (d / "test_skill.py").write_text("def test_x():\n    pass\n")
    belt = ToolBelt(SkillRegistry(tmp_path).discover())
    assert not belt.guard_ok("miles_to_km", "what's that in euros")             # the Sep 25 misfire
    assert belt.guard_ok("miles_to_km", "how many km is 5 miles")


def test_placeholder_city_becomes_home():
    from core.weather import fill_from_words
    assert "city" not in fill_from_words({"city": "Any City"}, "what's the weather")
    assert fill_from_words({"city": "Tilburg"}, "weather in tilburg")["city"] == "Tilburg"
