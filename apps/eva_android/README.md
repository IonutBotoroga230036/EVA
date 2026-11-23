# E.V.A. for Android (v0.3 milestone 9)

The app shows your real E.V.A. interface, the same page, orb, animation and panels as on the PC, loaded from
your PC over https. Her listening, Whisper, the brain, Kokoro and every safety rule stay on the PC.

## What the app adds around the page
- Android's microphone permission, handed to the page for the microphone only (camera and the rest refused).
- Her voice plays without a tap first.
- Address and token in Android's encrypted storage; the token goes in as the pairing cookie, never in a URL.
- Trust for Tailscale's certificate and for E.V.A.'s own CA if you installed it (this app only). No http at all.
- The mic is released whenever the app leaves the screen.
- Back button: Reload, Connection settings, Leave.
- Optional: "Use E.V.A. for long-press" instead of Gemini (switch back in Settings > Apps > Default apps >
  Digital assistant app). Gemini stays your assistant unless you choose this.

## New in round 2
- Alarms and timers: "Eva, set an alarm for 7:30" goes into your phone's clock app (the app must be open).
- Her voice as media, not a phone call (settings: "Media" or "Call"). In Media mode the mic pauses while she
  speaks, so talk after she finishes, or tap to interrupt.

## New in v0.3.1: hands-free ("Eva" with the screen off)

Menu (back button) > **Hands-free: off** turns it on. Android asks for two things once: notifications (the
"listening" notification, with a Stop button, is how Android lets an app use the mic in the background) and
**Display over other apps** (so she can open over any app or the lock screen). Then:

- The phone runs your wake word model itself (`WakeService.kt`, `WakeEngine.kt`, ONNX Runtime), the same three
  models the PC trained, downloaded from your PC with your token. Retrain on the PC and the phone picks it up.
  Nothing leaves the phone until it hears "Eva".
- "Eva, what time is it" works in one breath: it keeps recording until you pause and sends that to your PC.
- The overlay (`OverlayActivity.kt`, page `/overlay`) is the **Core** design: edge glow and your desktop orb,
  see-through, closing by itself a few seconds after you're done.
- **Locked phone:** time, weather, music, lights, timers and alarms work; adding a meeting, reminder or shopping
  item works (she reads it back); anything private (email, your calendar, memories, notes) asks you to unlock,
  then carries on by itself. **"Eva, work while locked"** (or status panel > Phone > Just work) makes everything
  work locked; it needs one unlock to switch on, and "ask me to unlock again" switches it back. Sending,
  deleting and paying still wait for your spoken yes. Anyone who says "Eva" near your phone can then hear your
  private things.
- While the app is open, the page listens as before and the service pauses.
- After a phone restart, open E.V.A. once: hands-free comes back on (the token is kept only in memory by the
  service, never written by it).
- In media mode, "Eva" now interrupts her while she speaks (plain talking doesn't, since the mic hears her too).
- Your PC double-checks every wake (Whisper must hear "Eva"), so talking near the phone doesn't open her.
- **Stay dark** ("Eva, stay dark", or the Phone switch in the status panel): with the screen off she answers by
  voice only and the screen stays off; follow-ups need no "Eva"; "that's all" ends it. "Stay dark off" lights
  the screen again. Alarms and timers work in both, and from the overlay.

## New in v0.3.2: messages (WhatsApp, Instagram) and Romanian

Menu > **Messages: off** turns it on. Android opens its **Notification access** screen once: allow E.V.A.
(if the switch is greyed out: Settings > Apps > E.V.A. > the three dots > **Allow restricted settings**, then
try again). Then:

- Each new WhatsApp, WhatsApp Business or Instagram chat message (sender, text, chat name) goes to your PC.
  Nothing else: no chat history, no contacts, no other apps. On the PC it stays in memory for 24 hours, never
  on disk; the log names only who and which app.
- When one matters (Mom, "urgent" or "call me", your name in a group, a direct question) she says it at once:
  in the open E.V.A. windows, and on the phone itself when the app isn't open. Not during a call, Do Not
  Disturb, or her quiet hours (23:00-08:00).
- "Eva, any messages?" reads the new ones. "Eva, reply to Mom: I'll be home at 7" shows and says the exact text
  first, in Romanian for Mom ("Ajung acasă la 7."), and sends it only after your yes (or "da").
- Replies use the notification's own Reply button, so they only work while that notification is still on the
  phone. If you already opened or swiped it away, she says so.
- After a phone restart, open E.V.A. once so the messages know where your PC is (kept in memory only).

## New on Nov 23: location, meeting recording, Share > E.V.A.

- Menu > **Location: on** (needs Hands-free on; Android asks for location once, "while using the app" is
  enough). The phone tells your PC where it is every few minutes, in the same notification as hands-free. Then:
  "remember this place as home", "remind me to buy milk when I leave work", "when I get home, turn on the
  lights", "remember where I parked" / "where did I park?". "Track my location" keeps a 30-day trail on the PC
  (off by default; "stop tracking my location" deletes it).
- **"Eva, record this meeting"** (Hands-free on): the phone records and sends the audio to your PC every 15 s.
  "Eva" still works meanwhile, answering by voice. "Eva, stop recording": notes (summary, decisions, to-dos,
  the transcript) a few minutes later.
- **Share > E.V.A.** from Pixel Recorder (a recording or its transcript) or any audio app: the same notes.
- "Tell Mom I'm on my way" works without a message to reply to: through her chat's reply button if she wrote
  since the phone started, otherwise WhatsApp opens with the text written (add her number under
  messages.numbers in config/settings.local.yaml) and you tap send.

## Build (after unzipping, once)
```powershell
cd "D:\Project E.V.A\eva\apps\eva_android"
Unblock-File .\tool_apply_overlay.ps1
flutter create --org nl.ionut --project-name eva_android --platforms android .   # only if android\ doesn't exist
.\tool_apply_overlay.ps1
flutter pub get
flutter run -d adb-4A161FDAS003K7-T8Bzjq._adb._tcp          # or plug in USB and just: flutter run
```

## Connect
- **Tailscale (recommended)**: `https://<your-pc>.<tailnet>.ts.net` (docs/REMOTE_ACCESS.md). Works at home and away.
- **Home Wi-Fi**: `https://192.168.188.101:8443`, after installing E.V.A.'s certificate in Android as a
  **CA certificate** (docs/PHONE_SETUP.md, step 4).
- Token: `data\remote_token.txt` on the PC.

## If she can't hear you
Watch E.V.A.'s terminal when you tap the mic. She now says exactly what's wrong:
- `mic audio is arriving`: the phone's mic reached her.
- `a listening window opened, but this device has sent no mic audio at all`: the page never got the mic
  (permission, or the certificate isn't trusted so the page isn't secure).
- `no mic audio has arrived for N s`: the mic stopped on the phone.
- `mic audio is arriving but it is silent`: another app holds the mic, or it's muted.

## Limits of this version
- Messages: WhatsApp and Instagram only (not SMS yet), and replies only while the notification is there.
- The Romanian voice is Piper's "Mihai", the only Romanian Piper voice (male).
