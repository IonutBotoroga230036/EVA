# E.V.A. interface redesign: brief for Claude Design

## How we work (step by step)
1. Open Claude Design and start a new project called **E.V.A. interface**.
2. Paste everything under "The brief" below as your first message.
3. Review the first pass. Useful follow-ups:
   - "Make the core feel more alive in the speaking state, but calmer when idle."
   - "Show screen 5 with 6 emails and a long subject line; nothing may overflow."
   - "Now the same screens at 390px wide for a phone."
   - "Give me the component spec: class names, spacing, radii, colours, type scale."
4. When you like it, export or copy the HTML and CSS for each screen (or take screenshots if export
   isn't available), plus the component spec.
5. Bring it back to this chat. I port it into `interfaces/web/eva.html`, keeping the WebSocket, audio,
   voice, and widget logic, then run the test suite and the browser checks.
6. You test it live; we iterate once more if needed.

## The brief (paste this)

Design the interface for **E.V.A.**, a local, voice-first personal AI assistant (think a calm, modern
JARVIS). It runs as a web app installed as a desktop app, and also on a phone.

**Identity.** One glowing reactor core in the centre is the whole personality. Colours: void background
#08060f to #0d0a1c, violet #8b5cf6, bright violet #a855f7, magenta #c026d3, plasma lilac #c4a4ff, ice
text #efeaff, dim text #7a7196. Fonts: Chakra Petch (display, labels) and Inter (body). Dark only.
Glass cards: translucent, blurred, thin lilac border, soft violet glow. Minimal: nothing on screen
unless it's needed. Absolutely no em-dashes in any text.

**Core states** (animated canvas, keep it as the centrepiece): idle (slow breathing), listening
(a waveform ring), thinking (tighter, magenta), speaking (pulsing). A caption under the core shows what
she says; a small connection dot top-left (red offline, violet live); clock top-right.

**Screens to design:**
1. Idle, desktop 1440x900.
2. Speaking, with a weather card: "Tomorrow in Breda: overcast, 15 to 21 degrees, 10 percent chance of rain."
3. Confirmation card: "Just to confirm: add Coffee with Tom to your calendar tomorrow at 15:00." Buttons: Go ahead / Cancel.
4. Calendar cards: "Still today" (a list of 5 timed items) and "Up next" (one item, with minutes to go).
5. Email card "Worth your attention": 3 emails with sender, subject, and a small reason line
   ("someone you've emailed", "sounds time-sensitive"). A quiet footer: "198 others can wait."
6. Proactive alert, visibly different from a normal reply because she started it: reminder
   ("call mom"), meeting starting in 10 minutes, important email. Must include a Snooze action.
7. Status panel sliding in from the right: voice (Kokoro), brain mode toggle (Auto / Cloud / Local),
   budget today and this month with a thin bar (EUR 0.12 of 1.50, EUR 3.40 of 50), memory (facts, turns),
   connections (Google, Telegram, MCP servers) with status dots, skills list, FORGE drafts waiting.
8. History drawer sliding up from the bottom: the recent conversation as a minimal transcript.
9. FORGE draft review card: skill name, one-line summary, tools, permissions (internet hosts or
   "works offline"), "passed security review and 5 tests", cost, buttons Install / Discard.
10. Phone layout at 390x844 for screens 1, 2, 3, and 6, with safe areas for notch and home bar.

**Problems in the current UI to design away:** cards overlap the caption; several cards stack into a
tall pile; long text overflows cards; on phones, content hides under the system bars. Define where
cards live, how many can show at once (one primary, older ones collapse), and how they leave.

**Controls** (bottom dock): wake-word toggle, a large mic button, keyboard toggle that reveals a text
field. Keep them reachable with a thumb on the phone.

**Constraints for building it:** a single HTML file with vanilla JavaScript and CSS, no framework and no
build step. The core stays a canvas animation. Cards are rendered from small JSON objects (below). Text
contrast AA or better. Respect prefers-reduced-motion. Everything must work at 390px and 1440px.

**Card data contract** (what each card receives):
- clock `{time, date}`
- weather `{city, when, temp_c | high_c + low_c, conditions, feels_like_c, rain_chance_pct | rain_mm, wind_kmh, note, source}`
- calendar `{title, events: [{time, title, day?}]}`
- email `{title, emails: [{from, subject, snippet}]}`
- confirm `{title, text}` with Go ahead / Cancel
- nowplaying `{what}`
- note / vision / reminder `{title, text}`

**Deliverables:** mockups for all 10 screens, then HTML and CSS for them, then a component spec
(class names, spacing scale, radii, shadows, colour tokens, type scale, motion timings).
