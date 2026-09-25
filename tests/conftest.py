import faulthandler
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
def live_event_bus():
    """A server test's shutdown stops the global bus; every test starts with it running."""
    from core.events.bus import get_bus
    bus = get_bus()
    bus.start_listening()
    bus._recent.clear()                     # each test sees only its own events
    yield


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


@pytest.fixture(autouse=True)
def no_real_services(tmp_path, monkeypatch):
    """Tests never touch your real accounts or files: no Telegram, no Google, no Claude key,
    and fresh reminders, routines, budget, and brain mode in a temp folder."""
    import core.brain as brain
    import core.budget as budget
    import core.claude as claude
    import core.google_api as google
    import core.oracle as oracle
    import core.telegram_bridge as tg
    monkeypatch.setattr(tg, "load_token", lambda: None)
    tg.set_active(None, None)
    monkeypatch.setattr(google, "TOKEN", tmp_path / "google_token.json")
    monkeypatch.setattr(google, "_client", None)
    monkeypatch.setattr(claude, "_load_key", lambda: None)
    monkeypatch.setattr(claude, "_client", None)
    monkeypatch.setattr(budget, "_budget", budget.BudgetTracker(track_file=str(tmp_path / "budget.json")))
    monkeypatch.setattr(brain, "STATE", tmp_path / "brain_mode.json")
    monkeypatch.setenv("EVA_TESTING", "1")
    monkeypatch.setenv("EVA_VAULT", str(tmp_path / "vault"))          # never your real notes
    monkeypatch.setenv("EVA_SCREENSHOTS", str(tmp_path / "screenshots"))
    import core.triage as triage
    monkeypatch.setattr(triage, "CONTACTS", tmp_path / "contacts.json")
    monkeypatch.setattr(triage, "PREFS", tmp_path / "email_prefs.json")
    monkeypatch.setattr(brain, "_brain", None)
    monkeypatch.setattr(oracle, "_oracle", oracle.Oracle(store=oracle.ReminderStore(tmp_path / "reminders.json"),
                                                         google_getter=lambda: None))
    yield
    tg.set_active(None, None)


@pytest.fixture(autouse=True)
def hang_watchdog():
    """If a test ever hangs, print every thread's stack after 90 s and stop, instead of waiting forever."""
    faulthandler.dump_traceback_later(90, exit=True)
    yield
    faulthandler.cancel_dump_traceback_later()
