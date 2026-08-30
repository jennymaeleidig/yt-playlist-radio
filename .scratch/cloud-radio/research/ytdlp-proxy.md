# yt-dlp proxy mechanics: routing, cost control, robustness, failure signatures

Research for yt-playlist-radio (Flask radio stream; yt-dlp used three ways:
`extract_flat` playlist, `--dump-json` metadata, and `-f bestaudio -o -` piped into ffmpeg).

**Evidence base**: yt-dlp `master` source (fetched 2026-02-13, commit of master branch tarball),
the options help text (which is generated into the README / manpage), and the yt-dlp wiki FAQ.
Line numbers refer to `yt-dlp master` at research time; treat them as pointers, not ABI.

---

## 1. Proxy options: which apply to ALL request types

### The options

| Option | Help text (README Network Options) | Notes |
|---|---|---|
| `--proxy URL` | "Use the specified HTTP/HTTPS/SOCKS proxy. To enable SOCKS proxy, specify a proper scheme, e.g. `socks5://user:pass@127.0.0.1:1080/`. Pass in an empty string (`--proxy ""`) for direct connection" | Sets `proxies = {'all': URL}` (`yt_dlp/YoutubeDL.py` `YoutubeDL.proxies`, ~L4205). `--proxy ""` becomes sentinel `__noproxy__`, which `clean_proxies` converts to `None` (direct), **overriding any env var**. |
| `--socket-timeout SECONDS` | "Time to wait before giving up, in seconds" | Passed to every request handler via `build_request_director` (`timeout` key, YoutubeDL.py ~L4348–4360). |
| `--source-address IP` | "Client-side IP address to bind to" (also `-4`/`-6` set it to `0.0.0.0`/`::`) | Also passed to all handlers. Binds the *client* socket — largely irrelevant (and ineffective) when traffic goes through an HTTP proxy; it matters only for direct connections / multi-homed machines. |

### Environment variables

When `--proxy` is **not** given, yt-dlp falls back to `urllib.request.getproxies()`
(YoutubeDL.py ~L4213), which reads `HTTP_PROXY`/`http_proxy`, `HTTPS_PROXY`/`https_proxy`,
`ALL_PROXY`/`all_proxy`, and `NO_PROXY`/`no_proxy` from the environment (on Windows also the
registry). Compat quirk (YoutubeDL.py ~L4215): **if `http` is set but `https` is not,
the `http` proxy is used for HTTPS URLs too** (comment: "Set HTTPS_PROXY to `__noproxy__` to revert").

Scheme normalization in `clean_proxies` (`yt_dlp/utils/networking.py` ~L169):
- scheme-less proxy URLs get `http://` prefixed
- `socks5` is remapped to `socks5h` (DNS resolved by the proxy — this is the compat behavior)
- `socks` is remapped to `socks4`

### Do they apply to ALL request types?

**Yes, for everything that goes through yt-dlp's own networking stack** — which means:

- webpage fetches (extractor HTTP requests),
- YouTube API/player calls during extraction (incl. `--dump-json` and `extract_flat`),
- media downloads and **every** fragment/range request of the native DASH/HLS/progressive
  downloaders.

Mechanism: all of these call `YoutubeDL.urlopen()` → `RequestDirector` → handlers
(`Requests`, `Urllib`, `CurlCFFI`, `WebSockets`), and the director is built once per run with
`proxies=self.proxies`, `source_address`, `timeout=socket_timeout`
(YoutubeDL.py `build_request_director`, ~L4342–4365). There is no separate "extraction proxy"
and "download proxy".

Known partial exceptions (only relevant with `--downloader <external>`, **not** for this app's
`-o - | ffmpeg` pipe, where ffmpeg is fed from yt-dlp's stdout and does no network I/O):

- **ffmpeg as downloader** (`FFmpegFD`, `yt_dlp/downloader/external.py` ~L416–433): passes
  `self.params.get('proxy')` — the `--proxy` **option value only, never the env fallback** —
  by setting `HTTP_PROXY`/`http_proxy` in the subprocess env. Warns that SOCKS is unsupported
  for ffmpeg. (If `--proxy` is unset, the child inherits the parent env, so an `HTTP_PROXY` env
  var would still reach ffmpeg through ffmpeg's own env handling.)
- **wget**: `--execute http_proxy=.../https_proxy=...` (same params-only lookup, L296–298).
- **aria2c**: `--all-proxy` (same params-only lookup, L324).
- JS-runtime subprocesses used by the YouTube extractor (deno/bun for signatures) *do* get the
  full proxy map translated into `HTTP_PROXY`/`HTTPS_PROXY` env
  (`yt_dlp/extractor/youtube/jsc/_builtin/deno.py` ~L92–100, `bun.py` ~L81–98) — note bun
  rejects unsupported proxy schemes.
- `NO_PROXY` is honored only by handlers supporting `Features.NO_PROXY` (Requests, Urllib,
  CurlCFFI); the WebSockets handler supports `ALL_PROXY`/`NO_PROXY`
  (`yt_dlp/networking/*.py` `_SUPPORTED_FEATURES`).

`--geo-verification-proxy` exists separately: it's only for the geo-verification IP check;
the default proxy is used for the actual download.

## 2. Are media fragments proxied for every request?

**Yes.** This is the load-bearing fact for cost: an audio stream downloaded via
`-f bestaudio -o -` consumes proxy bandwidth end-to-end.

- Progressive formats (typical YouTube audio itags): native `HttpFD` reads the response through
  `self.ydl.urlopen(...)` (`yt_dlp/downloader/http.py` L119, L153) → request director → proxy.
  Mid-stream resume after a drop re-requests with a `Range` header — that re-request also goes
  through the director.
- DASH/HLS (`FragmentFD`): each fragment fetch goes through `self.ydl.urlopen(...)`
  (`yt_dlp/downloader/fragment.py` L346 and `_download_fragment` path) → same director → proxy.
- Streaming to stdout (`-o -`) forces yt-dlp's own downloaders (no external merge/downloader),
  so the pipe to ffmpeg carries bytes already fetched through the proxy.

Caveats:
- `--concurrent-fragments N` opens N parallel proxied connections → N× proxy bandwidth in
  bursts. Leave at default 1 for cost predictability.
- With `--downloader ffmpeg` for HLS, ffmpeg does the network I/O and is only proxied via the
  env-var mechanism described above (see §1 exceptions).

## 3. Interplay with `--cookies`

There is no code-level conflict — the cookiejar is shared across the same request director that
holds the proxies, so cookies are sent on proxied requests including fragment downloads. The
caveats are operational, and the official wiki FAQ states them:

- **IP consistency (FAQ, "HTTP Error 403 / Cloudflare")**: "It requires cookies from a browser
  with the same IP address that you will be using with yt-dlp." If you route yt-dlp through a
  proxy, cookies exported from your home-IP browser may not satisfy the anti-bot check — the
  cookie session and the proxy exit IP must match (or be from the same IP block).
- **IP consistency (FAQ, "HTTP Error 429 / 402")**: after solving a CAPTCHA and passing the
  cookies, "if your machine has multiple external IPs then you should also pass exactly the same
  IP you've used for solving CAPTCHA with `--source-address`" — same principle applies to the
  proxy: the IP that solved the CAPTCHA should be the IP yt-dlp egresses from.
- **Cookie file hygiene (FAQ, "How do I pass cookies to yt-dlp?")**: Netscape format, first line
  must be `# HTTP Cookie File` (or `# Netscape HTTP Cookie File`), newline format matters —
  `HTTP Error 400: Bad Request` when using `--cookies` is the signature of bad newlines. A
  cookies file exported via yt-dlp contains cookies for **all** sites — treat as a secret.
- UNCERTAIN (not officially documented): community reports that logged-in YouTube cookies used
  from datacenter/proxy IPs can trigger "Sign in to confirm you're not a bot". The FAQ's
  same-IP requirement above is the closest official statement; there is no official doc saying
  YouTube cookies are IP-bound. Reason: not in README/FAQ/CHANGELOG; only issue-tracker lore.

## 4. Cost and robustness knobs

### (a) Capping bandwidth / cost

| Knob | What it does | Where it applies |
|---|---|---|
| `--limit-rate` / `-r RATE` | "Maximum download rate in bytes per second, e.g. 50K or 4.2M" | Implemented as `FileDownloader.slow_down` — sleeps inside the native downloader read loop (`yt_dlp/downloader/common.py` ~L200). **Per connection**: with `--concurrent-fragments N` each fragment is separately throttled. Not applied by external downloaders (wget is given `--limit-rate`; ffmpeg/aria2c are not, UNCERTAIN for aria2c — no matching flag found in Aria2cFD). |
| `--throttled-rate RATE` | "Minimum download rate in bytes per second below which throttling is assumed and the video data is re-extracted" (http.py ~L315) | Robustness more than cost; also triggers a new (re-)extraction, which costs API requests. |
| `--http-chunk-size SIZE` | "Size of a chunk for chunk-based HTTP downloading… May be useful for bypassing bandwidth throttling imposed by a webserver (experimental)" (default disabled) | FAQ: "YouTube throttles any request with an http chunk size of > 10MB". Larger chunks = fewer requests (cheaper metadata-wise); >10MB risks throttle. |
| `--sleep-interval` (+ `--max-sleep-interval`) | "Number of seconds to sleep **before each download**" | Pacing across videos in the radio rotation; also softens 429 pressure. |
| `--sleep-requests SECONDS` | "Number of seconds to sleep between requests during data extraction" | Paces the metadata/API phase (extract_flat playlist pages, --dump-json). |

### (b) Surviving proxy errors

| Knob | Default | Notes |
|---|---|---|
| `--retries RETRIES` | 10 ("infinite" allowed) | Retries of the native HTTP download loop (connection + read errors). Covers proxy connection failures and mid-stream resets. |
| `--fragment-retries RETRIES` | 10 ("infinite" allowed; help notes "DASH, hlsnative and ISM") | Per-fragment retries; `--skip-unavailable-fragments` is default-on so a dead fragment beyond retries is skipped rather than fatal for non-first fragments. |
| `--retry-sleep [TYPE:]EXPR` | none (immediate retry) | Types: `http`, `fragment`, `file_access`, `extractor`. EXPR: number, `linear=START[:END[:STEP=1]]` or `exp=START[:END[:BASE=2]]`. E.g. `--retry-sleep linear=1::2 --retry-sleep fragment:exp=1:20`. Backoff matters for a proxy that rate-limits or flaps. |
| `--extractor-retries RETRIES` | 3 | Retries "known extractor errors" — i.e. the metadata/API extraction phase (`RetryManager` in `extractor/common.py` ~L4071). This is the knob for proxy blips during `--dump-json` / playlist refresh. |
| `--socket-timeout SECONDS` | none (OS default) | Fail-fast on a hung proxy; pair with retries+backoff. |

Recommendation shape for a radio supervisor (not prescriptive):
`--retries 10 --fragment-retries infinite --retry-sleep linear=1:30 --extractor-retries 5 --socket-timeout 20`,
plus `--limit-rate` only if you genuinely want to cap bitrate; note capping a 128kbps audio
stream below ~160K makes buffering stalls likely.

## 5. Failure behavior of a proxied media download

### Retries (visible on stderr, non-fatal)

From `RetryManager.report_retry` (`yt_dlp/utils/_utils.py` ~L5290) and
`FileDownloader.report_retry` (`yt_dlp/downloader/common.py` ~L410):

```
[download] Got error: <error>. Retrying (1/10)...
[download] Sleeping 3.00 seconds ...
...
[download] Got error: <error>. Giving up after 10 retries
```

The `<error>` for proxy/transport problems is the networking exception string, e.g.
`HTTP Error 502: Bad Gateway` (from `networking/exceptions.py` `HTTPError`), or
`<In-completeRead>`/`URLError`-family text for dropped connections (`TransportError`).

### Terminal failure

- The download phase reports `ERROR: <message>` on **stderr** via `report_error` → `trouble`
  (YoutubeDL.py ~L1071). Typical signatures when a proxy dies:
  - `ERROR: unable to download video data: <network error>` (YoutubeDL.py ~L3595, catches
    `network_exceptions` around the download call)
  - `ERROR: [download] Got error: <error>. Giving up after N retries`
  - `ERROR: Did not get any data blocks` (http.py L329 — zero bytes ever arrived)
  - `ERROR: content too short (expected X bytes and served Y)` (ContentTooShortError)
  - extraction-phase (proxy down during `--dump-json`/playlist): `ERROR: <url>: Unable to
    download webpage: <error>` (extractor errnote), retried per `--extractor-retries`.
- **Exit codes** (`yt_dlp/__init__.py` ~L1080–1095, `_exit`):
  - `0` success
  - `1` any download/extraction error — both the uncaught-`DownloadError` path
    (`except (CookieLoadError, DownloadError, UnsafeExecExpansionError): _exit(1)`) and the
    `--ignore-errors` path (`_download_retcode = 1` returned at end of run)
  - `2` option/usage parse error
  - `100` only on updater failure (not download-related)
  - `KeyboardInterrupt` → `ERROR: Interrupted by user`, exit 1
  - `BrokenPipeError` (the consumer — e.g. ffmpeg — died first) → `ERROR: ...` on stderr, exit 1
- Media written to stdout goes to the pipe; **all** diagnostics go to stderr, so a supervisor
  can rely on: exit code != 0 ⇒ run failed; stderr line starting with `ERROR:` ⇒ failure with
  reason; `[download] Got error: ... Retrying` ⇒ transient, still working.
- Note for the pipe architecture: a proxy failure mid-stream kills yt-dlp's download loop, but
  ffmpeg at the other end of the pipe sees EOF, not an error — ffmpeg will likely exit 0 (or
  produce a short-but-valid file) on truncated input. **The supervisor must key off yt-dlp's
  exit code / stderr, not ffmpeg's.** (Inference from the architecture + BrokenPipeError
  handling; UNCERTAIN for ffmpeg's exact exit status on truncated stdin, not documented by
  yt-dlp.)

---

## Sources

- yt-dlp README (options help text; the `--proxy`, `--socket-timeout`, `--source-address`,
  `--limit-rate`, `--retries`, `--retry-sleep`, `--extractor-retries`, `--http-chunk-size`,
  `--sleep-interval` descriptions): <https://github.com/yt-dlp/yt-dlp#options>
- yt-dlp wiki FAQ ("How do I pass cookies to yt-dlp?", "HTTP Error 403…Cloudflare",
  "HTTP Error 429 or 402", "How do I stream directly to media player?"):
  <https://github.com/yt-dlp/yt-dlp/wiki/FAQ>
- Source, `yt-dlp master` (fetched 2026-02-13):
  - `yt_dlp/options.py` — option definitions (L609–663 network options; L1016–1047 retries;
    L1209–1218 sleep interval; L1905–1907 extractor-retries; L1077 http-chunk-size)
  - `yt_dlp/YoutubeDL.py` — `proxies` property (~L4205), `build_request_director` (~L4342),
    `trouble` (~L1071), exit plumbing, `unable to download video data` (~L3595)
  - `yt_dlp/utils/networking.py` — `clean_proxies` (env scheme normalization, socks5→socks5h)
  - `yt_dlp/downloader/http.py` — native downloader via `ydl.urlopen` (L119, L153), retry loop
    (L360), throttled-rate (L315), "Did not get any data blocks" (L329)
  - `yt_dlp/downloader/fragment.py` — fragment fetch via `ydl.urlopen` (L346), per-fragment
    retry manager (L456–461)
  - `yt_dlp/downloader/common.py` — `slow_down` rate limiting (~L200), `report_retry`
    signatures (~L410)
  - `yt_dlp/downloader/external.py` — FFmpegFD env proxy (L416–433), WgetFD (L296–298),
    Aria2cFD `--all-proxy` (L324)
  - `yt_dlp/utils/_utils.py` — `RetryManager.report_retry` message formats (~L5290)
  - `yt_dlp/networking/exceptions.py` — `HTTPError` message format ("HTTP Error NNN: reason")
  - `yt_dlp/__init__.py` — exit codes (`_exit`, main try/except ~L1080–1095)
  - `yt_dlp/extractor/youtube/jsc/_builtin/deno.py`, `bun.py` — JS runtime subprocess proxy env
