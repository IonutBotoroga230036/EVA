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
| 4 | **Conversation lane** | Longer, reflective conversations ("what would make you more useful to me?") with a warmer prompt, longer answers, follow-up questions, and knowledge of her own abilities and roadmap. Tasks keep the fast lane. |
| 5 | **Routines panel** (built, awaiting hardware test) | A section in the interface for routines, reminders, and moods: create, edit, pause, delete, and a week view. Plus a shopping list ("add milk to my shopping list"), kept as an Obsidian note. |
| 6 | **FORGE background jobs** | Builds run in the background with no time limit and a Telegram push when done ("Install?" Yes / No). Default local coder: qwen2.5-coder:14b. |
| 7 | **Local-first switches** | Default brain mode `local`. Per-feature choice (conversation, planning, thinking, FORGE) in settings and in the status panel. |

## v0.3 "Mobile and Autonomy"
| # | Milestone | What you get |
|---|---|---|
| 8 | **Private remote access** | Tailscale: your phone reaches E.V.A. at home securely from anywhere, no open ports. |
| 9 | **E.V.A. for Android** (Flutter) | Talks to the v0.2.5 voice pipeline. Set as your default assistant (long-press power), an on-device "Hey Eva" wake word in a background service, push notifications, cards, and location for store reminders. Runs alongside "Hey Google". |
| 10 | **ORACLE v2: proposals** | She plans with you: "Sir, you have a free evening tomorrow and haven't worked on ZippZapp this week. Plan a session, or relax since it's the weekend?" Project tracking, weekly review. |
| 11 | **Deep research** | Research across your notes and the web in the background, with a cited report written to Obsidian and a notification when it's ready. |
| 12 | **Voice identity** | A custom "Hey Eva" wake word trained on your voice, and speaker verification so she only takes commands from you. |
| 13 | **FORGE v1.1** | Skills delivered as pull requests from her own GitHub account, a Docker sandbox, and a Hermes skill importer through the same safety review. You approve every merge. |

## v0.4 "Presence"
| # | Milestone | What you get |
|---|---|---|
| 14 | **HERALD phone calls** (Twilio) | She can call you, and take or make calls on your behalf. |
| 15 | **Native desktop app** (Tauri) | Tray icon, global hotkey, starts with Windows. |
| 16 | **Location automations** | Arriving home sets your "home" mood; at the store she texts your shopping list. |
| 17 | **Smart home expansion** | More devices, and a Home Assistant bridge. |

Anytime: travel skills like NS train times make good FORGE tests.

## What the phone app needs first
The voice pipeline (1) so the app just streams audio to E.V.A., network safety (2) and private remote access (8)
so the phone can reach her securely, and a stable API for cards and push. With 1 and 2 done in v0.2.5,
the app can start right at the beginning of v0.3.
