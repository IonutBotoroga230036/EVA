# What E.V.A. can do (September 25, 2026, afternoon)

Say "Eva" (wake word on), tap the core, or type. Examples are phrasings she handles directly;
most work with natural variations too. Anything marked **asks first** waits for your "yes".

## Conversation and voice
- Her real voice (Kokoro), sentence by sentence, starting while she is still thinking.
- **Follow-up window:** after she answers, just keep talking for about 7 seconds, no wake word.
- **Interrupt:** tap the core while she speaks.
- **Speech fixes:** names in the Vocabulary section of EVA.md are corrected when misheard
  (Radboud, Nijmegen, Breda, ZippZapp...). Add your own lines.
- Small talk and questions about herself never trigger tools.

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

## Installing her as an app
- Edge or Chrome: open http://localhost:8001, click the install icon in the address bar.

## Not yet
- WhatsApp (no official API for personal accounts; Telegram covers it), phone alarms,
  full Spotify control (needs Spotify Premium and a developer app), deep research into notes,
  FORGE pull requests from her own git account.
