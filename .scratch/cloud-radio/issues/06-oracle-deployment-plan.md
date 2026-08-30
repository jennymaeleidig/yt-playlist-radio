# 06 — Oracle deployment plan (decision)

Type: grilling
Status: resolved
Blocked by: 04, 05

## Question

Turn the Oracle facts (05) and the routing/cost-guardrail decisions (04) into the deployment
section of the spec. Decide:

1. Provisioning steps: tenancy/VCN/security-list setup, OL9 packages, uv + Python 3.13 install,
   app user, where the repo and `.env` live.
2. Process shape: systemd unit for gunicorn (single worker, `--timeout 0`), restart policy,
   memory/swap safeguards for 1 GB RAM.
3. Env for the cloud: proxy credentials in `.env`, `BASE_URL`, cookies policy (per 04),
   logging destination.
4. **Idle-reclamation tension** (surfaced by 05): Oracle reclaims Always Free instances below
   ~20% CPU/network over 7 days, but the cost guardrails from 04 stop all traffic when nobody
   listens. Decide the spec's stance: accept reclamation risk, add a cheap keep-alive, or
   document redeploy-as-code so a reclaimed VM is a non-event.
5. Smoke-check list: what the operator verifies right after deploy (stream starts, metadata
   flows, idle shutoff observed, no YouTube traffic when nobody listens).
6. README rewrite plan: replace the Pi + Cloudflare Tunnel section with this plan.

## Answer

Decided in a grilling session. Facts from 05 research (`research/oracle-free-tier.md`); routing/guardrail decisions from 04. Provisioning detail below is spec-ready.

### 1. Provisioning (redeploy-as-code is the spine)
- A single **`provision.sh` in the repo** performs the full setup so a reclaimed VM is a ~10-minute non-event: EPEL (`dnf search epel` to confirm `oracle-epel-release-el9` on the box) → RPM Fusion free → `dnf swap ffmpeg-free ffmpeg --allowerasing` → uv → `uv python install 3.13` → app user `radio` → clone repo to `/opt/yt-playlist-radio` → `uv sync` → swapfile (2 GB, `vm.swappiness=10`) → systemd units → firewalld (`--add-port=80/tcp,443/tcp --permanent`) → certbot.
- **Capacity workaround** (Oracle "out of host capacity"): upgrade account to Pay As You Go — Always Free resources stay free — and retry; document this in the README deploy section.
- OCI-side networking: one stateful ingress rule, TCP 80 + 443 from 0.0.0.0/0, in a Security List or NSG (NSG preferred per Oracle docs). Egress already allow-all.

### 2. Process shape
- **nginx** (user choice over caddy; OL9 AppStream) on 80/443, proxying to gunicorn bound on `127.0.0.1:8080`. TLS via **certbot / Let's Encrypt** on the operator's domain; HTTP→HTTPS redirect. nginx buffers disabled for the stream location (long-lived response).
- gunicorn systemd unit: `--workers 1 --timeout 0 --bind 127.0.0.1:8080`, `Restart=always`, `RestartSec=5`, `MemoryHigh=600M` / `MemoryMax=700M` (the `--timeout 0` worker-heartbeat kill is disabled by design, so systemd is the hang/crash backstop).
- 2 GB swapfile as OOM safety net for the 1 GB shape.

### 3. Cloud env
- `.env` at `/opt/yt-playlist-radio/.env`, `chmod 600`, owner `radio`, gitignored: DataImpulse `login`/`password` (proxied fetches per 04), `BASE_URL=https://<domain>`, email-alert SMTP creds (msmtp). **No `COOKIES_FILE`** (per 04). Logging: journald (default stdout/stderr capture of the units).

### 4. Idle-reclamation stance
- **Listener-gated CPU keep-alive** (user-requested): tiny systemd service polls the app's loopback status endpoint and, **only while zero listeners are connected**, runs a throttled busy-loop targeting **~27% CPU** (slightly over the 20% threshold for percentile headroom), cgroup-capped and low-priority so it never contends with a live stream. CPU alone breaks Oracle's idle conjunction (CPU <20% AND network <20%; memory applies to A1 only) — no fake network traffic.
- **Accept residual risk** with `provision.sh` as the backstop; keep-alive + script together make reclamation a non-event. Caveat recorded: Oracle could change the policy; the script is the durable answer.

### 5. Smoke-check list (post-deploy, operator runs)
1. `https://<domain>` loads and `/stream` plays audio.
2. Icy metadata updates in the player.
3. Pause the stream → yt-dlp/ffmpeg processes exit within ~1 s (idle shutoff observed: `pgrep` empty).
4. With no listeners: `nethogs`/VNIC metrics show no googlevideo traffic (no YouTube fetch when idle) — cost guardrail verified.
5. Kill DataImpulse creds (or bad password) → radio pauses with backoff, email alert arrives, auto-resumes after cooldown (fail-closed + alerting verified).
6. Keep-alive: with zero listeners, 95th-percentile CPU ≥ 20% on the instance metrics.

### 6. README rewrite
- Replace the Raspberry Pi + Cloudflare Tunnel deployment section with this plan (OCI Free Tier, PAYG note, provision.sh, smoke checks). Pi/Tunnel content moves to git history, not maintained.
