# 02 — DataImpulse: mechanics, pricing, yt-dlp compatibility

Type: research
Status: resolved

Docs: https://docs.dataimpulse.com/

## Question

What does it take to route yt-dlp traffic through DataImpulse, and what does it cost?

Answer precisely, with sources:

1. **Proxy endpoint format** for the residential pool: host, port, auth scheme
   (username/password? token-in-username?), how to pin geography/session if needed.
2. **Pricing model**: per-GB cost, minimum spend, quota/pool differences (residential vs
   datacenter), what happens when quota runs out.
3. **Bandwidth exposure**: if an always-on audio stream (~86 MB/h at 192 kbps) went through
   the proxy, what is the realistic monthly GB and cost? Include a table for metadata-only
   vs metadata+media routing.
4. **Terms/compatibility**: any policy on streaming media or automated fetching; IP rotation
   behavior mid-download (does a long media download survive an IP change?).
5. **How to point yt-dlp at it** (confirm `--proxy http://user:pass@host:port` is the
   integration surface; note anything DataImpulse-specific such as TLS/SNI quirks).

Write findings to `.scratch/cloud-radio/research/dataimpulse.md`; the ticket answer gists it.

## Answer

Full findings: [research/dataimpulse.md](../research/dataimpulse.md). Gist:

1. **Endpoint/auth**: `gw.dataimpulse.com` — rotating HTTP/S `:823`, SOCKS5 `:824`, sticky ports 10000–20000 (IP held 1–120 min, default 30). Plain `user:pass`; geo/`sessid`/`sessttl` params embedded in the username (`login__cr.us;sessid.abc:pass`). Country targeting free; state/city/ASN 2× rate.
2. **Pricing** (PAYG, traffic never expires): residential **$1/GB**, datacenter $0.50/GB, mobile $2/GB. **$50 minimum deposit.** Quota exhaustion → `407 TRAFFIC_EXHAUSTED` on new requests; behavior for in-flight downloads undocumented.
3. **Cost of the always-on stream** (192 kbps 24/7 ≈ 63 GB/mo): residential ≈ **$63/mo**, datacenter ≈ $31/mo. **Metadata-only ≈ 1–3 GB ≈ $1–3/mo** (agent's estimate, flagged UNCERTAIN).
4. **Policy/rotation**: youtube/googlevideo not on the blocked list; no published streaming-media policy (UNCERTAIN — worth asking support). Rotating pool changes IP per request; sticky `sessid` pins ~30 min and replaces the IP if it goes offline — **no end-to-end guarantee a long media download keeps one IP**.
5. **yt-dlp integration**: `--proxy "http://user__cr.us:pass@gw.dataimpulse.com:823"` is the surface; plain CONNECT-tunneling proxy, no TLS interception documented.

**Decision-shaping fact**: full-stream routing costs ~$63/mo vs ~$1–3/mo metadata-only — but the split risks googlevideo IP-lock 403s unless metadata and media share one sticky IP, which routes media through the proxy anyway. This tension lands squarely in ticket 04.
