# 05: Pause semantics — alert email + auto-resume

**What to build:** When the supervisor pauses (failure budget or traffic exhausted), an alert email goes out via msmtp using an app-password from an existing mailbox, containing the last yt-dlp stderr excerpt and the failure counters. After a ~30 minute cooldown the radio auto-resumes with a fresh failure budget — transient proxy outages self-heal, and the human still hears about it. The mailer is a seam: unit tests use a fake mailer and never send real email.

**Blocked by:** 04.

**Status:** ready-for-agent

- [ ] Pause triggers one alert email with stderr excerpt + failure counters (unit-tested with a fake mailer)
- [ ] Auto-resume after the cooldown with a fresh failure budget (unit-tested)
- [ ] Resuming on a still-broken proxy re-pauses and re-alerts rather than looping hot
- [ ] SMTP config read from the env file; no credentials in code or logs
