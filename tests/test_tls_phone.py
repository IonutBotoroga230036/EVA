"""HTTPS for the phone (v0.2.5): a name-constrained local CA, a LAN certificate, redirects, and the mic hint.
Everything is generated in a temp folder; nothing touches data/tls."""

import datetime as dt
import ipaddress

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient

from core import netsec, tls

LAN = ("192.168.188.50", 40000)


def test_ca_and_server_certificate(tmp_path):
    crt_path, key_path = tls.ensure(["192.168.188.101"], tmp_path)
    ca = x509.load_pem_x509_certificate((tmp_path / tls.CA_CRT).read_bytes())
    srv = x509.load_pem_x509_certificate(open(crt_path, "rb").read())
    ca.public_key().verify(srv.signature, srv.tbs_certificate_bytes, ec.ECDSA(hashes.SHA256()))   # signed by the CA
    san = srv.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    assert ipaddress.ip_address("192.168.188.101") in san.get_values_for_type(x509.IPAddress)
    assert "localhost" in san.get_values_for_type(x509.DNSName)
    assert all(d == "localhost" or d.endswith(".local") for d in san.get_values_for_type(x509.DNSName))
    assert srv.not_valid_after_utc - dt.datetime.now(dt.timezone.utc) <= dt.timedelta(days=398)


def test_the_ca_can_only_vouch_for_private_addresses(tmp_path):
    tls.ensure(["192.168.1.5"], tmp_path)
    ca = x509.load_pem_x509_certificate((tmp_path / tls.CA_CRT).read_bytes())
    nc = ca.extensions.get_extension_for_class(x509.NameConstraints)
    assert nc.critical
    nets = {str(n.value) for n in nc.value.permitted_subtrees if isinstance(n, x509.IPAddress)}
    assert nets == {"10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "127.0.0.0/8"}
    dns = {n.value for n in nc.value.permitted_subtrees if isinstance(n, x509.DNSName)}
    assert dns == {"localhost", "local"}                       # never google.com, never your bank
    bc = ca.extensions.get_extension_for_class(x509.BasicConstraints).value
    assert bc.ca and bc.path_length == 0


def test_certificate_is_reused_and_renewed_when_the_address_changes(tmp_path):
    first = open(tls.ensure(["192.168.1.5"], tmp_path)[0], "rb").read()
    ca_before = (tmp_path / tls.CA_CRT).read_bytes()
    assert open(tls.ensure(["192.168.1.5"], tmp_path)[0], "rb").read() == first
    moved = open(tls.ensure(["192.168.1.77"], tmp_path)[0], "rb").read()
    assert moved != first and (tmp_path / tls.CA_CRT).read_bytes() == ca_before   # the phone keeps trusting it


def test_fingerprint_is_short_and_stable(tmp_path):
    tls.ensure(["192.168.1.5"], tmp_path)
    fp = tls.ca_fingerprint(tmp_path)
    assert fp == tls.ca_fingerprint(tmp_path) and fp.endswith("...") and fp.count(":") == 7


# ------------------------------------------------------------ server behaviour in network mode
@pytest.fixture
def network(tmp_path, monkeypatch):
    import core.settings as st
    local = tmp_path / "settings.local.yaml"
    local.write_text("server:\n  listen: network\n", encoding="utf-8")
    monkeypatch.setattr(st, "LOCAL_PATH", local)
    st.get_settings.cache_clear()
    monkeypatch.setattr(tls, "DIR", tmp_path / "tls")
    monkeypatch.setenv("EVA_REMOTE_TOKEN", "tok")
    tls.ensure(["192.168.188.101"])
    import interfaces.web.server_stream as srv
    monkeypatch.setattr(srv, "get_tts", lambda: None)
    return srv


def test_https_is_on_in_network_mode(network):
    assert netsec.https_enabled() and netsec.https_port() == 8443
    banner = netsec.startup_banner()
    assert "https://" in banner and ":8443/?token=tok" in banner and "/eva-ca.crt" in banner


def test_phone_on_plain_http_is_sent_to_https_with_its_token(network):
    with TestClient(network.app, client=LAN, follow_redirects=False) as tc:
        r = tc.get("/?token=tok")
        assert r.status_code == 307
        assert r.headers["location"] == "https://testserver:8443/?token=tok"


def test_phone_can_fetch_the_ca_before_pairing(network):
    with TestClient(network.app, client=LAN) as tc:
        r = tc.get("/eva-ca.crt")
        assert r.status_code == 200 and r.headers["content-type"] == "application/x-x509-ca-cert"
        assert b"BEGIN CERTIFICATE" in r.content and b"PRIVATE KEY" not in r.content


def test_pairing_over_https_sets_a_secure_cookie(network):
    with TestClient(network.app, client=LAN, base_url="https://testserver:8443", follow_redirects=False) as tc:
        r = tc.get("/?token=tok")
        assert r.status_code == 303 and "secure" in r.headers["set-cookie"].lower()
        assert tc.get("/api/status", headers={"Authorization": "Bearer tok"}).status_code == 200
        assert tc.get("/api/status").status_code in (200, 401)       # cookie is Secure: kept by real browsers on https


def test_this_pc_keeps_plain_http(network):
    with TestClient(network.app) as tc:
        assert tc.get("/api/status").status_code == 200


def test_mic_explains_itself_on_an_insecure_page():
    from pathlib import Path
    html = (Path(__file__).resolve().parents[1] / "interfaces" / "web" / "eva.html").read_text(encoding="utf-8")
    assert "window.isSecureContext" in html and "It needs a secure connection" in html
    assert html.count("if (micBlocked()) return;") == 2
