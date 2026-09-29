"""Milestone 9 (option A): the Android app is a native shell around the real interface. It can't be compiled in
this suite, so these checks hold it to its promises against the server and the bridge."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "apps" / "eva_android"
MAIN = (APP / "lib/main.dart").read_text(encoding="utf-8")
KOTLIN = (APP / "android_overlay/app/src/main/kotlin/nl/ionut/eva_android/MainActivity.kt").read_text(encoding="utf-8")
MANIFEST = (APP / "android_overlay/app/src/main/AndroidManifest.xml").read_text(encoding="utf-8")
NETSEC = (APP / "android_overlay/app/src/main/res/xml/network_security_config.xml").read_text(encoding="utf-8")
PUBSPEC = (APP / "pubspec.yaml").read_text(encoding="utf-8")
BRIDGE = (ROOT / "interfaces/web/eva.html").read_text(encoding="utf-8")


def test_the_native_audio_path_that_went_silent_is_gone():
    assert not (APP / "lib/mic.dart").exists() and not (APP / "lib/voice_out.dart").exists()
    assert "record:" not in PUBSPEC and "audioplayers:" not in PUBSPEC
    assert "webview_flutter:" in PUBSPEC


def test_https_only_and_no_cleartext():
    assert "u.scheme != 'https'" in MAIN
    assert 'cleartextTrafficPermitted="false"' in NETSEC and "usesCleartextTraffic" not in MANIFEST
    assert 'android:networkSecurityConfig="@xml/network_security_config"' in MANIFEST
    assert '<certificates src="user"/>' in NETSEC and '<certificates src="system"/>' in NETSEC


def test_the_page_gets_the_microphone_and_nothing_else():
    assert "WebViewPermissionResourceType.microphone" in MAIN and "request.deny()" in MAIN
    assert "request.types.every((t) => t == WebViewPermissionResourceType.microphone)" in MAIN
    assert "'requestMic'" in MAIN and '"requestMic"' in KOTLIN and "RECORD_AUDIO" in KOTLIN


def test_her_voice_plays_without_a_tap():
    assert "setMediaPlaybackRequiresUserGesture(false)" in MAIN


def test_the_token_never_travels_in_a_url():
    assert "?token=" not in MAIN
    assert "name: 'eva_token'" in MAIN                           # the cookie netsec checks
    from core import netsec
    assert netsec.COOKIE == "eva_token"
    assert "'Authorization': 'Bearer ${widget.token}'" in MAIN


def test_the_page_knows_it_is_in_the_app_and_releases_the_mic_when_hidden():
    assert "EVA-Android/0.3" in MAIN and "/EVA-Android/.test(navigator.userAgent)" in BRIDGE
    assert "if (document.hidden) { micRelease(); }" in BRIDGE


def test_the_assistant_gesture_taps_the_real_mic_button():
    assert "new CustomEvent('eva:mic')" in MAIN and "addEventListener('eva:mic'" in BRIDGE
    assert 'invokeMethod("assist"' in KOTLIN and "call.method == 'assist'" in MAIN
    for m in ("launchedByAssist", "requestAssistantRole"):
        assert f"'{m}'" in MAIN and f'"{m}"' in KOTLIN
    assert "android.intent.action.ASSIST" in MANIFEST      # eligible, never forced: Gemini stays unless you switch


def test_brackets_balance():
    for name, src in {"main.dart": MAIN, "MainActivity.kt": KOTLIN}.items():
        code = re.sub(r"'(?:[^'\\\n]|\\.)*'|\"(?:[^\"\\\n]|\\.)*\"|//[^\n]*", "", src)
        for a, b in ("()", "[]", "{}"):
            assert code.count(a) == code.count(b), (name, a)
