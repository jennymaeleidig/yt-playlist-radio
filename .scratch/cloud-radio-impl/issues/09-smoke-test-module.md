# 09: Smoke test module

**What to build:** A pytest `smoke` module consuming `--base-url` that encodes the post-deploy checklist as one runnable command: HTTPS page loads and `/stream` plays audio; icy-metadata updates between tracks; pausing the stream kills the fetch processes within ~1 s; no YouTube traffic with zero listeners; breaking the proxy credentials → radio pauses with backoff, alert email arrives, auto-resumes after the cooldown; keep-alive CPU percentile checked from OCI metrics where scriptable, else flagged for the operator. Demoable: `pytest -m smoke --base-url=https://<domain>` validates a fresh deployment end to end.

**Blocked by:** 07, 08.

**Status:** ready-for-agent

- [ ] `smoke` marker registered; module takes `--base-url`; default test run excludes it
- [ ] Covers: page + stream load, icy-metadata, idle shutoff within a bounded time, no idle YouTube traffic
- [ ] Covers: broken-creds → pause + alert + auto-resume (operator-assisted steps clearly flagged)
- [ ] Covers: keep-alive CPU percentile (OCI-metrics scriptable check or explicit operator flag)
- [ ] Operator-flagged check: DataImpulse auto-recharge is off in the dashboard (spec story 15 — no spend possible; 5 GB trial is the ceiling)
- [ ] One command validates a fresh deployment
