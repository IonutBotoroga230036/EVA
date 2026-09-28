"""Milestone 9: the Flutter app can't be compiled in this test suite, so these checks keep it honest against the
server: same protocol, same channel names, https only, the manifest Android needs, and the same fingerprint."""

import base64
import hashlib
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "apps" / "eva_android"
DART = {p.name: p.read_text(encoding="utf-8") for p in (APP / "lib").glob("*.dart")}
KOTLIN = (APP / "android_overlay/app/src/main/kotlin/nl/ionut/eva_android/MainActivity.kt").read_text(encoding="utf-8")
MANIFEST = (APP / "android_overlay/app/src/main/AndroidManifest.xml").read_text(encoding="utf-8")
SERVER = (ROOT / "interfaces/web/server_stream.py").read_text(encoding="utf-8")
PROTOCOL = (ROOT / "docs/VOICE_PROTOCOL.md").read_text(encoding="utf-8")


def test_every_message_the_app_sends_is_one_the_server_handles():
    sent = set(re.findall(r"'type':\s*'(\w+)'", DART["eva_client.dart"]))
    assert sent == {"message", "stop", "listen", "speaking"}
    for kind in sent - {"message", "stop"}:
        assert f'"{kind}"' in SERVER
    assert 'kind == "stop"' in SERVER and 'kind == "message"' in SERVER


def test_every_event_the_app_handles_exists_on_the_server_side():
    handled = set(re.findall(r"case '(\w+)':", DART["main.dart"])) - {"closed", "error"}
    for ev in handled:
        assert f"`{ev}`" in PROTOCOL or f'"type": "{ev}"' in SERVER or f"{ev} " in SERVER, ev


def test_channel_names_match_between_dart_and_kotlin():
    assert "MethodChannel('eva/assistant')" in DART["main.dart"] and '"eva/assistant"' in KOTLIN
    for method in ("launchedByAssist", "requestAssistantRole"):
        assert f"'{method}'" in DART["main.dart"] and f'"{method}"' in KOTLIN
    assert 'invokeMethod("assist"' in KOTLIN and "call.method == 'assist'" in DART["main.dart"]


def test_manifest_makes_eva_an_assistant_with_a_microphone():
    for needle in ("android.permission.RECORD_AUDIO", "android.permission.INTERNET",
                   "android.intent.action.ASSIST", "android.intent.action.VOICE_COMMAND", ".MainActivity"):
        assert needle in MANIFEST, needle


def test_the_token_never_travels_over_plain_http():
    c = DART["eva_client.dart"]
    assert "if (u.scheme != 'https' && u.scheme != 'wss')" in c and "scheme: 'wss'" in c
    assert "'Authorization': 'Bearer $token'" in c
    assert "http://" not in c.replace("'http://$host:8001/eva-ca.crt'", "").replace("https?://", "")


def test_audio_format_matches_the_pipeline():
    m = DART["mic.dart"]
    assert "AudioEncoder.pcm16bits" in m and "sampleRate: 16000" in m and "numChannels: 1" in m
    assert "echoCancel: true" in m


def test_brackets_balance_in_every_source_file():
    for name, src in {**DART, "MainActivity.kt": KOTLIN}.items():
        code = re.sub(r"'(?:[^'\\\n]|\\.)*'|\"(?:[^\"\\\n]|\\.)*\"|//[^\n]*", "", src)
        for a, b in ("()", "[]", "{}"):
            assert code.count(a) == code.count(b), (name, a, code.count(a), code.count(b))


def test_app_fingerprint_algorithm_equals_the_servers(tmp_path):
    """main/eva_client.dart: strip PEM armour, base64-decode, SHA-256, first 8 bytes. Same as core/tls.py."""
    from core import tls
    tls.ensure(["192.168.1.5"], tmp_path)
    pem = (tmp_path / tls.CA_CRT).read_text(encoding="utf-8")
    body = re.sub(r"\s", "", re.sub(r"-----[^-]+-----", "", pem))
    h = hashlib.sha256(base64.b64decode(body)).hexdigest().upper()
    as_the_app_does = ":".join(h[i:i + 2] for i in range(0, 16, 2)) + "..."
    assert as_the_app_does == tls.ca_fingerprint(tmp_path)
