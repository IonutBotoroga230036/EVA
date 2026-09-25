"""FORGE end to end with a fake Claude: build, review, real sandbox, repair, install, hot reload."""

import json

import pytest

import core.forge_engine as fe
import core.orchestrator_hybrid as orch_mod
from core.forge_engine import Forge
from core.memory.cortex import Cortex
from core.orchestrator_hybrid import HybridOrchestrator, ToolBelt
from skills.registry import SkillRegistry
from tests.test_orchestrator import fake_ollama, run_turn

GOOD_TOOLS = '''import json
TOOLS = [{"type": "function", "function": {"name": "km_to_miles", "description": "Convert kilometres to miles.",
          "parameters": {"type": "object", "properties": {"km": {"type": "number"}}, "required": ["km"]}}}]
def km_to_miles(km=0, **_):
    try:
        v = float(km) * 0.621371
    except Exception:
        return {"result": json.dumps({"error": "not a number"}), "say": "That isn't a number, sir."}
    return {"result": json.dumps({"miles": round(v, 2)}), "say": f"That's {round(v, 1)} miles, sir."}
FUNCTIONS = {"km_to_miles": km_to_miles}
'''
GOOD_TEST = '''import json, tools
def test_convert():
    assert json.loads(tools.km_to_miles(km=10)["result"])["miles"] == 6.21
def test_error():
    assert "error" in json.loads(tools.km_to_miles(km="x")["result"])
'''
SKILL_MD = "---\nname: unit_convert\ndescription: Convert kilometres to miles.\nenabled: true\ntrusted: true\n---\nUse km_to_miles.\n"


def spec(**over):
    s = {"feasible": True, "name": "unit_convert", "description": "Convert units", "network_hosts": [],
         "summary_for_user": "It converts kilometres to miles.", "skill_md": SKILL_MD,
         "tools_py": GOOD_TOOLS, "test_py": GOOD_TEST}
    return {**s, **over}


class FakeClaude:
    def __init__(self, specs):
        self.specs, self.calls = list(specs), []

    def available(self):
        return True

    def message(self, **kw):
        self.calls.append(kw)
        return {"tool_input": self.specs.pop(0), "cost_eur": 0.05, "text": ""}


@pytest.fixture
def forge(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)                              # data/forge state stays in the temp dir
    (tmp_path / "skills").mkdir()
    return lambda specs: Forge(tmp_path / "skills", claude=FakeClaude(specs))


def test_good_build_is_quarantined_untrusted_and_invisible(forge, tmp_path):
    f = forge([spec()])
    p = f.build("convert km to miles")
    assert p.status == "ready" and p.tests_passed == 2 and p.tools == ["km_to_miles"]
    md = (tmp_path / "skills" / "_forge" / "unit_convert" / "SKILL.md").read_text()
    assert "trusted: false" in md and "trusted: true" not in md             # Claude said true; forced false
    assert SkillRegistry(tmp_path / "skills").discover().skills == {}        # quarantine is never loaded


def test_install_moves_trusts_and_hot_reloads(forge, tmp_path):
    f = forge([spec()])
    f.build("convert km to miles")
    f.install("unit_convert")
    reg = SkillRegistry(tmp_path / "skills").discover()
    belt = ToolBelt(reg)
    assert belt.has("km_to_miles")
    assert "6.2 miles" in belt.execute("km_to_miles", {"km": 10})["say"]


def test_dangerous_draft_is_repaired_then_accepted(forge):
    evil = spec(tools_py=GOOD_TOOLS.replace("import json", "import json, subprocess"))
    fake = FakeClaude([evil, spec()])
    f = Forge(forge([]).skills_dir, claude=fake)
    p = f.build("convert km")
    assert p.status == "ready" and len(fake.calls) == 2 and p.cost_eur == pytest.approx(0.10)
    assert "subprocess" in fake.calls[1]["messages"][-1]["content"]          # Claude was told what to fix


def test_draft_that_fails_twice_cannot_be_installed(forge):
    phones_home = spec(test_py="import urllib.request\ndef test_net():\n    urllib.request.urlopen('https://example.com', timeout=2)\n")
    f = forge([phones_home, phones_home])
    p = f.build("something")
    assert p.status == "rejected" and "network is disabled" in p.reason
    with pytest.raises(PermissionError):
        f.install(p.name)


def test_undeclared_host_is_blocked_by_review(forge):
    sneaky = spec(tools_py=GOOD_TOOLS + '\nURL = "https://evil.example.com/steal"\n')
    p = forge([sneaky, sneaky]).build("x")
    assert p.status == "rejected" and "evil.example.com" in p.reason


def test_infeasible_requests_are_reported_not_built(forge, tmp_path):
    p = forge([{"feasible": False, "reason": "It needs a paid API key."}]).build("control my Tesla")
    assert p.status == "infeasible" and "paid API key" in p.reason
    assert not (tmp_path / "skills" / "_forge").exists()


def test_name_collision_gets_a_new_name_and_discard_cleans_up(forge, tmp_path):
    (tmp_path / "skills" / "unit_convert").mkdir()
    f = forge([spec()])
    p = f.build("convert")
    assert p.name == "unit_convert_2"
    f.discard(p.name)
    assert not (tmp_path / "skills" / "_forge" / "unit_convert_2").exists()


def test_the_whole_conversation(tmp_path, monkeypatch):
    """'build a skill that converts km to miles' -> confirm cost -> yes -> draft -> install? -> yes -> use it."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "skills").mkdir()
    import shutil
    from pathlib import Path
    for s in ("forge",):
        shutil.copytree(Path(__file__).resolve().parents[1] / "skills" / s, tmp_path / "skills" / s)
    engine = Forge(tmp_path / "skills", claude=FakeClaude([spec()]))
    monkeypatch.setattr(fe, "_forge", engine)
    reg = SkillRegistry(tmp_path / "skills").discover()
    import skills.registry as sr
    monkeypatch.setattr(sr, "_registry", reg)
    client, _ = fake_ollama(decisions=[{"tool": "km_to_miles", "km": 10}], answer="That's 6.2 miles, sir.")
    monkeypatch.setattr(orch_mod.httpx, "AsyncClient", client)
    o = HybridOrchestrator(session_id="f", cortex=Cortex(str(tmp_path / "c.db")), registry=reg)

    ask = run_turn(o, "build a skill that converts km to miles")
    assert "draft a new skill" in ask[-1]["text"] and "Shall I go ahead" in ask[-1]["text"]
    drafted = run_turn(o, "yes")
    assert drafted[0] == {"type": "ack", "text": "Drafting it now, sir. This takes about a minute."}
    assert "Shall I install it" in drafted[-1]["text"] and o.pending["tool"] == "forge_install"
    installed = run_turn(o, "yes please")
    assert installed[-1]["text"].startswith("Installed, sir.")
    used = run_turn(o, "how many miles is 10 km")                         # hot-reloaded: new tool is live
    assert used[-1]["text"] == "That's 6.2 miles, sir."
