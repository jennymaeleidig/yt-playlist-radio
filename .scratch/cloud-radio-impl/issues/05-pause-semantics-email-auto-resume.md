# 05: Pause semantics — alert email + auto-resume

**What to build:** When the supervisor pauses (failure budget or traffic exhausted), an alert email goes out containing the last yt-dlp stderr excerpt and the failure counters. After a ~30 minute cooldown the radio auto-resumes with a fresh failure budget — transient proxy outages self-heal, and the human still hears about it. The mailer is a seam: unit tests use a fake mailer and never send real email.

**Blocked by:** 04.

**Status:** done

- [x] Pause triggers one alert email with stderr excerpt + failure counters (unit-tested with a fake mailer)
- [x] Auto-resume after the cooldown with a fresh failure budget (unit-tested)
- [x] Resuming on a still-broken proxy re-pauses and re-alerts rather than looping hot
- [x] Mail config read from the env file; no credentials in code or logs

## Comments

**Deviation: Resend API instead of msmtp**

The issue specified msmtp with an app-password; the user directed the
implementation to use Resend (resend.com) API keys instead. The mailer seam
survives unchanged — `alerts.Mailer` is the seam, `alerts.ResendMailer` the
implementation — so swapping transport later stays a one-class change.
Config keys: `RESEND_API_KEY`, `ALERT_EMAIL_FROM` (verified sender on the
Resend account), `ALERT_EMAIL_TO`. The key travels only in the outbound
Authorization header and is never logged (pinned by test).

**Implementation notes**

- New `alerts.py`: `Mailer.send(subject, body) -> bool` is the seam;
  `ResendMailer.from_env()` returns None when config is missing, so an
  unconfigured deployment degrades to logging — a pause never crashes on
  mail. The HTTP call is module-level `_resend_post(api_key, payload)` (std
  lib urllib, no new dependency) so tests pin it without patching urllib.
- `yt_radio.MAILER` (module-level, tests monkeypatch it).
  `_pause_radio()` now sends exactly one alert per pause (the PAUSED guard
  makes re-entry a no-op) with: reason, consecutive/window counters, the
  yt-dlp stderr excerpt, and the cooldown notice. A failing mailer never
  un-guards the pause.
- Auto-resume: `_pause_radio` records `_PAUSED_AT_MONOTONIC`; the loop's
  paused branch checks it every poll and calls `resume_radio()` once
  `PAUSE_RESUME_COOLDOWN_MINUTES` (default 30) has elapsed — which grants a
  fresh failure budget. Cooldown not elapsed → keeps spawning nothing.
- Re-pause semantics: on a still-broken proxy, the resumed loop fails at
  most once (terminal 407 pauses immediately; budget re-pauses after 5),
  then re-alerts — one attempt + one email per cooldown, never a hot loop.
- `.env.template` documents the new keys.

**Verification**

- `pytest` (unit, hermetic): 71 passed (10 new in `test_pause_alerts.py` —
  fake-mailer alert tests, cooldown/auto-resume, re-pause + re-alert, and
  ResendMailer seam tests incl. key-never-logged).
- Code review (standards + spec axes) run post-implementation; spec 6/6 met.
  Standards findings fixed: the shared `radio_state` fixture moved to
  `tests/unit/conftest.py` (was triplicated), dead helper removed, unused
  imports dropped. Kept (documented judgement call): the defensive error
  boundary in `_send_pause_alert` — the seam contract is send() never
  raises, but the pause path must survive any seam implementation.
- `pytest -m integration`: 4 passed, and `pytest -m proxied`: 2 passed —
  verified by the user from a network-capable shell after this session (the
  agent sandbox denies all network, so its own run failed on EPERM, not
  code). Streaming works end-to-end direct and through the proxy.
