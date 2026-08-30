# 04 — Fetch routing matrix and cost guardrails (decision)

Type: grilling
Status: resolved
Blocked by: 01, 02, 03

## Question

With the facts from the baseline run (01), DataImpulse pricing (02), and yt-dlp's proxy surface (03):
decide the routing matrix and the cost-guardrail requirements the spec must contain.

Sub-decisions to put to the human:

1. Which fetch types go through DataImpulse — playlist listing, per-track metadata, media
   streaming — given the GB/cost table from 02? (All three was the leaning; media streaming is
   the expensive one.)
2. Cost guardrails the spec mandates: exact idle-shutoff semantics (kill yt-dlp/ffmpeg the moment
   the last subscriber leaves, even mid-download), any rate caps, and failure containment so a
   proxy outage can't burn quota or wedge the radio loop (01's baseline findings feed this).
3. Whether `COOKIES_FILE` stays alongside the proxy or is dropped for the proxied path.

## Answer

Decided in a grilling session (decisions by the human, facts from tickets 01–03 research):

### 1. Routing matrix
- **All three fetch types** (playlist listing, per-track metadata extraction, media streaming) route through DataImpulse. Media is the cost driver (~0.086 GB/h at 192 kbps upstream), but cost is bounded by the idle shutoff and the low-bitrate target below.
- One **sticky session per track fetch** (`sessid.<id>` param, so extraction + download share one exit IP) — avoids googlevideo IP-lock 403s from mixing rotating-exit metadata with different-IP media requests.
- **No direct YouTube traffic from the Oracle host, ever** (fail closed; see guardrails).

### 2. Upstream audio quality / bandwidth
- Format selector targets **≤56 kbps** upstream with a fallback chain: `bestaudio[abr<=56]` → `bestaudio[abr<=96]` → `bestaudio` (YouTube doesn't serve every video at every bitrate).
- Listener-side MP3 transcode unchanged; the upstream bitrate is a quality ceiling, not the listener bitrate.
- Expected cost ≈ **$2/mo** at ~3 h/day listening (~0.025 GB/h effective).

### 3. Cost guardrails the spec mandates
- **Idle shutoff** stays as-is: kill yt-dlp/ffmpeg when the last listener disconnects, even mid-download (validated in 01: ~0.5 s).
- **Dead-track retry storm fixed**: exponential backoff on track failures + a **failure budget** (5 consecutive failed tracks, or within 10 min) → supervisor pauses.
- **Proxy outage containment: fail closed.** Proxy down (connection refused, timeouts) → backoff retries, never fall back to direct/unproxied YouTube traffic.
- **`407 TRAFFIC_EXHAUSTED` is terminal**: pause immediately, no retries. DataImpulse **auto-recharge stays off** (current state: 5 GB free trial — that is the hard spend ceiling).
- yt-dlp knobs: `--retry-sleep` backoff (e.g. `linear=1:30`), `--socket-timeout` (~20 s) for hung-proxy fail-fast, modest `--retries`, `--http-chunk-size` ≤ 10 MB (YouTube throttles larger chunks).

### 4. Cookies
- **`COOKIES_FILE` removed from the proxied path** (delete from deployment config now). Spec documents the future path: if a bot-wall appears during Oracle validation, re-add cookies **exported through the same proxy exit** (yt-dlp FAQ same-IP rule). Validation may confirm cookies are never needed.

### 5. Alerting on pause
- Trigger: failure budget exhausted or traffic exhausted. Action: **email alert** (mechanism at spec time; e.g. SMTP/msmtp) **+ auto-resume** after a ~30 min cooldown with a fresh failure budget. Self-healing for transient proxy outages; the human still hears about it.
