"""Milestone 8: Tailscale awareness (parsing only; the real CLI never runs in tests)."""

import core.netsec as netsec
from core import tailnet

STATUS = {"BackendState": "Running", "Self": {"DNSName": "desktop-2ojub6h.tail1234.ts.net.",
                                              "TailscaleIPs": ["100.101.102.103", "fd7a::1"]}}
SERVE = {"Web": {"desktop-2ojub6h.tail1234.ts.net:443": {"Handlers": {"/": {"Proxy": "http://127.0.0.1:8001"}}}}}


def test_serving_gives_the_https_url():
    s = tailnet.parse(STATUS, SERVE, 8001)
    assert s["serving"] and s["url"] == "https://desktop-2ojub6h.tail1234.ts.net" and s["ip"] == "100.101.102.103"


def test_running_but_not_serving_explains_the_one_command(monkeypatch):
    monkeypatch.setattr(tailnet, "_run", lambda args: STATUS if args[0] == "status" else None)
    tailnet._cache.update(t=0.0, v=None)
    assert "tailscale serve --bg --https=443 http://127.0.0.1:8001" in tailnet.banner_line(8001)


def test_not_installed_is_quiet():
    assert tailnet.parse(None, None, 8001) == {"installed": False, "running": False, "url": "", "serving": False}
    assert tailnet.banner_line(8001) == ""


def test_through_tailscale_the_token_is_still_required():
    assert not netsec.is_local("127.0.0.1", {"x-forwarded-for": "100.64.0.5"})


def test_browser_behind_tailscale_serve_passes_the_origin_check():
    h = {"origin": "https://desktop-2ojub6h.tail1234.ts.net", "host": "127.0.0.1:8001",
         "x-forwarded-host": "desktop-2ojub6h.tail1234.ts.net"}
    assert netsec.origin_ok(h)
    assert not netsec.origin_ok({**h, "origin": "https://evil.example"})
