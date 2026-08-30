# 01 — Functional baseline inventory

Type: task
Status: resolved

## Question

Does the app work today, end to end, from this machine? Run it with the fixture playlist
`https://www.youtube.com/playlist?list=PLm8kNcH2l1F_uzh1rE8M6zKvjcTufIs7q` and exercise every
endpoint: `/`, `/playlist.m3u`, `/now_playing`, `/tracks`, and `/stream` (play audio for a few
minutes, verify icy-metadata updates between tracks).

Record as facts to feed later tickets:

- Which endpoints work / fail, with logs.
- **Idle-cost behavior**: confirm exactly what happens when the last listener disconnects —
  does yt-dlp/ffmpeg actually stop fetching from YouTube, or does the current track keep
  downloading to completion? (Read `_radio_loop` / `_stream_track` in `yt_radio.py` and verify
  against observed network activity.) This is the seam the DataImpulse cost guardrails hang on.
- Playlist refresh behavior (add/remove a track, confirm 60-min refresh logic — can be
  exercised by restarting with a short interval).
- Failure modes: what happens on a private/dead video in the playlist, on a bot-block, with
  `COOKIES_FILE` unset.
- Confirm no secrets are tracked in git (`cookies*.txt` gitignored, only `docs/COOKIES.md` tracked).

This ticket executes rather than decides, but it earns its place by unblocking the routing and
test-design decisions: they need to know the current cost-leak behavior and the real failure modes.

## Answer

Executed 2026-08-29, two runs (fixture playlist, 9 tracks; then a `.radio` file with 2 injected
dead IDs, 11 tracks). Full logs: `/tmp/yt-radio-baseline*.log` (ephemeral — gist below is canonical).

**Endpoints**
- `/` 200, HTML landing page. ✅
- `/stream` 200, real MP3 (ID3, 128 kbps 44.1 kHz), correct icy headers (`icy-br`, `icy-metaint`,
  `icy-charset`), icy-metadata track changes confirmed in log. ✅
- **BUG — `/playlist.m3u` and `/tracks` always serve an EMPTY playlist.** Root cause: `routes.py`
  does `from yt_radio import PLAYLIST`, binding the pre-bootstrap empty list object;
  `refresh_playlist()` later *rebinds* `yt_radio.PLAYLIST` to a new list, so `routes` keeps a
  stale reference forever. Deterministic since the bootstrap-thread refactor. The radio loop reads
  the global directly, which is why `/stream` still works. Fix direction: accessor function or
  module-attribute access (`yt_radio.PLAYLIST`) instead of from-import. 🔴
- `/now_playing` works but reports `"title": "Track 6"`-style fallbacks for tracks whose metadata
  fetch failed; after the playlist bug it also shows stale/global-mismatch data. ⚠️

**Idle-cost behavior (the seam ticket 04 hangs on)**
- Last listener disconnect → track killed **~0.5s later**; log shows `No subscribers remaining;
  stopping track early` and yt-dlp killed **mid-download (10.9%)**. No idle cost leak. ✅
- The remaining leak is only the *current* track's head-of-track fetch burst when a listener
  connects and immediately leaves.

**Refresh**
- Boot load ~1s; 1-minute refresh interval fired on schedule (3 refreshes in 80s observed).
- A later refresh failed with local sandbox `EPERM` on connect — app logged the exception and
  kept serving the old playlist (refresh failures are non-fatal by design). ✅

**Failure modes**
- Dead/invalid video IDs: metadata fetch fails → fallback "Track N" entries; at stream time each
  dead track is skipped in ~1s (`bytes_sent=0`) and the radio moves on. Radio never wedged or
  crashed with all fetches failing. ✅
- **No backoff on repeated failure**: all-fail state cycles ~1 track/second forever — log spam,
  and under a proxy this would be a retry storm burning quota. Spec must mandate backoff on
  consecutive failures. 🔴 (feeds ticket 04)
- Bot-block: not observed from this home IP without cookies (network-allowed phase). Cookie path
  (`COOKIES_FILE`) not exercised end-to-end this session (sandbox network block); injection code
  is a trivial `--cookies` flag on both yt-dlp call sites. Routing fetches through DataImpulse is
  ticket 04's decision regardless.

**Secrets hygiene**: `cookies*.txt` gitignored; only `docs/COOKIES.md` tracked; no secrets in git. ✅

**Environment facts**: app shells out to PATH `yt-dlp` (Homebrew 2026.08.19), not the venv's
2026.07.04 — version skew worth pinning (spec should state one yt-dlp, updated regularly —
yt-dlp decays fast against YouTube). venv needs `uv lock --upgrade-package yt-dlp && uv sync`
(HITL: sandbox blocked package install this session).
