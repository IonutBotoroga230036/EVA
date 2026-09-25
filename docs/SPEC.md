# E.V.A. Build Spec (v0.2, corrected)

Last updated: September 25, 2026 (afternoon), end of the triage + Telegram + routines release. Full user guide: docs/CAPABILITIES.md.
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
| `core/tools_native.py` | Built-in tools: datetime, weather, web search (ddgs, retries once), Spotify URI launch, media keys, exact volume with unmute (pycaw), open app/website. Exports ACTION_TOOLS. |
| `core/weather.py` | Weather v2: current, hourly, and daily forecasts up to 16 days, day/hour parsing, country to capital, city-local time. |
| `voice/speech.py` | ECHO v0.2: speech normalizer, sentence chunker, Kokoro engine (CPU), per-turn streaming speaker. |
| `core/mcp_client.py` | MCP client: stdio and HTTP servers become tools; one owner task per server; read-only tools run freely, others ask first. SDK 1.x and 2.x. |
| `core/forge_engine.py` | FORGE v1: Claude drafts a skill, AEGIS static review, network-off sandbox tests, one repair round, install on approval, hot reload. |
| `core/claude.py` | Claude API over plain HTTP; VAULT pre-check and recording; extended thinking; forced tool calls for structured output. |
| `core/budget.py` | VAULT: daily and monthly caps, local models free, prices configurable. |
| `core/brain.py` | Cloud or local for heavy jobs (FORGE, deep thinking): auto, cloud, local; voice switchable. |
| `core/google_api.py` | One Google sign-in for Calendar and Gmail; least-privilege scopes; no send permission. |
| `core/tempo.py`, `core/scribe.py` | Calendar and email logic with exact spoken lines. |
| `core/oracle.py` | Proactive loop: reminders, meeting heads-ups, new-email alerts, DND, quiet hours, briefing. |
| `core/triage.py` | Email importance: contacts, VIP/mute, Gmail labels, bulk and automated senders, urgency; local-model tie-break. |
| `core/telegram_bridge.py` | Telegram channel: pairing, allowlist, Yes/No buttons, ORACLE push, voice notes. |
| `core/timeparse.py` | Clock times, relative times, day windows. |
| `core/vocab.py` | Speech vocabulary correction from the Vocabulary sections of EVA.md / EVA.local.md (voice input only). |
| `core/memory/cortex.py` | CORTEX. One SQLite file `data/cortex.db`: every turn, every fact, fact vectors. Dedup, recall, forget, secret filter. |
| `core/memory/extractor.py` | Learns facts in the background after each answer (constrained JSON). |
| `core/prompt_builder.py` | Loads `EVA.md` each turn; assembles prompts; appends standing instructions. |
| `core/events/bus.py` | PULSE, in-process pub/sub. No Redis. |
| `core/settings.py` | Single loader for `config/settings.yaml`. |
| `skills/registry.py` | SKILL.md discovery, trust gate, semantic matching, tool loading. |
| `skills/memory/`, `skills/vision/`, `skills/morning-briefing/`, `skills/obsidian/` | Shipped skills. Obsidian: quick daily notes, named notes, search, read; sandboxed to the vault. |
| `interfaces/web/server_stream.py` | FastAPI + WebSocket on port 8001; streams Kokoro audio per sentence; barge-in; `GET /api/status`. |
| `interfaces/web/eva.html` | Claude Design interface (untouched) + bridge script (WebSocket, Kokoro audio with level and spoken-caption progress, speech recognition, UI events). `eva_classic.html` at /classic. |
| `EVA.md` | Standing context, instructions, and speech vocabulary, read every turn (like CLAUDE.md). |
| `EVA.local.md` | Private companion to EVA.md, git-ignored. Names, projects, private vocabulary. |
| `tests/` | 335 tests, all passing, isolated from `data/`, including MCP against a real server process. Browser player verified in Node. |

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
| Voice | Kokoro-82M, `af_heart` | Runs on the CPU by default so all VRAM stays with the LLM. |

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

### Tool loop rules (B.1, from real usage logs)

- Information tools may chain; a successful action tool ends the loop.
- A missing required argument triggers one repair call constrained to that tool's schema.
- `unavailable` is a decision option: no tool covers the request, so give the honest
  no-skill answer and publish `capability.missing` on PULSE (FORGE listens here later).
- Capability questions ("what else can you do") skip tools entirely.
- A tool that already succeeded is not called again in the same turn (retries only after an error).
- The terminal logs every tool result and every final answer (`EVA: ...`), so answers can be checked against data.

### Voice protocol (Milestone C)

Server to browser: `hello` (tts kokoro|browser), `phrase` (cached "Yes, sir?"),
`turn_start`, text events (`ack`, `token`, `widget`, `final`), `audio` (one base64 WAV
per sentence with `turn` and `seq`), `audio_end` (`count`). Browser to server:
`message`, `stop`. The browser plays audio strictly by `seq` and drops any turn it has
moved past, so an interrupted answer can never talk over the next one.

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

### Safety and truthfulness rules (B.2, from the Sep 24 evening log)

- Exact confirmations: tools return a `say` line; a turn made only of actions speaks those lines with
  no LLM call, so she can never misreport an action. Failed actions say so.
- Denial guard: if the model claims it can't see or do something a tool just did, the tool's own
  line replaces the answer.
- Intent guards: each action declares words that must appear in the request (`GUARDS`), so a cut-off
  sentence can't open a website and a chit-chat question can't write a standing instruction.
- Small talk and questions about herself skip tools entirely.
- An argument equal to a tool name is discarded; parameters are named distinctly (`app`, `site`).
- Apps launch without a shell (known apps, URI schemes, or programs on PATH); unknown sites go
  through DuckDuckGo's first result, never a guessed `www.<name>.com`.
- Confirmation gate: skills list tools in `CONFIRM`; MCP tools that aren't read-only ask by default.
  "Yes" runs the held action, "no" cancels, anything else drops it; it expires after 2 minutes.
- Acks are sent before a tool runs; tools run in worker threads, MCP on the event loop.

### Exactness rules (B.3, from the Sep 25 midnight log)

- Weather, time, calculations, budget, and vision answer with exact lines built from the data (`EXACT_TOOLS`
  or `exact: true`); the LLM never rephrases their numbers.
- The user's own words fill tool arguments (`ARG_FILLERS`): "the day after" beats a garbled `day after today`.
- "unavailable" is only believed when the request names an ability (calendar, email, lights...) and no tool ran.
- Follow-ups pass intent guards when the previous turn used that tool ("what about tomorrow").
- Web answers are generated, then checked: significant numbers must appear in the results (rounding allowed);
  one retry, then the source is quoted instead.
- Empty memory gets an exact honest line; EVA.md "About me" lines count as known facts.

### Weather trust rules

- NL/BE/LU use KNMI HARMONIE (`knmi_seamless`); elsewhere Open-Meteo `best_match`. Override with `weather.model`.
- A bare hour 1 to 6 ("at 6", "6:00", even a model-written "06:00") means the evening; the report carries
  `assumed` and she says the time, so a wrong guess is visible.
- Reports always name place, time, date, and source. Missing rain probability falls back to millimetres.
- Debug: `GET /api/weather?city=Tilburg&day=tomorrow&hour=6` returns exactly what she receives.

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
| B.1 | Log fixes | Done. Argument repair, action stop, unavailable, unmute, non-blocking embeddings, real phrasings. |
| C | Kokoro streaming TTS (done) | Server splits streamed tokens at sentence ends, synthesizes each with Kokoro (`af_heart`), sends base64 WAV over the WebSocket; browser plays an ordered queue. Acks synthesized first. Also fixes voice on the phone. |
| D | Weather v2 (done) | Forecast by day and hour ("tomorrow at 18:00 in Tilburg"), any city, from Open-Meteo hourly/daily data. |
| E | Obsidian skill (done) |
| B.2 | MCP + safety release (done) | MCP client, confirmation gate, exact confirmations, guards, vocabulary correction, installable desktop app. | Local only: create, search, read, append, daily note. Vault path in settings. |
| F | TEMPO calendar (done, native) | Agenda, free slots, add and delete (ask first). Setup: docs/GOOGLE_SETUP.md. | Google Calendar: today, range, create, delete (confirm), plan week. Calendar widget. |
| G | SCRIBE email (done, native) | Gmail unread, search, read, draft reply for approval. Email widget. |
| H | Spotify skill | spotipy: play, pause, skip, queue, volume, now playing with real metadata. Premium check at setup. |
| I | Deep research | Harvest im4peace planner/worker research engine as a skill; cited answers. |
| J | FORGE v1 (done) | Claude drafts, AEGIS review, sandbox tests, approve, hot reload. Also: deep thinking skill, budget tool. |
| K | ORACLE (done: reminders, meetings, email alerts, DND, briefing) | Proactive reminders, do-not-disturb, gap detection, hooks from PULSE. |
| L | Pipecat voice | Local Whisper STT, turn detection, barge-in, echo handling. |
| N | Next, in order | 1 UI redesign (docs/DESIGN_BRIEF.md), 2 Spotify skill, 3 deep research into notes, 4 FORGE pull requests with writer/tester agents, 5 Hermes skill importer. |
| M | HERALD | Phone calls/SMS via Twilio on Pipecat. |

Ideas backlog (brainstorm Sep 24, with assessment):

- **MCP client (done in B.2).** Harvested from im4peace (MIT) so any MCP server's tools appear in the
  ToolBelt like skill tools. Evaluate MCP-first for F and G: existing Google Calendar and Gmail MCP
  servers could save most of the build. Each server gets an allowlist entry and confirmation for actions.
- **Desktop app.** Step 1 (done in B.2): eva.html is an installable PWA (manifest + service worker). Chrome or
  Edge then gives her own window and taskbar icon, and the mic keeps working because localhost counts as
  secure. Step 2: a native shell (pywebview or Tauri) only after Whisper STT lands, because embedded
  webviews don't support the browser speech API.
- **Phone app with screen-off wake word.** Needs a native Android app (Flutter or Kotlin) with a
  foreground service and an on-device wake word model, streaming to E.V.A. over Tailscale. iOS does not
  allow third-party screen-off wake words. Interim: the PWA over Tailscale HTTPS gives a screen-on phone
  E.V.A. almost for free. Pipecat and LiveKit both ship mobile client SDKs for the real app.
- **UI design overhaul.** A dedicated design milestone; keep the single-core concept, rethink widgets,
  panels, history, and settings.
- **Her own git account.** A machine GitHub account with a fine-grained token scoped to her repos,
  stored in AEGIS. She pushes to branches and opens pull requests, never to main; Ionut reviews and
  merges. This becomes FORGE's delivery path: build, test in the sandbox, open a PR.

Backlog: a wake word trained on Ionut's voice (openWakeWord custom model plus speaker
verification), and Whisper STT with hint words so names like Radboud and Nijmegen stop
being misheard. Both belong with Milestone L.

Remaining small items: VAULT must count local Ollama calls at EUR 0 (the current
`record_usage` bills unknown models at Sonnet rates), a settings/history panel in
the UI, K.I.R.A. persona switching.

## FORGE v1 as built

Say "build a skill that converts currencies" (or "build it" after she says she lacks a skill). She states the
cost and waits for yes. Claude returns SKILL.md, tools.py, and test_skill.py through a forced tool call; files
land in `skills/_forge/<name>/` with `trusted: false` (the registry never loads that folder). AEGIS review
blocks subprocess, eval/exec, deleting files, undeclared hosts, and non-allowed imports. The skill's tests run
in a separate process with sockets disabled and writes confined to a temp folder. One repair round with the
exact failures. Then: "Shall I install it?" Yes moves it to `skills/<name>/`, sets `trusted: true`, and every
open session hot-reloads its tools. Next: Docker sandbox, and delivery as a pull request from her own git account.

## FORGE v1: original design

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
