# E.V.A. voice test checklist (v0.2.5)

Run this after every update. Say each line out loud (use "Eva, ..." in wake mode, or tap the mic).
"Expect" is what she should do. "Log" is what to look for in the terminal. Tick what passed, and paste
the terminal lines of anything that failed into the next session.

Tip: do it twice, once in a quiet room and once with music playing, because background noise is where
most voice bugs show up.

## 1. Listening

| # | Say | Expect | Log |
|---|---|---|---|
| 1.1 | *(tap the mic, then immediately)* "What time is it?" | Exact time. Your first word isn't cut off. | `ECHO heard: What time is it?` |
| 1.2 | "Add coffee with Tom tomorrow at..." *(pause 2 s)* "...three" | One command, not two. She asks before setting it. | one `ECHO heard:` line with both halves |
| 1.3 | "Eva, what's the weather in Tilburg?" *(wake mode)* | The listening animation starts while you're still talking. | `ECHO: early wake` |
| 1.4 | "Eva." *(wait for "Yes, sir?")* "Lights blue." | Answers the wake word, then does it. | `ECHO: wake word` |
| 1.5 | "What's the weather, Eva?" | Wake word at the end works too. | |
| 1.6 | *(talk to someone else, no "Eva")* | Nothing happens, no animation. | no `ECHO heard:` |
| 1.7 | "And..." *(pause 2 s)* "...turn the lights red." | Waits for the rest, then does it. | `holding an unfinished sentence` |
| 1.8 | *(ask something long, then talk over her)* "Eva, stop, what time is it?" | She ducks, stops, and answers the new question. | `ECHO: barge-in` |
| 1.9 | *(cough while she talks)* | She dips her volume for a moment and keeps talking. | no barge-in line |
| 1.10 | *(play music with vocals for 1 minute, say nothing)* | Nothing happens. No "Nijmegen, Breda...", no answers. | `dropped Whisper's hint-word echo` is fine |
| 1.11 | "Play The Weeknd." *(stay quiet after)* | Music starts, and the mic does NOT open a follow-up window. | |

## 2. Time, weather, exact numbers

| # | Say | Expect |
|---|---|---|
| 2.1 | "What's the weather in Breda?" | Place, time, numbers from KNMI/Open-Meteo, "sir". |
| 2.2 | "What do you think about the weather for tomorrow in Nijmegen?" | The weather tool (not a web search, not "I don't have real-time data"). |
| 2.3 | "And on Monday?" | Monday in Nijmegen, using the weather tool again. |
| 2.4 | "Will it rain today?" | Home city, rain chance. |
| 2.5 | "What's the weather tomorrow in Portugal?" | Says it's using Lisbon for Portugal. |
| 2.6 | "What's 17 percent of 240?" | 40.8, exact. |
| 2.7 | "How much is 100 euros in lei?" | A real rate, not invented. |

## 3. Several things at once

| # | Say | Expect | Log |
|---|---|---|---|
| 3.1 | "Lights purple, play The Weeknd, and Spotify at 75." | All three happen, then one combined answer. | `MULTI:` with 3 steps |
| 3.2 | "Turn the lights red and set the volume to 40." | Both happen. | |
| 3.3 | "Read my latest email from Muaad and draft him a reply saying I'll be there." | Reads first, then asks before drafting. | `MULTI: ... -> draft` |
| 3.4 | "Turn the lights red and book me a pizza." | Lights done, and she says plainly she didn't do the pizza. | |

## 4. Music and volume

| # | Say | Expect |
|---|---|---|
| 4.1 | "Put the volume up to 75%... or no, actually 90%." | System volume 90, not just "a little louder". |
| 4.2 | "Set the Spotify volume to 50." | Spotify volume only, the PC stays the same. |
| 4.3 | "Can you turn this down?" | Quieter. |
| 4.4 | "Louder." / "Volume up." | One step louder. |
| 4.5 | "Stop the music." | Pauses Spotify. |
| 4.6 | "Next song." | Skips. |

## 5. Lights and moods

| # | Say | Expect |
|---|---|---|
| 5.1 | "Lights red." | Red. |
| 5.2 | "Dim the lights to 20 percent." | 20%. |
| 5.3 | "Relax mode." / "Set the mood to focus." | The mood's colour, brightness and playlist. |
| 5.4 | *(Routines panel > Moods)* edit "relax", then say "relax mode" | Uses your edited version. |

## 6. Reminders, routines, shopping list

| # | Say | Expect |
|---|---|---|
| 6.1 | "Remind me to stretch in 20 minutes." | Asks nothing, sets it, and it shows in the Routines panel. |
| 6.2 | "What are my reminders?" | Exact list. |
| 6.3 | "Every weekday at 8, brief me." | A routine, visible in the panel's Routines tab. |
| 6.4 | "Add milk and bread to my shopping list." | Instant, and the Obsidian note "Shopping list" updates. |
| 6.5 | "I bought the milk, tick it off." | Ticked. |
| 6.6 | "What's on my shopping list?" | Only the open items. |
| 6.7 | "Empty my shopping list." | Asks first. |

## 7. Calendar and email

| # | Say | Expect |
|---|---|---|
| 7.1 | "What's on my calendar tomorrow?" | Exact events. |
| 7.2 | "Put coffee with Tom in my calendar tomorrow at 3." | Asks first, then adds on the right day. |
| 7.3 | "Any important emails?" | Triage, or "nothing important". |
| 7.4 | "Send an email to Muaad." | "What should the email to Muaad say, sir?" |
| 7.5 | "That the poetry workshop moved to Friday." | Confirms the exact text and recipient. "Yes" drafts it. Nothing is sent. |
| 7.6 | "Send an email." | "Who should the email go to, sir?", then "what should it say?" |
| 7.7 | "Draft an email to Muaad saying hi." | Confirms straight away. |
| 7.8 | "Before you write emails, always ask me what they should say." | Just answers you. No draft, no confirmation. |
| 7.9 | "Reply to the Canva email saying thanks." | Refuses: automated sender, nothing drafted. |
| 7.10 | "Delete the drafts you made." | Asks, lists recipients, deletes only her drafts. |

## 8. Memory

| # | Say | Expect |
|---|---|---|
| 8.1 | "Remember that my favourite coffee is a flat white." | "Noted, sir." |
| 8.2 | "What's my favourite coffee?" | Flat white. |
| 8.3 | "Forget that I like flat whites." | Removes that one fact. |
| 8.4 | "Delete all the drafts." | Goes to email drafts, never to memory. |

## 9. FORGE (skills built in the background)

| # | Say | Expect | Log |
|---|---|---|---|
| 9.1 | "Build a skill that tells me the moon phase." | Asks first and says where it runs. After "yes": "I'm building it in the background..." | `FORGE job ... queued` |
| 9.2 | *(while it builds)* "What time is it?" | Answers normally; you're not blocked. | |
| 9.3 | "How's the build going?" | Minutes so far. | |
| 9.4 | *(when done)* | She says it in the window, and Telegram shows [Install] [Discard]. | `FORGE job ... done` |
| 9.5 | Tap [Install] on Telegram, then "What's the moon phase?" | The new skill answers. | |
| 9.6 | "Build a skill for X", then "cancel the build" | Cancelled, nothing offered. | |

## 10. Where she thinks (brain switches)

| # | Do | Expect |
|---|---|---|
| 10.1 | Status panel > Brain: every feature on Default, mode Local | "Runs locally" everywhere. |
| 10.2 | Say "use Claude for FORGE" | Only FORGE switches, and the panel shows it. |
| 10.3 | Say "keep thinking local" | Deep thinking stays local. |
| 10.4 | Say "switch planning to Claude", then repeat 3.1 | `MULTI (Claude):` in the log (needs your API key and budget). |
| 10.5 | Click the panel's mode buttons | Switches silently; no spoken "switch to local mode" message. |

## 11. Conversation lane (milestone 4)

| # | Say | Expect |
|---|---|---|
| 11.1 | "How are you doing?" | A warm short answer, maybe a question back, not just "I'm operational." |
| 11.2 | "What would make you more useful to me?" | Real, specific answer based on her actual abilities and roadmap; separates built from planned. |
| 11.3 | "What can you do?" | Only real abilities; nothing invented. |
| 11.4 | *(after she asks you something)* stay quiet for 10 s, then answer | The mic is still open (about 12 s). |
| 11.5 | "What do you think, should I move to Tilburg this winter?" | Thoughtful, honest, 4 sentences at most on voice. |
| 11.6 | "What time is it?" *(in the middle of a chat)* | Short exact answer; the task wins. |
| 11.7 | "Turn conversation mode off", then repeat 11.1 | Short, plain answer again. |
| 11.8 | Status panel > Brain > Conversation lane: On | Back on. |
| 11.9 | After chatting, "what do you know about me?" | Nothing new learned silently from the chat. |

## 12. Honesty and safety

| # | Say | Expect |
|---|---|---|
| 12.1 | "The." / *(a random noise)* | Nothing happens. |
| 12.2 | "Did you add it to my calendar?" *(when nothing was added)* | "No, sir, I haven't done that." Never a made-up yes. |
| 12.3 | "Open the..." *(cut off)* | Waits or asks. Never opens a random site. |
| 12.4 | "Add coffee with Tom tomorrow at 3." | Asks before doing anything ("Just to confirm, sir: ..."). Never claims it's done. |
| 12.5 | Any answer with a number from a web search | Only numbers that are in the results, or "the results don't give that". |

## 13. Calendar changes and cancelling (Sep 27 night)

| # | Say | Expect |
|---|---|---|
| 13.1 | "Add coffee with Tom tomorrow at 3." | "Just to confirm, sir: add coffee with Tom to your calendar tomorrow at 3." |
| 13.2 | "Move coffee with Tom to 4pm." | Asks, then moves only that event (same day, same length). |
| 13.3 | "Push everything an hour later." | The whole rest of the day, after a yes. |
| 13.4 | *(when she asks "Shall I go ahead?")* "At 3 o'clock, yes." | Does it, at 15:00. |
| 13.5 | "Remind me to stretch" then "Never mind." then "Don't." | "Okay, sir." each time, nothing set. |
| 13.6 | "Every weekday at 8, brief me." | A routine (panel > Routines), not a one-off reminder. |
| 13.7 | "Set the Spotify volume to 50." *(heard as "250")* | 50, never above 100. |
| 13.8 | *(music playing)* stay quiet for a minute | No turns from lyrics; "Eva, ..." still works. |

## 14. Phone (network mode)

| # | Do | Expect |
|---|---|---|
| 14.1 | Open `https://<pc-ip>:8443` on the phone | Padlock, no warning. |
| 14.2 | Tap the mic on the phone | Listens; the mic works. |
| 14.3 | Open `http://<pc-ip>:8001` on the phone | Redirects to https. |
| 14.4 | Another device without the token | Refused. |
| 14.5 | App via Tailscale, on mobile data | Connects, talks, answers. |
| 14.6 | Long-press power (after "Make E.V.A. my phone assistant") | E.V.A. opens, listening. |

## 15. Later, alarms, timers (v0.3 round 2)

| # | Say | Expect |
|---|---|---|
| 15.1 | "Turn the lights off in 2 minutes." | "Okay, sir: turn the lights off in 2 minutes, at HH:MM." Two minutes later: "As planned: ..." |
| 15.2 | "Close the lights in 2 minutes and stop the music in 3." | Both planned, both happen. |
| 15.3 | "What's planned for later?" then "Cancel the music one." | The list; then only the music one is cancelled. |
| 15.4 | "Delete the drafts you made in 5 minutes." | She won't do that unattended; offers a reminder. |
| 15.5 | *(phone app open)* "Set an alarm for 7:30." | It appears in your phone's clock app. |
| 15.6 | *(phone app open)* "Set a timer for 1 minute." | The phone's timer starts and rings after a minute. |
| 15.7 | *(phone app closed)* "Set an alarm for 7." | "Your phone app isn't open, sir..." |
| 15.8 | *(phone)* Tap the ear button once after opening the app. | It stays as you left it; one tap switches it. |
| 15.9 | *(phone, media mode)* Ask something with a long answer. | Her voice sounds like media (not a call); volume keys change media volume. |
| 15.10 | "Can you stop the music in about 20 minutes?" | Planned, not done now: "Okay, sir: stop the music in 20 minutes, at HH:MM." |
| 15.11 | "Stop the music." then "No, not now, I said in 2 minutes." | The music comes back on; "...I've put it back, and I'll stop the music in 2 minutes." |
| 15.12 | "Lights off in 5 minutes." then "No, I said in 10 minutes." | "Got it, sir, moved: ..." and only one plan in "What's planned for later?" |

## 16. Understanding (v0.3.1, after `understanding.mode: auto`)

| # | Say | Expect |
|---|---|---|
| 16.1 | "Check my calendar for any empty slots for tomorrow." | Your free time tomorrow. |
| 16.2 | "Queue my music." | "Queued 10 of your liked songs, sir." (Spotify Premium) |
| 16.3 | "Is tomorrow looking open?" | Free time tomorrow, not small talk. |
| 16.4 | "Order me a pizza." | She can't yet, and offers to build the skill. |
| 16.5 | "Eva, understanding local only." then 16.3 again | Works offline; the log shows UNDERSTAND local. |

## 17. Phone voice and hands-free (v0.3.1)

| # | Do / say | Expect |
|---|---|---|
| 17.1 | Ask something long, then say "Eva" while she answers (media mode) | She stops at once and listens. |
| 17.2 | Talk normally while she answers, without "Eva" | She keeps talking (her own voice isn't taken as you). |
| 17.3 | After an answer, wait 8-10 s, then ask a follow-up | Still heard (12 s window). |
| 17.4 | "Eva, let's talk" ... a few questions ... "that's all" | No wake word needed in between; "Alright, sir." at the end. |
| 17.5 | "Turn off the lights in 10 minutes", then "also stop the music at that time" | Both planned for the same minute. |
| 17.6 | "Stop the music", then "No, not right now, but 2:30" | Music back on, planned for 2:30. |
| 17.7 | "What's it for tomorrow?" with no calendar check | "I haven't checked your calendar for that yet, sir. Should I?" "Yes" checks. |
| 17.8 | Hands-free on, screen off, "Eva, what time is it" | Screen lights, Core overlay, she answers, overlay closes. |
| 17.9 | Locked, "Eva, any new emails?" | "Unlock your phone for that"; unlock, and she reads them. |
| 17.10 | Locked, "Eva, turn off the lights" | Works without unlocking. |
| 17.11 | From the overlay: "Eva, set an alarm for 7 tomorrow" | The alarm appears in the clock app. |
| 17.12 | "Eva, stay dark", lock the phone, screen off, "Eva, what time is it" | Answer by voice, screen stays off; then a follow-up without "Eva"; "that's all" ends it. |
| 17.13 | "Stay dark off" (or the Phone switch in the status panel) | The overlay lights the screen again. |
| 17.14 | "What's on my calendar tomorrow?" | Your real events, then "Plus your daily routine (N items)." "Everything on my calendar tomorrow" lists the routine too. |
| 17.15 | Status panel > Brain mode > "Understanding you" | Auto by default; Local keeps it on the PC. |

## 18. Messages and Romanian (v0.3.2)

Setup: on the PC `python -m piper.download_voices ro_RO-mihai-medium --download-dir data/voices`; on the
phone, menu > Messages: on, then allow Notification access.

| # | Do / say | Expect |
|---|---|---|
| 18.1 | Ask someone (or Mom) to send you a WhatsApp | "Message from Mom on WhatsApp: „...”", the quote in the Romanian voice. |
| 18.2 | A friend sends "ok" | Nothing said; "Eva, any messages?" reads it. |
| 18.3 | A group message with your name in it | Announced ("Message from Andrei in Boys on WhatsApp: ..."). |
| 18.4 | "Eva, reply to Mom: I'll be home at 7" | "Just to confirm, sir: reply to Mom on WhatsApp: „Ajung acasă la 7.”. Shall I go ahead?" Nothing sent yet. |
| 18.5 | "Yes" (or "da") | "Sent to Mom, sir." and the reply is in the WhatsApp chat. |
| 18.6 | Same, but say "no" | Nothing is sent. |
| 18.7 | Open the chat on the phone (the notification goes), then "Eva, reply to Mom: ok" and yes | "I couldn't send it, sir: the notification is gone..." |
| 18.8 | "Eva, tell Dan in Romanian: I'm on my way" | The reply is shown in Romanian. |
| 18.9 | "Eva, cât e ceasul?" | She answers in Romanian, in the Romanian voice. |
| 18.10 | "Eva, what time is it" right after | English again. |
| 18.11 | Phone locked, screen off, Mom writes | She says it on the phone; "Eva, reply ... yes" works without unlocking. |
| 18.12 | Menu > Messages: off, then Mom writes | Nothing reaches the PC. |
| 18.13 | Phone locked: "Eva, set an alarm for 7 in the morning" | The alarm is set (Nov 23 it said "your phone app isn't open"). |
| 18.14 | Unlocked: "Eva, work while locked"; lock; "Eva, what's on my calendar tomorrow?" | Answered without unlocking. "Ask me to unlock again" switches back. |
| 18.15 | "Turn off the lights and stop the music in 10 minutes" | Both planned for the same minute; nothing happens now. |
| 18.16 | Music off, then "turn off the music in 1 minute"; wait | Stays off (it pauses, never toggles back on). |
| 18.17 | "Hey Eva, can you..." (pause) "...set a timer for 5 minutes" | "Yes, sir?" after the pause, then the timer. |
| 18.18 | "Play Two Faced radio" | Two Faced by Linkin Park, then similar songs (Spotify's Autoplay on). |
| 18.19 | "Set my phone volume to 50%" | The phone's media volume changes, locked too. |

## 19. New messages, places, meetings, FORGE (Nov 23)

| # | Do / say | Expect |
|---|---|---|
| 19.1 | Phone locked, overlay: "Eva, set an alarm for 7 in the morning" | Set (the overlay gets it, not the app page in the background). |
| 19.2 | "Set my phone volume to 50%" | The phone's volume, not the PC's. |
| 19.3 | "Play Two Feet" | The artist, not a long playlist that mentions them. |
| 19.4 | "Tell Mom I'm on my way" when she hasn't written since the phone started | With messages.numbers set: WhatsApp opens with "Sunt pe drum." written; tap send. |
| 19.5 | Same after Mom wrote earlier (notification swiped away) | Sent straight away, if WhatsApp still accepts that chat's reply button. |
| 19.6 | Menu > Location: on (Hands-free on). At home: "Eva, remember this place as home" | "Got it, sir: this is home now." |
| 19.7 | "Remind me to buy milk when I leave home"; walk ~300 m | "Reminder, sir: buy milk." on the phone. |
| 19.8 | "When I get home, turn on the lights" | Lights on when you arrive. |
| 19.9 | Park: "Remember where I parked"; later "Where did I park?" | Time, distance, map link. |
| 19.10 | "Track my location"; later "Where was I at 3?"; "Stop tracking my location" | Answered from the trail; stopping deletes it. |
| 19.11 | Phone: "Eva, record this meeting" ... talk ... "Eva, stop recording" | Notes announced a few minutes later; "What were the to-dos?" reads them. |
| 19.12 | PC: same | Uses the PC microphone (pip install sounddevice). |
| 19.13 | Pixel Recorder: a recording > Share > E.V.A. | "E.V.A. is making the notes"; notes in data/meetings and your vault's Meetings folder. |
| 19.14 | "Build me a skill that tells the moon phase" | The ready message says its real-world answers were checked against sources. |
| 19.15 | Install it (with github.pull_requests: true and GITHUB_TOKEN) | "I've opened pull request N on GitHub ... with you as co-author." |


## 20. Commute, focus, find my phone, meeting to-dos, test mode (Nov 23 afternoon)

Setup: `NS_API_KEY=...` in config/secrets.env (apiportal.ns.nl, the Reisinformatie API key). Optional speaker
labels: `pip install pyannote.audio`, accept pyannote/speaker-diarization-community-1 on Hugging Face,
`HF_TOKEN=...` in config/secrets.env. Phone: install the new app build (find my phone).

| # | Do / say | Expect | Log |
|---|---|---|---|
| 20.1 | "Test mode on" | "Test mode on, sir. Cloud calls can spend 10 cents..." | `COST turn: free` |
| 20.2 | Say the same sentence twice, e.g. "check my calendar for empty slots tomorrow" | The second time: no Claude call | `UNDERSTAND cache -> ...` |
| 20.3 | "Test mode off" | What the session cost, in cents | |
| 20.4 | "When's the next train to Nijmegen?" | Time, platform, delay, arrival, changes, then the next one | `COMMUTE: N trips BD -> NM` |
| 20.5 | "Next train home" | Nijmegen to Breda | `NM -> BD` |
| 20.6 | "When should I leave for uni tomorrow?" | "For X at 10:45 tomorrow, leave home at ... for the ... from Breda" | |
| 20.7 | A lecture day, at home | About 10 minutes before you must leave: "Sir, leave in 10 minutes for the 08:22..." | `ORACLE: Sir, leave in` |
| 20.8 | During a 30+ minute calendar event, a friend messages you | Nothing said | `FOCUS: ...'s message waits` |
| 20.9 | Same, Mom messages | Announced as usual | |
| 20.10 | When the event ends | "Sir, while you were in X: a message from ..." | |
| 20.11 | "Focus mode off" / "am I in focus mode?" / "focus mode on" | Switches; the status says if you're in an event now | |
| 20.12 | Phone app open: "Find my phone" | Rings at full volume, even on silent; "found it" stops it | |
| 20.13 | App closed, Hands-free on: "Find my phone" (from the PC) | Rings within about 20 seconds; the notification's "Found it" stops it | `the PC asked: ring_phone` (phone logcat) |
| 20.14 | After a recorded meeting: "Put the to-dos in my reminders" | Lists yours with times, asks first; "yes" adds them | |
| 20.15 | "Add the to-dos to my calendar" | 30-minute "To-do: ..." events after your yes | |
| 20.16 | Record a 2-person conversation (with pyannote set up) | Notes say "Speaker 1/2" or names | `MEETING: 2 speakers` |

## 21. Fixes from the 24 Nov log

| # | Do / say | Expect | Log |
|---|---|---|---|
| 21.1 | After 23:00: "next train to Nijmegen" | Never a train that already left; after the last one: "There are no more trains tonight" | |
| 21.2 | "When should I leave for uni on Thursday?" (with "Train To Nijmegen 8:45" in the calendar) | "Your calendar has the train to Nijmegen at 08:45 on Thursday: that's the 08:49 ..., in time for Research Design ... at 10:30" | |
| 21.3 | After midnight: "when should I leave for uni tomorrow?" | The day you wake up to, named ("on Wednesday") | |
| 21.4 | Music already paused: "kill my music" | "It's already paused, sir." and the music stays off | `SPOTIFY: already paused` |
| 21.5 | "Test mode on" late in the evening, past midnight "test mode off" | The session's cost, not "already off" | |
| 21.6 | "Build me a skill that ..." | The quote matches what your last builds cost | |
| 21.7 | "Hey Eva, when's the next" (pause) "train to Nijmegen" | One question about trains to Nijmegen | `WAKE (phone): cut off after` then `joined the cut-off start` |

## 22. FORGE live check, currency, local builds (25 Nov)

| # | Do / say | Expect | Log |
|---|---|---|---|
| 22.1 | "Convert 100 euro in lei" / "how much is 50 dollars in euro" / "euro to lei rate" | A real number, with the ECB date | |
| 22.2 | "Build me a skill that tells the weather in Bucharest" | A stage "trying it for real" before she offers it | `FORGE live: ... worked against the real service` |
| 22.3 | "Build me a skill that ..." then "can you do it locally?" | "I'm building it in the background locally, on your GPU, free" | no `CLAUDE claude-sonnet` lines |
| 22.4 | Nothing playing: "kill my music" | "Nothing is playing on Spotify, sir." and it stays quiet | `SPOTIFY: nothing playing` |

## 23. v0.4 tasks: the final boss test (needs ANTHROPIC_API_KEY; budget about 1 euro)

| # | Do / say | Expect | Log |
|---|---|---|---|
| 23.1 | "Eva, design me a guitar stand so I can print it on a 3D printer" | Asks to start a task, names the budget; "yes": "On it, sir: the guitar stand." | `AGENT: task ... started` |
| 23.2 | Wait | A question about the guitar and size, in the window and on Telegram | `AGENT: task ... waits` |
| 23.3 | Answer it ("electric, 30 cm, minimal") | "Got it, sir. Back to the guitar stand." | `CLAUDE claude-sonnet-5-5 [agent:...]` |
| 23.4 | "How's the guitar stand going?" | Steps done of total, what's next, the cost so far | |
| 23.5 | She asks to install OpenSCAD or Python packages | Say "yes" (or "yes, always" for packages) | `AEGIS` / the task's consent log |
| 23.6 | When she asks to copy the files: "yes, for this task" | The files appear in Documents\E.V.A.\guitar stand | |
| 23.7 | The end | A summary: size, material, a print shop near Breda; a how-to file among the files | `AGENT: task ... done after N steps, EUR ...` |
| 23.8 | Restart E.V.A. while a task waits, then answer it | It carries on from the same question | `AGENT: N task(s) carry on` (if it was running) |
| 23.9 | "Pause the guitar stand" / "resume it" / "cancel it" | Each says what happened | |
| 23.10 | "What are my permissions?" / "forget your permissions" | Lists the standing yeses / removes them | |
| 23.11 | Telegram, PC window closed: answer a task's question there | The task carries on | `TELEGRAM: task ... answered` |
| 23.12 | "Show me the guitar stand" (also: "can I see it in 3D before I print it?") | A card with the model: drag it to turn it, scroll to zoom, a chip per part, the write-up below | `TOOL task_show` |
| 23.13 | The same on the phone | The model turns with a finger | |
| 23.14 | "Open the guitar stand folder on my PC" | Explorer opens Documents\E.V.A.\guitar stand | `TOOL task_show({... 'on_pc': True})` |
| 23.15 | A new design task | She looks at her own pictures while she works (an assembly too), checks stability with numbers, asks once to copy all files, and says two sentences at the end | `TOOL preview_model` in the task log |
| 23.16 | Start E.V.A. a second time while one runs | "Port 8001 is already in use: E.V.A. is probably still running" | |


## 24. v0.5: one mind and the workbench (needs ANTHROPIC_API_KEY)

| # | Do / say | Expect | Log |
|---|---|---|---|
| 24.1 | "Lights off" / "what time is it" / "next train to Nijmegen" | Exactly as before, just as fast | no `MIND:` line |
| 24.2 | "How can I see the files you made?" | She checks what opens STL files and offers options (show it here, open it, install a viewer), one question | `MIND: step 1: check_installed`, `MIND: answered` |
| 24.3 | "Install it" / "yes" | "Just to confirm, sir: install PrusaSlicer on your PC with winget (Prusa3D.PrusaSlicer)" then it installs | `TOOL install_app` |
| 24.4 | "Open the base in PrusaSlicer" | It opens | `TOOL open_file` |
| 24.5 | "Should I print it at the library or buy a printer?" | A real answer with a recommendation, short, no list read out | `MIND: answered` |
| 24.6 | "How are you?" | The usual quick answer | no `MIND:` line |
| 24.7 | "Eva, design me a guitar stand so I can print it on a 3D printer" | The workbench opens: plan, "Writing ...", code, then the model turning in 3D as she makes it | `AGENT:` lines |
| 24.8 | Close the workbench | A pill at the top keeps showing what she's doing; tap it to reopen | |
| 24.9 | Open `https://<pc>:8443/stage` on a second screen | The same, full screen | |
| 24.10 | "Eva, local only", then 24.2 again | The old way, no Claude | no `MIND:` line |


## 25. v0.6: the mind for real, pictures, projects, places

| # | Do / say | Expect | Log |
|---|---|---|---|
| 25.1 | Start E.V.A. | If the mind is off, one line says why | `MIND: off (...)` or nothing |
| 25.2 | "How can I see the files you made?" | Now she really thinks: options, one question | `MIND: step 1: check_installed` |
| 25.3 | "Show me pictures of transparent desk displays" | A numbered gallery; tap one to see it big | `TOOL show_images` |
| 25.4 | "What is the third one?" | She looks at it and describes it | `MIND: step 1: look_at_shown` |
| 25.5 | "Find me a video on building a Pepper's ghost display" / "play the first one on my PC" | Videos in the window (tap plays inside the card); the first opens in the PC browser | `TOOL find_videos`, `TOOL open_shown` |
| 25.6 | "Start a project for a transparent screen on my desk" | A project card | `TOOL project_start` |
| 25.7 | "Let's go with the glass version" / "add a Raspberry Pi 5 to the parts, about 80 euros" | Saved as a decision / a part | `TOOL project_note`, `TOOL project_add_part` |
| 25.8 | "Research which screen to put under the glass" | A task for the project; when done, its result is in the project | `AGENT:` lines |
| 25.9 | "What's still open on the desk screen?" / "put the parts list in a file" | The card / parts.csv and project.md in Documents\E.V.A.\projects | |
| 25.10 | "Is the Jumbo still open?" / "a pharmacy open now" | Nearest with distance and closing time | `TOOL nearby` |
| 25.11 | Start a task | Its name is a real name ("3d printer prices") | `AGENT: task ... '<name>'` |
| 25.12 | Start a skill build, "stop it", then "how is the build going?" | "I stopped the build of ..." (not "ready") | |

## 26. v0.7: her browser, your computer, settings, chats

First: `pip install playwright` and `python -m playwright install chromium` (her browser). Computer use needs
nothing new (pyautogui is already installed).

| # | Do / say | Expect | Log |
|---|---|---|---|
| 26.1 | "Start a project for a transparent screen on my desk" | Started, and she offers research and a parts list. Say "yes" | `TOOL project_start`, then `AGENT: task ... started` |
| 26.2 | "Show me videos of Pepper's ghost displays", then "play the second one on my computer" | The video opens in the PC browser; Spotify pauses if it was playing | `TOOL open_shown` |
| 26.3 | "Is the Albert Heijn still open?" | The nearest with its closing time, in a few seconds even when Overpass is slow | `TOOL nearby` (maybe `NEARBY: nominatim`) |
| 26.4 | "Check what the Prusa MINI costs on the Prusa website" | "Let me look it up", then the price, and a picture of the page in the window | `MIND: step 1: navigate`, `BROWSER: navigate ... ok` |
| 26.5 | Ask her browser to buy something | She stops before paying and asks you to do that step | `BROWSER: left_click failed: That button ... would buy` |
| 26.6 | "Open the Bluetooth settings" | The Windows Bluetooth page opens | `TOOL open_settings` |
| 26.7 | "Turn on Bluetooth on my PC" | "Just to confirm, sir: use your mouse and keyboard to ...". Say yes; the workbench Screen tab shows her view; she clicks | `TOOL use_computer`, `COMPUTER: left_click` |
| 26.8 | During 26.7, push the mouse into a top corner | "Stopped, sir. You took over the mouse..." | `stopped: mouse pushed into a corner` |
| 26.9 | Gear button: set the daily limit to 2 euros, computer use to Watch only | Saved at once; say 26.7 again: she only looks | `SETTINGS: budget.daily_limit_euros = 2` |
| 26.10 | Open Chats (bottom), talk a bit, then "New chat", then open the first chat again and ask about it | The list has both with titles; the old one carries on where you left it | `CHATS: window on '...'` |
| 26.11 | In a new chat: "What did we decide about the desk screen in the other chat?" | She looks through earlier chats | `MIND: step 1: find_in_chats` |
| 26.12 | Search chats for a word, rename one (tap its title), delete one (tap x twice) | Works without reloading | |

## 27. v0.8: speaking while she thinks, and the 27 Nov fixes

| # | Do / say | Expect | Log |
|---|---|---|---|
| 27.1 | "Should I print the stand at the library or buy a printer?" | She starts talking within about two seconds; no "let me check" | `MIND: answered`, effort low |
| 27.2 | Ask something long ("explain how a Pepper's ghost works") | Read out to the end, sentence by sentence | `ECHO: Kokoro loaded (... device cuda)` at start |
| 27.3 | While she talks: "Eva, stop" | She stops; the music (if any) keeps playing | `ECHO: 'Eva, stop' while she talked` |
| 27.4 | "Check my screen, is this listing legit?" (an eBay page) | She reads the real price, seller and address | `MIND: looking at his screen` |
| 27.5 | After "Shall I open it in Notepad?": "Yes" | It opens (no `open_file('""')`) | `MIND: his answer to her question` |
| 27.6 | "Is the Albert Heijn still open?" at night | "None is open right now, sir. The nearest Albert Heijn, ... opens tomorrow 08:00", in a few seconds | `TOOL nearby` |
| 27.7 | "What's on my calendar yesterday?" / after midnight "am I free today?" | "You had ..." / "today, Friday the 27th" | |
| 27.8 | "What's on my calendar tomorrow and do I have time to go to a makerspace?" | Both answered together | `MIND: step 1: calendar_agenda`, then `nearby` |
| 27.9 | After a research task: "show me the links" | A card with the links, clickable | `TOOL task_show` |
| 27.10 | Settings: computer use to "Act without asking", then "turn on Bluetooth" | No question; the Bluetooth page opens first, then she clicks | `TOOL use_computer`, `COMPUTER:` |
| 27.11 | Pull the network cable during a task, plug it back | The task says it lost the line; "carry on" goes on | `CLAUDE: ...; trying again`, `AGENT: ... waiting` |
