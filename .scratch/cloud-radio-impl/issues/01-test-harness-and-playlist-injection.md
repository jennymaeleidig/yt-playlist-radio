# 01: Test harness + playlist injection

**What to build:** A local pytest harness (unit / integration markers registered in project config, shared fixtures) plus the smallest prefactor that proves it works: the Flask route module receives the live playlist by injection instead of from-importing it. The known empty-playlist bug — playlist endpoints serving a stale pre-bootstrap snapshot — is fixed and pinned by a regression test. Direct-mode integration tests (shape-only: playlist non-empty, track id/title/duration present, stream yields MP3 bytes with icy-metadata) run green against real YouTube from the home IP. App behaviour is otherwise unchanged.

**Blocked by:** None (can start immediately).

**Status:** done

- [x] pytest + pytest-timeout installed as dev dependencies; `unit` and `integration` markers registered; integration deselected by default
- [x] Regression test: after a playlist refresh, the playlist endpoints reflect the new tracks
- [x] Route module no longer from-imports the playlist; playlist is injected
- [x] Direct-mode integration tests pass with shape-only assertions
- [x] Default `pytest` run (unit only) needs no network and passes in seconds

**Completion notes (deviations from "behaviour otherwise unchanged"):**

- `/tracks` now includes an `id` field per track (empty string when metadata
  hasn't arrived) — required by the shape-only integration assertions and
  useful for thumbnails; additive, no existing field changed.
- The radio producer now starts lazily on the first `/stream` request instead
  of at import/boot. Necessary so importing `webapp` has no side effects
  (the injection seam); listener-visible behaviour is identical.
- Integration run recorded: `pytest -m integration` → 4 passed (real YouTube,
  home IP), including the metadata warm-up via `/playlist.m3u`.
