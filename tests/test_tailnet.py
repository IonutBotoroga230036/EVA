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


def test_through_tailscale_serve_nothing_is_redirected_or_refused(tmp_path, monkeypatch):
    """Network mode on + Tailscale Serve: TLS already happened in Tailscale; the token still decides."""
    import json
    import core.settings as st
    import interfaces.web.server_stream as srv
    from fastapi.testclient import TestClient
    local = tmp_path / "settings.local.yaml"
    local.write_text("server:\n  listen: network\n", encoding="utf-8")
    monkeypatch.setattr(st, "LOCAL_PATH", local)
    st.get_settings.cache_clear()
    monkeypatch.setenv("EVA_REMOTE_TOKEN", "tok")
    monkeypatch.setattr(srv, "get_tts", lambda: None)
    proxy = {"X-Forwarded-For": "100.70.182.39", "Tailscale-User-Login": "ionut@example.com"}
    with TestClient(srv.app, client=("127.0.0.1", 5000), follow_redirects=False) as tc:
        assert tc.get("/api/status", headers=proxy).status_code == 401              # still needs the token
        r = tc.get("/?token=tok", headers=proxy)
        assert r.status_code == 303 and r.headers["location"] == "/"               # paired, not bounced to :8443
        with tc.websocket_connect("/ws", headers={**proxy, "Authorization": "Bearer tok"}) as ws:
            assert json.loads(ws.receive_text())["type"] == "hello"
