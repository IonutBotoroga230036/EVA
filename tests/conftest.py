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
def classic_routing_unless_marked(request, monkeypatch, tmp_path):
    """v1.0: the one brain is the shipped default. Tests written for the classic routing keep it; tests marked
    one_brain get the new loop. Either way her journal, made skills and risk verdicts live in a temp folder."""
    import core.one as one
    from core.one import journal, made, risk
    monkeypatch.setattr(journal, "DIR", tmp_path / "journal")
    monkeypatch.setattr(made, "DIR", tmp_path / "skills_made")
    monkeypatch.setattr(risk, "STORE", tmp_path / "risk_judgments.json")
    if not request.node.get_closest_marker("one_brain"):
        monkeypatch.setattr(one, "mode", lambda: "classic")
    yield


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
    import core.understand.brains as understanding
    import core.understand.jev as jev
    monkeypatch.setattr(jev, "_secret", lambda name: None)          # never your real Jev or OpenRouter key
    monkeypatch.setattr(understanding, "_jev", None)
    monkeypatch.setattr(understanding, "_claude", None)
    # the shipped default is auto (Nov 22); tests that check understanding pick their mode explicitly
    real_cfg = understanding.cfg
    monkeypatch.setattr(understanding, "cfg", lambda: {**real_cfg(), "mode": "legacy"})
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


@pytest.fixture(autouse=True)
def local_test_client(tmp_path, monkeypatch):
    """Starlette's TestClient connects as host "testclient": treat it as this PC, and never use a real token."""
    import core.netsec as netsec
    monkeypatch.setattr(netsec, "LOOPBACK", netsec.LOOPBACK | {"testclient"})
    monkeypatch.setattr(netsec, "TOKEN_FILE", tmp_path / "remote_token.txt")
    monkeypatch.setattr(netsec, "SECRETS_ENV", tmp_path / "no-secrets.env")
    monkeypatch.delenv("EVA_REMOTE_TOKEN", raising=False)
    yield


@pytest.fixture(autouse=True)
def isolate_moods(tmp_path, monkeypatch):
    """Never your real data/moods.json."""
    import core.lights as lights
    monkeypatch.setattr(lights, "MOODS_PATH", tmp_path / "moods.json")
    yield


@pytest.fixture(autouse=True)
def immediate_acks(monkeypatch):
    """Existing tests expect the ack at once; tests/test_acks.py sets its own delays."""
    import core.orchestrator_hybrid as orch
    monkeypatch.setattr(orch, "ACK_AFTER_OVERRIDE", 0.0)
    yield


@pytest.fixture(autouse=True)
def isolate_tls(tmp_path, monkeypatch):
    """Never your real data/tls (the CA your phone trusts)."""
    import core.tls as tls
    monkeypatch.setattr(tls, "DIR", tmp_path / "tls")
    yield


@pytest.fixture(autouse=True)
def inline_forge_jobs(tmp_path, monkeypatch):
    """FORGE jobs run inline in tests (no background threads) and never write your data/forge_jobs.json."""
    import core.forge_jobs as fj
    monkeypatch.setattr(fj, "JOBS_PATH", tmp_path / "forge_jobs.json")
    delivered = []
    manager = fj.JobManager(threaded=False, on_done=lambda job, msg: delivered.append((job, msg)))
    manager.delivered = delivered
    monkeypatch.setattr(fj, "_jobs", manager)
    yield


@pytest.fixture(autouse=True)
def isolate_eva_drafts(tmp_path, monkeypatch):
    """Never your real data/eva_drafts.json (the list of drafts E.V.A. may delete)."""
    import core.scribe as scribe
    monkeypatch.setattr(scribe, "DRAFTS_PATH", tmp_path / "eva_drafts.json")
    yield


@pytest.fixture(autouse=True)
def isolate_conversation(tmp_path, monkeypatch):
    """Never your real data/conversation.json switch."""
    import core.conversation as conv
    monkeypatch.setattr(conv, "STATE", tmp_path / "conversation.json")
    yield


@pytest.fixture(autouse=True)
def no_real_romanian_or_messages(tmp_path, monkeypatch):
    """v0.3.2: never your real Ollama or Piper voice for Romanian, and a fresh message inbox per test."""
    import core.messages as messages
    import core.romanian as romanian
    monkeypatch.setattr(romanian, "_local_translate", lambda prompt, text: None)
    monkeypatch.setattr(romanian, "_claude_translate", lambda prompt, text: None)
    monkeypatch.setattr(romanian, "_cfg", lambda: {"voice": "ro_RO-mihai-medium", "dir": str(tmp_path / "voices")})
    monkeypatch.setattr(romanian, "_voice", None)
    monkeypatch.setattr(messages, "_inbox", None)
    import core.phone_prefs as phone_prefs            # never your real data/phone.json (stay dark, unlock)
    monkeypatch.setattr(phone_prefs, "PATH", tmp_path / "phone.json")
    import core.places as places                      # never your real places or location history
    monkeypatch.setattr(places, "PATH", tmp_path / "places.json")
    monkeypatch.setattr(places, "HISTORY", tmp_path / "location_history.jsonl")
    monkeypatch.setattr(places, "_places", None)
    import core.meetings as meetings                  # never your real recordings
    monkeypatch.setattr(meetings, "DIR", tmp_path / "meetings")
    monkeypatch.setattr(meetings, "_recorder", None)
    monkeypatch.setattr(meetings, "_hf_token", lambda: None)        # never your Hugging Face token (speaker labels)
    import core.device as device                      # no windows left over from another test
    device.CONNECTIONS.clear()
    yield
    device.CONNECTIONS.clear()


@pytest.fixture(autouse=True)
def no_real_tailscale(monkeypatch):
    """Never run the real tailscale CLI in tests."""
    import core.tailnet as tn
    monkeypatch.setattr(tn, "_run", lambda args: None)
    tn._cache.update(t=0.0, v=None)
    yield


@pytest.fixture(autouse=True)
def isolate_nov23_afternoon(tmp_path, monkeypatch):
    """Never your real understanding cache, test mode, NS key, focus switch or phone queue; a fresh one per test."""
    import core.testmode as testmode
    import core.understand.cache as ucache
    monkeypatch.setattr(ucache, "PATH", tmp_path / "understand_cache.json")
    monkeypatch.setattr(ucache, "_cache", None)
    monkeypatch.setattr(testmode, "PATH", tmp_path / "test_mode.json")
    try:
        import core.commute as commute
        monkeypatch.setattr(commute, "_key", lambda: None)          # never your real NS key
        monkeypatch.setattr(commute, "FETCH", None)
        monkeypatch.setattr(commute, "NOW", None)
        commute._plans.clear()
    except ImportError:
        pass
    try:
        import core.focus as focus
        monkeypatch.setattr(focus, "PATH", tmp_path / "focus.json")
        focus.reset()
    except ImportError:
        pass
    import core.device as device
    device.PENDING.clear()
    device.LAST_POLL["ts"] = 0.0
    device.RINGING["ts"] = 0.0
    import core.activity as _activity
    monkeypatch.setattr(_activity, "_last", 0.0)                 # a turn in one test never delays the next
    import core.agent.consent as _consent
    import core.agent.pc as _apc
    import core.agent.runner as _arun
    import core.agent.tasks as _atasks
    monkeypatch.setattr(_atasks, "_store", _atasks.Store(tmp_path / "tasks"))   # never your real tasks
    import core.projects as _proj
    monkeypatch.setattr(_proj, "_store", _proj.Store(tmp_path / "projects"))   # never your real projects
    import core.show as _show
    monkeypatch.setattr(_show, "_shown", {"kind": "", "query": "", "items": [], "ts": 0.0})
    monkeypatch.setattr(_show.webbrowser, "open", lambda *a, **k: True)          # tests never open a browser
    monkeypatch.setattr(_consent, "PATH", tmp_path / "permissions.json")       # or your standing yeses
    monkeypatch.setattr(_apc, "AGENT_DIR", tmp_path / "agent")
    monkeypatch.setattr(_apc, "VENV", tmp_path / "agent" / "venv")
    monkeypatch.setattr(_apc, "PROGRAMS", tmp_path / "agent" / "programs.json")
    monkeypatch.setattr(_apc, "LINK", {"conn": None, "loop": None})
    monkeypatch.setattr(_apc, "_cfg", lambda: {"output_dir": str(tmp_path / "out")})   # never your Documents
    monkeypatch.setattr(_apc.LocalPC, "install_program",                              # tests never install
                        lambda self, winget_id, exe="": {"ok": False, "output": "tests never install programs"})
    monkeypatch.setattr(_apc.LocalPC, "pip_install",
                        lambda self, packages: {"ok": False, "output": "tests never install packages"})
    monkeypatch.setattr(_arun, "_manager", None)
    monkeypatch.setattr(_arun, "default_notify", lambda task, kind, text: None)  # no Telegram from tests
    import core.forge_engine as _fe
    monkeypatch.setattr(_fe, "live_check", lambda d, plan: None)  # never a real service from a test
    import os as _os
    import core.pc_tools as _pct
    monkeypatch.setattr(_os, "startfile", lambda *a, **k: None, raising=False)          # tests never open apps
    import types as _types
    monkeypatch.setattr(_pct, "subprocess", _types.SimpleNamespace(Popen=lambda *a, **k: None, DEVNULL=-3))
    try:
        from interfaces.web import server_stream as _ss
        _ss._WAKE_PARTIAL.clear()                                  # a cut-off "Hey Eva, ..." never leaks
    except Exception:
        pass
    yield


@pytest.fixture(autouse=True)
def isolate_later(tmp_path, monkeypatch):
    """Never your real data/later.json; a fresh scheduler per test."""
    import core.later as later
    monkeypatch.setattr(later, "STORE", tmp_path / "later.json")
    monkeypatch.setattr(later, "_later", None)
    yield


@pytest.fixture(autouse=True)
def isolate_v07(tmp_path, monkeypatch):
    """v0.7: never your real data/settings_ui.json, her real browser profile, or your real screen and mouse."""
    import core.settings as st
    monkeypatch.setattr(st, "UI_PATH", tmp_path / "settings_ui.json")
    st.get_settings.cache_clear()
    import core.browser as browser
    monkeypatch.setattr(browser, "PROFILE", tmp_path / "browser_profile")
    monkeypatch.setattr(browser, "SHOT", tmp_path / "browser" / "shot.png")
    monkeypatch.setattr(browser, "installed", lambda: False)        # tests that want her browser give a fake one
    monkeypatch.setattr(browser, "_browser", None)
    import core.computer as computer

    def no_screen():
        raise AssertionError("tests never move your real mouse")
    monkeypatch.setattr(computer, "_gui", no_screen)
    import core.claude as claude_mod                  # v0.8: a failing fake call fails at once (no waiting)
    monkeypatch.setattr(claude_mod, "RETRY_WAITS", ())
    import core.agent.runner as runner_mod
    monkeypatch.setattr(runner_mod, "LINE_WAITS", ())
    import types as _types                            # v0.8: never a screenshot of your real screen
    fake_gui = _types.ModuleType("pyautogui")

    def _no_shot(*a, **k):
        raise RuntimeError("tests never look at your real screen")
    fake_gui.screenshot = _no_shot
    monkeypatch.setitem(__import__("sys").modules, "pyautogui", fake_gui)
    import core.glance as glance                      # v0.9: no weather or train calls; already greeted today
    from datetime import date as _date
    monkeypatch.setattr(glance, "GREETED", tmp_path / "greeted.txt")
    (tmp_path / "greeted.txt").write_text(_date.today().isoformat())
    monkeypatch.setattr(glance, "weather", lambda: None)
    monkeypatch.setattr(glance, "train", lambda now: None)
    glance._cache.clear()
    import core.review as review                      # her notes go to the test folder
    monkeypatch.setattr(review, "DIR", tmp_path / "reviews")
    import httpx as _httpx                            # v0.9b: no test reaches the internet by accident (a map test
                                                      # reached Nominatim once its search ran in parallel)
    def _offline(url, *a, **k):
        raise _httpx.ConnectError(f"tests never reach the internet ({str(url)[:60]})")
    monkeypatch.setattr(_httpx, "get", _offline)
    monkeypatch.setattr(_httpx, "post", _offline)
    import core.chats as chats                       # never your real chats; titles inline
    monkeypatch.setattr(chats, "DIR", tmp_path / "chats")
    monkeypatch.setattr(chats, "THREADED", False)
    yield
    st.get_settings.cache_clear()
