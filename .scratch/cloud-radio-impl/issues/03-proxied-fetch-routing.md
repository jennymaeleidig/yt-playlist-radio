# 03: Proxied fetch routing

**What to build:** With DataImpulse credentials in the env, every YouTube fetch class — playlist listing, per-track metadata, and media streaming — rides the residential proxy, with one sticky session per track so metadata extraction and the media download share one exit IP. Upstream audio is capped at ≤56 kbps with the fallback chain (`abr<=56` → `abr<=96` → `bestaudio`), and the mandated flag baseline is applied (chunk size ≤10 MB, modest `--retries`/`--fragment-retries` with backoff, `--retry-sleep linear=1:30` — never immediate retry loops — 20 s socket timeout, `--extractor-retries 5` for the metadata phase). Cookies are removed from the proxied path: when the proxy is configured, `COOKIES_FILE` must not be passed to yt-dlp (the future bot-wall path, cookies exported through the same proxy exit IP, is recorded but not built). The fail-closed invariant is enforced at the transport seam: when the proxy is configured, the direct path is structurally unreachable, pinned by a regression test. Without credentials, the app runs direct exactly as before. Demoable: set creds, hear the stream through the proxy (exercised deliberately behind the proxied-integration opt-in to protect quota).

**Blocked by:** 02.

**Status:** ready-for-agent

- [ ] Proxy URL built at runtime from env credentials; never hardcoded
- [ ] Per-track sticky session (`sessid`) covers metadata + media for that track
- [ ] Format-selector fallback chain implemented and unit-tested via the fake transport
- [ ] Mandated flag baseline present on proxied invocations: `--http-chunk-size` ≤10 MB, modest `--retries`/`--fragment-retries` with backoff, `--retry-sleep linear=1:30`, `--socket-timeout 20`, `--extractor-retries 5` (metadata phase)
- [ ] `COOKIES_FILE` excluded from proxied invocations (unit-tested: `--cookies` never present when proxy is configured); direct mode keeps current cookies behavior
- [ ] Fail-closed regression test: no unproxied spawn ever attempted when the proxy is configured
- [ ] Unit tests: `--proxy` present when configured, absent otherwise
- [ ] Proxied integration opt-in (loud quota warning) verified once by hand; direct mode unaffected without creds; no bot-wall seen (evidence gate for the recorded cookies future path) (loud quota warning) verified once by hand; direct mode unaffected without creds
