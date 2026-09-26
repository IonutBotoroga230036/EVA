"""Local network safety (v0.2.5 milestone 2): local-only by default, token for remote devices, no cross-site."""

import json

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import core.netsec as netsec
import interfaces.web.server_stream as srv

LAN = ("192.168.1.42", 50123)


@pytest.fixture
def token(monkeypatch):
    monkeypatch.setenv("EVA_REMOTE_TOKEN", "s3cret-token")
    return "s3cret-token"


def test_binds_to_this_pc_only_by_default():
    assert netsec.bind_host() == "127.0.0.1" and not netsec.network_mode()
    assert "this PC only" in netsec.startup_banner()


def test_network_mode_binds_all_interfaces_and_prints_a_pairing_link(tmp_path, monkeypatch, token):
    import core.settings as st
    local = tmp_path / "settings.local.yaml"
    local.write_text("server:\n  listen: network\n", encoding="utf-8")
    monkeypatch.setattr(st, "LOCAL_PATH", local)
    st.get_settings.cache_clear()
    assert netsec.bind_host() == "0.0.0.0"
    assert "?token=s3cret-token" in netsec.startup_banner()


def test_token_is_generated_once_and_kept(tmp_path):
    first = netsec.load_token()
    assert len(first) >= 24 and netsec.load_token() == first
    assert netsec.TOKEN_FILE.read_text(encoding="utf-8").strip() == first


def test_token_from_secrets_env(tmp_path, monkeypatch):
    env = tmp_path / "secrets.env"
    env.write_text("OTHER=x\nEVA_REMOTE_TOKEN=\"from-file\"\n", encoding="utf-8")
    monkeypatch.setattr(netsec, "SECRETS_ENV", env)
    assert netsec.load_token() == "from-file" and netsec.token_ok("from-file") and not netsec.token_ok("nope")


@pytest.mark.parametrize("origin,host,ok", [
    (None, "localhost:8001", True),                       # native app / curl
    ("http://localhost:8001", "localhost:8001", True),
    ("http://127.0.0.1:8001", "localhost:8001", True),    # same PC, other spelling
    ("http://evil.example", "localhost:8001", False),
    ("http://localhost:5173", "localhost:8001", False),   # another local app
    ("null", "localhost:8001", False),
    ("http://192.168.1.10:8001", "192.168.1.10:8001", True),
])
def test_origin_check(origin, host, ok):
    headers = {"host": host, **({"origin": origin} if origin else {})}
    assert netsec.origin_ok(headers) is ok


def test_a_local_proxy_counts_as_remote():
    assert netsec.is_local("127.0.0.1", {})
    assert not netsec.is_local("127.0.0.1", {"x-forwarded-for": "100.64.0.7"})
    assert not netsec.is_local("192.168.1.42", {})


def test_remote_http_needs_the_token(token):
    with TestClient(srv.app, client=LAN) as tc:
        assert tc.get("/api/status").status_code == 401
        assert tc.get("/api/status", headers={"Authorization": "Bearer wrong"}).status_code == 401
        assert tc.get("/api/status", headers={"Authorization": f"Bearer {token}"}).status_code == 200


def test_pairing_link_sets_a_cookie_and_strips_the_token(token):
    with TestClient(srv.app, client=LAN, follow_redirects=False) as tc:
        r = tc.get(f"/?token={token}")
        assert r.status_code == 303 and r.headers["location"] == "/"
        assert "httponly" in r.headers["set-cookie"].lower() and "samesite=strict" in r.headers["set-cookie"].lower()
        assert tc.get("/api/status").status_code == 200    # the cookie now works on its own


def test_this_pc_needs_no_token():
    with TestClient(srv.app) as tc:
        assert tc.get("/api/status").status_code == 200


def test_cross_site_writes_are_refused_even_from_this_pc():
    with TestClient(srv.app) as tc:
        r = tc.post("/api/routines/reminders", json={"text": "x", "due": "2030-01-01T10:00"},
                    headers={"Origin": "http://evil.example"})
        assert r.status_code == 403


def test_websocket_from_another_website_is_refused():
    with TestClient(srv.app) as tc:
        with pytest.raises(WebSocketDisconnect):
            with tc.websocket_connect("/ws", headers={"Origin": "http://evil.example"}) as ws:
                ws.receive_text()


def test_remote_websocket_needs_the_token(monkeypatch, token):
    monkeypatch.setattr(srv, "get_tts", lambda: None)
    monkeypatch.setattr(srv, "pipeline_status", lambda: {"engine": "browser"})
    with TestClient(srv.app, client=LAN) as tc:
        with pytest.raises(WebSocketDisconnect):
            with tc.websocket_connect("/ws") as ws:
                ws.receive_text()
        with tc.websocket_connect("/ws", headers={"Authorization": f"Bearer {token}"}) as ws:
            assert json.loads(ws.receive_text())["type"] == "hello"
        with tc.websocket_connect(f"/ws?token={token}") as ws:
            assert json.loads(ws.receive_text())["type"] == "hello"
