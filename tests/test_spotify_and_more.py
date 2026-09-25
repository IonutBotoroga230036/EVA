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
    (d / "test_skill.py").write_text("def test_x():\n    pass\n", encoding="utf-8")
    belt = ToolBelt(SkillRegistry(tmp_path).discover())
    assert not belt.guard_ok("miles_to_km", "what's that in euros")             # the Sep 25 misfire
    assert belt.guard_ok("miles_to_km", "how many km is 5 miles")


def test_placeholder_city_becomes_home():
    from core.weather import fill_from_words
    assert "city" not in fill_from_words({"city": "Any City"}, "what's the weather")
    assert fill_from_words({"city": "Tilburg"}, "weather in tilburg")["city"] == "Tilburg"


# ---------------------------------------------------------------- Sep 25 16:35 log
@pytest.mark.parametrize("text,what", [
    ("play me the playlist would you teach yourself into me", "the playlist would you teach yourself into me"),
    ("play the playlist ruin me girl", "the playlist ruin me girl"),
    ("play ruin me girl playlist", "ruin me girl playlist"),
    ("can you play me some The Weeknd", "The Weeknd"),
])
def test_play_anything_goes_straight_to_spotify(text, what):
    assert fast_path(text, ToolBelt(SkillRegistry("skills").discover())) == ("spotify_play", {"what": what})


def test_playlist_names_are_found_in_your_library():
    api = FakeSpotifyAPI()
    api_lists = [{"name": "Ruin Me Girl", "uri": "spotify:playlist:ruin"},
                 {"name": "Would You Teach Yourself Into Me", "uri": "spotify:playlist:teach"}]
    orig = api.handler

    def handler(req):
        if req.url.path.endswith("/me/playlists"):
            return httpx.Response(200, json={"items": api_lists})
        return orig(req)
    c = sp.Spotify({"access_token": "a", "refresh_token": "r", "expires_at": 9e12},
                   http=httpx.Client(transport=httpx.MockTransport(handler)))
    assert c.resolve("the playlist ruin me girl")["context_uri"] == "spotify:playlist:ruin"
    assert c.resolve("ruin me girl playlist")["context_uri"] == "spotify:playlist:ruin"
    assert c.resolve("the playlist would you teach yourself into me")["context_uri"] == "spotify:playlist:teach"


def test_a_playlist_name_cannot_trigger_forge():
    belt = ToolBelt(SkillRegistry("skills").discover())
    assert not belt.guard_ok("forge_build", "play me the playlist would you teach yourself into me")
    assert belt.guard_ok("forge_build", "make a skill that can play any playlist by its name")
    assert belt.guard_ok("forge_build", "learn to tell me the moon phase")


def test_no_invented_device_and_no_fake_playing(tmp_path, monkeypatch):
    import core.orchestrator_hybrid as orch_mod
    from core.memory.cortex import Cortex
    from core.orchestrator_hybrid import NOT_DONE, HybridOrchestrator
    from tests.test_orchestrator import fake_ollama, run_turn
    belt = ToolBelt(SkillRegistry("skills").discover())
    assert "device" not in belt.fill("spotify_play", {"what": "x", "device": "computer"}, "play x")
    assert belt.fill("spotify_play", {"what": "x", "device": "phone"}, "play x on my phone")["device"] == "phone"
    client, _ = fake_ollama(decisions=[{"tool": "web_search", "query": "girl group songs"}],
                            answer="Playing girl group songs on DESKTOP-2OJUB6H.")
    monkeypatch.setattr(orch_mod.httpx, "AsyncClient", client)
    import core.tools_native as tn
    monkeypatch.setattr(tn, "_search", lambda q: [{"title": "Top girl groups", "snippet": "a list", "url": "u"}])
    monkeypatch.setattr(tn, "SEARCH_OK", True)
    o = HybridOrchestrator(session_id="p", cortex=Cortex(str(tmp_path / "c.db")), registry=SkillRegistry("skills").discover())
    assert run_turn(o, "search girl group songs")[-1]["text"] == NOT_DONE


def test_no_then_a_new_request_handles_both(tmp_path, monkeypatch):
    import core.orchestrator_hybrid as orch_mod
    from core.memory.cortex import Cortex
    from core.orchestrator_hybrid import HybridOrchestrator
    from tests.test_orchestrator import fake_ollama, run_turn
    client, _ = fake_ollama(decisions=[], answer="x")
    monkeypatch.setattr(orch_mod.httpx, "AsyncClient", client)
    o = HybridOrchestrator(session_id="n", cortex=Cortex(str(tmp_path / "c.db")), registry=SkillRegistry("skills").discover())
    run_turn(o, "make a skill that tells me the moon phase")
    assert o.pending and o.pending["tool"] == "forge_build"
    out = run_turn(o, "no make a skill that can play any playlist by its name")
    assert o.pending["args"]["request"] == "can play any playlist by its name"      # cancelled the first, asked about the second
    assert "Shall I go ahead" in out[-1]["text"]


# ---------------------------------------------------------------- your library first, move and resume
def _client_with(lists, api=None):
    api = api or FakeSpotifyAPI()
    orig = api.handler

    def handler(req):
        if req.url.path.endswith("/me/playlists"):
            return httpx.Response(200, json={"items": lists})
        return orig(req)
    return sp.Spotify({"access_token": "a", "refresh_token": "r", "expires_at": 9e12},
                      http=httpx.Client(transport=httpx.MockTransport(handler))), api


def test_misheard_playlist_name_still_finds_yours():
    c, _ = _client_with([{"name": "Ruin Me Girl", "uri": "spotify:playlist:ruin"}])
    assert c.resolve("rainy girl playlist")["context_uri"] == "spotify:playlist:ruin"       # "rainy girl"
    assert c.resolve("ruin me girl")["context_uri"] == "spotify:playlist:ruin"             # no "playlist" word


def test_all_your_playlists_are_searched_not_just_50():
    pages = {0: [{"name": f"List {i}", "uri": f"u{i}"} for i in range(50)],
             50: [{"name": "Late Night Jazz", "uri": "spotify:playlist:late"}]}
    api = FakeSpotifyAPI()
    orig = api.handler

    def handler(req):
        if req.url.path.endswith("/me/playlists"):
            off = int(req.url.params.get("offset", 0))
            return httpx.Response(200, json={"items": pages.get(off, []), "next": "more" if off == 0 else None})
        return orig(req)
    c = sp.Spotify({"access_token": "a", "refresh_token": "r", "expires_at": 9e12},
                   http=httpx.Client(transport=httpx.MockTransport(handler)))
    assert c.resolve("late night jazz playlist")["context_uri"] == "spotify:playlist:late"


def test_play_it_on_my_computer_moves_the_music(monkeypatch):
    import core.tools_native as tn
    c, api = _client_with([])
    monkeypatch.setattr(sp, "connected", lambda: True)
    monkeypatch.setattr(sp, "get_spotify", lambda: c)
    assert tn.tool_spotify_play(what="it", device="computer")["say"] == "Moved the music to LEGION, sir."
    method, path, _, body = api.calls[-1]
    assert (method, path, body) == ("PUT", "/me/player", {"device_ids": ["pc"], "play": True})
    assert tn.tool_spotify_play(what="")["say"] == "Resuming, sir."
    assert not any(p == "/search" for _, p, _, _ in api.calls)                         # never searched for "it"


@pytest.mark.parametrize("text,expected", [
    ("play", ("spotify_play", {"what": ""})),
    ("play it on my computer", ("spotify_play", {"what": "", "device": "computer"})),
    ("on my phone", ("spotify_play", {"what": "", "device": "phone"})),
])
def test_move_and_resume_fast_paths(text, expected):
    assert fast_path(text, ToolBelt(SkillRegistry("skills").discover())) == expected


def test_what_do_you_know_about_me_is_exact(monkeypatch, tmp_path):
    import core.prompt_builder as pb
    from core.memory.cortex import Cortex
    (tmp_path / "EVA.md").write_text("## About me\n- My name is Ionuț. Address me as \"sir\".\n- Home city: Breda.\n", encoding="utf-8")
    (tmp_path / "EVA.local.md").write_text("## About me (private)\n- Studying in the Radboud pre-master in AI (Nijmegen).\n", encoding="utf-8")
    monkeypatch.setattr(pb, "EVA_MD", tmp_path / "EVA.md")
    monkeypatch.setattr(pb, "EVA_LOCAL_MD", tmp_path / "EVA.local.md")
    c = Cortex(str(tmp_path / "c.db"))
    c.remember("Is user 7443422148")
    monkeypatch.setattr("core.memory.cortex._cortex", c)
    out = SkillRegistry("skills").discover().functions()["recall_memory"](query="")
    assert out["say"].startswith("Here's what I know, sir. You're Ionuț. Studying in the Radboud pre-master")
    assert "7443422148" not in out["say"] and out["exact"]
