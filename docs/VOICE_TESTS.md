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

## 13. Phone (network mode)

| # | Do | Expect |
|---|---|---|
| 13.1 | Open `https://<pc-ip>:8443` on the phone | Padlock, no warning. |
| 13.2 | Tap the mic on the phone | Listens; the mic works. |
| 13.3 | Open `http://<pc-ip>:8001` on the phone | Redirects to https. |
| 13.4 | Another device without the token | Refused. |
