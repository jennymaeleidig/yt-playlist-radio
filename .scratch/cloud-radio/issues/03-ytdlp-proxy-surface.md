# 03 — yt-dlp proxy surface and rate-limit controls

Type: research
Status: resolved

Docs: https://github.com/yt-dlp/yt-dlp (README + wiki)

## Question

What are the exact yt-dlp mechanisms for routing traffic through a proxy, and which knobs
matter for cost and robustness?

Answer precisely, with sources (yt-dlp README/options list, wiki):

1. **Proxy options**: `--proxy`, `--socket-timeout`, `--source-address`, environment variables
   (`HTTP_PROXY`/`HTTPS_PROXY`/`ALL_PROXY`) — which apply to *all* request types (flat playlist
   extraction, `--dump-json`, media download) or only some?
2. **Do media downloads honor the proxy** for every fragment/range request of a DASH/streaming
   download, not just the initial webpage fetch?
3. **Interplay with `COOKIES_FILE`**: any known conflicts or caveats using cookies + proxy together?
4. **Cost/robustness knobs**: `--limit-rate`, `--sleep-interval`, `--retries`, `--retry-sleep`,
   `--extractor-retries` — which are useful for (a) keeping costs down and (b) surviving proxy
   errors mid-stream?
5. **Failure behavior**: how does a yt-dlp media download fail when the proxy errors mid-stream
   (exit code, stderr signature) — what should the app's supervisor detect?

Write findings to `.scratch/cloud-radio/research/ytdlp-proxy.md`; the ticket answer gists it.

## Answer

Full findings: [research/ytdlp-proxy.md](../research/ytdlp-proxy.md). Gist:

1. **One global proxy**: `--proxy URL` (or `HTTP_PROXY`/`HTTPS_PROXY`/`ALL_PROXY` env when `--proxy` is absent) applies to *every* yt-dlp request — webpage, API/player calls, and media downloads alike — via the single `RequestDirector`. `--proxy ""` forces direct. Quirks: `HTTP_PROXY` is reused for HTTPS if `HTTPS_PROXY` unset; `socks5` remaps to `socks5h`.
2. **Media fragments are proxied**: native HTTP/DASH/HLS downloaders route every fragment/range through `ydl.urlopen()`. With the app's `-f bestaudio -o - | ffmpeg` pipe, all audio bandwidth flows through the proxy — confirmed for the media-routing cost question.
3. **Cookies + proxy**: no code conflict; FAQ caveat is IP consistency — cookies must be exported from an IP matching the proxy exit.
4. **Cost knobs**: `--limit-rate`, `--http-chunk-size` (≤10 MB; YouTube throttles larger), `--sleep-interval`/`--sleep-requests`. **Robustness**: `--retries`, `--fragment-retries`, `--retry-sleep linear=1:30`, `--extractor-retries`, `--socket-timeout`.
5. **Failure signatures**: stderr `[download] Got error: … Retrying/Giving up after N retries`, terminal `ERROR: unable to download video data: <err>` (or "Did not get any data blocks", "content too short"); exit codes 0/1/2/100. **Supervisors must key on yt-dlp's exit code — ffmpeg only sees EOF and can exit 0 on truncated input.** Directly relevant to ticket 04's failure-containment design.
