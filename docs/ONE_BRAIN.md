# One brain (v1.0)

Until v0.9 a sentence went through regex reflexes, five understanding modes, a local decision step, guards,
canned confirmations and then maybe the mind. Each layer could swallow or misroute it. On 27 Nov, "Stop the task."
was taken as an answer to a waiting task four times, a failed search fell back to a 3B model that made up a price,
and "Just to confirm, sir" came before everything. v1.0 replaces that with one loop.

```
his sentence
  |- reflex (lights, volume, "stop the task", timers)        -> runs at once, journaled
  '- the brain (core/one/loop.py)
        thinks (local: thinking steps; cloud: Claude's adaptive thinking)
        calls tools  -> the risk judge (core/one/risk.py)
                          free  reading, looking up             -> runs
                          tell  changes he can undo             -> runs, the journal keeps how to undo it
                          ask   sending as him, deleting,       -> not run; she asks in her own words,
                                installing, > 2 euros at once      his yes runs it
        no tool fits -> make_skill (a small sandboxed function), dry run, run_skill
        answers: each sentence is spoken as it comes; a sentence that claims an action nobody ran, promises one
        without a tool call, states a live fact nobody checked, or uses a number no tool gave is held back and
        she fixes it once before he hears it
```

## Backends (`one.backend`, or Settings > Her brain > Who thinks)

| | What | Cost | Notes |
|---|---|---|---|
| `local` | Ollama on the PC, `one.local_model` (qwen3.5:4b: tools, thinking, images, 3.4 GB) | free | Nothing leaves the PC. Slower; a small model gets more wrong. |
| `local_first` (default) | local first; Claude takes the turn when the local model fails a check twice, breaks, takes over `one.local_timeout_s`, or calls `hand_to_cloud` | usually free, a fraction of a cent when Claude steps in | What already ran is carried over, nothing runs twice. |
| `cloud` | Claude Haiku 5.5 with every tool; `think_harder` switches to Sonnet for the rest of the turn | about 0.3 cents a turn | Fastest and best. |

"Eva, local only" and "Eva, use auto" set the same switch. Until `ollama pull qwen3.5:4b` has run she uses
`inference.local.decision_model` and says so once in the log (`BRAIN: qwen3.5:4b isn't on this PC`).

The local model sees about 22 tools a turn (picked by embeddings and words, plus the core set); `find_tools` gives
it more. Claude sees all of them (prompt-cached).

## The journal (`core/one/journal.py`, Settings > What she did)

Every turn: what he said, which brain, her thinking, every step (tool, risk, why, decision, what it changed before
and after, how to undo it), her answer, cost, time. `data/journal/<date>.jsonl`, kept 30 days (`one.journal_days`).
Undo per step in the window, "Eva, undo that" by voice, `what_i_did` for "what did you change today?".
Undo exists for calendar add/move/shift/delete, shopping add/remove, reminders, settings, things planned for later,
and every action inside a skill she made. Tasks keep their own trail (plan, what they asked you, each step):
Settings > Tasks > Decisions.

## Skills she makes (`core/one/made.py`)

`make_skill(name, purpose, code, parameters, example_args)`: one `def run(eva, ...)` function; `eva.call(tool,
**args)` reaches her tools. Checked (allowed imports only, no files, no processes, no `_` attributes), saved to
`data/skills_made/<name>/`, then dry-run: reads are real, actions are only planned. The risk of a skill is the
worst risk of what its dry run planned. `run_skill` runs it for real, each action through the judge and into the
journal. A ready skill is a tool next time: `skill_<name>`. Each run is its own `python -I` process, 30 s, at most
60 tool calls.

## The JARVIS test, automatic (`core/jarvis_test.py`, `core/jarvis_scenarios.yaml`)

22 scenarios in a fake world (calendar, messages, tasks, web, weather: nothing real is touched), checked for: the
right tools, asking only when it should, the calendar afterwards, numbers that come from tools, no stock phrases,
time. `python -m core.jarvis_test [--backend local|cloud|local_first] [--only id,id]`, or Settings > JARVIS test.
With `jarvis_test.nightly` it runs every night at `jarvis_test.hour` (3:00) when he's been quiet for 30 minutes,
with the local brain (free). Results: `data/jarvis_runs/<time>.json`.

## The classic routing

`one.mode: classic` (Settings > Her brain > How she decides) brings back v0.9 exactly, for this version. The test
suite still covers it (tests not marked `one_brain`).

## Log lines

`BRAIN: step N: tool, tool (model)`, `BRAIN: answered after N steps (model, s)`, `BRAIN: check failed (...)`,
`BRAIN: the local model handed over (...)`, `REFLEX: tool`, `JOURNAL: undid ...`, `SKILLS: made ...`,
`JARVIS TEST: n/m (score%)`.

## v1.1 (28 Nov): after his first evening with v1.0

- **She trusts her journal.** The situation lists what she did lately ("22:29 open_file: opened the preview"),
  so she no longer "corrects" herself about things she really did. A claim is only checked when nothing changed
  in the last 15 minutes; an offer ("say so and I'll open it") is never an empty promise; a fix that talks about
  the check ("nothing needs fixing") is dropped for her first answer; the check's note tells her never to mention it.
- **Reflexes are for short, plain commands.** More than 9 words, two sentences, a question about what she knows, or
  media words next to "task / project / setup": the brain decides ("can you locate my phone? do you know my
  location?" no longer rings it; "resume the task for the face recognition camera" no longer resumes the music).
- **Her files** (`core/one/files.py`): `my_files`, `read_my_file`, `run_my_script` (her own Python in
  data/agent/venv, network allowed; the first run of a script asks once, a changed file asks again),
  `adopt_skill` (a script becomes `skill_<name>` for good, after his yes once). Code files open in VS Code.
- **Tasks ask less**: installing into her own environment and delivering to his E.V.A. folder are logged, not
  asked (`agent.quiet`, [] brings the questions back). New hands: `download_file` (models, weights, data: the
  sandbox has no network), `find_my_files`, `import_file`. Only the newest 3 screenshots stay in a task's request.
- **Listening**: `voice.pipeline.patience_secs` (0.7): after Smart Turn says he's done she waits a little; if he goes
  on it's the same sentence. "uh", "um", "you know", "so like" at the end wait for the rest (`carry_secs`), and a
  half sentence that gets no rest still reaches her. Settings > Listening changes it live.
- **The biography** (`core/memoir.py`, Settings > Biography): "I want to tell you a story" starts it; she listens
  quietly (a soft tone per part, a recording pill with "That's the end"), with longer pauses and turns; "that's the
  end" writes a chapter in his own voice (Claude Sonnet when allowed, else local), keeps his exact words in
  sessions/, rebuilds Biography.md in the order things happened, and keeps the questions she'd ask next.
- **What she notices** (`core/one/patterns.py`): the same change on 3 different days in two weeks is offered once
  as something to automate. Skills she made that nobody used for 30 days are retired.
- **The HUD and the latency meter**: /stage shows "Lately" (her latest decisions) and "Speed today"; under the orb,
  "first sound 1.8 s" after each answer.
- Fixes: browser results keep their own blocks (the 22:37 API error), Windows file locks are retried (the 23:07
  task crash), the first-byte wait grows with the request (up to 14 s), the JARVIS run really uses the brain it
  names, free_slots uses the time it's given. The JARVIS test has 28 scenarios.
