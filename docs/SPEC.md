# E.V.A. Build Spec (v0.2, corrected)

Last updated: September 24, 2026, end of Milestone B.
This replaces the earlier one-shot build prompt. It describes the project as it
actually is, the decisions already made, and the order to build the rest in.
Any session working on E.V.A. (this chat, Claude Code, or another model) should
read this file and `EVA.md` first.

## How to use this spec

- **In a Claude chat with a code sandbox:** give the repo URL. The assistant clones
  it, reads this file, and works milestone by milestone.
- **In Claude Code:** open the repo; point it at `docs/SPEC.md` and `EVA.md`.
- **In a chat without tools:** flatten the repo (`npx repomix`) and attach it.

## Working method (non-negotiable)

1. One milestone at a time. Never ship ten components in one untested pass.
2. Every changed file is delivered complete, never as a snippet or diff.
3. Before handing over, the assistant compiles and runs `pytest tests/` in its
   sandbox, with Ollama mocked through `httpx.MockTransport`.
4. Ionut then tests only what needs real hardware: Ollama on the GPU, audio,
   OAuth logins, the phone.
5. Green means commit: `git commit -m "Milestone X: ..."` so every step has a
   restore point.

## Current state

### Active v0.2 files

| File | Role |
|---|---|
| `core/orchestrator_hybrid.py` | The brain. Fast path, constrained-decoding tool choice, bounded tool loop (3), streamed answer, memory + skills + EVA.md in the prompt, background fact extraction. |
| `core/tools_native.py` | Built-in tools: datetime, weather (Open-Meteo), web search (ddgs), Spotify URI launch, media keys, exact volume (pycaw), open app/website. |
| `core/memory/cortex.py` | CORTEX. One SQLite file `data/cortex.db`: every turn, every fact, fact vectors. Dedup, recall, forget, secret filter. |
| `core/memory/extractor.py` | Learns facts in the background after each answer (constrained JSON). |
| `core/prompt_builder.py` | Loads `EVA.md` each turn; assembles prompts; appends standing instructions. |
| `core/events/bus.py` | PULSE, in-process pub/sub. No Redis. |
| `core/settings.py` | Single loader for `config/settings.yaml`. |
| `skills/registry.py` | SKILL.md discovery, trust gate, semantic matching, tool loading. |
| `skills/memory/`, `skills/vision/`, `skills/morning-briefing/` | First three skills. |
| `interfaces/web/server_stream.py` | FastAPI + WebSocket on port 8001; `GET /api/status`. |
| `interfaces/web/eva.html` | Purple core UI, half-duplex browser voice, follow-up window, widgets. |
| `EVA.md` | Standing context and instructions, read every turn (like CLAUDE.md). |
| `tests/` | 44 tests, all passing. |

### Legacy v0.1 files (keep, do not build on)

`core/orchestrator.py`, `core/orchestrator_stream.py`, `core/router.py`,
`core/tool_executor.py`, `core/memory/episodic.py`, `core/memory/semantic.py`,
`interfaces/web/server.py`, `skills/computer_control/`, `skills/web_search/`.
The legacy orchestrator still runs because the new PULSE bus accepts the old
`PulseEventBus(redis_host=..., redis_port=...)` signature.

### Models (RTX 3060 Laptop, 6 GB VRAM)

| Purpose | Model | Notes |
|---|---|---|
| Decisions + answers | `qwen2.5:3b-instruct` | Fits fully on the GPU. The 7B spilled to CPU (70 s per turn). |
| Embeddings | `nomic-embed-text` | ~270 MB. Drives memory recall and skill matching. Optional; keyword fallback. |
| Vision | `qwen2.5vl:3b` | ~3.2 GB. Loaded per request, unloaded immediately (`keep_alive: 0`). |

Constrained decoding (Ollama `format` = JSON schema) is what makes the 3B
reliable at choosing tools. It guarantees valid, on-menu output; it does not
guarantee the right choice, so tool descriptions matter.

## Architecture

### One turn

1. Embed the message once. The same vector drives memory recall and skill matching.
2. Fast path for unambiguous commands (time, volume, media, remember, forget,
   "from now on", screen). No model call. Everything else goes to the model, so
   nuance is never capped.
3. Constrained decision over a flat schema built automatically from all loaded
   tools (built-ins plus skills). Built-ins win name collisions.
4. Up to 3 tool rounds; an identical repeated call ends the loop.
5. Stream the answer with: persona, EVA.md, up to 6 relevant facts, matched skill
   bodies, response rules, real tool data.
6. Log both turns to CORTEX; extract facts in the background (no added latency).

### EVA.md (the CLAUDE.md equivalent)

Plain markdown at the repo root: about the user, how to work with them, standing
instructions. Read fresh every turn, so edits apply immediately. "From now on X"
makes E.V.A. append X under `## Standing instructions` herself. Keep it under
~3000 characters; a small model gets worse with long system prompts.

### Skills (the Claude Code skills equivalent)

```
skills/<name>/
  SKILL.md    frontmatter (name, description, enabled, trusted, triggers,
              permissions, always) + instructions
  tools.py    optional: TOOLS (schemas), FUNCTIONS (name -> callable), ACKS
```

- Progressive disclosure: only descriptions are indexed; a body enters the
  prompt when the request matches (cosine >= `skills.match_threshold`, or a trigger).
- Instruction-only skills are valid (see `morning-briefing`): know-how, no code.
- **Trust gate (AEGIS):** `tools.py` is imported only when `trusted: true`.
  FORGE-generated skills are written with `trusted: false` and stay inert until
  Ionut approves them.

### CORTEX memory

- One SQLite file, WAL mode. FTS5 for episode search.
- Facts carry an embedding; recall is brute-force cosine in numpy (instant at
  personal scale). No ChromaDB on the v0.2 path.
- Dedup rule, "newest phrasing wins": a new fact whose similarity to an existing
  fact in the same category is >= `memory.dedup_threshold` replaces it. One rule
  covers restatements (165 cm vs 5 ft 5) and corrections (Breda -> Tilburg).
- Never stored: passwords, keys, tokens, card numbers, IBANs (regex filter).
- A new session resumes the last 6 turns from within `memory.continuity_hours`.

## Decisions already made (do not reverse without a reason)

- **No Redis, no Celery.** Single-machine monolith; Redis isn't native on
  Windows; Celery was never used. asyncio tasks + in-process PULSE instead.
- **SQLite + Ollama embeddings instead of ChromaDB + sentence-transformers.**
  One store, no torch load for memory.
- **Secrets stay on the existing `cryptography`-based AEGIS vault**, not age/sops.
- **Browser voice stays until Kokoro streaming lands** (Milestone C). The UI is
  half-duplex (no listening while she speaks) with a ~7 s no-wake-word
  follow-up window.
- **Pipecat is the future voice layer** (turn detection, barge-in, echo
  handling), adopted as its own milestone, not mixed into feature work.
- **Mine im4peace/Jarvis (MIT), don't fork it.** Already harvested: hybrid router
  pattern, bounded loop, see_screen. Still to harvest: deep-research engine,
  registry confirmation gate. Keep the MIT notice on lifted code.

## Hard facts that constrain the roadmap

- **Spotify:** Web API playback control (play, pause, skip, queue, volume)
  requires Spotify Premium. Without Premium: search and now-playing only.
- **Gmail:** request `gmail.readonly` + `gmail.compose` (drafts). Never the send
  scope unless Ionut explicitly enables sending later.
- **Google OAuth on Windows:** use `InstalledAppFlow.run_local_server(port=0)`;
  it sidesteps the ampersand-in-URL problem that broke the OpenJarvis flow.
- **pycaw:** use `AudioUtilities.GetSpeakers().EndpointVolume`; `.Activate()`
  no longer exists on the wrapper. Call `CoInitialize()` in the calling thread.
- **Web search package** is `ddgs` (renamed from `duckduckgo-search`).

## Design rules

- **Phone-call rhythm:** short spoken ack before a tool runs, then the concise
  answer. Never narrate mechanics.
- **Concise:** one or two sentences. "sir" for E.V.A.; "Ionut" for K.I.R.A.
- **Honest failure:** retry once, then say so. Missing capability: "I don't have
  a skill for that yet, sir. Once FORGE is live I can build one, with your approval."
- **UI:** single purple core (#8b5cf6, #c026d3, lilac), widgets only when
  relevant, fade after ~14 s. Don't refactor the canvas core. Vanilla JS, one
  HTML file, no build step.
- **Security:** nothing installs without approval; no network for a skill unless
  declared and approved; secrets encrypted; every tool call in the audit log;
  FORGE code runs sandboxed; HERALD never reveals private info to unknown callers.
- **Budget:** EUR 50/month hard cap. Local handles 80%+ of turns. Cloud (Claude
  API) only for tasks local models can't do, metered by VAULT, with local-only
  fallback at 90% of the daily cap.
- **Writing:** no em-dashes anywhere, code comments and docs included.

## Roadmap (in order)

| # | Milestone | Scope |
|---|---|---|
| A | Hybrid orchestrator | Done. |
| B | Foundation | Done. PULSE in-process, CORTEX persistent memory, EVA.md, SKILL.md registry, vision skill, 44 tests. |
| C | Kokoro streaming TTS | Server splits streamed tokens at sentence ends, synthesizes each with Kokoro (`af_heart`), sends base64 WAV over the WebSocket; browser plays an ordered queue. Acks synthesized first. Also fixes voice on the phone. |
| D | Weather v2 | Forecast by day and hour ("tomorrow at 18:00 in Tilburg"), any city, from Open-Meteo hourly/daily data. |
| E | Obsidian skill | Local only: create, search, read, append, daily note. Vault path in settings. |
| F | TEMPO calendar | Google Calendar: today, range, create, delete (confirm), plan week. Calendar widget. |
| G | SCRIBE email | Gmail unread, search, read, draft reply for approval. Email widget. |
| H | Spotify skill | spotipy: play, pause, skip, queue, volume, now playing with real metadata. Premium check at setup. |
| I | Deep research | Harvest im4peace planner/worker research engine as a skill; cited answers. |
| J | FORGE v1 | Self-improvement loop (below). |
| K | ORACLE | Proactive reminders, do-not-disturb, gap detection, hooks from PULSE. |
| L | Pipecat voice | Local Whisper STT, turn detection, barge-in, echo handling. |
| M | HERALD | Phone calls/SMS via Twilio on Pipecat. |

Remaining small items: VAULT must count local Ollama calls at EUR 0 (the current
`record_usage` bills unknown models at Sonnet rates), a settings/history panel in
the UI, K.I.R.A. persona switching.

## FORGE v1: how E.V.A. improves herself

Goal: E.V.A. proposes a change to herself, tests it in isolation, and Ionut
approves it after testing it himself.

1. **Detect:** a request no tool covers, a repeated failure in the logs, or an
   explicit "Eva, build a skill for X".
2. **Plan:** write a short plan (what, which permissions, which tests).
3. **Generate:** a skill folder with `SKILL.md` (`trusted: false`), `tools.py`,
   and `test_skill.py`. Never edits core files in v1.
4. **Test in a sandbox:** Docker container, no network, repo mounted read-only,
   run the skill's tests plus the full suite. Failures go back to step 3, at most 3 times.
5. **Present:** "Sir, I built X. It needs [permissions]. Tests: N passed. Here's
   the diff." Shown in the UI.
6. **Approve:** Ionut tests it; on "install it", `trusted` flips to true, PULSE
   publishes `skills.changed`, the registry hot-reloads. On "discard", the folder is deleted.

Model choice: a 3B local model cannot write reliable code. FORGE uses the
Claude API (Sonnet) for steps 2 and 3 only, metered by VAULT, roughly cents
per attempt, under the EUR 50 cap. The local model handles detection and
conversation. Revisit a local coder when the 12 GB+ GPU arrives.

## Proof it works (after each milestone)

- "What time is it?" -> instant, `TOOL get_datetime` in the terminal.
- "Remember that I train on Tuesdays", restart the server, "What do you know about me?"
- "From now on, answer in metric units" -> line appears in EVA.md.
- "What's on my screen?" -> "Taking a look, sir." then a description.
- "Good morning" -> briefing with date, time, and weather.
- `pytest tests/` -> all green.
