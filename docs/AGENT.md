# v0.4 "Agent": tasks

E.V.A. can now take on jobs that take many steps: design something, research and compare, plan, write. You talk
to her as always; she works in the background and comes back when she needs you.

```
"Eva, design me a guitar stand I can 3D print"
  "Just to confirm, sir: start a task ... up to 1.00 euros before I ask for more. Shall I go ahead?"  "yes"
  "On it, sir: the guitar stand. ..."
  ... "Sir, about the guitar stand: what guitar is it for, and how tall?"   "electric, about 30 cm"
  ... "Sir, for the guitar stand, may I install OpenSCAD on your PC (winget OpenSCAD.OpenSCAD)?"  "yes"
  ... "Sir, the guitar stand is done. The stand is 120 x 90 x 300 mm ... MakerSpace Breda prints it ..."
"How's the guitar stand going?" / "pause the guitar stand" / "resume it" / "cancel the guitar stand"
```

## How it fits together

| Part | File | What it does |
|---|---|---|
| Task | `core/agent/tasks.py` | goal, title, status, plan, log, consent log, cost, budget, what it waits for. `data/tasks/<id>/` |
| Loop | `core/agent/runner.py` | Claude (`agent.model`, Sonnet 5.5) with the hands, prompt caching, one task at a time in a thread |
| Hands | `core/agent/hands.py` | plan, ask, tell, files, sandboxed Python, pip, programs, web, places, E.V.A.'s tools, deliver, finish |
| Consent | `core/agent/consent.py` | free / logged / ask, standing permissions in `data/permissions.json` |
| PC | `core/agent/pc.py` | everything on your computer goes through here: LocalPC now, RemotePC via the PC agent |
| PC agent | `interfaces/pc_agent.py` | runs on the PC and serves the same calls over `/ws/pc` when the brain lives elsewhere |
| Conversation | `core/agent/tools.py` | task_start / status / answer / pause / resume / cancel / list, permission_grant / revoke / list |

A task waits (status `waiting`) whenever it needs you. Everything needed to resume is saved with it: the exact
tool call it stopped at, the results of the calls before it in the same turn, and the ones after it. Your answer
becomes that call's result and it carries on. This survives restarts: running tasks resume when E.V.A. starts,
waiting ones keep waiting. Your answer arrives from wherever you are: the window you talk to (her question takes
your next words, her "may I" takes your yes or no), the phone with the screen off, or Telegram (Yes / Always /
No buttons, or just reply).

## Consent tiers

| Tier | What | Examples |
|---|---|---|
| free | looking | reading the task's files, web search, reading a page, finding places, checking if a program is installed |
| logged | reversible work in the task's folder, written to its consent log | writing files, Python in the sandbox, running a program you have on the task's files |
| ask | changes your PC or the world, or costs money | installing a program (winget), Python packages, copying files out, going over the budget, E.V.A. tools that act (calendar, messages) |

"Yes, always" (or "always allow Python packages") gives a standing yes for that kind: it becomes logged.
"Yes, for this task" gives it for this task only. Spending more, E.V.A. tools that act, and deleting can never get
a standing yes. "What are my permissions?" / "forget your permissions".

## The sandbox

`run_python` runs in its own process with the task folder as its working directory: no network, writes only
inside the folder, a time limit, packages from E.V.A.'s own venv (`data/agent/venv`, created on the first package
you approve), never your project's `.venv`. `run_program` runs an installed program with the task folder as its
folder and refuses paths outside it. Programs are found on PATH or in Program Files (OpenSCAD installs to
`C:\Program Files\OpenSCAD\openscad.exe`, not on PATH). Finished files reach you only through `deliver_file`, into
`Documents/E.V.A./<task>` (`agent.output_dir`).

Not yet: a Docker sandbox. The process sandbox blocks the network and writes outside the folder for Python, but a
program you approved runs as you. That is why installing a program always asks.

## Money

Each task has a budget (`agent.budget_eur`, 1 euro). Before each Claude turn she checks it; at the limit she asks
"continue with up to 1 euro more?" (`agent.budget_step_eur`). Prompt caching keeps a long task cheap: the
instructions and tools are read from the cache at a tenth of the price. Test mode and the daily budget apply as for
everything else. After `agent.max_steps` (40) turns in one go she asks whether to keep going.

## Moving the brain later

Today the brain runs on your PC and LocalPC does the work. When the brain runs in the cloud:

1. Start the brain there (same code).
2. On your PC: `python -m interfaces.pc_agent --server https://<brain>:8443 --token <token>`.
3. Set `agent.pc: remote` so a task never works on the cloud machine by mistake.

The task folders then live on your PC; the brain only keeps the conversation and the task's state.

## Tool retrieval

With many tools, understanding only sees the ones that fit the sentence (`core/understand/retrieve.py`): past
`understanding.retrieve.above` (150) tools, the best `keep` (50) by their words, plus the ones used in the last
turns and a short always-list. At about 108 tools today it is not active yet.

## Rule from now on

Fast paths (regexes in `orchestrator_hybrid.fast_path`) are for reflexes only: lights, volume, time, stop, switch
a mode. Everything else goes through understanding, where a new tool needs only a good description.
