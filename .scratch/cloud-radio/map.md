# Map: cloud-radio

Label: `wayfinder:map`

## Destination

A reviewed **spec** (`.scratch/cloud-radio/spec.md`) that fully specifies, with no decisions left open: (1) routing all YouTube fetches through DataImpulse with cost guardrails, (2) deployment on Oracle Cloud Free Tier (`VM.Standard.E2.1.Micro`, Oracle Linux 9), and (3) a local-only test suite design. Destination is reached when the spec can be handed off and implemented without further planning. Per the map's override in Notes, the baseline-functionality check is executed inside the map; everything else is specified, not built.

## Notes

- Domain docs: `CONTEXT.md`, `docs/adr/` at repo root. Consult `docs/agents/issue-tracker.md` for tracker conventions.
- Skills every session should consult: `grilling` + `domain-modeling` for decision tickets, `research` for research tickets, `tdd` for the test-suite design ticket.
- Standing preferences:
  - Keep **yt-dlp** as the extraction engine; DataImpulse is a transport layer (proxy), not an API replacement.
  - **Cost minimization is first-class**: the stream must shut off (no YouTube media fetch) when there are no listeners, and be robust enough that cost never leaks.
  - **No CI.** All tests run locally.
  - Deployment target fixed: `VM.Standard.E2.1.Micro`, Oracle Linux 9. This **replaces** the Pi + Cloudflare Tunnel deployment in the README.
  - The playlist `https://www.youtube.com/playlist?list=PLm8kNcH2l1F_uzh1rE8M6zKvjcTufIs7q` is a **test fixture only**, not production config; all playlists are structured the same.
  - `COOKIES_FILE` mechanism: verify against yt-dlp docs, don't remove yet; revisit after proxy validation.

## Decisions so far

<!-- empty: no tickets resolved yet -->

- [Functional baseline inventory](01-baseline-functionality.md): app boots in ~1s and streams real MP3 with icy-metadata; last-listener disconnect kills yt-dlp mid-download in ~0.5s (no idle cost leak); dead tracks skipped gracefully but with no backoff (~1 retry/sec forever — retry-storm risk under a metered proxy); **real bug found**: `/playlist.m3u` and `/tracks` always serve an empty playlist because `routes.py` from-imports `PLAYLIST` and holds a stale pre-bootstrap reference; cookies path unexercised (sandbox network block), no bot-wall seen from home IP.
- [DataImpulse: mechanics, pricing, yt-dlp compatibility](02-dataimpulse-mechanics-pricing.md): `gw.dataimpulse.com:823` HTTP proxy, user/pass with geo/session params in username; residential $1/GB, $50 min deposit; always-on 192 kbps stream ≈ $63/mo vs metadata-only ≈ $1–3/mo; sticky sessions (~30 min) give no guarantee a full media download keeps one IP — googlevideo IP-lock makes split routing risky. Detail in the ticket; full findings in `research/dataimpulse.md`.
- [yt-dlp proxy surface and rate-limit controls](03-ytdlp-proxy-surface.md): `--proxy` (or HTTP(S)_PROXY/ALL_PROXY env) routes *all* yt-dlp requests including every media fragment — the audio pipe's bandwidth fully rides the proxy; cookies must be exported from an IP matching the proxy exit; cost knobs `--limit-rate`/`--http-chunk-size`≤10MB; failure detection must key on yt-dlp's exit code because ffmpeg exits 0 on truncated input. Detail in the ticket; full findings in `research/ytdlp-proxy.md`.
- [Oracle Free Tier: E2.1.Micro + OL9 facts](05-oracle-free-tier-facts.md): 2× E2.1.Micro Always Free, capacity-outage workaround is PAYG upgrade (stays free), 7-day idle-reclamation makes an unlistened radio unsafe; ffmpeg via EPEL→RPM Fusion `dnf swap`, Python 3.13 via uv; open TCP 80/443 in both the OCI security list and firewalld; with all YouTube traffic proxied, the old OCI bot-block is neutralized. Detail in the ticket; full findings in `research/oracle-free-tier.md`.
- [Fetch routing matrix and cost guardrails](04-fetch-routing-and-cost-guardrails.md): all fetch types proxied via per-track sticky session, fail-closed on proxy outage; upstream audio capped at ≤56 kbps with fallback chain (~$2/mo); dead-track backoff + failure budget (5 tracks/10 min) → pause; `407 TRAFFIC_EXHAUSTED` terminal, auto-recharge off (5 GB trial is the ceiling); `COOKIES_FILE` removed from proxied path; pause = email alert + auto-resume after ~30 min cooldown.
- [Oracle deployment plan](06-oracle-deployment-plan.md): redeploy-as-code via a single `provision.sh` (EPEL→RPM Fusion ffmpeg, uv + Python 3.13, `radio` user, 2 GB swap, firewalld 80/443); nginx on 80/443 with certbot TLS in front of gunicorn (`--workers 1 --timeout 0`, systemd `Restart=always`, `MemoryMax=700M`); PAYG upgrade for capacity; idle-reclamation countered by a listener-gated ~27% CPU keep-alive (zero-listener spin, breaks Oracle's idle conjunction without fake traffic) with the script as backstop; journald logging; smoke-check list verifies stream, metadata, idle shutoff, no idle YouTube traffic, fail-closed alerting, and keep-alive percentiles.
- [Test suite design](07-test-suite-design.md): pytest with `unit`/`integration`/`smoke` markers, all local; one transport seam for yt-dlp/ffmpeg spawns (scripted exit codes make guardrail tests deterministic) + playlist injection fixing the empty-playlist bug with a regression test; integration tests default direct (free), proxied mode behind `PROXY_INTEGRATION=1` quota warning; shape-only assertions; smoke module takes `--base-url` and encodes the 06 checklist.
- [Assemble spec.md](08-assemble-spec.md): `.scratch/cloud-radio/spec.md` written — pillars (proxied routing + guardrails, Oracle deploy, local test suite) assembled from tickets 01–07 with no open decisions; future work (cookies/bot-wall path) recorded in the spec, not as tickets.

## Destination reached

All tickets resolved; the spec is the hand-off artifact. Remaining fog (cookies fate) lives in the spec's "Out of Scope" section as evidence-gated future work.

## Not yet specified

- **Fate of cookies**: post-spec validation on Oracle may show a bot-wall appears anyway; 04 documented the future path (re-add cookies exported through the same proxy). Revisit only on evidence.

## Out of scope

- **CI / GitHub Actions**: user ruled out; all tests local. Not fog — settled.
- **Implementation** of DataImpulse routing, Oracle deployment, or test-suite code: destination is the spec; execution happens after hand-off, outside this map.
- **Raspberry Pi + Cloudflare Tunnel deployment**: replaced by Oracle target; no docs maintenance for it.
- **Non-YouTube sources / playlist features**: untouched by this effort.
