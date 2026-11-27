# E.V.A. handoff (for a new Claude session)

Last updated: Friday 27 November 2026, 12:30 (Europe/Amsterdam). Read this first, then EVA.md, docs/ROADMAP.md,
docs/UNDERSTANDING.md and docs/VOICE_TESTS.md.

## Who and what

- Owner: Ionuț (address him as Ionuț; he calls her "she"). Be direct, no em-dashes in anything you write.
- E.V.A. is his local-first JARVIS: a Python server on his Windows PC (`D:\Project E.V.A\eva`, venv `.venv`,
  Python 3.12, NVIDIA GPU, Ollama with qwen2.5:3b-instruct and nomic-embed-text, Whisper small for listening,
  Kokoro voice, Piper "ro_RO-mihai-medium" for Romanian), a web page (`interfaces/web/eva.html`) and a Flutter
  Android app (`apps/eva_android`, Pixel 9 Pro XL, Android 17) that reaches the PC over Tailscale (https :8443).
- Started with `python -m interfaces.web.server_stream` (runs the module as `__main__`; the module aliases itself in
  `sys.modules` and the shared window set lives in `core/device.py` because of that).
- Repo: github.com/IonutBotoroga230036/EVA. Default branch `main` (at 2317c0c: `claude/messages-romanian` was
  merged as PR #1). Current work: branch `claude/commute-focus-findphone` (pushed, not merged yet). Merge it
  (pull request) before starting new work, or branch from it.

## How to work with him (rules that always hold)

1. Check current library versions with a web search before building on them.
2. Tests first, then the full suite (`python -m pytest -q`, about 1140 tests), plus a run with
   `PYTHONUTF8=0 PYTHONIOENCODING=cp1252` for his Windows console.
3. Tests never touch his real accounts or files: no real Claude/Jev keys, Google, Spotify, Telegram, Ollama,
   Piper voice, lights, phone, `data/*.json`. `tests/conftest.py` already isolates these (Claude key, Jev,
   understanding pinned to legacy, romanian translate/voice, message inbox, `data/phone.json`, places,
   meetings, device connections). Keep it that way for anything new.
4. Deliver changed files as a zip with a NEW filename each time (last: `eva_v092_stop_and_local.zip`) plus short
   install steps. Never put `config/settings.local.yaml`, `EVA.local.md` or `config/secrets.env` in a zip.
5. Keys go in `config/secrets.env`, never in chat. The remote token never travels over plain http, never goes in
   URLs from the app, never repeated in chat.
6. Commit to a `claude/...` branch (not main), push, and set the tracking refspec after pushing
   (`git config --add remote.origin.fetch +refs/heads/<b>:refs/remotes/origin/<b>`). Commit trailers:
   `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>` and the session link.
7. The Android app can't be built in the cloud session. Check Kotlin with kotlinc 2.1.0 against an android-34
   `android.jar` (raw.githubusercontent.com/Reginer/aosp-android-jar/main/android-34/android.jar) with small
   stubs for Flutter and WakeEngine. Keep `apps/eva_android/android_overlay/...` and `apps/eva_android/android/...`
   copies identical (`tool_apply_overlay.ps1` copies overlay -> android; Windows blocks it unless
   `Unblock-File`).
8. He tests by voice and sends logs. Read the log carefully: each odd line is usually a bug worth fixing.

## Architecture (where things are)

- `core/orchestrator_hybrid.py`: the turn. `process_stream` (Romanian wrapper) -> `_process_stream`: pending
  confirmation / awaiting answer, `_DANGLING` ("can you..." -> "Yes, sir?"), fast_path regexes, 0a place triggers
  ("when I get home ..."), 0b multistep (a shared "in 10 minutes" applies to every step), 0c `_when_later`, then
  understanding -> ToolBelt (guards, `needs_confirm`, `describe`, fillers, `asks`), fact guard (`unchecked_fact`),
  locked-phone gate (`LOCKED_OK`, `_fine_locked`, `_messages_when_locked`).
- `core/understand/brains.py`: understanding modes legacy|local|jev|claude|auto. Default auto = Claude Haiku 5.5
  (forced tool call, ~0.7 s, EUR 0.0001), then Jev, then local. "Local only" by voice blocks the cloud.
  `core/understand/cache.py` (Nov 23): same sentence + same situation reuses the cloud decision, 7 days,
  personal text in memory only. `core/testmode.py`: "test mode on" caps cloud spend (10 cents) via
  `budget.can_spend`; every turn logs `COST turn: ...` (orchestrator `process_stream` wrapper).
- `core/commute.py` (Nov 23): NS Reisinformatie trips v3 (`gateway.apiportal.ns.nl/reisinformatie-api/api/v3/
  trips`, header Ocp-Apim-Subscription-Key, NS_API_KEY), station codes table, `train_times`, `commute_plan`
  (first Nijmegen event, searchForArrival), ORACLE `leave_alerts` (not in quiet hours' way, skipped when the
  phone is >2 km from home). Field names verified only against a public type definition: first real call is
  the real test.
- `core/focus.py` (Nov 23): events >= 30 min (not all-day/routine) hold message and email announcements except
  vip/urgent; ORACLE `_focus_tick` says what came in afterwards. Server `/api/phone/message` checks it.
- Find my phone (Nov 23): `device.tool_phone_ring`; open window with cap "ring" first, else `device.PENDING`
  for the hands-free service, which polls `GET /api/phone/poll` every 20 s (actions expire after 2 min).
  Android `PhoneRinger.kt` (USAGE_ALARM, max alarm volume, restores it, "Found it" notification).
- Meetings: `meeting_todos_add` (confirm first, yours unless "all", due day at 9 else tomorrow 9, never twice:
  meta `todos_added`), speaker labels via pyannote.audio 4.0.x community-1 when HF_TOKEN is set (`diarize`).
- `core/tools_native.py`: native tools + registration of device, messages, places, meetings tools.
- `core/device.py`: phone actions (`_ask` to the window this turn came from via `CURRENT` contextvar, then the
  next), alarms, timers, phone volume, `phone_ask_unlock` ("work while locked"), `CONNECTIONS`.
- `core/messages.py` (9c): WhatsApp/Instagram via Android notifications. In-memory inbox (24 h, never on disk),
  importance (VIP Mom, urgent words, mentions, questions), announcements, `message_reply` (reply or new message;
  Romanian for Mom; the words must be in what he said now, else she asks "What should I tell Mom, sir?";
  invented/too-long translations refused; "no, change the message" asks again). New messages: the chat's kept
  reply button, else WhatsApp opens with the text (`messages.numbers` in settings.local.yaml) and the optional
  accessibility "E.V.A. send" presses Send for exactly that text.
- `core/romanian.py` (9d): detection, per-sentence Piper voice mixed into Kokoro's clip, translation (Claude if
  cloud allowed, else local), Whisper en/ro detection (`voice/stt.py`), Romanian turns answered in Romanian.
- `core/places.py`: places, arrive/leave rules (reminders or actions decided now), "where did I park",
  opt-in 30-day history, visits. Phone reports via `POST /api/phone/location`.
- `core/meetings.py`: recording (phone hands-free mic in 15 s chunks, or PC mic via sounddevice), shared files
  (Share > E.V.A., Pixel Recorder), Whisper large-v3-turbo on GPU, notes by Claude (or local), saved to
  `data/meetings` and the Obsidian vault's Meetings folder. "What were the to-dos?"
- `core/forge_*.py` + `core/forge_facts.py`: FORGE builds skills (planner, coder, tester, fixer, reviewer);
  facts in tests are checked against the web by Claude (web_search_20250305) before coding. Default forge mode
  auto (Claude when available). `core/github_pr.py`: an installed skill opens a pull request through the
  GitHub API with Ionuț as co-author (`github.pull_requests: true` + `GITHUB_TOKEN`; off by default).
- `core/lights.py`: tinytuya strips; fail fast (2.5 s, 1 retry), rediscover a moved IP once, remember it.
- `core/later.py`, `core/oracle.py`, `core/tempo.py`, `core/google_api.py`, `core/spotify.py` (song/artist
  "radio"; playlist substring matching tightened), `core/phone_prefs.py` (stay dark, ask_unlock).
- `interfaces/web/server_stream.py`: FastAPI + websockets. Phone endpoints: `/api/wakeword/*`, `/overlay`,
  `/api/phone`, `/api/phone/turn`, `/api/phone/message`, `/api/phone/location`, `/api/meetings/{id}/audio`,
  `/api/meetings/upload`.
- Android (`apps/eva_android/android_overlay/app/src/main/kotlin/nl/ionut/eva_android/`): MainActivity (method
  channel, share target), WakeService (foreground mic + location, wake word ONNX, PC verify, voice-only turns,
  meeting recording), WakeEngine, OverlayActivity, DeviceActions, MessageListener (notification listener,
  replies, new messages), EvaLocation, EvaSend (accessibility auto-send), EvaLink (address/token in memory).
  Flutter `lib/main.dart`: menu toggles Hands-free, Messages, Send without tapping, Location.

## State on 27 Nov 2026 (13:00, v0.9.2: local mode diagnostics)

Same branch `claude/v09b-stop-tasks`. His 11:07 log in local mode: 120 s decisions, a 245 s answer. Added
core/localcheck.py (GPU/CPU placement, Ollama timings), 25/30 s local timeouts with an honest line, a 24-tool menu
for the local model, colour-mode dimming. The energy saver task wasn't stopped: it finished (Energy Saver on, 11:04).
Ask him for the `LOCAL:` lines from his next start-up. Full suite 1324 passed, 3 skipped, also cp1252.

## State on 27 Nov 2026 (12:30, v0.9.1: stopping her work)

Branch `claude/v09b-stop-tasks` (from `claude/v09-glance-notes`). His 10:58 log: a computer task resumed after the
restart, he couldn't stop it by voice (understanding was local only, by his earlier voice command, so the local
model made up "disabled"/"stopped") nor in the window. Now a stop reflex, stop checks before every action, Pause /
Resume / Stop on the workbench, Tasks and Projects tabs in the panel, per-sentence claim checks, JPEG screenshots
for Claude (every drop was a big request at ~15 s; drops now log the KB). His brain mode was Local in the status
panel: tell him to tap Auto (or say "Eva, use auto for understanding") or the mind stays off. Full suite 1319
passed, 3 skipped, also cp1252; tests can't reach the internet any more.

## State on 27 Nov 2026 (11:30, v0.9: the glance, memory panel, her notes)

His v0.8 logs (02:06 to 02:17 and 08:48 to 10:03): long answers are read now, retries recovered two line drops,
computer use worked. Problems fixed on branch `claude/v09-glance-notes` (from `claude/v08-streaming-voice`), see
docs/MIND.md v0.9: a story replaced by the claim guard, "continue" playing Spotify, "stop the music" eaten by a
pending question, "move the items from 9 until now" (no ranges), "Nothing has been moved" caught as a claim, a
budget pause from base64 counted as tokens, three heads-ups at once, a made-up preference, project nagging, Kokoro
still on the CPU (now logs why; his torch may be a CPU build). New: core/glance.py, core/review.py (in-memory log,
only reviews saved), memory panel API, morning heads-up, first-sound timing. Tests: tests/test_v09.py; conftest
isolates glance (no weather/train, already greeted) and reviews. Fixed two old test races (a threaded global
manager in a v0.6 test; hello() looked up the chat after "stt"). Full suite 1312 passed, 3 skipped, also cp1252.

## State on 27 Nov 2026 (02:00, v0.8: streaming, voice v2, the 27 Nov fixes)

His first v0.7 session (00:03-01:10) went well: computer use pressed keys (so the toolset API version was accepted),
projects grew by talking. Problems: three task crashes and two mind fallbacks, all "Server disconnected without
sending a response" about 15 s into a call; long answers not voiced (the window's 12 s audio watchdog gave up while
Kokoro, on the CPU, voiced the whole answer as one clip); "let me check, sir" on nearly every mind turn (ack after
0.9 s); "Albert Heim is closed" from an empty list; "yesterday" read today; see_screen answers from the small local
vision model were wrong; "show me the links" said "no 3D models"; "yes" to her Notepad offer became open_file('""').
All fixed on branch `claude/v08-streaming-voice` (from `claude/v07-browser-computer-chats`); see docs/MIND.md v0.8.
Tests: tests/test_v08.py; conftest also stubs pyautogui (no real screenshots), RETRY_WAITS and LINE_WAITS.
Full suite 1290 passed, 3 skipped, also under cp1252. Not testable here: the real stream from the API (format from
the docs, Nov 2026), Kokoro on his GPU, a real line drop.

## State on 26 Nov 2026 (00:30, v0.7: her browser, your computer, settings, chats)

Branch `claude/v07-browser-computer-chats` (from `claude/v06-show-projects`, which holds the "v0.7 prep" commit
with the fixes from his 23:32 log: project_start offers research and "yes" starts it, projects by number and
project_delete, "play the second one on my computer" opens the shown video and pauses music, Nominatim fallback
and brand aliases for the map, look_at_shown attaches the picture, vague FORGE requests resolved). New:
`core/browser.py` (browser toolset, Playwright, checked in real Chromium here), `core/computer.py` (computer
toolset with pyautogui, NOT run on a real screen here: no display; first real test is his 26.7), `core/ui_settings.py`
(gear panel, data/settings_ui.json), `core/chats.py` (Chats drawer, data/chats). The mind gets the browser toolset
when allowed; tasks get it plus the computer toolset for use_computer tasks (`Task.toolsets`). Toolset calls use
anthropic-version `TOOLSET_API_VERSION` (2026-10-21, from the docs' example) with a flip-once fallback on a 400:
if his first browser answer fails with a version error, look at `core/claude.py._send`. Tests: tests/test_v07.py;
conftest isolates the settings file, her browser (fake), the screen (no pyautogui) and chats. Full suite 1261
passed, 3 skipped, also under cp1252. Docs: MIND.md v0.7, VOICE_TESTS 26, CAPABILITIES, ROADMAP.

## State on 25 Nov 2026 (23:20, v0.6b: a task stuck on budget)

His 23:14 log: the desk display task (86396a4c) had paused when the daily limit (then 1.50) had 17 cents left and a
Sonnet turn can cost up to 17 cents (16000 max tokens). He raised the limit to 3.00, but "carry on" went to
task_resume, which only knew "paused" ("isn't paused, it's waiting"), and the status read the raw
BudgetExceeded text. And "what's my budget" lost its answer to the calendar fact guard ("Local work is free"
matched "is free"). Fixed (zip `eva_v060b_budget_pause.zip`): budget pauses are waits with consent_kind "budget"
and a plain message (`_budget_words`); resume() answers a waiting task ("Carry on."); at start, budget-paused tasks
go on by themselves when there's budget (`_budget_ok`); `_add_user` merges an answer into a trailing user message
(a pause before her first reply or after tool results left two user turns in a row: the API rejects that); the
fact guard skips a tool's own line and treats "free"/"open" as calendar claims only next to a day or calendar word.

## State on 25 Nov 2026 (23:00, v0.6 showing, projects, places)

His 21:36 log: "much better", but no MIND line at all: understanding handed sentences over (work_it_out) and the
mind refused them silently. His saved brain state is mode local with understanding on Claude; the mind (a feature
he had never chosen) followed the stricter rule. Now `mind.why_off()` follows understanding's cloud permission
unless the mind itself is set local, and logs the reason once. Also from the log: FORGE said "Your skill is ready"
after he cancelled (job.say kept the ready line; now "I stopped the build of ...", and forge_status returns the
request for a rebuild); task titles came from a regex ("prices online for a small"; now Haiku, `name_task`); the
workbench pill showed empty because `.bench-pill{display:flex}` beat `[hidden]`. Built (zip
`eva_v060_show_projects.zip`, docs/MIND.md v0.6 section): core/show.py, core/projects.py, core/nearby.py, cards
gallery/media/project in eva.html (pictures in eva_classic), Task.project, project brief in the mind's situation
and in task instructions, results back into the project. Not reachable from the build machine: the map servers
(nearby is tested with recorded answers only). He also got an Excel file (outside the repo) with the full
checklist (66 items, status), the JARVIS test (22 scenarios, 0 to 2 per version, totals and per-dimension
scores) and 12 proposals; docs/JARVIS_TEST.md has the scenarios. Not done yet from his wishes: long stories
(proposal), her own sandboxed browser (proposal), computer use (proposal, later), chats like Gemini (v0.7).

## State on 25 Nov 2026 (21:00, v0.5 one mind + the workbench)

He wants EDITH/J.A.R.V.I.S.: she should think along, show her work live, remember across chats, take on a real
project (a transparent desk screen). Plan agreed and written into ROADMAP ("Toward EDITH"): v0.5 one mind + the
workbench (built now), v0.6 visual research ("something like this?") + projects, v0.7 chats like Gemini.
Built (zip `eva_v050_mind_bench.zip`, docs/MIND.md): `core/mind.py` + `HybridOrchestrator._mind`: when no tool
ran (or understanding picks the new `work_it_out` tool, or "unavailable") and it isn't personal chat
(`mind.is_chat`), Claude Haiku 5.5 (effort medium) runs a tool loop with every belt tool (cached), the situation
and recalled facts; `think_harder` switches to Sonnet; actions stop at `_ask_confirmation`; fact and claim guards
apply; failures fall back to the old path with whatever was gathered. Feature `mind` (auto) in brain FEATURES;
local only turns it off. `core/pc_tools.py`: check_installed (PATH/Program Files + the Windows file association),
install_app (winget, CONFIRM), open_file (task files, document types only, optional program, never NEVER_RUN).
The conversation lane keeps personal chat only (it used to take "should I", "advice", "what would you"). Jev is
unchanged: it stays a picker and can pick work_it_out. Workbench: `core/agent/live.py` (emit/state/recent, labels),
hands and runner emit doing/did/plan/code/output/model/say/status; PULSE `agent.live` -> `send_live` ->
`task_live`; `GET /api/tasks/live`, `GET /api/tasks/{id}/live`, `GET /stage`; eva.html bench (plan, activity,
Model/Code/Output tabs, scan line, pill, stage mode). Fixed on the way: in v0.4c the model card's viewer sat in
the bridge script, where h() doesn't exist and the card couldn't see it, so the card would have failed; both now
live in the UI script (a test checks the split) and the real page was run in Chromium with a fake socket at desk,
phone and stage sizes. The calendar fact guard no longer reads "Nothing on your PC" as "nothing on (the calendar)".

## State on 25 Nov 2026 (19:40, the first finished guitar stand)

Second run (19:24 to 19:37): the guitar stand finished in 7 steps for 8 cents (trimesh + numpy after his yes, two
STLs delivered, two separate copy questions). Rendered here, the design is weak: a 210 x 190 mm plate with a cradle
and a 200 mm upright T post, so a Strat stands nearly vertical on a small footprint; she couldn't see it. He then
asked four times to see the model; understanding had no tool for it (task_status read the whole markdown summary
aloud, see_screen said "No."). Fixed (zip `eva_v040c_see_it.zip`): `core/agent/preview.py` (STL to PNG, three
views, depth buffer and crease lines, numpy + Pillow only); hand `preview_model` returns the picture to Claude as
an image block in the tool_result (`_size` counts a picture as 3000, `_compact` drops old pictures); the system
prompt asks her to look at every model plus an assembly and to check stability with numbers; `deliver_file` takes
`paths` (one question); `finish` has `summary` (spoken, `runner.spoken` keeps two sentences, no markdown) and
`details` (screen); finishing ticks the plan off; `task_show` (model card, `on_pc` opens Explorer); PC methods
`preview` and `file_b64`; `GET /api/tasks/{tid}/file` (task folder only, model/png/md/txt/scad); the window's
`model` card (WebGL viewer in eva.html, pictures in eva_classic.html); a finished task's announcement carries the
card. FORGE: the planner knows skills can't control the mouse, keyboard or screen (feasible=false: has to be
built in), and the crew stops when a fix still imports a module skills can't use (cursor control cost 26 cents
over 4 rounds plus a review). Starting E.V.A. twice now says "Port 8001 is already in use". Next idea from him:
E.V.A. controlling the cursor (a built-in computer-use tool with consent, not a FORGE skill; pyautogui is already
a dependency).

## State on 25 Nov 2026 (19:15, first real task)

His first guitar stand (log 25 Nov 19:08 to 19:13): task_start with consent worked, she asked bed size and guitar
(Squier Strat), his answer resumed her. Then a 400 "Invalid `signature` in `thinking` block ... bound to a different
conversation" killed it. Cause: the task's system prompt held `%H:%M`, so it changed every minute; Sonnet 5.5 binds
each thinking block to its prefix (system, tools, earlier messages), and the cache never hit either. Fixed (zip
`eva_v040b_first_task.zip`): the system prompt is written once per task (`Task.system`) and resent unchanged;
agent calls send adaptive thinking with `block_binding.prefix_mismatch_behavior` "error" plus the beta header
`thinking-binding-controls-2026-08-01`; the binding error becomes `ThinkingBound`, and the task then switches to
"drop_block" for good (`Task.drop_thinking`), as after `_compact` trims; `agent.max_tokens` 4000 -> 16000 (the
third turn was cut at 4000 and read as a question) and a `max_tokens` stop now carries on with a note instead of
asking him; `agent.effort` medium. Also: "any other questions?" while a task runs gets "Not right now"; "Sir,
about X: Sir, ..." fixed; a failed task can be resumed ("try again"); task updates are spoken in the window he
last talked to and only shown in the others (he heard one twice with the phone app and a browser open); Windows
ConnectionResetError tracebacks from dropped connections are no longer printed; the manifest link sends the
pairing cookie (`crossorigin="use-credentials"`, it got 401).

## State on 25 Nov 2026 (v0.4 "Agent", built overnight)

Branch `claude/v04-agent` (from `claude/commute-focus-findphone`, which isn't merged yet: merge that first),
zip `eva_v040_agent.zip`. Untested on his PC. See docs/AGENT.md. New: `core/agent/` (tasks, consent tiers and
standing permissions, the PC as a device with LocalPC/RemotePC and the `/ws/pc` link, `interfaces/pc_agent.py`,
hands, the loop on Sonnet 5.5 with prompt caching, conversation tools), `core/understand/retrieve.py` (tool
retrieval past 150 tools), ClaudeClient returns every tool_use and the raw content and counts cache tokens.
Task questions/consents reach every window (pending/awaiting primed, `on_no` for a task's no), the dark phone
session and Telegram (Yes/Always/No buttons). The guitar stand is a scripted end-to-end test
(`tests/test_agent.py`); the real one is VOICE_TESTS section 23.
Rule from now on: fast-path regexes only for reflexes; everything else goes through understanding.

## State on 25 Nov 2026 (01:00)

Second run: commute planning from his "Train To Nijmegen" event and "no more trains tonight" worked. FORGE built
currency_converter; its tests passed against a fake service, it was installed, and every real call failed
(api.frankfurter.app moved to api.frankfurter.dev/v1). Fixed (zip `eva_v034c_live_check.zip`): FORGE now calls a
network skill once for real before offering it (`forge_engine.live_check`: own process, network only to the
declared hosts, a probe of the service URL with status/redirect/body for the fixer, a redirect to a sister host
is allowed; "trying it for real" stage); `skills/currency_converter` rewritten by hand (frankfurter.dev/v1,
euro/lei/dollars names); "can you do it locally" to the build question now builds locally (`Brain.override`,
`Job.where`) instead of counting as yes for Claude; Spotify "no active device" no longer presses play/pause.

## State on 25 Nov 2026 (00:30)

His first v034 run (log 24 Nov 23:57 to 00:10): NS works (field names confirmed against ns.nl), phone polling
works, recording from the phone worked (first Whisper large-v3-turbo download), FORGE built and installed
moon_phase (cost 40 cents). Fixed on the same branch (zip `eva_v034b_log_fixes.zip`): trains that already left
were offered; his own "Train To Nijmegen" event was treated as a lecture (now it IS the plan; courses are
found by code like SOW-BKI141 or the Radboud calendar; campus_min 20 for the bus to RU; trains elsewhere that
day are mentioned); after midnight "tomorrow" = the day he wakes up to (commute only so far); Spotify
'Restriction violated' made the media key START paused music; test mode ended at midnight (now 12 hours,
spending counted across days); FORGE quoted 10 to 20 cents (now from real past job costs, `Job.cost_eur`);
the phone's wake clip cut commands off ("when's the next...") so `wake_command` keeps a cut-off start and
`merge_partial` joins it to what he says next; "Have you ever ..." at the start = "Hey Eva".
Still open: CORTEX embedding timeouts (`ollama ps`), GitHub PRs need his token + `github.pull_requests: true`.

## State on 23 Nov 2026 (afternoon)

Built on `claude/commute-focus-findphone`, zip `eva_v034_commute_focus.zip`, all untested on real hardware:
understanding cache + test mode, NS commute (needs NS_API_KEY), focus mode, find my phone (needs the new app
build), meeting to-dos into reminders/calendar, optional speaker labels. Kotlin compiled with kotlinc 2.1.0
against android-34 with stubs. ORACLE now reads the calendar in quiet hours too (for 07:xx leave alerts) and
looks 4 hours ahead.

## State on 23 Nov 2026 (morning)

Working on his phone: hands-free wake word with PC verification, overlay, alarms/timers locked, phone volume,
messages reading and announcing, Romanian voice, new message to Mom (chat opens with the text; auto-send just
added, untested on a real phone). Installed by him up to `eva_v033d`; `eva_v033e_autosend.zip` was just sent.

Not yet tested on a real phone: auto-send button press (WhatsApp view ids `com.whatsapp:id/entry` and
`:id/send`), location reminders, meeting recording, Share > E.V.A., FORGE fact checks and GitHub PRs with
real keys.

Known open points:
- Ollama sometimes slow ("CORTEX: embedding slow"); he reinstalled the NVIDIA driver. Check `ollama ps`.
- Reusing a reply button after its notification is gone may or may not work in WhatsApp.
- The PC's announcement of an important message isn't confirmed by voice yet (a log line was added).

## Next (agreed list)

1. His test logs: v034 (NS key, find my phone with the new app build, focus mode in a real lecture), v034c
   (currency), and v0.4: the guitar stand for real (VOICE_TESTS 23).
2. After the real guitar stand run: fix what it shows (prompt, hands, consent wording, costs).
3. v0.4 follow-ups: a Docker sandbox for approved programs; tasks that use the phone (photos, location);
   a task view in the UI (plan, files, log); vision for checking a rendered model (OpenSCAD --render to PNG).
