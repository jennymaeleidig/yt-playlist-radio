# 04: Cost guardrails — backoff + failure budget

**What to build:** The radio supervisor stops retry-storming and stops spending on systemic failure. Consecutive track failures back off exponentially (replacing the ~1 retry/sec-forever loop). A failure budget — 5 consecutive failures, or 5 within 10 minutes — pauses the radio loop. A `407 TRAFFIC_EXHAUSTED` response is terminal: pause immediately, zero retries. Supervision keys on yt-dlp's exit code, never ffmpeg's, and yt-dlp stderr is captured for later alerting. All guardrail behaviour is deterministic unit tests through the fake transport. Demoable: script fake failures and watch the supervisor pause; script a 407 and watch it stop dead.

**Blocked by:** 02.

**Status:** ready-for-agent

- [ ] Exponential backoff on consecutive track failures; no fixed ~1/sec retry loop remains
- [ ] Failure budget (5 consecutive / 5 in 10 min) pauses the supervisor loop
- [ ] `407 TRAFFIC_EXHAUSTED` → immediate pause, zero retries (unit-tested)
- [ ] Failure detection keys on yt-dlp exit codes; ffmpeg's exit code ignored for failure decisions
- [ ] yt-dlp stderr captured at failure time and available to the pause path
- [ ] All behaviours covered by unit tests via scripted exit-code sequences
