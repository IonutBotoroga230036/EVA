# E.V.A. roadmap

Principle: **local by default, Claude by choice.** Every "heavy brain" feature (conversation, planning,
deep thinking, FORGE, research) runs on local models unless you switch that feature, or everything, to Claude.

## v0.2 (done)
Hybrid brain with fast paths and exact answers, CORTEX memory, EVA.md personalization, Kokoro voice,
weather (KNMI), calculator, currency and crypto, Obsidian notes, vision, Google Calendar and Gmail with
importance triage, Spotify on any device, ORACLE (reminders, routines, meeting and email alerts,
do-not-disturb, briefing), Telegram channel, FORGE v1, Claude Design interface, lights and moods
(waiting for strip pairing), safety guards (intent guards, confirmations, no fake claims).

## v0.2.5 "Voice and Brain" (next)
| # | Milestone | What you get |
|---|---|---|
| 1 | **Pipecat voice pipeline** (built, awaiting hardware test) | Server-side listening: your mic streams to E.V.A., local Silero VAD, local Whisper, smart turn detection that waits through a thinking pause, a rolling buffer so your first words are never lost, barge-in while she speaks. Works in any browser, and later in the phone app. Replaces the browser's speech recognition. |
| 2 | **Local network safety** (built, awaiting hardware test) | The server listens only on this PC by default, with a token for any remote device. Needed before the phone app. |
| 3 | **Multi-step commands** (built, awaiting hardware test) | Any number of commands in one sentence, run in order or in parallel, with dependent steps passing results along: "lights red, play The Weeknd, Spotify at 50 and the PC at 100" or "read Tom's last emails and draft him a warm reply saying X, Y, Z". One combined answer. |
| 4 | **Conversation lane** (built, awaiting hardware test; on/off switch) | Longer, reflective conversations ("what would make you more useful to me?") with a warmer prompt, longer answers, follow-up questions, and knowledge of her own abilities and roadmap. Tasks keep the fast lane. |
| 5 | **Routines panel** (built, awaiting hardware test) | A section in the interface for routines, reminders, and moods: create, edit, pause, delete, and a week view. Plus a shopping list ("add milk to my shopping list"), kept as an Obsidian note. |
| 6 | **FORGE background jobs** (built, awaiting hardware test) | Builds run in the background with no time limit and a Telegram push when done ("Install?" Yes / No). Default local coder: qwen2.5-coder:14b. |
| 7 | **Local-first switches** (built, awaiting hardware test) | Default brain mode `local`. Per-feature choice (conversation, planning, thinking, FORGE) in settings and in the status panel. |

## v0.3 "Mobile and Autonomy"
| # | Milestone | What you get |
|---|---|---|
| 8 | **Private remote access** (built, awaiting hardware test) | Tailscale: your phone reaches E.V.A. at home securely from anywhere, no open ports. |
| 9 | **E.V.A. for Android** (Flutter shell around the real interface: same orb and panels, mic, voice, optional long-press) | Talks to the v0.2.5 voice pipeline. Set as your default assistant (long-press power), an on-device "Hey Eva" wake word in a background service, push notifications, cards, and location for store reminders. Runs alongside "Hey Google". |
| 10 | **ORACLE v2: proposals** | She plans with you: "Sir, you have a free evening tomorrow and haven't worked on ZippZapp this week. Plan a session, or relax since it's the weekend?" Project tracking, weekly review. |
| 11 | **Deep research** | Research across your notes and the web in the background, with a cited report written to Obsidian and a notification when it's ready. |
| 12 | **Voice identity** | A custom "Hey Eva" wake word trained on your voice, and speaker verification so she only takes commands from you. |
| 9b | **Android, hands-free** (wake-word recorder built; model training and the screen-off service next) | On-device "Hey Eva" in a foreground service (screen off), a VoiceInteractionService overlay above the current app, push notifications. |
| 9c | **Your phone as E.V.A.'s hands** (alarms and timers built; messages next) | While the app is connected, the phone offers E.V.A. tools she can call: set alarms and timers, open apps, navigate, and (with your one-time permission) read incoming WhatsApp, Instagram and SMS notifications so she can tell you which new messages matter. Replies and calls only after your yes. Uses Android's own intents and the notification listener, never screen-scraping. |
| 9d | **Romanian mode** | Piper's Romanian voice next to Kokoro (chosen per sentence), Whisper in Romanian when you switch ("Eva, hai să vorbim în română") or when you dictate to a contact marked Romanian (Mom). |
| 10b | **Actions later** (built, awaiting hardware test) | "Turn the lights off in 5 minutes, stop the music in 10": ORACLE runs the tool at that time, with the same confirmation rules at the moment it runs. |
| 13a | **FORGE Code: E.V.A. edits her own projects** | "Eva, make the orb bigger in the phone app": she reads apps/eva_android, proposes a diff, runs `flutter analyze` and the tests, shows you the change, and applies it on a git branch only after your yes. Never on main, never unattended. |
| 13b | **FORGE crew** (built, awaiting hardware test) | Separate roles instead of one model grading its own homework: a planner, a coder, an independent test writer, and a reviewer. Sequential on the local GPU; parallel only with Claude. |
| 13 | **FORGE v1.1** | Skills delivered as pull requests from her own GitHub account, a Docker sandbox, and a Hermes skill importer through the same safety review. You approve every merge. |

## v0.4 "Presence"
| # | Milestone | What you get |
|---|---|---|
| 14 | **HERALD phone calls** (Twilio) | She can call you, and take or make calls on your behalf. |
| 15 | **Native desktop app** (Tauri) | Tray icon, global hotkey, starts with Windows. |
| 16 | **Location automations** | Arriving home sets your "home" mood; at the store she texts your shopping list. Uses Android geofencing (the phone wakes only when you cross a boundary), not constant GPS, so the battery lasts. |
| 17 | **Smart home expansion** | More devices, and a Home Assistant bridge. |

Anytime: travel skills like NS train times make good FORGE tests.

## What the phone app needs first
The voice pipeline (1) so the app just streams audio to E.V.A., network safety (2) and private remote access (8)
so the phone can reach her securely, and a stable API for cards and push. With 1 and 2 done in v0.2.5,
the app can start right at the beginning of v0.3.
