# 07 — Test suite design (decision)

Type: grilling
Status: resolved
Blocked by: 01, 04

## Question

Design the local-only test suite the spec will mandate (no CI, all tests local, per the map's
Notes). Decide:

1. **Layout and markers**: pytest structure with `unit` / `integration` / `smoke` markers;
   where fixtures live (the fixture playlist `PLm8kNcH2l1F_uzh1rE8M6zKvjcTufIs7q` as the
   default integration fixture, with the rule that any playlist works).
2. **Mock seams**: what gets faked for unit tests — yt-dlp subprocess calls, ffmpeg, cache —
   given the fetch-routing matrix from 04 (proxy env handling needs its own test).
3. **Cost-guardrail tests**: how to assert idle shutoff and failure containment deterministically
   (fake slow/dead tracks, fake listener disconnects) — built on 01's observed behavior.
4. **Integration tests**: real YouTube hits behind DataImpulse vs direct — how to toggle,
   what to assert (metadata fields, m3u shape, stream produces MP3 bytes with icy-metadata).
5. **Smoke script**: post-deploy local checks against a running VM (or local instance) — the
   checklist from 06 expressed as a runnable script or pytest-smoke module.

## Answer

Decided in a grilling session; all recommendations accepted. Facts: repo is ~700 lines across `yt_radio.py` / `routes.py`, yt-dlp spawned as subprocess, no test infra today.

### 1. Layout and markers
- pytest added as a uv dev-dependency (`uv add --dev pytest pytest-timeout`), tests under `tests/` with three markers registered in `pyproject.toml`: `unit` (default run), `integration` (deselected by default), `smoke` (post-deploy, takes `--base-url`).
- Fixtures live in `tests/conftest.py`; the fixture playlist `PLm8kNcH2l1F_uzh1rE8M6zKvjcTufIs7q` is the default integration fixture, with the standing rule that **any** playlist URL works (config via `PLAYLIST_URL`, never hardcoded).

### 2. Mock seams (the minimal refactor is mandated)
- **One transport seam**: all yt-dlp/ffmpeg subprocess spawns go through a single small transport object; unit tests fake it wholesale (scripted exit codes, fake slow/dead tracks, fake proxy failures). No test asserts on raw argv strings.
- **Playlist injection**: `routes.py` stops from-importing `PLAYLIST` (fixes the real empty-playlist bug from 01) and receives the live playlist; a regression test asserts `/playlist.m3u` and `/tracks` reflect tracks added after bootstrap.
- Unit suite covers: proxy env handling (`--proxy` present when configured, absent otherwise), backoff/failure-budget state machine (scripted exit-code sequences), idle-shutdown on last-disconnect (fake listener disconnect → transport killed), format-selector fallback chain (`abr<=56` → `<=96` → bestaudio), sticky-`sessid` construction per track.

### 3. Cost-guardrail tests (deterministic, via the fake transport)
- Idle shutoff: fake listener disconnect mid-"download" → assert transport terminated within a bounded time (pytest-timeout).
- Failure containment: 5 consecutive fake track failures → supervisor pauses; cooldown elapses → fresh budget; `407 TRAFFIC_EXHAUSTED` exit → immediate pause, zero retries.
- Proxy outage: connection-refused script → backoff retries, and an assertion that **no direct (unproxied) spawn is ever attempted** (fail-closed).

### 4. Integration tests
- Default run goes **direct** (home IP, free, validated in 01); proxied mode behind an explicit `PROXY_INTEGRATION=1` flag with a loud quota-spend warning. Proxied mode is exercised deliberately pre-deploy, not on every save.
- Assertions are **shape-only**: playlist non-empty; each track has id/title/duration; `/stream` yields MP3 bytes starting with a valid frame and icy-metadata present. No exact codec/bitrate assertions (rot-proof).

### 5. Smoke checks
- A pytest `smoke` module consuming `--base-url`, one runner for everything: `pytest -m smoke --base-url=https://<domain>`. It encodes the 06 checklist: stream plays, metadata flows, idle shutoff observed, no YouTube traffic when idle, fail-closed alerting fires, keep-alive 95th-percentile CPU ≥ 20% (queried from OCI metrics where scriptable, else flagged for the operator's eyeball).
- No CI anywhere; all suites local per the map's standing preference.
