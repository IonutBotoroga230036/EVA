# v0.5: one mind and the workbench

> v1.0: the mind became the one brain, which now decides every turn (local first, Claude when stuck). See
> docs/ONE_BRAIN.md. What follows describes the classic routing (`one.mode: classic`).

## One mind

Before v0.5 every sentence went the same way: a quick model (Claude Haiku, or Jev as backup) picked ONE tool, the
tool ran, and E.V.A. read out the tool's own sentence. When nothing fitted, a small local model chatted back.
Nothing in that path could think "he wants to look at an STL; nothing on his PC opens STL files; I could show it
here or install a viewer". That is why she felt stiff.

Now:

```
"lights off" / "next train to Nijmegen" / "remind me at 5"
    -> fast path or understanding (Haiku, Jev, local) picks the tool -> it runs -> her line   (as before)

"how can I see the files?" / "should I print it at the library?" / "what would you do?" / vague / several steps
    -> understanding says work_it_out, or no tool ran, and it isn't personal chat
    -> the mind (core/mind.py): Claude Haiku 5.5 with every tool, the conversation, the situation (time, open
       tasks, what the last task made, focus), what you told her before; think_harder switches to Sonnet
    -> she looks things up, checks your PC (check_installed), offers options, asks one question,
       opens files (open_file), installs apps after your yes (install_app), starts tasks for big jobs
```

| Part | Where |
|---|---|
| The loop | `HybridOrchestrator._mind` (core/orchestrator_hybrid.py): up to `mind.max_steps` tool rounds |
| Instructions, tools, situation | `core/mind.py` (the instructions never change within a session, so the cache holds) |
| Hands on the PC | `core/pc_tools.py`: check_installed, install_app (always asks), open_file (task files only, documents only) |
| Handing over | the `work_it_out` tool in understanding's list; Jev and Haiku can pick it |
| Chat stays chat | feelings, herself, "let's talk" still go to the conversation lane (`mind.is_chat`) |

Safety is unchanged: anything that acts goes through "Just to confirm, sir" (the turn ends there, your yes runs
it); the fact guard (no calendar, email or weather facts without a check) and the claim guard (no "done" without
an action) apply to her answers too. Local only, no key or no budget: the old way, exactly as before. Off:
`mind.enabled: false`, or "use local for the mind".

Cost: Haiku 5.5 is $0.10 per million input tokens; one thought with all tools is a fraction of a cent (the tool
list is cached). Sonnet only when she decides it's hard.

## The workbench

While a task works, the window shows a live stage (`core/agent/live.py` -> PULSE `agent.live` -> `task_live` to
every window):

- the plan, ticking off; what she's doing right now ("Writing stand.py", "Looking at assembly.stl")
- the activity feed, with what she says between steps
- tabs: Model (each new version turning in 3D as soon as she makes it), Code (what she's writing), Output (what it
  printed, red when it failed)
- a scanning line while she works; status Working / Needs you / Done

It opens by itself when a task starts (close it and a small pill at the top keeps showing what she's doing; the
wrench button brings it back). `/stage` is the same thing full screen, for a second monitor and later the desk
screen. A window that opens mid-task loads everything so far (`GET /api/tasks/live`, `GET /api/tasks/{id}/live`).
Nothing on the workbench is spoken; she still speaks only at milestones, questions and the result.


## v0.6: showing, projects, places

| Part | Where | What |
|---|---|---|
| The mind's gate | `mind.why_off` | It follows understanding: on whenever understanding may use Claude, unless you set the mind itself to local ("Eva, use local for the mind"). When it's off, the log says why once: `MIND: off (...)`. On 25 Nov it stayed off all evening because the saved brain state was "local" with only understanding on Claude. |
| Pictures and videos | `core/show.py` | show_images, find_videos (ddgs 9.16 images/videos, safesearch moderate), open_shown (big in the window or in the PC browser), look_at_shown (she downloads that one picture, public addresses only, 5 MB, and sees it). What's on screen is in the situation, so "the second one" works. look_at_shown always goes to the mind. |
| Projects | `core/projects.py` | data/projects/<id>.json: goal, decisions, ideas, open questions and answers, research, parts (qty, price, link, why, status), links, tasks. The active project's brief is in every mind turn; task_start(project=...) puts it in the task's instructions and the result comes back as research. project_export writes parts.csv and project.md to Documents/E.V.A./projects/<name>. |
| Places with hours | `core/nearby.py` | OpenStreetMap Overpass (overpass-api.de, fallback overpass.private.coffee): POST body, a User-Agent and Referer, ten minutes of cache, two seconds between calls. Opening hours read for the common forms (days, times, several spans, past midnight, off, 24/7); unusual ones are passed on as written. Where: the phone's fix, else saved home, else the city centre. |
| Task names | `tasks.name_task` | Haiku names a task in two to four words; the regex is the fallback. |

## v0.7: her browser, your computer, settings, chats

| Part | Where | What |
|---|---|---|
| Her own browser | `core/browser.py` | Claude's browser toolset (`browser_toolset_20260801`, no beta header) run with Playwright 1.65 in a separate Chromium profile (`data/agent/browser_profile`, none of your sign-ins). The mind gets it when `browser.enabled` and Playwright are installed: a few steps inside an answer ("check the price on the Prusa site"), then the last page shows in the window. Tasks get it too. Refs `ref_N` map to Playwright's `aria-ref=eN`. It refuses payments and orders (pay, buy, checkout, afrekenen, bestellen, betalen...), password and card fields, card numbers, your network (LAN, localhost, names that point home) and JavaScript. Every result echoes `toolset_name`; after a failed action the rest of that batch is "Not executed". |
| Your computer | `core/computer.py` | Claude's computer toolset (`computer_toolset_20260801`) run with pyautogui, only as a task you said yes to (`use_computer` always asks). Screenshots are scaled to at most 1366 x 800 and clicks scaled back. Settings: off, watch (screenshots only), ask. pyautogui's fail-safe: push the mouse into a corner and the task is cancelled at once. She never types card numbers; the task rules forbid passwords, payments, sending, deleting and account changes without ask_user. The workbench's Screen tab shows what she sees. `open_settings` opens a Windows settings page directly (ms-settings: URIs). |
| Settings in the window | `core/ui_settings.py`, the gear button | Budget limits, test mode, brain switches (same state as saying them), mind, tasks, her browser, computer use, focus. Saved to `data/settings_ui.json` on top of your YAML files (never rewritten). Every value is checked; POSTs need the `X-EVA: 1` header so another site can't change them. |
| Chats | `core/chats.py`, the Chats drawer | Every window turn goes into a named chat (`data/chats/<id>.json`). A window opened within two hours carries on in the last chat; "New chat" starts fresh. Titles by Haiku (a few dozen tokens) when the cloud is allowed, else the first words. Search, rename, delete in the drawer. What she knows about you (CORTEX) is shared by every chat; a chat only decides which recent turns she has in front of her. `find_in_chats` lets her look through earlier chats ("what did we decide in the guitar chat?"). |

## v0.8: she speaks while she thinks, and a dropped line is not a crash

| Part | Where | What |
|---|---|---|
| Streaming | `core/claude.py` (`read_stream`, `_once`, `_retrying`) | Every Claude call streams (server-sent events). On 27 Nov all three task crashes and both mind fallbacks were "Server disconnected without sending a response", about 15 s into a call: something on the line drops quiet connections. A streamed call is never quiet. A call that breaks before any words arrived is sent again after 1.5 s and 4 s (also on HTTP 429/5xx/529 and overloaded stream errors); once words were delivered it raises `ClaudeCut`. Plain JSON answers (test fakes) still work. |
| Tasks | `core/agent/runner.py` (`LINE_WAITS`) | After the client's quick retries, a task waits 10 s and 30 s and tries again; then it waits with consent_kind "network" ("Say 'carry on' when the internet is back") instead of failing, and goes on by itself after a restart. |
| The mind's voice | `HybridOrchestrator._mind`, `_streamed`, `_Speech` | The mind's words come as they are written; each finished sentence goes through the claim and fact guards and is spoken at once. A risky sentence is held back and the whole answer goes through the usual guards (a replaced answer is spoken instead). Words she writes before a tool ("Let me check the Prusa site") are spoken too. A dropped line says so honestly instead of letting the old way answer (it promised a task it never started). |
| Speed | `mind.chat_effort` | Plain talk (no tool hint) thinks at "low"; working answers (work_it_out, projects, the screen) at `mind.effort`. "One moment, sir" only for working answers still silent after `mind.ack_after_s` (2.5 s); tool acks say what she does ("Checking the weather, sir") after `voice.acks.after_ms` (1500). "Let me check, sir" is gone. |
| Long answers | `voice/speech.py` | Finals are voiced sentence by sentence; `audio_pending` tells the window a sentence is on its way (its 12 s watchdog gave up on long answers, which were one clip on the CPU). Kokoro runs on the GPU when there is one (`voice.tts.device: auto`, falls back to the CPU). "Eva, stop" over her voice just stops her (`_SHUSH`, a `quiet` event): not the music, not a new turn. |
| Computer use | `core/computer.py` | `computer.mode: auto` ("Act without asking" in the settings): use_computer starts at once; the corner stop and the never-list stay. A goal about a settings page opens it first (ms-settings:), and the task rules say how to open pages with Win+R and to batch sure actions. |
| Fixes from 27 Nov | | Places: the question isn't the shop name ("Is the Albert Heim still open?" is Albert Heijn), all closed says closed with when it opens, map servers asked at once (7 s max). Calendar: "yesterday", "the 26th", "You had" for past days, "today, Friday the 27th" after midnight, calendar plus anything else goes to the mind. The screen: see_screen goes to the mind with the screenshot. task_show shows a result's links and files. His answer to her question goes back to the mind (no `open_file('""')`, no splitting "yes, but..."). A site named by what was said before ("the projector's shop page") goes to the mind. "I've ordered" counts as a claim. |

## v0.9: the glance, what she knows, her notes, and the 27 Nov morning fixes

| Part | Where | What |
|---|---|---|
| The glance | `core/glance.py`, `GET /api/glance`, the window | When nothing has happened for 45 s, a row of chips under the orb: weather, the next event, the train (only on a Nijmegen day), and one of a running task, her unread notes or the project. `/stage` (the second monitor, later the desk screen) shows it full screen with a big clock whenever no task is running. Cached; no cloud model; the calendar comes from what ORACLE already polls. |
| What she knows | `Cortex.edit`, `forget_id`, `/api/memory`, Settings > What she knows | Every fact, searchable, with where it came from (you told her / she picked it up); edit or forget each (forget asks twice). Secrets (passwords, API keys by their shape, card numbers) are refused here too. |
| Her notes | `core/review.py`, Settings > Her notes | Today's log lines worth reading (his words, her answers, tools, the mind, warnings; secrets hidden) are kept in memory only. After 23:00, once he's been quiet 20 minutes, Claude Haiku writes her review: a headline, each mistake (what, why, fix) and what went well; only that is saved (data/reviews). "Review today now" and the tool `review_day` ("how did you do today?") run it at once. "Copy for Claude" gives text to paste instead of a whole log. |
| Morning heads-up | `glance.greeting_once` | The first thing he says between 05:00 and 12:00 gets "Good morning, sir. It's 7 degrees and cloudy, up to 9. First up: ... at 10:30, and 4 more after it." plus the train and unread notes, once a day. |
| Stories | `creative()`, `_Speech(words=True)` | Stories, poems and "N minutes" talks are words, not claims: no claim or fact guard (02:08: a story with "had been sent" became "No, sir, I haven't done that"). "A story for N minutes" gets room for N x 140 words in one go (up to 25). "Continue" after her story or question goes back to her (it played Spotify). |
| Claims | `claims()` | Negated sentences are not claims ("Nothing has been moved", 10:01). "I've ordered" is. |
| A new command during her question | `_NEW_STOP` | "Stop the music" while she asks "Shall I go ahead?" drops the question and stops the music (09:51 it said "Cancelled" and the music played on). |
| Calendar ranges | `tempo.shift_events(from_time, until_time)`, `agenda(include_past)` | "Move the items from 9 until now 30 minutes later" moves exactly those; the question says "move the events today from 9:00 until now 30 minutes later"; a shift without an amount goes to the mind to ask. |
| The budget | `claude._tokens_in` | A screenshot counts as about 1,600 tokens, not a third of its base64 (10:03: the computer task "could cost more" than 91 cents). |
| The line | `claude.FIRST_BYTE_S`, `inference.cloud.http2` | An answer that hasn't started after 9 s is asked again (two calls hung 15 s at 10:02); every drop logs how long it took and in which phase. HTTP/2 is a switch to try. |
| Small ones | | Heads-ups at the same minute are one sentence; the fact extractor no longer turns requests into preferences ("Prefers Kanye West while working"); she doesn't tack project questions onto a thank-you; `ECHO: first sound N s after you finished` answers "is she faster?"; Kokoro says why it's on the CPU. |

## v0.9.1: stopping her work, and smaller screenshots

On 27 Nov at 10:59 he said "Can you drop the energy saver settings thing?" and "can you stop this?" while a computer task clicked on; understanding was set to local only, the local model answered "Energy saver settings disabled." and nothing stopped. Now:

| Part | Where | What |
|---|---|---|
| The reflex | `stop_task()` in the fast path | "Drop / cancel / stop / abort / forget the <task>" cancels the task it names (any unfinished one); "stop this / that / it" cancels the one that's working right now. Music words ("stop the music"), a bare "stop" and no working task leave it alone. Works in local mode too. |
| A stop is a stop | `Manager._stopped()` | A running task checks before every action and after every retry wait: a cancel or pause from the window or by voice wins even when Claude's answer was already on its way. The skipped calls get "Not done: he cancelled the task." so a paused task can resume cleanly. |
| The window | the workbench, Settings > Tasks, Settings > Projects | Pause, Resume and Stop on the workbench. Tasks lists what's going on and what came earlier, with Show, Pause, Carry on / Try again and Stop. Projects lists every project with its decisions, open questions and parts (links), and Delete (asks twice). `GET /api/tasks`, `POST /api/tasks/{id}/cancel|pause|resume`, `GET /api/projects`, `POST /api/projects/{id}/delete`, all POSTs with `X-EVA: 1`. |
| Claims | `claims()` per sentence | "Energy saver settings disabled." / "... stopped, sir." with nothing done is a claim now; "Nothing was stopped" isn't. |
| The 15-second drops | `computer.jpeg_block` | Every drop in the 10:58 log happened at about 15 s while a computer task's request (several PNG screenshots) was still being sent. Claude now gets JPEG screenshots (a few times smaller; the window keeps the PNG), and a drop logs the request size in KB. |
| Tests | conftest | httpx.get and httpx.post raise in tests unless a test fakes them (a map test reached Nominatim). |

## v0.9.2: local mode that says what's wrong

27 Nov 11:07 to 11:16, understanding local only: a decision took 120 s and failed, an answer 245 s ("I had trouble forming that reply"). A 3B model on a GPU answers a decision in a second or two, so Ollama was slow, not the code: likely the model partly on the CPU (6 GB of VRAM shared with Whisper, the embedding model and other apps) or models swapping. Now:

- `core/localcheck.py`: at start-up and after a slow call the log says where each model sits (`LOCAL: qwen2.5:3b-instruct is 100% on the GPU (2.4 GB)`, or a warning when part is on the CPU), and every local call logs Ollama's own timings (`LOCAL: decision 1.6 s (load 0.0 s, 2950 prompt tokens in 0.9 s, 24 out)`).
- Timeouts: 25 s for a local decision, 30 s until a local answer starts (was 120 s each). After a timeout she says so ("My local brain isn't answering in time, sir...") instead of trying another two minutes.
- The local model chooses from the 24 tools that fit the sentence (`retrieve.select(above=, keep=)`), not all 72.
- Lights: "dim the lights" on a strip in colour mode (Tuya error 907) dims the colour's V (`set_hsv`, tinytuya 1.21.1).
- Honest scope: on 6 GB of VRAM a 3B model is fine for one-step commands (music, lights, volume, timers) and weak at multi-step tool use; the mind stays on Claude until a bigger local model fits.
