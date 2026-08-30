# Cloud Radio — yt-playlist-radio on Oracle Free Tier with proxied YouTube routing

Status: ready-for-agent

Spec output of the `cloud-radio` wayfinder effort; every decision below is recorded in a closed
ticket under `.scratch/cloud-radio/issues/` (see `map.md` → Decisions-so-far for provenance and
`research/` for the underlying findings).

## Problem Statement

The radio currently runs on a Raspberry Pi behind a Cloudflare Tunnel, reaching YouTube from a
residential IP. That deployment is fragile, tied to hardware at home, and cannot be reproduced
if the Pi dies. Worse, the app's current failure behaviour is unsafe for any metered network:
a dead track is retried about once per second forever, and there is no ceiling on what a
misbehaving fetch loop could spend. Moving to a cloud VM means every YouTube request comes from
a datacenter IP, where YouTube blocks or bot-checks aggressively — so all YouTube traffic must
ride a residential proxy (DataImpulse), which is metered and costs real money. Without strict
cost guardrails, an internet radio that fetches audio 24/7 through a $1/GB proxy would cost
~$63/month, and a retry storm or outage could leak spend silently. Finally, there is no test
infrastructure at all, and there is a real bug: the playlist endpoints always serve an empty
playlist after bootstrap.

## Solution

Deploy the existing Flask radio to an Oracle Cloud Always Free VM (`VM.Standard.E2.1.Micro`,
Oracle Linux 9), reproducible from a single provisioning script in the repo. Route every YouTube
fetch — playlist listing, per-track metadata, and media streaming — through DataImpulse
residential proxies with one sticky session per track, an upstream audio bitrate ceiling of
≤56 kbps, and a fail-closed rule: when the proxy is configured, the app is structurally unable
to talk to YouTube directly. Cost is contained by the existing idle shutoff (kill the fetch the
moment the last listener leaves), exponential backoff on dead tracks, a failure budget (5
consecutive failures or 5 in 10 minutes) that pauses the radio, and a terminal pause with an
email alert on `407 TRAFFIC_EXHAUSTED`, auto-resuming after a ~30 minute cooldown. The VM stays
alive against Oracle's idle-reclamation policy via a listener-gated CPU keep-alive that only
burns cycles while nobody is listening. A local-only pytest suite (`unit` / `integration` /
`smoke`, no CI) pins all of this down through a single transport seam.

Standing constraints, binding on everything below:

- yt-dlp remains the extraction engine; DataImpulse is a transport layer only, never an API
  replacement.
- Cost minimization is first-class: no YouTube media fetch when no one is listening.
- The fixture playlist is a test fixture only; any playlist must work, nothing is special-cased.
- No CI. All tests run locally.

## User Stories

1. As a listener, I want to open the radio's URL and hear music immediately, so that I don't
   need to know or care where it is hosted.
2. As a listener, I want a continuous MP3 stream with icy-metadata, so that track titles show
   up in my player and update between tracks.
3. As a listener, I want the stream to keep playing through transient proxy hiccups, so that
   brief upstream failures don't interrupt my listening session.
4. As a listener, I want dead tracks to be skipped rather than stalling the stream, so that one
   broken video doesn't silence the radio.
5. As a listener, I want the radio to pause itself cleanly when the proxy is out of quota,
   rather than hanging silently, so that I understand why there's no music.
6. As an operator, I want every YouTube request routed through DataImpulse, so that YouTube
   never sees my VM's datacenter IP and blocks it.
7. As an operator, I want the direct (unproxied) path to YouTube to be structurally unreachable
   when the proxy is configured, so that a bug can never silently leak unproxied traffic.
8. As an operator, I want metadata extraction and the media download for a track to share one
   proxy exit IP (sticky session), so that googlevideo IP-lock 403s don't break playback.
9. As an operator, I want upstream audio capped at ≤56 kbps with a fallback chain, so that
   bandwidth cost stays around $2/month at typical listening levels.
10. As an operator, I want larger chunks throttled to ≤10 MB with sane retry/backoff settings,
    so that YouTube's throttling rules aren't tripped.
11. As an operator, I want the fetch killed mid-download the moment the last listener
    disconnects, so that no YouTube bytes are ever pulled for an empty room.
12. As an operator, I want exponential backoff on consecutive track failures, so that a dead
    track or proxy outage can't produce a metered retry storm.
13. As an operator, I want a failure budget (5 consecutive failures, or 5 within 10 minutes)
    that pauses the radio, so that systemic failures stop spending money.
14. As an operator, I want a `407 TRAFFIC_EXHAUSTED` response to pause immediately with zero
    retries, so that the free trial balance (then the deposit) is a hard spend ceiling.
15. As an operator, I want auto-recharge on DataImpulse left off, so that no spend can happen
    without my explicit action.
16. As an operator, I want an email alert whenever the radio pauses, containing the yt-dlp
    stderr excerpt and failure counters, so that I learn about outages without watching logs.
17. As an operator, I want the radio to auto-resume after a ~30 minute cooldown with a fresh
    failure budget, so that transient proxy outages self-heal without my involvement.
18. As an operator, I want proxy failures to cause backoff retries only — never a fallback to
    direct traffic — so that the cost and blocking guarantees always hold.
19. As an operator, I want yt-dlp supervised by its exit code rather than ffmpeg's, so that
    truncated downloads are actually detected as failures.
20. As an operator, I want the whole VM reproducible from one provisioning script, so that a
    reclaimed or dead instance is a ~10-minute non-event, not a weekend of archaeology.
21. As an operator, I want the VM on Oracle's Always Free tier with documented PAYG-upgrade
    fallback for capacity outages, so that hosting costs $0.
22. As an operator, I want TLS via Let's Encrypt with HTTP→HTTPS redirect, so that the stream
    URL is secure and browsers don't warn.
23. As an operator, I want a 2 GB swapfile and memory-capped gunicorn, so that the 1 GB VM
    doesn't OOM under ffmpeg + Python.
24. As an operator, I want a listener-gated CPU keep-alive that only runs while zero listeners
    are connected, so that Oracle doesn't reclaim the idle instance but a live stream never
    competes with the busy-loop.
25. As an operator, I want the keep-alive cgroup-capped and low-priority, so that it can never
    degrade stream quality.
26. As an operator, I want secrets (proxy credentials, SMTP app-password) in a gitignored,
    chmod-600 env file, so that no credentials land in the repo or logs.
27. As an operator, I want journald capturing all service output, so that debugging uses
    standard tooling with no extra log infrastructure.
28. As an operator, I want post-deploy smoke checks as a runnable pytest module, so that
    validating a fresh deployment is one command, not a checklist in my head.
29. As a maintainer, I want a single transport seam through which all yt-dlp/ffmpeg spawns
    flow, so that the whole fetch pipeline is testable with fakes and the fail-closed invariant
    is enforceable in one place.
30. As a maintainer, I want the playlist injected into the route module rather than
    from-imported, so that the empty-playlist bug is fixed and cannot silently return.
31. As a maintainer, I want a unit suite that runs in seconds with no network, so that I get
    fast feedback on every change.
32. As a maintainer, I want deterministic cost-guardrail tests (scripted exit codes through the
    fake transport), so that idle shutoff, backoff, the failure budget, and the terminal 407
    pause are pinned by tests rather than hope.
33. As a maintainer, I want a regression test asserting no unproxied spawn is ever attempted
    when the proxy is configured, so that the fail-closed invariant is testable, not folklore.
34. As a maintainer, I want integration tests that default to direct (free, home-IP) access and
    only hit the proxy behind an explicit opt-in flag, so that normal development never spends
    proxy quota.
35. As a maintainer, I want integration assertions to be shape-only (non-empty playlist, track
    fields present, stream yields MP3 bytes with icy-metadata), so that tests don't break on
    unrelated upstream changes.
36. As a maintainer, I want the venv's yt-dlp used exclusively (never a PATH binary), so that
    version skew doesn't cause ghost failures, and upgrades are a one-line lockfile change.
37. As a maintainer, I want smoke tests parameterized by a base URL, so that the same module
    validates any deployment (local, staging, production).
38. As a maintainer, I want the README deployment section to describe the new target, so that
    future me (or an agent) isn't following dead Pi/Tunnel instructions.

## Implementation Decisions

### Fetch routing

- DataImpulse residential pool, HTTP endpoint `gw.dataimpulse.com:823`, plain user/pass auth
  built at runtime from env credentials — never hardcoded.
- All three fetch classes are proxied: playlist listing, per-track metadata extraction, and
  media streaming. A single `--proxy` flag on yt-dlp routes every request class, including all
  media fragments (verified against yt-dlp docs).
- One sticky session per track: a unique `sessid` per track fetch, so metadata extraction and
  the media download share one exit IP. This avoids the documented googlevideo IP-lock 403
  failure mode of mixing rotating-exit metadata with a different-IP media request.
- No country targeting; state/city/ASN targeting (2× rate) is never used.
- Upstream format selector targets ≤56 kbps with fallback chain:
  `bestaudio[abr<=56]` → `bestaudio[abr<=96]` → `bestaudio`. The listener-facing MP3 transcode
  is unchanged; upstream bitrate is a quality ceiling, not the listener bitrate.
- Mandated yt-dlp flags for all proxied invocations: per-track-sticky `--proxy`;
  `--http-chunk-size` ≤ 10 MB; modest `--retries` and `--fragment-retries` with backoff;
  `--retry-sleep linear=1:30` (never immediate retry loops); `--socket-timeout 20`;
  `--extractor-retries 5` for the metadata phase.
- yt-dlp is pinned to the venv's copy (invoked via the venv interpreter), never a PATH binary;
  upgrades happen through the lockfile.
- Cookies are removed from the proxied path. If post-deploy validation shows a bot-wall, the
  future path (recorded, not built) is cookies exported through the same proxy exit IP.

### Cost guardrails

- Idle shutoff is kept exactly as-is: last listener disconnect kills yt-dlp mid-download
  (observed ~0.5 s). This is the primary cost control and must not regress.
- Dead-track handling replaces the current ~1 retry/sec-forever loop with exponential backoff
  on consecutive failures.
- Failure budget: 5 consecutive failed tracks, or 5 failures within 10 minutes, pauses the
  supervisor loop.
- Fail-closed: proxy transport errors (connection refused, timeouts) cause backoff retries
  only. The implementation must make the direct path structurally unreachable when the proxy is
  configured — this is enforced at the transport seam.
- `407 TRAFFIC_EXHAUSTED` is terminal: pause immediately, zero retries. DataImpulse
  auto-recharge stays off; the 5 GB trial (then the $50 deposit) is the hard spend ceiling.
- Supervision keys on yt-dlp's exit code, never ffmpeg's (ffmpeg exits 0 on truncated input;
  yt-dlp exits non-zero on download/extraction errors). yt-dlp's stderr is captured for the
  alert email.
- Pause semantics: an email alert via msmtp using an app-password from an existing mailbox,
  including the last yt-dlp stderr excerpt and failure counters; auto-resume after a ~30 minute
  cooldown with a fresh failure budget.

### Deployment

- Target: `VM.Standard.E2.1.Micro` (Always Free, 1 OCPU / 1 GB / 480 Mbps), Oracle Linux 9,
  home region; min boot volume 47 GB. On "out of host capacity", the documented remedy is a
  Pay-As-You-Go account upgrade (Always Free resources stay free).
- A single provisioning script committed to the repo performs the entire setup: EPEL → RPM
  Fusion → ffmpeg swap; uv + Python 3.13; a dedicated `radio` user with the repo cloned to
  `/opt` and dependencies synced; 2 GB swapfile with low swappiness; systemd units; firewalld
  80/443; certbot TLS with HTTP→HTTPS redirect.
- OCI networking: one stateful ingress rule, TCP 80 + 443 from anywhere, in a Security List or
  NSG (NSG preferred). Egress is allow-all by default.
- Process shape: nginx (OL9 AppStream) on 80/443 proxying to gunicorn on loopback; proxy
  buffering disabled on the stream location; gunicorn single worker with the worker timeout
  disabled (long-lived streaming response), systemd `Restart=always` as the hang/crash backstop,
  and systemd memory caps (~600M high / 700M max).
- Env file on the VM holds app config (playlist URL, randomize, refresh interval), DataImpulse
  credentials, the public base URL, and SMTP credentials for msmtp — chmod 600, gitignored.
- Idle-reclamation keep-alive: a tiny systemd service polls the app's loopback status endpoint
  and, only while zero listeners are connected, runs a throttled busy-loop targeting ~27% CPU
  (headroom over Oracle's 20% 95th-percentile threshold), cgroup-capped and low-priority. No
  fake network traffic. Residual risk (Oracle changing policy) is accepted; the provisioning
  script is the durable answer.
- The README's Pi + Cloudflare Tunnel deployment section is replaced by this plan; that content
  is no longer maintained.

### Repository changes required

- Fix the empty-playlist bug: the route module from-imports the playlist and holds a stale
  pre-bootstrap reference; fix by injecting the live playlist. A regression test pins it.
- Introduce the transport seam: all yt-dlp/ffmpeg subprocess spawns go through one object.
- Pin yt-dlp to the venv's copy, as above.

## Testing Decisions

- Good tests assert external behaviour only — exit codes observed through the supervisor, HTTP
  responses, stream bytes — never implementation details. No test asserts on raw argv strings.
- Layout: pytest with `pytest-timeout` as dev dependencies; tests under `tests/`; three
  markers registered in project config: `unit` (the default run), `integration` (deselected by
  default), `smoke` (post-deploy, takes `--base-url`). Fixtures live in a shared conftest; the
  fixture playlist is the default integration fixture, with any playlist URL working via config.
- The one transport seam is the testing workhorse: unit tests fake it wholesale with scripted
  exit codes, fake slow/dead tracks, and fake proxy failures. This makes every cost guardrail
  deterministic.
- Playlist injection is the second seam: a regression test asserts the playlist endpoints
  reflect tracks added after bootstrap.
- Unit suite covers: proxy env handling (`--proxy` present when configured, absent otherwise);
  the backoff / failure-budget state machine; idle shutdown on last disconnect; the
  format-selector fallback chain; per-track sticky-session construction; and the fail-closed
  invariant (no unproxied spawn ever attempted when the proxy is configured).
- Cost-guardrail tests (via the fake transport): listener disconnect mid-"download" terminates
  the transport within a bounded time; 5 consecutive failures pause the supervisor and the
  cooldown resets the budget; a `407 TRAFFIC_EXHAUSTED` exit pauses immediately with zero
  retries; a connection-refused script produces backoff retries and never a direct spawn.
- Integration tests default to direct (home IP — free, validated in the baseline ticket);
  proxied mode is behind an explicit opt-in env flag with a loud quota-spend warning, exercised
  deliberately pre-deploy. Assertions are shape-only: playlist non-empty; each track has id /
  title / duration; the stream yields MP3 bytes starting with a valid frame and icy-metadata
  present.
- Smoke tests: a pytest module consuming `--base-url`, encoding the post-deploy checklist —
  HTTPS page loads, `/stream` plays, icy-metadata updates, pause kills the fetch processes
  within ~1 s, no YouTube traffic with zero listeners, broken proxy credentials → pause + alert
  + auto-resume, zero-listener CPU ≥ 20% on OCI metrics (queried where scriptable, else flagged
  for the operator).
- No prior test suite exists in the codebase, so the unit suite establishes the pattern; the
  closest prior art is the baseline-functionality ticket's manual verification procedure, which
  the smoke module automates.

## Out of Scope

- CI / GitHub Actions — ruled out; all tests run locally.
- Cookies / bot-wall handling — removed from the proxied path; re-added only on post-deploy
  evidence, following the recorded future path (same-IP export rule). No speculative code.
- TLS on gunicorn-direct, and DataImpulse's datacenter pool ($0.50/GB) — future options only.
- Any non-YouTube source, playlist features, or changes to the listener-facing audio pipeline
  beyond the upstream bitrate ceiling.
- Maintaining the Raspberry Pi + Cloudflare Tunnel deployment documentation.

## Further Notes

- Cost model: ≤56 kbps upstream ≈ 0.025 GB/h ≈ $2/month at ~3 h/day listening; the always-on
  192 kbps worst case (~$63/mo) is made unreachable by the idle shutoff + failure budget +
  terminal 407 pause.
- Sticky sessions last ~30 minutes and give no guarantee a single media download keeps one IP —
  the per-track sticky session is the mitigation, and the fallback chain absorbs the odd
  failure.
- The provisioning script is the real disaster-recovery plan: Oracle can reclaim the instance,
  and the response is "re-run the script", not "remember how this was set up".
- Implementation ordering is at the implementing agent's discretion, but the transport seam and
  playlist injection must land before (or with) the proxied-routing work, since the fail-closed
  invariant is enforced at that seam and the routing tests depend on it.
