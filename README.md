# yt-playlist-radio
Takes a YouTube playlist and converts it to an audio stream, similar to internet radio
- `/playlist.m3u` provides a playlist of songs to be played, supports track skipping, like a traditional playlist
- `/stream` provides a buffered audio stream

## How to run?
First set the environment variables as per `.env.template`, then just run it with gunicorn or something else (gunicorn comes bundled as part of the deps here)
```bash
uv sync

# macOS only: required to avoid Objective-C fork-safety crashes
export OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES

uv run gunicorn routes:app --bind 0.0.0.0:8000 -k gthread --threads 50 --workers 1 --timeout 0 --keep-alive 5
```

> **macOS note:** The `OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES` workaround must be set in your shell environment before starting gunicorn. Putting it in `.env` will not work because the crash happens during gunicorn's worker fork, before your application code loads `load_dotenv()`.
> Note that `--timeout 0` is a strict requirement if using `/stream` endpoint due to Gunicorn's default timeout policy
> Similarly, you should use a single persistent worker if you want everyone listening to the same stuff on `/stream`

## Example Landing Page
<img width="936" height="992" alt="image" src="https://github.com/user-attachments/assets/e70879ad-bdff-46b0-8018-130211d950a1" />

## Local Playlist
In case when fetching from YouTube takes too long for some reason, you can also specify a local `.radio` file. An example file has been included in this repo.

The radio code will parse links from this file as the playlist instead. Set `PLAYLIST_URL="PATH_TO_.RADIO_FILE"`

The file must end with `.radio`

## Playlist Auto-Refresh

By default, the app re-fetches the YouTube playlist every **60 minutes** in the background, so new tracks you add to the playlist show up automatically without restarting the server.

Set the interval in `.env`:

```
PLAYLIST_REFRESH_INTERVAL_MINUTES=60
```

- Set to `0` to disable auto-refresh.
- The refresh is atomic: listeners stay connected and the currently playing track is not interrupted.
- Removed tracks disappear from the playlist once the refresh completes.

---

## Deployment

The only supported way to host `yt-playlist-radio`:

### Raspberry Pi + Cloudflare Tunnel (self-hosted, home)

Run it on a home Pi with **no Nginx, no Certbot, and no router port forwarding**. `cloudflared` dials out to Cloudflare's edge, so it works behind carrier-grade NAT and keeps your home IP private. A home IP also avoids the YouTube bot-blocking that datacenter IPs run into, so yt-dlp fetches work without extra auth.

-> [docs/DEPLOY-PI.md](docs/DEPLOY-PI.md)

### YouTube cookies (bot blocks)

If yt-dlp ever returns **"Sign in to confirm you're not a bot"**, pass authenticated browser cookies via the `COOKIES_FILE` env var. The app injects `--cookies` into both its metadata and stream yt-dlp calls. Rarely needed on a home connection.

-> [docs/COOKIES.md](docs/COOKIES.md)
