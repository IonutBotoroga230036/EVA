"""v1.2: from his 28 Nov night with v1.1. A five-minute story came back as forty seconds of text, "that's the end"
was taken for her own echo, a check's "fix" moved his train, she couldn't see a script's error, a task typed pip
into his own terminal, adopt_skill got the script's code four times, and he wants her code, her console, her
folder on D and his places. Nothing here touches a real account, file, camera or device."""

import asyncio
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

import core.orchestrator_hybrid as orch_mod
from core import console, memoir, places
from core.one import files, journal, made, risk, selfcode
from core.one.backends import Step
from core.one.loop import no_dashes
from tests.test_listen import FakeSTT, make, silence, speech
from tests.test_v10 import call, eva, say, script  # noqa: F401  (eva is a fixture)

pytestmark = pytest.mark.one_brain


# ---------------------------------------------------------------- the story that lost its middle
def test_long_speech_is_cut_where_he_paused():
    from voice.stt import split_long
    sr = 16000
    rng = np.random.default_rng(0)
    talk = lambda s: (rng.standard_normal(int(s * sr)) * 0.3).astype(np.float32)      # noqa: E731
    quiet = lambda s: np.zeros(int(s * sr), dtype=np.float32)                         # noqa: E731
    audio = np.concatenate([talk(17), quiet(0.6), talk(20), quiet(0.6), talk(15), quiet(0.6), talk(12)])
    pieces = split_long(audio)
    assert len(pieces) >= 3 and all(len(p) <= 26 * sr for p in pieces)
    assert sum(len(p) for p in pieces) == len(audio)                       # not one sample lost
    first_cut = len(pieces[0]) / sr
    assert 17.0 <= first_cut <= 17.6                                       # in the pause, not mid-word
    assert split_long(talk(20))[0].size == 20 * sr                         # a command stays whole


class PieceSTT(FakeSTT):
    def __init__(self):
        super().__init__()
        self.lengths = []

    def transcribe(self, audio):
        self.lengths.append(len(audio) / 16000)
        return f"part{len(self.lengths)}"


def test_a_long_story_reaches_her_whole():
    stt = PieceSTT()

    async def go():
        lst, log, _ = make(stt, max_utterance_secs=200, max_pause_secs=5)
        lst.arm(5)
        await lst.feed(speech(2000) + silence(100))                        # 64 s of talking
        await lst.idle()
        return log
    log = run_async(go)
    assert len(stt.lengths) >= 3 and max(stt.lengths) <= 26
    assert log["commands"] == [" ".join(f"part{i + 1}" for i in range(len(stt.lengths)))]


def run_async(fn):
    return asyncio.run(fn())


def test_his_words_after_she_stopped_speaking_are_not_her_echo():
    """01:00:13: she said 'say "that's the end"', he said it three seconds later, and it was dropped as echo."""
    stt = FakeSTT("That's the end.", "That's the end.")

    async def go():
        lst, log, clock = make(stt)
        lst.said("Would you like to keep going, or say that's the end and I'll file it?")
        await lst.set_speaking(True)
        lst.arm(10)
        await lst.feed(speech(30) + silence(100))           # while her voice plays: her own echo
        await lst.idle()
        before = list(log["commands"])
        await lst.set_speaking(False)
        clock.t += 3.0
        lst.arm(10)
        await lst.feed(speech(30) + silence(100))           # three seconds after she stopped: him
        await lst.idle()
        return before, log["commands"]
    before, after = run_async(go)
    assert before == [] and after == ["That's the end."]


def test_an_answer_to_her_question_goes_into_the_chapter(monkeypatch, tmp_path):
    seen = []

    def writer(raw, topic):
        seen.append((raw, topic))
        return {"title": "The Empty Book", "text": raw, "people": ["Ionut", "my mom", "the teacher"], "year": 2008,
                "questions": ["What did you think about?"], "_writer": "test"}
    monkeypatch.setattr(memoir, "_write_json", writer)
    memoir.start("kindergarten")
    memoir.add("The teacher said my book was empty.")
    first = memoir.stop()
    assert first["people"] == ["my mom", "the teacher"]                    # he tells it; he isn't a "person" in it
    out = json.loads(memoir.tool_memoir_add("I felt like an outsider, doing my own thing.")["result"])
    assert out["title"] == "The Empty Book" and out["added"] == 9
    raw, topic = seen[-1]
    assert "book was empty" in raw and "outsider" in raw and "The Empty Book" in topic
    chapter = Path(first["file"]).read_text(encoding="utf-8")
    assert "outsider" in chapter and len(memoir.chapters()) == 1          # the same chapter, rewritten
    assert "added later" in Path(first["session"]).read_text(encoding="utf-8")
    assert risk.judge("memoir_add", {})[0] == "tell"


# ---------------------------------------------------------------- the checks
def test_an_offer_is_not_a_claim_and_busy_people_are_not_his_calendar():
    assert not orch_mod.claims("No problem, sir. Just say when you want the groceries added to the list or the "
                               "reminder set for when you leave for Dordrecht.")
    assert orch_mod.claims("I've added milk to your list, sir.")
    assert orch_mod.unchecked_fact("Feeling like an outsider while everyone else is busy with their own work.",
                                   []) is None
    assert orch_mod.unchecked_fact("Your afternoon looks busy, sir.", []) == "calendar"


def test_a_correction_only_fixes_words(eva):
    """00:31: the claim check sent her back, and her "fix" moved Train To Dordrecht to 12:09."""
    b = script(eva, [Step(text="I've set the reminder for you, sir."),
                     Step(calls=[call("calendar_shift", minutes=9, from_time="9", until_time="10")]),
                     Step(text="I haven't set anything yet, sir. Want me to?")])
    reply, _ = say(eva, "No, I was thinking.")
    assert reply == "I haven't set anything yet, sir. Want me to?"
    assert [e["start"] for e in eva.world.events][:2] == ["09:00", "09:30"]      # nothing moved
    steps = journal.recent(1)[0]["steps"]
    assert steps[0]["tool"] == "calendar_shift" and steps[0]["decision"] == "refused"
    assert "Fix your words only" in b.calls_seen[1]["transcript"][-1]["text"]


def test_a_correction_may_act_when_he_asked_for_it(eva):
    """v1.2.1: he asked, she said 'moved' without calling the tool: the correction does it."""
    script(eva, [Step(text="I've moved Standup and Review fifteen minutes later, sir."),
                 Step(calls=[call("calendar_shift", minutes=15, from_time="9", until_time="9:45")]),
                 Step(text="Done: Standup and Review are fifteen minutes later.")])
    reply, _ = say(eva, "Move everything between 9 and 9:45 fifteen minutes later")
    assert [e["start"] for e in eva.world.events][:2] == ["09:15", "09:45"]
    from core.one.loop import asks_for_action
    assert asks_for_action("Can you move the train to 12:09?") and not asks_for_action("No, I was thinking.")


def test_after_his_yes_she_sees_the_whole_error(eva):
    """00:38: she ran the setup, got a traceback, and said 'I don't have the error text'."""
    trace = "Traceback (most recent call last):\n" + "  File x\n" * 80 + "ModuleNotFoundError: No module named 'cv2'"
    eva.belt.execute = lambda name, args: {"result": json.dumps({"ok": False, "error": "ModuleNotFoundError: No "
                                                                 "module named 'cv2'", "errors": trace}),
                                           "say": "The script stopped with an error, sir."}
    eva.pending = {"tool": "run_my_script", "args": {"path": "eva_face.py"}, "desc": "run eva_face.py",
                   "ts": __import__("time").time(), "one": True}
    b = script(eva, [Step(text="It needs OpenCV, sir; installing it in my own environment fixes that.")])
    say(eva, "Yes.")
    note = b.calls_seen[0]["transcript"][-1]["text"]
    assert "No module named 'cv2'" in note and "fix what you can yourself" in note


def test_no_em_dashes_in_her_words():
    assert no_dashes("Done, sir \u2014 sent the WhatsApp message to Muaad.") == \
        "Done, sir, sent the WhatsApp message to Muaad."


# ---------------------------------------------------------------- her scripts install what they need
def test_a_missing_module_is_installed_into_her_environment_and_the_script_runs_again(tmp_path, monkeypatch):
    d = tmp_path / "EVA" / "face recognition camera"
    d.mkdir(parents=True)
    p = d / "eva_face.py"
    p.write_text("import json\nimport cv2\nprint(json.dumps({'ok': True}))\n")
    monkeypatch.setattr(files, "python", lambda: sys.executable)
    installed = []

    def fake_install(pk):
        installed.extend(pk)
        (d / "cv2.py").write_text("")                                     # now the import works
        return {"ok": True, "packages": pk}
    monkeypatch.setattr(files, "install", fake_install)
    out = files.run_script(str(p))
    assert installed == ["opencv-python"] and out["ok"] and out["json"] == {"ok": True}
    assert out["installed"] == ["opencv-python"]


def test_a_failing_script_says_why_first(tmp_path, monkeypatch):
    d = tmp_path / "EVA" / "tools"
    d.mkdir(parents=True)
    p = d / "boom.py"
    p.write_text("raise ValueError('the camera index is wrong')\n")
    monkeypatch.setattr(files, "python", lambda: sys.executable)
    out = files.run_script(str(p))
    assert not out["ok"] and out["error"] == "ValueError: the camera index is wrong"


def test_install_goes_to_her_environment_only(tmp_path, monkeypatch):
    from core.agent import pc as pc_mod
    ran = []

    class R:
        returncode, stdout, stderr = 0, "", ""
    monkeypatch.setattr(files.subprocess, "run", lambda cmd, **k: ran.append(cmd) or R())
    (pc_mod.VENV / "bin").mkdir(parents=True)
    out = files.install(["opencv-python", "numpy==1.26.4"])
    assert out["ok"] and ran[-1][1:5] == ["-m", "pip", "install", "--disable-pip-version-check"]
    assert ran[-1][0] != sys.executable or not (pc_mod.VENV / "bin" / "python").exists()
    assert not files.install(["opencv-python; rm -rf /"])["ok"]
    assert risk.judge("install_package", {"packages": ["x"]})[0] == "tell"


def test_adopt_skill_takes_the_code_or_the_path(tmp_path, monkeypatch):
    """00:45-01:05: adopt_skill got the whole script as 'script', four times, and failed every time."""
    from core.one.tools import tool_adopt_skill
    d = tmp_path / "EVA" / "face recognition camera"
    d.mkdir(parents=True)
    code = "import json\nprint(json.dumps({'who': 'Ionut'}))\n"
    (d / "who_is_here.py").write_text(code)
    out = json.loads(tool_adopt_skill("who_is_here", "see who is at the PC", code)["result"])
    assert out["ok"] and out["script"] == str(d / "who_is_here.py")          # matched to the file that has it
    new_code = "print('fresh')\n"
    out = json.loads(tool_adopt_skill("greeter", "say hi", new_code)["result"])
    assert out["ok"] and Path(out["script"]).read_text() == new_code
    assert json.loads(tool_adopt_skill("who2", "x", "face recognition camera/who_is_here.py")["result"])["ok"]


# ---------------------------------------------------------------- her folder on D
def test_her_folder_is_next_to_her_code(monkeypatch):
    import importlib
    real = importlib.reload(files)                        # the module without the test's patched root
    try:
        monkeypatch.setattr("core.agent.pc._cfg", lambda: {"output_dir": "~/Documents/E.V.A."})
        assert real.root() == real.code_root().parent / "E.V.A. Files"
        monkeypatch.setattr("core.agent.pc._cfg", lambda: {"output_dir": ""})
        assert real.root() == real.code_root().parent / "E.V.A. Files"
        monkeypatch.setattr("core.agent.pc._cfg", lambda: {"output_dir": "/somewhere/else"})
        assert real.root() == Path("/somewhere/else")
    finally:
        importlib.reload(files)


def test_the_old_folder_moves_and_its_paths_follow(tmp_path, monkeypatch):
    old = tmp_path / "Documents" / "E.V.A."
    (old / "face recognition camera").mkdir(parents=True)
    script_ = old / "face recognition camera" / "who_is_here.py"
    script_.write_text(f'FACE_DIR = r"{old}/face recognition camera"\n')
    (old / "Biography").mkdir()
    (old / "Biography" / "index.json").write_text(json.dumps({"file": str(old / "Biography" / "x.md")}))
    new = tmp_path / "Project" / "E.V.A. Files"
    (new / "Biography").mkdir(parents=True)                                  # already there: never overwritten
    out = files.migrate([old], new)
    assert out["moved"] == ["face recognition camera"] and any("Biography" in k for k in out["kept"])
    moved = new / "face recognition camera" / "who_is_here.py"
    assert moved.exists() and str(new) in moved.read_text() and str(old) not in moved.read_text()
    assert (old / "Biography").exists()                                      # kept where it was


# ---------------------------------------------------------------- her own code and console
def test_she_reads_her_code_but_never_the_private_files(tmp_path, monkeypatch):
    root = tmp_path / "eva"
    (root / "voice").mkdir(parents=True)
    (root / "config").mkdir()
    (root / "voice" / "listen.py").write_text("max_pause_secs = 1.2\n")
    (root / "config" / "secrets.env").write_text("ANTHROPIC_API_KEY=sk-ant-secret\n")
    (root / "config" / "settings.local.yaml").write_text("max_pause_secs: 9\n")
    (root / "EVA.local.md").write_text("max_pause_secs private\n")
    monkeypatch.setattr(selfcode, "code_root", lambda: root)
    hits = selfcode.find("max_pause_secs")
    assert hits == ["voice/listen.py:1: max_pause_secs = 1.2"]
    assert "1.2" in selfcode.read("voice/listen.py")["text"]
    for p in ("config/secrets.env", "config/settings.local.yaml", "EVA.local.md", "../outside.py", ".git/config"):
        assert "private" in selfcode.read(p)["error"]


@pytest.mark.skipif(not __import__("shutil").which("git"), reason="needs git")
def test_she_changes_a_copy_tests_it_and_only_his_yes_applies_it(tmp_path, monkeypatch):
    live = tmp_path / "Project" / "eva"
    (live / "core").mkdir(parents=True)
    (live / "core" / "hello.py").write_text("GREETING = 'hi'\n")
    (live / "tests").mkdir()
    (live / "tests" / "test_hello.py").write_text("from core.hello import GREETING\n\ndef test_it():\n"
                                                  "    assert GREETING\n")
    (live / "core" / "__init__.py").write_text("")
    git = lambda *a: subprocess.run(["git", *a], cwd=live, capture_output=True, text=True)    # noqa: E731
    git("init", "-q")
    git("add", "-A")
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "base")
    monkeypatch.setattr(selfcode, "code_root", lambda: live)
    assert selfcode.change("core/hello.py", "GREETING = 'hi'", "GREETING = 'good evening, sir'", "warmer")["ok"]
    assert (live / "core" / "hello.py").read_text() == "GREETING = 'hi'\n"                 # the real code: untouched
    assert selfcode.diff()["files"] == ["core/hello.py"]
    assert selfcode.test("tests/test_hello.py")["ok"]
    assert risk.judge("code_apply", {})[0] == "ask" and risk.judge("code_change", {})[0] == "tell"
    assert "secrets" in selfcode.change("config/secrets.env", "", "x", "no")["error"] or \
        "private" in selfcode.change("config/secrets.env", "", "x", "no")["error"]
    assert selfcode.apply()["ok"]
    assert (live / "core" / "hello.py").read_text() == "GREETING = 'good evening, sir'\n"
    assert "core/hello.py" in git("status", "--porcelain").stdout                          # he commits it himself
    selfcode.reset()
    assert not selfcode.workshop().exists()


def test_her_console_is_in_memory_and_hides_keys():
    from loguru import logger
    console.LINES.clear()
    console.install()
    logger.warning("TOOL run_my_script returned an error: No module named 'cv2'")
    logger.info("key check: api_key=sk-ant-abc123")
    rows = console.tail(10, "warning")
    assert len(rows) == 1 and "cv2" in rows[0]["text"]
    assert "sk-ant" not in console.text(console.tail(10))
    out = json.loads(console.tool_my_console(contains="cv2")["result"])
    assert out["count"] == 1


# ---------------------------------------------------------------- places
@pytest.fixture
def pl(tmp_path, monkeypatch):
    p = places.Places(tmp_path / "places.json", tmp_path / "trail.jsonl")
    monkeypatch.setattr(places, "_places", p)
    return p


def test_house_is_home(tmp_path):
    (tmp_path / "p.json").write_text(json.dumps({"places": {"house": {"lat": 51.58, "lon": 4.77, "radius": 120}},
                                                 "inside": {"house": 1.0}, "rules": [{"place": "house"}]}))
    p = places.Places(tmp_path / "p.json", tmp_path / "t.jsonl")
    assert "home" in p.data["places"] and "house" not in p.data["places"] and p.data["rules"][0]["place"] == "home"
    assert places.place_name("my house") == "home" and places.place_name("the shop") == "shop"


def test_the_nearest_albert_heijn_becomes_his_shop_and_his_list_comes_when_he_arrives(pl, monkeypatch):
    from core import nearby
    pl.update(51.5890, 4.7760, 20)
    monkeypatch.setattr(nearby, "search", lambda what, centre, radius: [
        {"lat": 51.5950, "lon": 4.7800, "tags": {"name": "Albert Heijn", "addr:street": "Far", "addr:housenumber": "9"}},
        {"lat": 51.5900, "lon": 4.7770, "tags": {"name": "Albert Heijn", "addr:street": "Ginnekenweg",
                                                 "addr:housenumber": "120"}}])
    out = places.tool_place_save("my shop", "nearest Albert Heijn")
    assert "Ginnekenweg 120" in out["say"] and pl.data["places"]["shop"]["label"] == "Albert Heijn"
    act = places.tool_place_action("shop", "arrive", "shopping_list", {}, "Don't forget the groceries")
    assert "every time" in act["say"]
    refused = json.loads(places.tool_place_action("shop", "arrive", "message_reply", {})["result"])
    assert "needs his yes" in refused["error"]
    pl.update(51.6100, 4.8000, 20)                                       # away
    fired = pl.update(51.5900, 4.7770, 15)                               # at the shop
    assert [f["rule"]["tool"] for f in fired] == ["shopping_list"]
    assert pl.data["rules"]                                               # every time: still there
    assert "His places: shop = Albert Heijn, Ginnekenweg 120" in pl.summary()


def test_her_situation_names_his_places(eva, pl):
    pl.remember_at("shop", 51.59, 4.777, label="Albert Heijn")
    b = script(eva, [Step(text="Yes, sir.")])
    say(eva, "where do I shop?")
    assert "His places: shop = Albert Heijn" in b.calls_seen[0]["transcript"][-1]["text"]


# ---------------------------------------------------------------- skills are kept, and the JARVIS world is clean
def test_the_jarvis_world_never_sees_his_real_projects(monkeypatch):
    from core import jarvis_test as jt
    import core.mind as mind_mod
    seen = []
    real = mind_mod.situation

    def ideal(transcript, tools):
        seen.append(transcript[-1]["text"])
        return Step(text="Doing well, sir.")
    from core.one.backends import ScriptedBackend

    def make_():
        b = ScriptedBackend(ideal, "ideal")
        b.kind = "cloud"
        return [b]
    asyncio.run(jt.run_all(["small_talk"], make_, "ideal"))
    assert "The test world" in seen[0] and mind_mod.situation is real
