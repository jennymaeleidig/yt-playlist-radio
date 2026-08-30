# Research: Routing yt-dlp through DataImpulse — endpoint, cost, policy

Ticket: what does it take to route yt-dlp traffic through DataImpulse (https://docs.dataimpulse.com/), and what does it cost?

Researched against primary sources (DataImpulse docs + homepage pricing). All claims cite a source URL. Items not confirmable from primary sources are marked UNCERTAIN with the reason.

---

## 1. Proxy endpoint format (residential pool)

- **Host:** `gw.dataimpulse.com` (DNS hostname, recommended) or IP `74.81.81.81`. Source: https://docs.dataimpulse.com/proxies/connection-hosts.md
- **Ports:**
  - Rotating HTTP/HTTPS: **823**; rotating SOCKS5: **824**. Source: https://docs.dataimpulse.com/proxies/types-of-connections.md
  - Sticky: **ports 10000–20000** (any protocol). IP is bound to the port for 1–120 min (default 30 min when unset/`0`). Source: https://docs.dataimpulse.com/proxies/types-of-connections.md
- **Auth scheme:** plain **username:password**, generated in the dashboard's "Proxy Access" section (resettable). No token-in-username by default. IP whitelisting is also available (connect without credentials). Sources: https://docs.dataimpulse.com/authentication-methods/user-pass-authentication.md, https://docs.dataimpulse.com/authentication-methods.md
- **Parameters go in the username**, appended after the login with `__` delimiter, `key.value;key2.value` format:
  - Pin geography (country, free): `login__cr.de:password@gw.dataimpulse.com:823` — multiple countries comma-separated. Source: https://docs.dataimpulse.com/proxies/parameters.md
  - Sticky session without sticky port: `sessid.<any-string>` pins a specific IP for **30 minutes** (e.g. `login__cr.au;sessid.123:password@...`). Source: https://docs.dataimpulse.com/proxies/parameters/session-id.md
  - Control rotation interval on sticky: `sessttl.<minutes>` (e.g. `cr.fr;sessttl.60`). Source: https://docs.dataimpulse.com/proxies/parameters/session-interval.md
  - State/city/ZIP/ASN targeting: `st.` / `city.` / `zip.` / `asn.` params — billed at **double** the standard rate, returns `503 NO_RAY` if no IP available. Source: https://docs.dataimpulse.com/proxies/targeting.md
  - Country targeting/exclusion and ASN *exclusion* are free (Default Targeting). Sources: https://docs.dataimpulse.com/proxies/targeting.md, https://docs.dataimpulse.com/proxies/targeting/default-targeting.md
- **Protocols:** HTTP, HTTPS, SOCKS5 (and UDP per https://docs.dataimpulse.com/proxies/udp.md). Source: https://docs.dataimpulse.com/proxies/protocols.md

## 2. Pricing model

From https://dataimpulse.com/ (public pricing/FAQ) and https://docs.dataimpulse.com/resellers/deposit-calculation-update.md:

| Pool | Price per GB |
|---|---|
| Residential | **$1/GB** |
| Datacenter | **$0.50/GB** |
| Mobile | **$2/GB** |
| Premium Residential | **$5/GB** |

- **Model:** pay-as-you-go, no subscription, **traffic never expires**. Volume discount: >1 TB → $0.80/GB; 5 TB+ custom pricing. Source: https://dataimpulse.com/
- **Minimum spend:** a **$50 deposit provides 50 GB** of residential traffic (coefficient x1; datacenter x0.5, mobile x2, premium x5). Source: https://docs.dataimpulse.com/resellers/deposit-calculation-update.md. The homepage pricing tables list minimum purchase buckets (residential 5 GB, datacenter 10 GB, mobile 2.5 GB, premium 1 GB) but exact per-bucket dollar amounts are not shown — UNCERTAIN whether the smallest bucket can be bought for less than the $50 deposit; the $50 deposit figure is the one documented.
- **Auto-recharge** exists: when balance/traffic falls below a threshold, a new order is auto-created and charged (card/PayPal). Source: https://docs.dataimpulse.com/billing/auto-recharge.md
- **Quota exhaustion mid-request:** the proxy returns **`407 TRAFFIC_EXHAUSTED`** on HTTP (SOCKS5: auth denied). New requests fail until you top up; **docs do not describe what happens to an in-flight download** when the quota runs out mid-transfer — UNCERTAIN (documented behavior only covers new request attempts). Sources: https://docs.dataimpulse.com/errors.md, https://docs.dataimpulse.com/billing/auto-recharge.md
- Other limits: **2000 threads** (concurrent connections) per plan, more via support+KYC (`407 THREADS_EXHAUSTED`). Outbound ports restricted to 80, 443, 53, 8080, 8443, etc. — fine for HTTPS media. Sources: https://docs.dataimpulse.com/proxies/threads.md, https://docs.dataimpulse.com/port-access.md

## 3. Bandwidth exposure (192 kbps audio stream, 24/7)

Math (decimal GB, as proxy providers bill):

- 192 kbps = 24 kB/s = **86.4 MB/hour** ≈ 0.0864 GB/h
- 24/7 month (730 h): **≈ 63 GB/month** (62.2–63.1 GB depending on month length)

| Routing scenario | Monthly GB | Monthly cost |
|---|---|---|
| Full stream via **residential** ($1/GB) | ~63 GB | **~$63/mo** |
| Full stream via residential **with target filter** (2×) | ~63 GB | **~$126/mo** |
| Full stream via **datacenter** ($0.50/GB) | ~63 GB | **~$31/mo** |
| Full stream via residential at >1 TB volume tier | — | $0.80/GB (never reached at this volume) |
| **Metadata-only** (playlist listing + per-track JSON, no media) | ~1–3 GB est. | **~$1–3/mo** |

Metadata estimate is mine, not from DataImpulse: yt-dlp's per-track extraction (client JSON/watch pages) is typically a few hundred KB to ~2 MB per track; refreshing a few hundred tracks plus the playlist index monthly lands at roughly 1–3 GB. Actual cost is cents-to-dollars. UNCERTAIN (no primary source; depends on playlist size, refresh cadence, and which yt-dlp client extraction is used).

**Key takeaway:** pushing the audio media itself through the proxy is the whole cost (~$63/mo residential). If only metadata goes through the proxy and media downloads go direct, cost collapses to ~$1–3/mo — but see §4's IP-consistency caveat before splitting routes.

## 4. Terms/policy on streaming media + IP rotation behavior

- **Blocked websites:** the published block list (https://docs.dataimpulse.com/blocked-websites.md) covers government, banking/payments, ticket/traffic-monetization sites, 4chan, openstreetmap, etc. **YouTube / googlevideo are NOT on the list.** Unblocking exists for higher-trust tiers only.
- **Streaming-media download policy:** the docs contain **no explicit policy** on downloading streaming media or video/audio files through the proxy. UNCERTAIN — no published ToS page was reachable at `dataimpulse.com/terms*` variants (404 during research); no restriction or allowance is documented. If this matters, ask support@dataimpulse.com before committing.
- **IP rotation during a long download:**
  - Rotating pool: IP changes "with each new request" — within one HTTP request/connection the IP is fixed. A single media download to `googlevideo.com` is typically one (or few range) request(s) on one connection, so it survives *between-request* rotation, but multi-request downloads (yt-dlp range requests, retries) can hop IPs mid-file on the rotating pool. Source: https://docs.dataimpulse.com/proxies/types-of-connections.md
  - Sticky ports / `sessid` pin an IP for 30 min default (1–120 min configurable), but **not guaranteed**: "if the IP provider you are using goes offline, your IP will be automatically replaced" mid-session. Sources: https://docs.dataimpulse.com/proxies/parameters/session-id.md, https://docs.dataimpulse.com/proxies/parameters/session-interval.md
  - No document guarantees an uninterrupted same-IP download end-to-end. UNCERTAIN for downloads longer than the sticky interval.
  - Operational note (from yt-dlp behavior, not DataImpulse docs): googlevideo media URLs are commonly **IP-locked to the IP that requested them**; mixing a rotating metadata request with a different-IP media request can cause 403s. Use a sticky port or `sessid` so extraction + download share one IP — this also argues for routing *both* metadata and media through the same sticky session, which eats into the §3 cost table.

## 5. Pointing yt-dlp at it

- `--proxy http://user:pass@host:port` is the standard mechanism; DataImpulse is a plain HTTP proxy that tunnels HTTPS via CONNECT — every doc example fetches `https://api.ipify.org` through `http://login:password@gw.dataimpulse.com:823`, so HTTPS targets are confirmed supported. Source: https://docs.dataimpulse.com/proxies/protocols.md
- Confirmed form for yt-dlp:
  ```bash
  # rotating, US residential
  yt-dlp --proxy "http://LOGIN__cr.us:PASSWORD@gw.dataimpulse.com:823" <url>
  # sticky (30 min default) via sticky port
  yt-dlp --proxy "http://LOGIN:PASSWORD@gw.dataimpulse.com:10000" <url>
  # pinned session + TTL + country, on rotating port
  yt-dlp --proxy "http://LOGIN__cr.us;sessid.abc123;sessttl.30:PASSWORD@gw.dataimpulse.com:823" <url>
  # SOCKS5 alternative
  yt-dlp --proxy "socks5://LOGIN:PASSWORD@gw.dataimpulse.com:824" <url>
  ```
- **TLS/SNI quirks:** DataImpulse documents **no TLS interception, no custom CA, and no SNI requirements** for HTTPS targets; traffic is tunneled, not terminated (the only SSL-cert doc concerns resellers' own white-label domains). UNCERTAIN in the sense that this is an absence of documentation, not an explicit guarantee. Source: https://docs.dataimpulse.com/readme.md?ask=… (TLS/SNI query), https://docs.dataimpulse.com/resellers/manage-ssl-certificates.md
- No yt-dlp-specific integration guide exists in the docs (only curl/requests/Scrapy/Puppeteer-style examples); the generic HTTP-proxy form above is the documented integration path. Source: https://docs.dataimpulse.com/proxies.md

---

## Showstoppers / risks summary

1. **Cost of full-stream routing:** ~$63/mo residential for one always-on 192 kbps stream — real money vs. ~$0 direct. Metadata-only routing is ~$1–3/mo but risks googlevideo IP-lock 403s unless metadata+media share a sticky IP, which then routes media through the proxy anyway.
2. **No guaranteed IP stability mid-download** — sticky sessions can be replaced if the underlying IP goes offline.
3. **No published streaming-media policy; ToS page not reachable** — confirm with support before relying on it.
4. **$50 minimum deposit** to start; traffic never expires, so this is one-time, not monthly.

## Sources

- https://docs.dataimpulse.com/ (welcome/index, `.md` versions of all pages)
- https://docs.dataimpulse.com/proxies.md
- https://docs.dataimpulse.com/proxies/connection-hosts.md
- https://docs.dataimpulse.com/proxies/types-of-connections.md
- https://docs.dataimpulse.com/proxies/protocols.md
- https://docs.dataimpulse.com/proxies/parameters.md
- https://docs.dataimpulse.com/proxies/parameters/session-id.md
- https://docs.dataimpulse.com/proxies/parameters/session-interval.md
- https://docs.dataimpulse.com/proxies/targeting.md
- https://docs.dataimpulse.com/proxies/threads.md
- https://docs.dataimpulse.com/authentication-methods.md
- https://docs.dataimpulse.com/authentication-methods/user-pass-authentication.md
- https://docs.dataimpulse.com/port-access.md
- https://docs.dataimpulse.com/errors.md
- https://docs.dataimpulse.com/blocked-websites.md
- https://docs.dataimpulse.com/billing/auto-recharge.md
- https://docs.dataimpulse.com/resellers/deposit-calculation-update.md
- https://dataimpulse.com/ (public pricing: $1/GB residential, $0.50/GB datacenter, $2/GB mobile, $5/GB premium; pay-as-you-go; volume tiers)
