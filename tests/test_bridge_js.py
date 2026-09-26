"""The browser bridge's mic downsampler (48 kHz float -> 16 kHz PCM16 blocks) and script syntax, run in Node.

Skipped when Node is not installed. The downsampler source is taken from eva.html between its markers,
so the test checks exactly the code the AudioWorklet runs.
"""

import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

HTML = Path(__file__).resolve().parents[1] / "interfaces" / "web" / "eva.html"
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node not installed")


def run_node(code: str) -> dict:
    """Run JavaScript from a temp file. Never `node -e`: Windows caps a command line at 32767 characters."""
    with tempfile.TemporaryDirectory() as d:
        script = Path(d) / "check.js"
        script.write_text(code, encoding="utf-8")
        out = subprocess.run([NODE, str(script)], capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def downsampler_source() -> str:
    html = HTML.read_text(encoding="utf-8")
    m = re.search(r"/\*DOWNSAMPLER-START\*/(.*?)/\*DOWNSAMPLER-END\*/", html, re.S)
    assert m, "downsampler markers missing from eva.html"
    return m.group(1)


def test_downsampler_48k_to_16k_blocks():
    code = downsampler_source() + r"""
const push = makeDownsampler(48000, 16000, 512);
let blocks = [];
for (let k = 0; k < 20; k++) {                       // 20 x 128-sample render quanta of a 440 Hz tone at 0.5
  const q = new Float32Array(128);
  for (let i = 0; i < 128; i++) q[i] = 0.5 * Math.sin(2 * Math.PI * 440 * (k * 128 + i) / 48000);
  blocks = blocks.concat(push(q));
}
const more = [];
for (let k = 0; k < 40; k++) more.push(...push(new Float32Array(128).fill(2.0)));   // clipping input
const b = blocks[0];
let peak = 0; for (const v of b) peak = Math.max(peak, Math.abs(v));
console.log(JSON.stringify({ n: blocks.length, len: b.length, type: b.constructor.name, peak,
                             clipped: more[more.length - 1][511] }));
"""
    r = run_node(code)
    assert r["n"] == 1 and r["len"] == 512 and r["type"] == "Int16Array"   # 2560 in -> 853 out -> one full block
    assert 15000 < r["peak"] < 16500                                          # 0.5 amplitude survives the box filter
    assert r["clipped"] == 32767


def test_downsampler_44k1_keeps_the_rate():
    code = downsampler_source() + r"""
const push = makeDownsampler(44100, 16000, 512);
let n = 0;
for (let k = 0; k < 441; k++) n += push(new Float32Array(100)).length;   // exactly 1 s of audio
console.log(JSON.stringify({ blocks: n }));
"""
    assert run_node(code)["blocks"] == 31                                    # 16000 / 512 = 31.25


def test_bridge_scripts_parse():
    html = HTML.read_text(encoding="utf-8")
    scripts = re.findall(r"<script>(.*?)</script>", html, re.S)
    code = "const s = " + json.dumps(scripts) + r""";
let ok = 0; for (const b of s) { new Function(b); ok++; }
console.log(JSON.stringify({ ok }));
"""
    assert run_node(code)["ok"] == len(scripts) >= 2


def test_bridge_speaks_the_listening_protocol():
    html = HTML.read_text(encoding="utf-8")
    for needle in ("type: 'speaking'", "type: 'listen', arm:", "type: 'listen', wake: true", "case 'stt':",
                   "case 'barge_in':", "case 'wake':", "echoCancellation: true", "registerProcessor('eva-pcm16'"):
        assert needle in html, needle


def test_routines_panel_is_wired():
    html = HTML.read_text(encoding="utf-8")
    for needle in ('id="evaRoutinesBtn"', 'id="evaRoutines"', "emit('routines'", "addEventListener('eva:routines'",
                   "api('POST', '/reminders'", "api('PATCH', '/routines/'", "api('POST', '/shopping/tick'",
                   "'routines-open'"):
        assert needle in html, needle
    bridge = html.split("E.V.A. bridge")[1]
    assert ".innerHTML" not in bridge and "insertAdjacentHTML" not in bridge   # user text never becomes markup


def test_panel_helper_renders_deeply_nested_content():
    """Sep 26: the Week tab printed "[object HTMLDivElement]" because nested lists were only flattened once."""
    html = HTML.read_text(encoding="utf-8")
    m = re.search(r"/\*EL-START\*/(.*?)/\*EL-END\*/", html, re.S)
    assert m
    code = r"""
class Node { constructor(tag) { this.tag = tag; this.kids = []; this.nodeType = 1; this.attrs = {}; }
  append(x) { this.kids.push(x); } setAttribute(k, v) { this.attrs[k] = v; } }
const document = { createElement: t => new Node(t), createTextNode: t => ({ nodeType: 3, text: t }) };
""" + m.group(1) + r"""
const day = (n) => [el('div', null, 'Day ' + n), [el('div', null, 'a'), el('div', null, 'b')]];
const box = el('div', null, [day(1), day(2)], null, false, 'tail');
const texts = [];
(function walk(n) { for (const k of n.kids) { if (k.nodeType === 3) texts.push(k.text); else walk(k); } })(box);
console.log(JSON.stringify({ direct: box.kids.map(k => k.nodeType), texts }));
"""
    r = run_node(code)
    assert r["direct"] == [1, 1, 1, 1, 1, 1, 3]                     # six elements and the tail text, no strings
    assert r["texts"] == ["Day 1", "a", "b", "Day 2", "a", "b", "tail"]
    assert not any("[object" in t for t in r["texts"])


def test_status_and_routines_open_separately():
    html = HTML.read_text(encoding="utf-8")
    assert ".eva.panel-open .eva-panel{" not in html                # that rule opened BOTH panels
    assert ".eva.panel-open #evaPanel{transform:none;visibility:visible}" in html
    assert ".eva.routines-open #evaRoutines{transform:none;visibility:visible}" in html
