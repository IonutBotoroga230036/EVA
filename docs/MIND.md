# v0.5: one mind and the workbench

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
