# What E.V.A. can do (September 25, 2026, afternoon; v1.0 notes 27 November)

**v1.0, one brain:** one model decides every turn (local qwen3.5:4b first, Claude when it gets stuck; Settings >
Her brain). Changes you can undo just happen; sending as you, deleting, installing and spending more than 2 euros
in one go ask first, in her own words. Settings > What she did shows every decision with an Undo; "Eva, undo that"
works too. When no tool does what you ask she writes a small skill, dry-runs it and uses it.
v1.1: she finds and runs her own scripts (asks once per script), wires a script in as a skill, listens more
patiently (Settings > Listening), and writes your biography from the stories you tell her (Settings > Biography). The JARVIS test runs
by itself every night. Chats are in a sidebar on the left. See docs/ONE_BRAIN.md.

Say "Eva" (wake word on), tap the core, or type. Examples are phrasings she handles directly;
most work with natural variations too. Anything marked **asks first** waits for your "yes".

## Conversation and voice
- **She listens herself (v0.2.5):** your mic streams to E.V.A.; Silero VAD, Smart Turn and Whisper run on the PC.
  She waits through a thinking pause, keeps the first words you said, and hears names from the Vocabulary list.
  If the pipeline is off or can't load, the browser's speech recognition takes over automatically.
- **Talk over her:** start speaking while she talks; she lowers her voice, and stops if you keep going.
- **Wake word:** "Eva, what time is it?", "What's the weather, Eva?", or just "Eva" and then your request.
- Her real voice (Kokoro), sentence by sentence, starting while she is still thinking.
- **Follow-up window:** after she answers, just keep talking for about 7 seconds, no wake word.
- **Interrupt:** tap the core while she speaks, or just talk over her.
- **Speech fixes:** names in the Vocabulary section of EVA.md are corrected when misheard
  (Radboud, Nijmegen, Breda, ZippZapp...). Add your own lines.
- Small talk and questions about herself never trigger tools.

## Just talking (v0.2.5 conversation lane)
- "How are you?", "What would make you more useful to me?", "Should I take the Tilburg flat?": when no task is
  involved she answers like a conversation: warmer, a bit longer (4 sentences at most on voice), may ask one
  question back, and knows what she can really do (and what is only planned) from this guide and the roadmap.
- Tasks always win: "what time is it" in the middle of a chat gets the short exact answer.
- She doesn't learn facts in the background from chats; say "remember that..." to keep something.
- Off / on: "turn conversation mode off", the status panel (Brain > Conversation lane), or
  `conversation: enabled: false` in config/settings.local.yaml (the panel and voice win over the file;
  delete data/conversation.json to go back to the file).

## Building new skills in the background (v0.2.5)
- "Build a skill that tells me the moon phase": she confirms where it runs, then builds it in the background.
  Keep talking to her meanwhile. When it's done she tells you in every open window and on Telegram with
  [Install] / [Discard] buttons. Nothing is installed without your yes.
- "How's the build going?" / "Cancel the build".
- Local builds use qwen2.5-coder:14b (ollama pull qwen2.5-coder:14b) with no time limit. The build pauses while you
  talk to her, so she stays responsive, and resumes when you're quiet.
- A crew builds each skill: a planner, a coder, an independent test writer, a fixer that sees the real errors,
  and a reviewer. "How's the build going?" tells you the stage (planning, coding, fixing round 2 of 4, reviewing).

## Where she thinks (v0.2.5)
- Local by default: nothing leaves this PC unless you choose Claude.
- Per feature, by voice or in the status panel: "use Claude for FORGE", "keep thinking local",
  "switch planning to auto". Features: conversation, planning, thinking, FORGE.

## Several things at once (v0.2.5)
- "Lights red, play The Weeknd, Spotify at 50 and the PC at 100": each part runs, independent ones at the same time,
  and she answers once with the exact result of each.
- "Read Tom's last email and draft him a warm reply saying Friday works": the second step sees the first one's result.
- Anything that normally asks first still asks first, one at a time ("Next, sir: ...").
- A part she can't do is said plainly; she never invents a step you didn't say.

## Shopping list (v0.2.5)
- "Add milk and eggs to my shopping list" / "Take eggs off my shopping list" / "I bought the milk, tick it off"
- "What's on my shopping list?" / "Clear the ticked items" / "Empty my shopping list" **asks first**
- Kept in Obsidian as the note "Shopping list", with checkboxes, so it syncs to your phone with the vault.

## Email drafts (v0.2.5)
- She asks what an email should say, then confirms the exact text and recipient before drafting.
- "Delete the drafts you made": removes only drafts E.V.A. created (after a yes); yours are never touched.

## Routines panel (v0.2.5)
- The clock button at the top right: a week view, and tabs to add, edit, pause, and delete reminders and routines,
  edit moods (or reset a built-in one), and manage the shopping list.

## Time, weather, maths
- "What time is it?"
- "What's the weather in Breda?" / "What about tomorrow?" / "...and the day after?"
- "Weather in Tilburg tomorrow at 6" (6 means 18:00; she says the time she assumed)
- "Weather in Portugal on Friday" (uses Lisbon and says so). Dutch and Belgian cities use KNMI HARMONIE.
- "1 + 1", "What's 15% of 80?", "Square root of 144", "2^10"
- "What's the price of Bitcoin?" (CoinGecko, in euros) / "84687 dollars in euros" / "What's that in euros?" (ECB rates)

## Calendar (needs docs/GOOGLE_SETUP.md)
- "What's on my calendar?" (today: what's still to come) / "What am I doing tomorrow?" / "What do I have this week?"
- "What's next on my schedule?" / "What's my next meeting?"
- "Any opening tomorrow afternoon?" / "When am I free on Friday?"
- "Add coffee with Tom tomorrow at 3pm" **asks first**
- "Cancel the dentist on Friday" **asks first**
- "Move everything 1 hour later" / "Push my schedule back by 30 minutes" **asks first**
- She warns you about 10 minutes before each meeting (see Proactive).

## Email (needs docs/GOOGLE_SETUP.md)
- "Anything interesting in my email?" / "Any important emails?" (people, work, study, deadlines; not promotions)
- "Emails from Tom are important" / "Don't tell me about Instagram" (she learns what matters to you)
- "Check my email" / "Do I have any new emails?"
- "Find emails from Tom" / "Emails about Deloitte"
- "Read me the latest email from Radboud"
- "Draft a reply to Tom saying Friday works" goes to Gmail **drafts**. She can never send.

## Reminders and proactive help (ORACLE)
- "Remind me to call mom at 18:00" / "Remind me in 20 minutes to stretch" / "Remember to pay rent tomorrow"
- "What are my reminders?" / "Cancel the reminder about rent"
- When a reminder fires: "Snooze it" (10 min) or "Snooze it for 5 minutes"
- "Do not disturb for an hour" / "I'm back" (reminders are held, never lost)
- Routines: "Every weekday at 8, brief me" / "Every Sunday at 19:00 remind me to plan the week" /
  "What are my routines?" / "Stop the Sunday routine"
- She speaks up by herself: reminders, routines, meetings starting soon, and only IMPORTANT new email. Quiet hours 23:00 to 08:00
  silence calendar and email alerts, not reminders you set.
- "Good morning" / "Brief me": time, weather, today's calendar, unread mail, today's reminders.

## On your phone (Telegram)
- Setup: in Telegram talk to @BotFather, /newbot, copy the token into config/secrets.env as
  TELEGRAM_BOT_TOKEN=..., restart E.V.A., then send "/pair <code>" (the code is in the terminal) to your bot.
- Text her anything you'd say at the desk. Confirmations come with Yes / No buttons.
- At the desk: "Send me a message on Telegram saying buy milk" / "Text me on my phone that the train leaves at 15:45"
- Reminders, routines, meetings, and important mail arrive as push notifications, screen off.
- Voice notes work once faster-whisper is installed (pip install faster-whisper).
- Only your paired account can talk to her. The PC must be on.

## WhatsApp and Instagram messages (v0.3.2, Android app: Messages on)
- "Any messages?" / "What did Mom say?" / "Read my Instagram messages"
- She tells you at once when one matters: Mom (settings messages.vip), "urgent" or "call me", your name in a
  group, a direct question. Change who and what in settings `messages:`.
- "Reply to Mom: I'll be home at 7" (goes out in Romanian for Mom), "Tell Dan in Romanian: on my way".
  Always read back and sent only after your yes. Works while the notification is still on the phone.

## Romanian (v0.3.2)
- Speak Romanian and she answers in Romanian, read by the Romanian voice ("Eva, cât e ceasul?").
- Romanian in her English answers (a message from Mom) is read in the Romanian voice.
- One-time setup: `python -m piper.download_voices ro_RO-mihai-medium --download-dir data/voices`.

## Places (Nov 23, Android app: Location on)
- "Remember this place as home" / "this is the gym" / "remember where I parked" / "where did I park?"
- "Remind me to buy milk when I leave work" / "when I get home, turn on the lights" / "every time I get to the
  gym, play my workout playlist"
- "Track my location" (opt-in, 30 days, this PC only) / "where was I yesterday at 3?" / "when did I get home?"

## Meetings (Nov 23)
- "Record this meeting" ... "stop recording" (phone with Hands-free, or the PC microphone)
- Share a Pixel Recorder recording or transcript to E.V.A.
- "What were the to-dos?" / "what did we decide?" / "summarize the meeting"
- Whisper large-v3-turbo on your GPU; notes by Claude when the cloud is allowed, otherwise locally.
- "Put the to-dos in my reminders" / "add the to-dos to my calendar" / "add all the to-dos": yours only unless
  you say all, on their due day at 9 (else tomorrow at 9), **asks first**, never twice.
- Speaker labels ("Speaker 1: ...", names when they're said) when pyannote.audio and HF_TOKEN are set up.

## Trains and commute (Nov 23, NS_API_KEY in config/secrets.env)
- "When's the next train to Nijmegen?" / "next train home" / "is my train delayed?" / "trains to Utrecht at 8"
- "When should I leave for uni tomorrow?": from your first Nijmegen event and live NS trains
- She says "Sir, leave in 10 minutes for the 08:22 to Nijmegen" on lecture days (not when your phone says
  you're away from home). Settings `commute:` (stations, walk time, how early you want to be there).

## Focus mode (Nov 23)
- During calendar events of 30 minutes or longer she holds WhatsApp/Instagram and email announcements, except
  Mom and urgent ones, and afterwards tells you what came in. "Focus mode off" / "focus mode on" / "am I in
  focus mode?". Reminders and "leave for your train" still come.

## Find my phone (Nov 23)
- "Find my phone" / "where's my phone?" / "ring my phone": it rings at full alarm volume, even on silent, for
  45 seconds. "Found it" stops it, or the notification's button. Works with only Hands-free on (it checks in
  every 20 seconds).

## Tasks: bigger jobs in the background (v0.4, docs/AGENT.md)
- "Design me a guitar stand I can 3D print" / "research the best snowboard bindings under 200 euros" / "plan my
  weekend in Brasov": she asks to start a task (up to 1 euro, then she asks), works in the background with
  Claude, asks you when a choice matters, asks before installing anything, and tells you when it's done.
- "How's the guitar stand going?" / "what tasks are you working on?" / "pause / resume / cancel the guitar stand"
- "Show me the guitar stand" / "can I see it in 3D?": the model in the window, turned with a finger or the mouse;
  "open its folder on my PC" opens Explorer there. "Try the guitar stand again" retries a failed task.
- Her questions: just answer (here, on the phone, or on Telegram). Her "may I...": "yes", "yes, always",
  "yes, for this task", or "no".
- Finished files land in Documents\E.V.A.\<task>. "What are my permissions?" / "forget your permissions"

## Testing cheaply (Nov 23)
- "Test mode on" (or "test mode on with 50 cents"): cloud spending capped at 10 cents, each turn's cost in the
  log. "Test mode off" says what the session cost. A sentence you said before costs nothing the second time.

## Memory and notes
- "Remember that I prefer tea over coffee" / "What do you know about me?"
- "What did I tell you about Tom?" (searches facts, past conversations, and notes together)
- "Forget that I live in Breda" / "Delete what you just remembered" **asks first**
- "From now on, answer in metric units" (becomes a standing instruction in EVA.md)
- "Note that I need to follow up with Tom" (today's daily note in Obsidian)
- "Make a project note about ZippZapp branding" / "What did I note about Deloitte?"

## Lights and moods (after docs/LIGHTS_SETUP.md)
- "Turn the lights purple" / "Lights to 30 percent" / "Warm white" / "Dim the lights" / "Turn off the bed lights"
- "I'm home" / "I'm home, I feel red" / "I feel blue" / "Set the mood to relax" / "What moods do I have?"
- Built-in moods: home, red (The Weeknd), blue (blues), purple, relax (jazz), focus (Deep Focus), party, night
- "Create a mood called study: cool white at 80 percent with Deep Focus" (lights plus a playlist)
- Any colour works as a mood: "I feel crimson"

## Computer
- "Open YouTube" / "Open VS Code" / "Open github.com"
- Spotify (after docs/SPOTIFY_SETUP.md, Premium): "Play The Weeknd", "Play my Chill Evenings playlist",
  "Play jazz on my phone", "What's playing?", "Add Blinding Lights to the queue", "Pause", "Next song"
- Without the Spotify setup she opens Spotify and presses play, as before
- "Set the volume to 40" / "Max volume" / "Mute"
- "What's on my screen?" / "Read this error" (local vision model, screenshots stay on the PC)

## Knowledge
- "Search the web for news about the Netherlands" (numbers are checked against the sources)
- "Think hard about whether I should move to Tilburg" (deep reasoning)

## Building new skills (FORGE)
- "Build a skill that converts currencies" / "Learn to tell me the moon phase" **asks first** (cost or local)
- After "I don't have a skill for that": "Build it"
- She drafts, reviews the code, tests it offline, then asks "Shall I install it?" **asks first**
- "Which skills are waiting?" lists drafts.

## Cloud or local
- "Switch to local mode" (FORGE and deep thinking run on your PC: private, free, slower)
- "Switch to cloud mode" / "Use auto mode" (Claude when a key and budget exist, else local)
- "How much have you spent today?" (daily EUR 1.50 and monthly EUR 50 caps are enforced)

## Connected services (MCP)
- Any MCP server in settings.yaml becomes tools. Read-only tools run freely; others **ask first**.

## The interface
- Designed with Claude Design: cards live in a rail beside the core, one primary card per answer, older ones
  collapse, proactive cards look different and always offer Snooze. Tap the panel icon for status
  (voice, brain mode, budget, memory, connections, skills, FORGE drafts) and the history icon for the transcript.
- The previous interface is still at http://localhost:8001/classic. The design walkthrough is at /?demo.

## Other devices and safety (v0.2.5)
- By default only this PC can reach E.V.A. To use her from your phone on the same Wi-Fi, set
  `server: listen: network` in config/settings.local.yaml and follow docs/PHONE_SETUP.md: she serves HTTPS
  on port 8443 with her own (name-constrained) certificate, so the phone's microphone works.
- Websites you visit can't talk to her behind your back (cross-site requests are refused).

## E.V.A. on your phone (v0.3)
- The Android app (apps/eva_android): talk to her, hear her voice, confirm actions with buttons, type.
- From anywhere with Tailscale (docs/REMOTE_ACCESS.md), or on home Wi-Fi.
- "Make E.V.A. my phone assistant": long-press power opens her, already listening.
- While the app is open: "Eva, ..." hands-free. (Screen-off wake word is the next step.)

## Later, and on your phone (v0.3)
- "Turn the lights off in 5 minutes", "stop the music in 10": she tells you the time it will happen, does it
  then, and reports the real result. "What's planned for later?" / "Cancel the music one". Anything that needs
  your yes is not done unattended (she offers a reminder instead).
- With the phone app open: "Set an alarm for 7:30", "Wake me up at 7", "Set a timer for 10 minutes". They go
  into your phone's own clock app. If the app isn't open, she says so.
- Wake-word training: open /wakeword (PC or phone) and record the clips it asks for.

## Installing her as an app
- Edge or Chrome: open http://localhost:8001, click the install icon in the address bar.

## Not yet
- WhatsApp (no official API for personal accounts; Telegram covers it), phone alarms,
  full Spotify control (needs Spotify Premium and a developer app), deep research into notes,
  FORGE pull requests from her own git account.

## Testing her
- docs/VOICE_TESTS.md: a checklist of voice requests to run after every update.


## Thinking things through (v0.5, docs/MIND.md)
- Ask her anything that needs thinking ("how can I see the files?", "should I...?", "what would you do?"): she
  looks things up, checks your PC, gives options and asks one question. Commands stay instant.
- "What opens STL files on my PC?" / "install PrusaSlicer" (asks first) / "open the base in PrusaSlicer"
- The workbench: while a task works, watch it live (plan, what she's doing, code, the model in 3D). The wrench
  button opens it; `/stage` is full screen for a second monitor.


## Showing, projects, places (v0.6)
- "Show me pictures of ..." / "find me a video on ..." / "the second one" / "play it on my PC" / "what is the third one?"
- "Start a project for ...", "let's go with ...", "add ... to the parts", "what's still open?", "put the parts list in a file"
- "Is the Jumbo still open?", "a pharmacy open now", "a café near me", "how long until ... closes"


## Her browser, your computer, settings, chats (v0.7)
- "Check what ... costs on the ... website": she opens it in her own browser and shows you the page. She never pays,
  orders or types passwords; she hands those steps to you.
- "Open the Bluetooth settings" (any Windows settings page) / "turn on Bluetooth on my PC" (asks first, then uses the
  mouse and keyboard; push the mouse into a corner to stop her). Off / watch only / ask in the settings.
- The gear button: budget limits, test mode, brain switches, the mind, tasks, her browser, computer use, focus.
- Chats (bottom of the window): named conversations, search, rename, delete, "New chat". "What did we decide in the
  other chat?" works: she looks through them.


## Speaking and speed (v0.8)
- She starts talking while she still thinks; long answers are read to the end. "Eva, stop" while she talks stops her.
- Settings: "How hard plain talk thinks" (low is fastest); computer use can "Act without asking".
- "Show me the links" after a research task; "check my screen" reads the real page; "yesterday" and "the 26th" in the calendar.


## The glance, memory and her notes (v0.9)
- Leave the window alone: weather, next event, train and a task or project show under the orb; /stage shows the day full screen.
- Settings > What she knows: everything she remembers about you; edit or forget each.
- Settings > Her notes, or "how did you do today?": her own review of her mistakes, with a copy button for Claude.
- Mornings: the first thing you say gets a short heads-up first.
- "Tell me a story for 10 minutes", then "continue". "Move the items from 9 until now 30 minutes later."
