# 04: Cost guardrails — backoff + failure budget

**What to build:** The radio supervisor stops retry-storming and stops spending on systemic failure. Consecutive track failures back off exponentially (replacing the ~1 retry/sec-forever loop). A failure budget — 5 consecutive failures, or 5 within 10 minutes — pauses the radio loop. A `407 TRAFFIC_EXHAUSTED` response is terminal: pause immediately, zero retries. Supervision keys on yt-dlp's exit code, never ffmpeg's, and yt-dlp stderr is captured for later alerting. All guardrail behaviour is deterministic unit tests through the fake transport. Demoable: script fake failures and watch the supervisor pause; script a 407 and watch it stop dead.

**Blocked by:** 02.

**Status:** done

- [x] Exponential backoff on consecutive track failures; no fixed ~1/sec retry loop remains
- [x] Failure budget (5 consecutive / 5 in 10 min) pauses the supervisor loop
- [x] `407 TRAFFIC_EXHAUSTED` → immediate pause, zero retries (unit-tested)
- [x] Failure detection keys on yt-dlp exit codes; ffmpeg's exit code ignored for failure decisions
- [x] yt-dlp stderr captured at failure time and available to the pause path
- [x] All behaviours covered by unit tests via scripted exit-code sequences

## Comments

**Implementation notes**

- `yt_radio.FailureBudget`: the 5-consecutive / 5-in-10-minute rolling window
  logic, with an injectable clock so window behaviour is deterministic in unit
  tests (`tests/unit/test_cost_guardrails.py::TestFailureBudget`). Successes
  reset the consecutive counter but the rolling window keeps counting.
- `yt_radio.backoff_delay(n)`: base 2s × 2^(n−1), capped at 60s (both env-
  tunable via `TRACK_FAILURE_BACKOFF_BASE_SECONDS` / `..._MAX_SECONDS`). The
  old fixed `sleep(1)` in the loop's failure path is gone; the wait is an
  interruptible `RADIO_STOP.wait(delay)`.
- `yt_radio.TrackFailure`: raised by `_stream_track` when a track ends with a
  nonzero yt-dlp exit code. To get the REAL exit code, `TrackPipeline.close()`
  now reaps yt-dlp first with a 2s grace wait (`transport.YTDLP_EXIT_GRACE_SECONDS`)
  before killing; ffmpeg is killed outright — its exit code is deliberately
  never read for failure decisions. `TrackFailure` carries yt-dlp's stderr.
- `407 TRAFFIC_EXHAUSTED` detection: regex match on yt-dlp stderr
  (`TRAFFIC_EXHAUSTED_PATTERNS`: `HTTP Error 407`, `407 Proxy`,
  `TRAFFIC_EXHAUSTED`) → `_pause_radio(...)` immediately, no backoff, no
  second attempt. Deliberately stricter than a bare `"407"` substring —
  byte counts and ids contain "407", and this pause is terminal.
- Pause state: `PAUSED` (Event), `PAUSE_INFO` (reason, counters, stderr
  excerpt), `LAST_TRACK_FAILURE` (url, yt-dlp exit code, full stderr, time).
  The loop's paused branch spawns nothing and polls every 5s for
  `resume_radio()` (which also grants a fresh budget) or shutdown. Issue 05
  consumes `PAUSE_INFO` / `LAST_TRACK_FAILURE` for the alert email.
- The radio loop now closes the track generator explicitly (`stream.close()`)
  so early-abandoned streams are marked interrupted deterministically and
  never feed the failure budget; only natural completions are classified
  (nonzero yt-dlp exit → failure; exit 0 → success, resetting consecutive).
  A mid-stream read error propagates as itself (not masked by TrackFailure)
  and is still budgeted by the loop.
- Spawn-level failures (proxy unreachable, no exit code exists) are budgeted
  exactly like failed tracks — a dead proxy must burn the budget too.

**Scope note**: the 407 check runs on the track/media path only. Metadata
fetch failures still degrade to fallback metadata (playback can survive
them); if 407s start showing up on `run_ytdlp` metadata dumps, the same
`_traffic_exhausted` helper can be wired there later.

**Verification**

- `pytest` (unit, hermetic): 61 passed (18 new guardrail tests).
- `pytest -m integration`: 4 passed — stream still plays direct with the new
  exit-code reaping in `close()`.
- Code review (standards + spec axes) run post-implementation; fixed its two
  findings: stricter 407 regex (no bare-substring false positives) and
  un-masking of in-flight read errors in `_stream_track`'s finally.
