---
name: spotify
description: Control the user's Spotify on any device (PC, phone, speaker) - what's playing, queue a song, Spotify volume. Playing music uses spotify_play.
enabled: true
trusted: true
triggers: ["spotify", "what's playing", "what song", "queue", "on my phone"]
permissions:
  network: ["api.spotify.com", "accounts.spotify.com"]
  filesystem: ["data/spotify/token.json"]
---
spotify_play (built in) plays artists, playlists (including "my X playlist"), albums, songs; pass device when
the user names one ("on my phone"). spotify_now_playing for "what's playing". spotify_queue to add a song next.
