import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture(autouse=True)
def isolate_personal_settings(tmp_path, monkeypatch):
    """Tests use the shipped defaults only, never your config/settings.local.yaml."""
    import core.settings as st
    monkeypatch.setattr(st, "LOCAL_PATH", tmp_path / "no-personal-settings.yaml")
    st.get_settings.cache_clear()
    yield
    st.get_settings.cache_clear()


@pytest.fixture(autouse=True)
def isolate_real_data(tmp_path, monkeypatch):
    """Tests must never touch data/cortex.db or the real audit log."""
    from core.memory import cortex as cortex_mod
    from core.security import audit as audit_mod
    monkeypatch.setattr(audit_mod.audit, "log_dir", tmp_path / "logs")
    (tmp_path / "logs").mkdir()
    test_cortex = cortex_mod.Cortex(str(tmp_path / "test_cortex.db"))
    monkeypatch.setattr(cortex_mod, "_cortex", test_cortex)
    yield
    monkeypatch.setattr(cortex_mod, "_cortex", None)
