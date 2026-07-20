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

This section covers running `yt-playlist-radio` on a VPS or cloud instance with a reverse proxy and HTTPS. It assumes a Debian/Ubuntu-like server.

### 1. Server requirements

- **OS**: Ubuntu 22.04 LTS (or similar)
- **RAM/CPU**: 1 GB RAM and 1 CPU is plenty for a small personal stream
- **Dependencies**: `git`, `python3`, `uv`, `nginx`, `certbot`

### 2. Clone and install

```bash
sudo apt update
sudo apt install -y git python3 nginx certbot python3-certbot-nginx

# Install uv if you don't have it
curl -LsSf https://astral.sh/uv/install.sh | sh

git clone https://github.com/pinapelz/yt-playlist-radio.git /opt/yt-playlist-radio
cd /opt/yt-playlist-radio

uv sync
```

### 3. Configure environment

Copy the template and edit:

```bash
cp .env.template .env
nano .env
```

At minimum set:

```
PLAYLIST_URL="https://www.youtube.com/playlist?list=..."
BASE_URL="https://radio.example.com"
```

### 4. Run with systemd

Create a service so the app starts on boot and restarts if it crashes:

```bash
sudo nano /etc/systemd/system/yt-radio.service
```

```ini
[Unit]
Description=YouTube Playlist Radio
After=network.target

[Service]
Type=simple
User=www-data
Group=www-data
WorkingDirectory=/opt/yt-playlist-radio
Environment="PATH=/opt/yt-playlist-radio/.venv/bin"
EnvironmentFile=/opt/yt-playlist-radio/.env
ExecStart=/opt/yt-playlist-radio/.venv/bin/gunicorn routes:app --bind 127.0.0.1:8000 -k gthread --threads 50 --workers 1 --timeout 0 --keep-alive 5
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Then enable and start it:

```bash
sudo systemctl daemon-reload
sudo systemctl enable yt-radio
sudo systemctl start yt-radio
```

Check logs with:

```bash
sudo journalctl -u yt-radio -f
```

### 5. Configure Nginx

Create a site config:

```bash
sudo nano /etc/nginx/sites-available/yt-radio
```

```nginx
server {
    listen 80;
    server_name radio.example.com;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # Important for long-lived /stream connections
        proxy_buffering off;
        proxy_read_timeout 86400;
        proxy_send_timeout 86400;
    }
}
```

Enable it:

```bash
sudo ln -s /etc/nginx/sites-available/yt-radio /etc/nginx/sites-enabled/
sudo rm /etc/nginx/sites-enabled/default  # optional
sudo nginx -t
sudo systemctl restart nginx
```

### 6. HTTPS with Certbot

```bash
sudo certbot --nginx -d radio.example.com
```

Certbot will modify the Nginx config automatically. Renewal is handled by a systemd timer.

### 7. Firewall

Open the ports Nginx listens on:

```bash
sudo ufw allow OpenSSH
sudo ufw allow 'Nginx Full'
sudo ufw enable
```

Or if using another firewall (e.g., Oracle Cloud, AWS, etc.), open **TCP 80**, **TCP 443**, and **TCP 22** at the cloud provider level as well as on the instance.

### 8. Verify

Open `https://radio.example.com` in a browser, or test the stream with:

```bash
curl -I https://radio.example.com/stream
```
