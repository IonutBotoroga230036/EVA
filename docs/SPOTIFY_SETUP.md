# Connecting E.V.A. to Spotify

About 5 minutes, once. Playback control (play, pause, skip, queue, choosing a device) needs **Spotify Premium**.

## 1. Create a Spotify app
1. Go to https://developer.spotify.com/dashboard and log in with your Spotify account.
2. **Create app**. Name `EVA`, any description.
3. Redirect URI, exactly: `http://127.0.0.1:8888/callback` (Spotify does not accept `localhost`; it must be `127.0.0.1`).
4. Tick **Web API**. Save.
5. Open the app's **Settings** and copy the **Client ID**. No client secret is needed: E.V.A. signs in with PKCE.

## 2. Tell E.V.A.
In `config/settings.local.yaml`:

    spotify:
      client_id: "paste-your-client-id-here"

## 3. Sign in
From `D:\Project E.V.A\eva` with the venv active:

    python -m core.spotify

Approve in the browser; you'll see "E.V.A. is connected to Spotify". Or just ask her to play something.

## Using it
- Spotify has to be open on the device you want to use (the PC app or the app on your phone).
- "Play The Weeknd", "Play my Chill Evenings playlist", "Play jazz on my phone", "What's playing?",
  "Add Blinding Lights to the queue", "Next song", "Pause".
- In development mode only your own account (and people you add under User Management) can use the app,
  which is exactly right for a personal assistant.

## Troubleshooting
- "INVALID_CLIENT: Invalid redirect URI": the URI in the dashboard must match `http://127.0.0.1:8888/callback` exactly.
- Nothing plays: open Spotify on the PC or phone first; the API can only control devices where Spotify runs.
- "Spotify only allows that with a Premium account": the account is Free; she falls back to opening Spotify.
- To disconnect: delete `data/spotify/token.json`.
