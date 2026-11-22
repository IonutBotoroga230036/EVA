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
  then carries on by itself.
- While the app is open, the page listens as before and the service pauses.
- After a phone restart, open E.V.A. once: hands-free comes back on (the token is kept only in memory by the
  service, never written by it).
- In media mode, "Eva" now interrupts her while she speaks (plain talking doesn't, since the mic hears her too).

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
- "Eva, ..." hands-free works while the app is on screen (the ear button). Screen-off wake word: milestone 9b.
- Opens full screen; an overlay above other apps needs a VoiceInteractionService (9b).
