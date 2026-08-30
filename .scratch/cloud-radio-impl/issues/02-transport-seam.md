# 02: Transport seam

**What to build:** All yt-dlp and ffmpeg subprocess spawns flow through a single transport object. Unit tests fake the transport wholesale — scripted exit codes, fake slow/dead tracks, fake proxy failures — with no test asserting on raw argv strings. yt-dlp is pinned to the venv's copy (invoked via the venv interpreter), never a PATH binary, so version skew can't cause ghost failures. Pure "make the change easy": app behaviour is unchanged and the app still streams.

**Blocked by:** 01.

**Status:** done

- [x] Every yt-dlp/ffmpeg spawn in the app goes through the one transport object
- [x] Unit tests exercise the supervisor through a fake transport with scripted exit codes
- [x] yt-dlp is resolved from the venv exclusively; no PATH lookup
- [x] Full unit suite green; manual smoke: stream still plays direct

## Comments

**Implementation notes**

- New `transport.py`: a single `Transport` object with `run_ytdlp(args)` (synchronous
  metadata dumps) and `open_track_pipeline(url, ytdlp_format=, bitrate_kbps=)` (the
  yt-dlp|ffmpeg pipe, returning a `TrackPipeline` whose `close()` kills + reaps both
  children and returns their stderr). `yt_radio` exposes it as `TRANSPORT`; the module
  no longer imports `subprocess` at all (pinned by a test).
- yt-dlp is invoked as `<venv interpreter> -m yt_dlp` — never a PATH binary. One test
  pins the argv construction (the only place argv is asserted; supervisor tests script
  outcomes only). ffmpeg remains a PATH/system binary — it isn't a Python dependency.
- `tests/unit/test_transport_seam.py`: fake transport scripts exit codes (metadata
  success/403/cookies-once), slow tracks, dead tracks (immediate EOF), spawn failures
  (proxy down), and a radio-loop test proving a failed spawn is skipped and playback
  continues. No test asserts on raw argv.

**Deviation: new runtime dependency `yt-dlp-ejs`**

Venv-pinning yt-dlp exposed a real version-skew ghost, just not the one the issue
anticipated: the PATH binary (`/opt/homebrew/bin/yt-dlp`) bundles the `yt-dlp-ejs`
JS-challenge-solver package, the venv's pip install did not. With cookies enabled the
venv copy deterministically failed extraction ("The page needs to be reloaded") while
the PATH binary worked — the app was silently load-bearing on a binary outside the
venv. Fix: `yt-dlp-ejs>=0.8.0` added to `pyproject.toml` and installed into the venv.

**Note for the user:** `uv.lock` could not be regenerated from this session (the
sandbox denies writes to `~/.local/share/mise`, so `mise exec -- uv lock` fails with
EPERM). The next `uv sync` will re-lock automatically; run `uv lock` manually if you
want it committed eagerly.

**Verification**

- `pytest` (unit, hermetic): 25 passed.
- `pytest -m integration` (real YouTube, home IP): 4 passed — stream yields MP3 bytes
  with icy-metadata through the transport seam.
- Manual smoke (`.scratch/cloud-radio-impl/smoke_transport.py`): metadata extracted,
  256 KB of media streamed through `TRANSPORT.open_track_pipeline` in ~9 s.
