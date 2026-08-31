# 03: Proxied fetch routing

**What to build:** With DataImpulse credentials in the env, every YouTube fetch class — playlist listing, per-track metadata, and media streaming — rides the residential proxy, with one sticky session per track so metadata extraction and the media download share one exit IP. Upstream audio is capped at ≤56 kbps with the fallback chain (`abr<=56` → `abr<=96` → `bestaudio`), and the mandated flag baseline is applied (chunk size ≤10 MB, modest `--retries`/`--fragment-retries` with backoff, `--retry-sleep linear=1:30` — never immediate retry loops — 20 s socket timeout, `--extractor-retries 5` for the metadata phase). Cookies are removed from the proxied path: when the proxy is configured, `COOKIES_FILE` must not be passed to yt-dlp (the future bot-wall path, cookies exported through the same proxy exit IP, is recorded but not built). The fail-closed invariant is enforced at the transport seam: when the proxy is configured, the direct path is structurally unreachable, pinned by a regression test. Without credentials, the app runs direct exactly as before. Demoable: set creds, hear the stream through the proxy (exercised deliberately behind the proxied-integration opt-in to protect quota).

**Blocked by:** 02.

**Status:** done

- [x] Proxy URL built at runtime from env credentials; never hardcoded
- [x] Per-track sticky session (`sessid`) covers metadata + media for that track
- [x] Format-selector fallback chain implemented and unit-tested via the fake transport
- [x] Mandated flag baseline present on proxied invocations: `--http-chunk-size` ≤10 MB, modest `--retries`/`--fragment-retries` with backoff, `--retry-sleep linear=1:30`, `--socket-timeout 20`, `--extractor-retries 5` (metadata phase)
- [x] `COOKIES_FILE` excluded from proxied invocations (unit-tested: `--cookies` never present when proxy is configured); direct mode keeps current cookies behavior
- [x] Fail-closed regression test: no unproxied spawn ever attempted when the proxy is configured
- [x] Unit tests: `--proxy` present when configured, absent otherwise
- [x] Proxied integration opt-in (loud quota warning) verified once by hand; direct mode unaffected without creds; no bot-wall seen (evidence gate for the recorded cookies future path) (loud quota warning) verified once by hand; direct mode unaffected without creds

## Comments

**Implementation notes**

- `transport.py` grew the proxy machinery: `build_dataimpulse_proxy_url(user, pass)`
  (URL-quoted, returns `None` when either credential is missing), `Transport(proxy_url=...)`
  with a `proxied` property, the mandated `PROXIED_FLAG_BASELINE` (chunk size 10M,
  retries 3/3, `linear=1:30` backoff, socket timeout 20) applied to every proxied spawn,
  and `--extractor-retries 5` added by `run_ytdlp` only (metadata phase).
- **Sticky sessions are port-based on DataImpulse** (verified against
  docs.dataimpulse.com): the HTTP gateway is `gw.dataimpulse.com:823` (rotating) and
  ports 10000–20000 hold one exit IP for 1–120 min; there is no `sessid` username
  parameter. So "one sticky session per track" is implemented as a deterministic
  sticky port derived from the track URL (`sticky_port_for_track`): metadata and media
  for the same track always get the same port, hence the same exit IP. Stateless — no
  coordination between the metadata and media paths.
- Fail-closed by construction: `Transport.yt_dlp_argv` has no branch that combines
  `--proxy` with `--cookies` — with the proxy configured, cookies are structurally
  unreachable and every spawn carries `--proxy` + the baseline. Without the proxy,
  argv is byte-identical to the pre-proxy direct mode (cookies preserved, no
  baseline flags), pinned by tests.
- `yt_radio`: env creds → `PROXY_URL` → `TRANSPORT`; `_media_format_selector()` picks
  `PROXIED_FORMAT_CHAIN` (`bestaudio[abr<=56]/bestaudio[abr<=96]/bestaudio`) when
  proxied, `YTDLP_FORMAT` otherwise; `fetch_metadata` passes the track URL as sticky
  key; playlist listing (in-process `YoutubeDL` API) gets `proxy` in its opts when
  configured. A startup log line announces proxied mode.
- `tests/unit/test_proxied_fetch.py` (17 tests): transport-level argv assertions (the
  flag baseline / cookies exclusion / fail-closed invariant ARE argv facts, mirroring
  issue 02's venv-pinning exception) + supervisor-level tests through the fake
  transport (format chain selection, sticky key) with no argv assertions.
- `tests/integration/test_proxied_radio.py`: the proxied opt-in — `proxied` marker
  (deselected by default via `addopts`, runs only under `pytest -m proxied`), loud
  quota warning on start, self-skips without creds, verifies real metadata + ≥64 KB
  of real media through the proxy and gates on "no bot-wall seen".
- `tests/conftest.py` pins `DATAIMPULSE_*` to empty so every default/direct test run
  is deterministic direct-mode regardless of the local `.env`.

**Blockers on the two evidence checkboxes (environment, not code):**

1. ~~No DataImpulse credentials configured~~ **RESOLVED**: user set creds in `.env` and ran both suites by hand.
2. ~~Session sandbox blocked all outbound network~~ **RESOLVED**: network returned in a fresh (unsandboxed) terminal.

**Hand-verification evidence (user-run, after setting creds in `.env`)**

- `pytest -m integration` → **4 passed in 17.20s** — direct mode unaffected; stream still plays from the home IP.
- `pytest -m proxied -s` → **2 passed in 9.67s** (loud quota warning printed) — real metadata + ≥64 KB of real media rode the DataImpulse residential proxy; **no bot-wall seen** (the 403/"Sign in to confirm" gate passed). Evidence gate satisfied: the recorded cookies-through-proxy future path is not needed for now.

Issue complete: 43 unit tests green; every checkbox met. The fail-closed invariant means no code changes are needed per-environment — creds in `.env` switch the whole app to proxied mode; unset creds run direct exactly as before.

**Verification (done)**

- Unit suite (hermetic): **43 passed** (25 pre-existing + 18 new).
- `python -m py_compile` clean on all app modules.
- Proxied opt-in verified to be safely inert without creds (skips with a clear reason;
  cannot accidentally spend quota in a default or `-m integration` run).

**Code review outcomes (both axes applied)**

- Spec axis: 9/10 met; deviation (port-based stickiness instead of a `sessid`
  parameter) judged justified and disclosed; no scope creep.
- Standards axis flagged the silent rotating-gateway fallback when a proxied spawn
  forgot `sticky_key` (Speculative Generality, latent fail-open) — fixed: proxied
  spawns without a `sticky_key` now raise `ValueError`, pinned by a test.
- Both axes noted playlist listing (in-process `YoutubeDL` API) rode the proxy
  without the baseline — fixed: `transport.proxied_ydl_opts()` applies the
  baseline's Python-API equivalents (chunk size, socket timeout, retries,
  extractor retries) to the playlist path; `linear=1:30` has no clean API
  equivalent and is documented as such. Pinned by tests in both modes.
