# E.V.A. handoff (for a new Claude session)

Last updated: Monday 23 November 2026, 12:20 (Europe/Amsterdam). Read this first, then EVA.md, docs/ROADMAP.md,
docs/UNDERSTANDING.md and docs/VOICE_TESTS.md.

## Who and what

- Owner: Ionuț (address him as Ionuț; he calls her "she"). Be direct, no em-dashes in anything you write.
- E.V.A. is his local-first JARVIS: a Python server on his Windows PC (`D:\Project E.V.A\eva`, venv `.venv`,
  Python 3.12, NVIDIA GPU, Ollama with qwen2.5:3b-instruct and nomic-embed-text, Whisper small for listening,
  Kokoro voice, Piper "ro_RO-mihai-medium" for Romanian), a web page (`interfaces/web/eva.html`) and a Flutter
  Android app (`apps/eva_android`, Pixel 9 Pro XL, Android 17) that reaches the PC over Tailscale (https :8443).
- Started with `python -m interfaces.web.server_stream` (runs the module as `__main__`; the module aliases itself in
  `sys.modules` and the shared window set lives in `core/device.py` because of that).
- Repo: github.com/IonutBotoroga230036/EVA. Default branch `main` (at 62df4ee). All work since is on branch
  `claude/messages-romanian` (pushed, not merged yet; it also contains `claude/fix-round`). Merge it into main
  (pull request) before starting new work, or branch from it.

## How to work with him (rules that always hold)

1. Check current library versions with a web search before building on them.
2. Tests first, then the full suite (`python -m pytest -q`, about 1014 tests), plus a run with
   `PYTHONUTF8=0 PYTHONIOENCODING=cp1252` for his Windows console.
3. Tests never touch his real accounts or files: no real Claude/Jev keys, Google, Spotify, Telegram, Ollama,
   Piper voice, lights, phone, `data/*.json`. `tests/conftest.py` already isolates these (Claude key, Jev,
   understanding pinned to legacy, romanian translate/voice, message inbox, `data/phone.json`, places,
   meetings, device connections). Keep it that way for anything new.
4. Deliver changed files as a zip with a NEW filename each time (last: `eva_v033e_autosend.zip`) plus short
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

## State on 23 Nov 2026

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

1. Commute help: NS trains Breda to Nijmegen (Radboud), delays, "leave in 10 minutes", from calendar + location.
2. Meeting to-dos into reminders or the calendar on his yes; speaker labels in notes.
3. Focus mode: during calendar events hold announcements except Mom or urgent.
4. Find my phone from the PC (ring at full volume).
5. Anything new from his next log.
