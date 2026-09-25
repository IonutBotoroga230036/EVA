import core.settings as st


def test_local_overrides_merge_key_by_key(tmp_path, monkeypatch):
    (tmp_path / "s.yaml").write_text("voice:\n  tts:\n    engine: kokoro\n    voice_eva: af_heart\n    speed: 1.0\n"
                                     "oracle:\n  lead_minutes: 10\n")
    (tmp_path / "l.yaml").write_text("voice:\n  tts:\n    voice_eva: bf_emma\noracle:\n  lead_minutes: 5\n")
    monkeypatch.setattr(st, "SETTINGS_PATH", tmp_path / "s.yaml")
    monkeypatch.setattr(st, "LOCAL_PATH", tmp_path / "l.yaml")
    st.get_settings.cache_clear()
    try:
        s = st.get_settings()
        assert s["voice"]["tts"] == {"engine": "kokoro", "voice_eva": "bf_emma", "speed": 1.0}
        assert s["oracle"]["lead_minutes"] == 5
    finally:
        st.get_settings.cache_clear()


def test_missing_or_broken_local_file_is_harmless(tmp_path, monkeypatch):
    (tmp_path / "s.yaml").write_text("a: 1\n")
    (tmp_path / "bad.yaml").write_text("a: [unclosed\n")
    monkeypatch.setattr(st, "SETTINGS_PATH", tmp_path / "s.yaml")
    for local in (tmp_path / "missing.yaml", tmp_path / "bad.yaml"):
        monkeypatch.setattr(st, "LOCAL_PATH", local)
        st.get_settings.cache_clear()
        assert st.get_settings() == {"a": 1}
    st.get_settings.cache_clear()
