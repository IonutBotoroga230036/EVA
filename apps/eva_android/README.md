# E.V.A. for Android (v0.3 milestone 9)

The phone is E.V.A.'s microphone, speaker and screen. Listening (Silero VAD, Smart Turn, Whisper), the brain,
her voice (Kokoro) and every safety rule stay on your PC. The app speaks the same protocol as the web
interface: docs/VOICE_PROTOCOL.md.

## What this first version does
- Connects over **https/wss only** with your remote token (kept in Android's encrypted storage).
- **Anywhere** through Tailscale (milestone 8), or on **home Wi-Fi** with E.V.A.'s certificate pinned.
- Streams your voice (PCM16, 16 kHz, mono, Android echo cancellation) while the app is on screen; tap the orb
  to talk; follow-ups without "Eva"; talk over her to interrupt (she ducks, then stops).
- "Wake word" switch: say "Eva, ..." hands-free **while the app is open**.
- Plays her voice sentence by sentence in order, shows captions, the transcript, and Yes / No buttons for
  anything she asks you to confirm.
- **Default assistant**: "Make E.V.A. my phone assistant" in settings. Then long-press power (or your
  assistant gesture) opens E.V.A. already listening, instead of Google.

## Build it (Windows, once)
1. Install Flutter (stable) and Android Studio's SDK; enable USB debugging on the phone.
2. In PowerShell:
   ```powershell
   cd "D:\Project E.V.A\eva\apps\eva_android"
   flutter create --org nl.ionut --project-name eva_android --platforms android .
   .\tool_apply_overlay.ps1
   flutter pub get
   flutter run            # phone plugged in; or: flutter build apk --release
   ```
   `flutter create .` only adds the missing Android project files; it keeps `lib/` and `pubspec.yaml`.
3. In the app: settings, then your address and token (`data\remote_token.txt` on the PC).

## Connect
- **Tailscale (recommended)**: address `https://<your-pc>.<tailnet>.ts.net`. No certificate step.
- **Home Wi-Fi**: E.V.A. in network mode, then in the app "Fetch" with the PC's address, compare the fingerprint
  with E.V.A.'s terminal, "It matches", address `https://192.168.x.x:8443`.

## Honest limits of this version
- "Hey Eva" works only while the app is open. Hands-free with the screen off needs an on-device wake-word
  service in the background (a foreground service with a small wake-word model): the next step.
- Long-press power opens the app full screen. An overlay above whatever you're doing needs a
  VoiceInteractionService: planned.
- Android forgets the assistant choice when you reinstall the app during development; pick it again.
- No push notifications yet (reminders still come via Telegram).
