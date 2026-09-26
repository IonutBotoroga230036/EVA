# E.V.A. voice protocol (v0.2.5)

One WebSocket at `ws://<host>:8001/ws` carries everything: text turns, her voice, and your microphone.
The browser interface speaks it today; the Android app (v0.3) speaks the same protocol, so the app only
has to capture audio, play WAV chunks, and render events.

## Handshake (server -> client)

| Event | Fields | Meaning |
|---|---|---|
| `hello` | `tts: "kokoro" \| "browser"` | Her voice comes from the server (Kokoro) or the client must speak. |
| `phrase` | `key: "wake"`, `text`, `audio` (base64 WAV) | Cached "Yes, sir?" in her real voice. Only with Kokoro. |
| `stt` | `engine: "server" \| "browser"`, `rate`, `detail` or `reason` | `server`: stream the mic. `browser`: use on-device speech recognition and send text. Can arrive again later (for example if Whisper fails), and the client must switch. |

## Microphone (client -> server)

- **Audio:** binary WebSocket frames, PCM16 little-endian, mono, **16 kHz**. Any frame size works; 512 samples (32 ms) is ideal. Frames over 256 KB are dropped.
- **Other rates:** send `{"type": "audio_format", "rate": 48000}` first and the server resamples. 16 kHz is preferred.
- **Echo cancellation:** enable the platform's AEC, noise suppression and auto gain (browser `getUserMedia` constraints; Android `VOICE_COMMUNICATION` audio source), so her own voice doesn't reach the server.

## Listening controls (client -> server)

| Message | Meaning |
|---|---|
| `{"type": "listen", "arm": 8000}` | Accept the next utterance that starts within 8 s as a command, no wake word. Tap on the mic = 8000; follow-up after her reply = 7000. `arm: 0` closes the window. |
| `{"type": "listen", "wake": true}` | Wake-word mode: any utterance with "Eva" / "Hey Eva" in its first three words, or as its last word, is a command. `false` turns it off. |
| `{"type": "speaking", "on": true}` | Her voice started playing on this device. `false` when it stops. Needed for barge-in. |
| `{"type": "stop"}` | Stop her reply now (tap during speech). |
| `{"type": "message", "text": "...", "voice": false}` | A typed (or on-device recognized, `voice: true`) request. |

Without `arm` or `wake`, the server runs VAD only (for barge-in) and never transcribes. The Android app with an
on-device "Hey Eva" model sends `arm` when its wake word fires, streams audio, and stops streaming when idle.

## Listening events (server -> client)

| Event | Meaning | Typical UI |
|---|---|---|
| `listen` `state: "speech"` | You started talking inside an armed window. | Listening animation. |
| `listen` `state: "pause"` | A pause, but Smart Turn says you're not finished. | Keep listening. |
| `listen` `state: "end"` | Turn complete, transcribing. | Thinking. |
| `listen` `state: "noise"` | Too short, or Whisper heard nothing. The window stays open if time is left. | Back to idle if nothing follows. |
| `listen` `state: "timeout"` | The window closed in silence. | Idle. |
| `wake` `text` | She heard her name alone and armed an 8 s command window. | Play the cached wake phrase, listen. |
| `heard` `text`, `stt: true` | The command she heard (after vocabulary correction). A normal turn follows. | Show as your message. |
| `barge_in` `stage: "duck"` | You started talking over her. | Lower her volume. |
| `barge_in` `stage: "stop"` | You kept talking: her reply is cancelled, your words become the command. | Stop audio, listen. |
| `barge_in` `stage: "resume"` | It was only a blip. | Restore her volume. |

Then the usual turn events follow: `turn_start`, `ack` / `token` / `widget` / `final`, `audio` (one base64 WAV
per sentence with `turn` and `seq`, played strictly in `seq` order), `audio_end`.

## Pipeline (server)

pre-roll ring (700 ms) -> Silero VAD (Pipecat, CPU) -> Smart Turn v3 (Pipecat, CPU; waits through thinking
pauses up to 3 s) -> faster-whisper (GPU if available, else CPU; Vocabulary terms as hint words) -> gate
(armed window, wake word, barge-in, echo guard) -> the orchestrator, exactly like a typed message.
Settings: `voice.pipeline` and `voice.stt` in `config/settings.yaml`. Status: `GET /api/status` -> `stt`.

## Security note

Until milestone 2 (local network safety), any device that can reach port 8001 can use this socket.
Keep E.V.A. on your home network until then.
