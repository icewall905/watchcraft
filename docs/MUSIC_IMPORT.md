# Import music playlists

In Music Videos, choose **Import playlist**, select YouTube or Music Assistant, and optionally give the imported playlist a name. Imports create an editable playlist in the active WatchCraft profile. The source playlist is unchanged; imports are snapshots.

YouTube accepts a YouTube or YouTube Music playlist link containing `list=…`. Public playlists work without extra setup; private playlists require the existing server-side YouTube cookie configuration. Explicit imported videos are preserved, including audio uploads. Deleted or unavailable entries without a video ID are skipped.

Music Assistant **2.11 or newer** is required for bounded playlist reads. Older versions must be upgraded; WatchCraft does not request an unbounded playlist as a fallback.

For Music Assistant, set these values in the WatchCraft server's `.env` file:

```dotenv
MUSIC_ASSISTANT_URL=http://YOUR_MA_SERVER:8095
MUSIC_ASSISTANT_TOKEN=YOUR_LONG_LIVED_ACCESS_TOKEN
```

Create the token in **Music Assistant → Settings → Profile**. Use the direct server address, rather than a Home Assistant ingress address. Restart/recreate the API container after changing the environment. The Compose service already reads `.env` through `env_file`; keep the token on the server.

Paste a Music Assistant playlist URI, for example `library://playlist/13` or a provider's `provider://playlist/id` URI. WatchCraft reads through the configured server using the token's permissions. YouTube Music tracks retain their video IDs. Tracks from other providers are matched by both artist and title against WatchCraft's official music video search; unmatched tracks are skipped. This imports playable YouTube versions, rather than the original provider's audio.

Imports inspect at most the first 300 entries, keep their order, and remove repeated video IDs. The result reports imported, skipped and duplicate counts, and warns when the source exceeds the limit. WatchCraft never fetches a user-supplied Music Assistant server address.

Protocol reference: [Music Assistant API](https://www.music-assistant.io/api/) (`POST /api`, bearer token, `music/playlists/playlist_tracks`).
