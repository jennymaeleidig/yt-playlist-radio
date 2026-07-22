# Self-host on a Raspberry Pi (Cloudflare Tunnel)

Run the station on a home Raspberry Pi with **no Nginx, no Certbot, and no router port forwarding**. Cloudflare Tunnel (`cloudflared`) dials out to Cloudflare's edge, so it works behind carrier-grade NAT and keeps your home IP private. HTTPS is terminated at the edge.

**Best for**: a personal station with a handful (1–5) of listeners. Sustained outbound bandwidth is limited by your home upload link — size your listener count to it (~192 kbps × listeners).

---

## 1. Prepare the Pi

```bash
sudo apt update
sudo apt install -y git python3 ffmpeg

# Install uv
curl -LsSf https://astral.sh/uv/install.sh | sh
```

## 2. Deploy the app

```bash
git clone https://github.com/jennymaeleidig/yt-playlist-radio.git ~/yt-playlist-radio
cd ~/yt-playlist-radio
uv sync

cp .env.template .env
nano .env
```

Set at least:

```env
PLAYLIST_URL="https://www.youtube.com/playlist?list=..."
BASE_URL="https://radio.x86-soundscape.jenny-page.online"
```

## 3. Run gunicorn as a systemd service

Bind to `127.0.0.1` — only the tunnel needs to reach it:

```bash
sudo nano /etc/systemd/system/yt-radio.service
```

```ini
[Unit]
Description=YouTube Playlist Radio
After=network.target

[Service]
Type=simple
User=pi
WorkingDirectory=/home/pi/yt-playlist-radio
Environment="PATH=/home/pi/yt-playlist-radio/.venv/bin:/home/pi/.local/bin"
EnvironmentFile=/home/pi/yt-playlist-radio/.env
ExecStart=/home/pi/yt-playlist-radio/.venv/bin/gunicorn routes:app --bind 127.0.0.1:8000 -k gthread --threads 50 --workers 1 --timeout 0 --keep-alive 5
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now yt-radio
sudo journalctl -u yt-radio -f
```

## 4. Install cloudflared

For a 64-bit Pi (Pi 3B+/4/5 running 64-bit Raspberry Pi OS):

```bash
wget -q https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-arm64.deb
sudo dpkg -i cloudflared-linux-arm64.deb
```

For a 32-bit Pi, replace `arm64` with `arm`.

## 5. Create and configure the tunnel

```bash
cloudflared tunnel login
cloudflared tunnel create yt-radio
```

Note the tunnel UUID printed. Then create the config:

```bash
sudo mkdir -p /etc/cloudflared
sudo nano /etc/cloudflared/config.yml
```

```yaml
tunnel: <your-tunnel-uuid>
credentials-file: /root/.cloudflared/<your-tunnel-uuid>.json

ingress:
  - hostname: radio.x86-soundscape.jenny-page.online
    service: http://localhost:8000
  - service: http_status:404
```

Point DNS at the tunnel (requires the domain's zone to be on Cloudflare-managed nameservers):

```bash
cloudflared tunnel route dns yt-radio radio.x86-soundscape.jenny-page.online
```

## 6. Run cloudflared as a service

```bash
sudo cloudflared service install
sudo systemctl enable --now cloudflared
```

## 7. Verify

```bash
curl -I https://radio.x86-soundscape.jenny-page.online/stream
```

Open `https://radio.x86-soundscape.jenny-page.online` in a browser. Both `yt-radio` and `cloudflared` survive reboots — the station starts automatically once the Pi is up.

## Caveats

- **Home upload is the bottleneck**: each listener uses ~192 kbps upstream. 1–5 listeners is comfortable on a typical fiber/cable upload link; more than that needs bitrate headroom or a non-home route.
- **Cloudflare ToS**: small personal audio streams are fine; consistently large sustained outbound may attract scrutiny.
- **Tunnel → gunicorn directly**: there is no Nginx buffering layer, so Cloudflare's edge buffers the client side; streaming still works because Cloudflare disables caching on live responses.
- **Persistent cache**: `cache.json` is written to the working directory, so the metadata cache survives restarts.
