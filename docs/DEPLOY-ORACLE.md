# Deploy on Oracle Cloud Free Tier (Oracle Linux)

A repeatable guide to run `yt-playlist-radio` on an Oracle Cloud **Always Free** Ampere A1 instance running **Oracle Linux**. Uses `dnf`, the default `opc` user, `iptables`, Nginx, and Certbot.

> **Always Free limits (2026):** 2 OCPUs / 12 GB RAM / 200 GB block+boot storage. Keep the shape within those or it will be stopped.

---

## 1. Create the instance

1. Sign in at [cloud.oracle.com](https://cloud.oracle.com).
2. **Compute → Instances → Create Instance**.
3. **Name**: `yt-radio`.
4. **Placement**: pick your home region (capacity varies; try Ashburn/Phoenix if Ampere is full).
5. **Image**: **Oracle Linux** (e.g. Oracle Linux 9).
6. **Shape**: click **Change shape** → **Ampere** → **VM.Standard.A1.Flex**. Set **Number of OCPUs**: `2`, **Amount of memory**: `12 GB`.
7. **Networking**: choose **Create new virtual cloud network** and keep all the defaults (auto-created VCN, public subnet, Internet Gateway, route table, and a "Default Security List" are all you need). The default security list only opens SSH (22) inbound — you'll add 80/443 in Step 2A.
8. **Add SSH keys**: generate a new key pair and download the private key.
9. **Boot volume**: leave at the default **50 GB** (plenty) or raise it up to 200 GB total. **Use in-transit encryption** can stay default.
10. Click **Create** and wait for **RUNNING**.

> **Public IP note:** the instance wizard only offers an **ephemeral** public IP ("lifetime bound to the private IP"). That's fine — it persists across reboots and stop/start, and is only released if you **terminate** the instance. For a single long-lived server this is all you need.
>
> A **Reserved Public IP** (persistent across instance deletion, moveable between instances) can be created at **Networking → IP Management → Reserved Public IPs**, then assigned to the VNIC replacing the ephemeral one. Whether reserved IPs are free on an Always-Free-only tenancy is not clearly documented — check the Billing page for any resulting charge before relying on it.

---

## 2. Open the firewall (two layers)

Oracle blocks traffic at **both** the VCN Security List and the instance OS firewall.

### A. VCN Security List

1. On the instance details page, click the **Subnet** link.
2. Click **Security Lists → Default Security List**.
3. **Add Ingress Rules**:

| Source CIDR | IP Protocol | Destination Port Range |
| --- | --- | --- |
| `0.0.0.0/0` | TCP | `22` |
| `0.0.0.0/0` | TCP | `80` |
| `0.0.0.0/0` | TCP | `443` |

### B. Instance OS firewall (iptables)

Oracle Linux images come with `iptables` rules that default to allowing only SSH. SSH into the instance:

```bash
ssh -i ~/.ssh/your-key opc@YOUR_INSTANCE_PUBLIC_IP
```

Then open HTTP/HTTPS and persist them:

```bash
sudo iptables -I INPUT -p tcp --dport 80 -j ACCEPT
sudo iptables -I INPUT -p tcp --dport 443 -j ACCEPT
sudo iptables-save | sudo tee /etc/sysconfig/iptables
```

---

## 3. Install dependencies (Oracle Linux / dnf)

```bash
sudo dnf update -y
sudo dnf install -y oracle-epel-release-el9   # enables EPEL (for certbot)

# Re-enable in case EPEL is present but disabled:
sudo dnf config-manager --enable epel || true

# Core deps (EPEL provides certbot). Install separately from ffmpeg so a
# ffmpeg failure does not abort the whole transaction.
sudo dnf install -y git python3 python3-pip nginx certbot python3-certbot-nginx
```

Verify the core tools before continuing:

```bash
nginx -v && certbot --version && python3 --version
```


> **`ffmpeg` is not in EPEL, and `dnf install ffmpeg` deadlocks on aarch64 without extra setup** — the dependency chain needs `ladspa` (from the CodeReady Builder repo) and `rubberband`/`x264-libs`/`x265-libs` (from RPM Fusion free). Enable both, then install:

```bash
# 1. Enable Oracle's CodeReady Builder (CRB) — provides ladspa
sudo dnf config-manager --enable ol9_codeready_builder

# 2. Enable RPM Fusion free — provides ffmpeg, x264/x265 libs
sudo dnf install -y https://mirrors.rpmfusion.org/free/el/rpmfusion-free-release-9.noarch.rpm

# 3. Makecache so dnf sees the new repos
sudo dnf makecache

# 4. Install ladspa first (root of the dependency chain), then ffmpeg
sudo dnf install -y ladspa
sudo dnf install -y ffmpeg

ffmpeg -version | head -n 3   # expect "ffmpeg version 5.1.10"
```

> **Fallback if the RPM chain still fails on your arch:** install a self-contained static binary instead, then skip the `dnf install ffmpeg` line:

```bash
cd /tmp
curl -sL -o ffmpeg.gz  https://github.com/eugeneware/ffmpeg-static/releases/download/b6.1.1/ffmpeg-linux-arm64.gz
curl -sL -o ffprobe.gz https://github.com/eugeneware/ffmpeg-static/releases/download/b6.1.1/ffprobe-linux-arm64.gz
gunzip -f ffmpeg.gz ffprobe.gz
sudo install -m 0755 ffmpeg  /usr/local/bin/ffmpeg
sudo install -m 0755 ffprobe /usr/local/bin/ffprobe
ffmpeg -version | head -n 3
```

> On an x86_64 (AMD) Oracle instance, swap the static-binary URLs for `-linux-x64.gz`.


Install `uv`:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="$HOME/.local/bin:$PATH"   # for this session; installer also adds it to ~/.bashrc
uv --version   # expect uv 0.11.30 (or later)
```
---

## 4. Clone and configure

```bash
sudo mkdir -p /opt/yt-playlist-radio
sudo chown opc:opc /opt/yt-playlist-radio
git clone https://github.com/jennymaeleidig/yt-playlist-radio.git /opt/yt-playlist-radio
cd /opt/yt-playlist-radio
uv sync

cp .env.template .env
nano .env
```

At minimum set:

```env
PLAYLIST_URL="https://www.youtube.com/playlist?list=..."
BASE_URL="https://radio.x86-soundscape.jenny-page.online"
```

---

## 5. Run gunicorn as a systemd service

The service runs as the default `opc` user so `cache.json` can be written without permission fixes.

```bash
sudo nano /etc/systemd/system/yt-radio.service
```

```ini
[Unit]
Description=YouTube Playlist Radio
After=network.target

[Service]
Type=simple
User=opc
Group=opc
WorkingDirectory=/opt/yt-playlist-radio
Environment="PATH=/opt/yt-playlist-radio/.venv/bin:/home/opc/.local/bin:/usr/bin"
EnvironmentFile=/opt/yt-playlist-radio/.env
ExecStart=/opt/yt-playlist-radio/.venv/bin/gunicorn routes:app --bind 127.0.0.1:8000 -k gthread --threads 50 --workers 1 --timeout 0 --keep-alive 5
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

> **SELinux note:** if SELinux is enforcing, Nginx may be blocked from proxying to gunicorn. Allow it with: `sudo setsebool -P httpd_can_network_connect 1`.
>
> A second SELinux gotcha bites before Nginx is even involved: systemd can refuse to *execute* gunicorn because its interpreter (uv-managed Python under `~/.local/share/uv/python/`) carries the `data_home_t` label, which `init_t` is not allowed to exec. You'll see `status=203/EXEC` in the journal and an AVC `denied { execute } ... name="python3.13" ... tcontext=...data_home_t:s0` from `sudo ausearch -m AVC -ts recent`. Fix it by relabeling that tree as `bin_t` — see **Troubleshooting** below.
---

## 6. Point your domain at the instance

Before Nginx/Certbot can serve HTTPS, your hostname must resolve to the instance's public IP. The example below uses a Vercel-managed zone (`jenny-page.online`) with a nested subdomain `radio.x86-soundscape.jenny-page.online`. Any DNS provider works the same way.

### On the DNS provider (Vercel)

1. Vercel dashboard → **Domains** → click `jenny-page.online` → **Advanced Settings / Manage DNS Records**.
2. **Add Record**:
   - **Type**: `A`
   - **Name**: `radio.x86-soundscape`   *(Vercel auto-appends `.jenny-page.online`)*
   - **Value**: your instance's public IPv4 (from the Oracle console / your SSH host)
   - **TTL**: default / Auto
3. Save, then wait for propagation and verify from the instance (or any machine):

```bash
dig A radio.x86-soundscape.jenny-page.online +short
# should return your instance's public IP
```

> **Multi-label subdomains:** Vercel's "Name" field accepts multiple labels (e.g. `radio.x86-soundscape`), so the full record becomes `radio.x86-soundscape.jenny-page.online`. No separate record for the intermediate `x86-soundscape` label is required.
>
> **Ephemeral IP reminder:** the instance's public IP persists across reboots and stop/start, but is released if the instance is **terminated** — in that case re-point the A record at the new IP. A Reserved Public IP avoids that.

---

## 7. Configure Nginx

```bash
sudo nano /etc/nginx/conf.d/yt-radio.conf
```

```nginx
server {
    listen 80;
    server_name radio.x86-soundscape.jenny-page.online;

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

```bash
sudo nginx -t
sudo systemctl enable --now nginx
```

---

## 8. HTTPS with Certbot

> The A record from Step 6 must resolve before this will work — Certbot's HTTP-01 challenge needs to reach the instance on port 80.

```bash
sudo certbot --nginx -d radio.x86-soundscape.jenny-page.online
```

Certbot rewrites the Nginx config for TLS and installs a renewal timer automatically.

---

## 9. Verify

```bash
curl -I https://radio.x86-soundscape.jenny-page.online/stream
```

Open `https://radio.x86-soundscape.jenny-page.online` in a browser. Both `yt-radio` and `nginx` survive reboots via systemd.

---

## Troubleshooting

- **`status=203/EXEC` / `Permission denied` at spawn (SELinux exec denial)**: SELinux blocks systemd from executing uv's managed Python, which lives under `~/.local/share/uv/python/` with the `data_home_t` label. (The `.venv/bin/gunicorn` script's shebang points there.) Relabel it as `bin_t`:

  ```bash
  # requires policycoreutils-python-utils if semanage is missing:
  sudo dnf install -y policycoreutils-python-utils
  sudo semanage fcontext -a -t bin_t '/home/opc/.local/share/uv/python(/.*)?'
  sudo restorecon -Rv /home/opc/.local/share/uv/python
  sudo systemctl restart yt-radio
  ```

  Confirm with `ls -Z "$(readlink -f /opt/yt-playlist-radio/.venv/bin/python)"` — it should now show `bin_t`, not `data_home_t`. The `semanage fcontext` rule is persistent, so future `uv sync` reinstalls (even of a new Python version, since the path matches `(/.*)?`) keep the label.
- **PermissionError writing `cache.json`**: the service user can't write to the working directory. `sudo chown -R opc:opc /opt/yt-playlist-radio` and `sudo systemctl restart yt-radio`.
- **"403 / 502 Bad Gateway"**: verify the service is up (`systemctl status yt-radio`) and Nginx can reach `127.0.0.1:8000` (`curl -I http://127.0.0.1:8000`).
- **No public access**: confirm both firewall layers — VCN Security List ingress rules **and** the OS `iptables` rules (Step 2).
- **Can't get the Ampere shape ("Out of host capacity")**: try a different region or retry off-peak. The Always Free Arm allocation is shared and sometimes temporarily full.
