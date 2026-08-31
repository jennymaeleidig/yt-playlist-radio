from yt_dlp import YoutubeDL
from file_util import _load_urls_from_file, _create_or_get_cache, _save_cache
from transport import Transport, build_dataimpulse_proxy_url, proxied_ydl_opts
from alerts import ResendMailer
import json
import re
import sys
import threading
import random
import os
from collections import deque
from dotenv import load_dotenv
import logging
import time
from queue import Queue
import uuid


load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


BITRATE_KBPS = int(os.environ.get("BITRATE_KBPS", "192"))
BURST_SECONDS = int(os.environ.get("BURST_SECONDS", "10")) # allow pre-buffering of 10 sec on /stream
BASE_URL = os.environ.get("BASE_URL", "http://localhost:8000")
PLAYLIST_URL = os.environ.get("PLAYLIST_URL")
CACHE_FILE = os.environ.get("CACHE_FILE", "cache.json")
RANDOMIZE_PLAYLIST = bool(os.environ.get("RANDOMIZE_PLAYLIST", "false"))
META_INTERVAL_SECONDS = int(os.environ.get("META_INTERVAL_SECONDS", "5"))

# --- Cost guardrails (issue 04) ----------------------------------------------
# The supervisor must stop retry-storming and stop spending on systemic
# failure. Consecutive track failures back off exponentially; the failure
# budget (5 consecutive, or 5 within a 10-minute rolling window) pauses the
# loop entirely; a 407 TRAFFIC_EXHAUSTED proxy response is terminal — pause
# immediately, zero retries.
FAILURE_BUDGET_CONSECUTIVE = 5
FAILURE_BUDGET_WINDOW_FAILURES = 5
FAILURE_BUDGET_WINDOW_MINUTES = 10
FAILURE_BUDGET_WINDOW_SECONDS = FAILURE_BUDGET_WINDOW_MINUTES * 60

# Backoff for the nth consecutive failure: base * 2^(n-1), capped.
BACKOFF_BASE_SECONDS = float(os.environ.get("TRACK_FAILURE_BACKOFF_BASE_SECONDS", "2"))
BACKOFF_MAX_SECONDS = float(os.environ.get("TRACK_FAILURE_BACKOFF_MAX_SECONDS", "60"))

# While paused the loop polls this often for a resume or shutdown.
PAUSE_POLL_SECONDS = 5.0

# yt-dlp stderr patterns for a terminal proxy traffic-exhaustion response.
# Deliberately stricter than a bare "407" substring: byte counts, URLs and
# track ids often contain "407", and this pause is terminal — false
# positives are costly.
TRAFFIC_EXHAUSTED_PATTERNS = (
    re.compile(r"HTTP Error 407"),
    re.compile(r"\b407\s+Proxy"),
    re.compile(r"TRAFFIC_EXHAUSTED"),
)

# How much of the failing spawn's stderr is kept for the pause path
# (the alert email consumes this).
STDERR_EXCERPT_CHARS = 2000

# --- Pause alerting + auto-resume (issue 05) ---------------------------------
# After a pause the supervisor waits out this cooldown, then auto-resumes
# with a fresh failure budget — transient proxy outages self-heal, and each
# pause (including a re-pause after a failed resume) alerts exactly once.
PAUSE_RESUME_COOLDOWN_MINUTES = float(
    os.environ.get("PAUSE_RESUME_COOLDOWN_MINUTES", "30")
)
PAUSE_RESUME_COOLDOWN_SECONDS = PAUSE_RESUME_COOLDOWN_MINUTES * 60

# Path to a cookies.txt file for YouTube auth. Required when YouTube bot-blocks
# the server's IP — export cookies from a logged-in browser session.
# When unset, yt-dlp runs without cookies (works from non-flagged IPs).
COOKIES_FILE = os.environ.get("COOKIES_FILE")

# --- Residential proxy (DataImpulse) -----------------------------------------
# When both credentials are set, EVERY YouTube fetch (playlist listing,
# per-track metadata, media streaming) rides the proxy, and COOKIES_FILE is
# ignored on the proxied path (cookies exported through a different IP than
# the proxy exit would be worse than none; the future bot-wall path — cookies
# exported through the same proxy exit IP — is recorded but not built).
# The URL is built at runtime from the credentials; never hardcode it.
DATAIMPULSE_USER = os.environ.get("DATAIMPULSE_USER")
DATAIMPULSE_PASS = os.environ.get("DATAIMPULSE_PASS")
PROXY_URL = build_dataimpulse_proxy_url(DATAIMPULSE_USER, DATAIMPULSE_PASS)

# yt-dlp format selection with fallback chain for resilience against
# YouTube experiments that make pure audio-only formats unavailable.
YTDLP_FORMAT = os.environ.get(
    "YTDLP_FORMAT", "bestaudio[ext=webm]/bestaudio[ext=m4a]/bestaudio/best"
)

# Proxied fetches cap the upstream audio at <=56 kbps (proxy egress is paid
# per GB) with a graceful fallback chain if the capped tier is unavailable.
PROXIED_FORMAT_CHAIN = "bestaudio[abr<=56]/bestaudio[abr<=96]/bestaudio"

# The single transport object through which every yt-dlp / ffmpeg subprocess
# spawn flows (see transport.py). yt-dlp is pinned to the venv's copy (run via
# the venv interpreter); unit tests replace this object wholesale.
TRANSPORT = Transport(cookies_file=COOKIES_FILE, proxy_url=PROXY_URL)
if PROXY_URL:
    logger.info(
        "Residential proxy configured: all YouTube fetches ride the proxy "
        "(one sticky session per track); COOKIES_FILE is ignored on the proxied path"
    )

# The mailer seam for pause alerts (issue 05). None when alert email is not
# configured (RESEND_API_KEY / ALERT_EMAIL_FROM / ALERT_EMAIL_TO unset) —
# pausing then only logs. Unit tests replace this object wholesale; no test
# ever sends real email.
MAILER = ResendMailer.from_env()

# Optional one-shot: if yt-dlp is 403-blocked/bot-walled, point the
# operator at COOKIES_FILE once so a persistent block doesn't spam every track.
_cookies_recommended = threading.Event()


def _maybe_log_cookies_recommendation(ytdlp_stderr: str) -> None:
    '''If yt-dlp failed with an HTTP 403 (or the YouTube bot-wall), log a
    one-time recommendation. From a bot-flagged IP the media download 403s
    even when metadata fetches succeed, so affected tracks are otherwise
    silently skipped with no clue why. The remedy is to pass authenticated
    browser cookies via COOKIES_FILE.
    '''
    if _cookies_recommended.is_set() or not ytdlp_stderr:
        return
    if ("403" in ytdlp_stderr and "Forbidden" in ytdlp_stderr) or "Sign in to confirm you're not a bot" in ytdlp_stderr:
        _cookies_recommended.set()
        where = "COOKIES_FILE is not set" if not COOKIES_FILE else f"COOKIES_FILE={COOKIES_FILE} (expired or wrong session?)"
        logger.error(
            "yt-dlp is being blocked by YouTube (403 Forbidden / bot-wall); %s. "
            "Export browser cookies and set COOKIES_FILE in .env — see docs/COOKIES.md. "
            "Affected tracks will be skipped until then.",
            where,
        )
# optional / page
SITE_TITLE = os.environ.get("SITE_TITLE", "yt_radio.py")
SITE_IMAGE = os.environ.get("SITE_IMAGE", "")
# NOTE: the PLAYLIST_URL presence check moved into start_background_work() so
# that importing this module stays side-effect-free (unit tests import it).

METADATA = {}
NOW_PLAYING = {"index": None, "title": "Nothing", "artist": "Unknown", "id": ""}

_CACHE_LOCK = threading.Lock()
_CACHE = _create_or_get_cache(CACHE_FILE)

SUBSCRIBERS = {}  # sid -> Queue[bytes]
SUBSCRIBERS_LOCK = threading.Lock()
RADIO_THREAD = None
RADIO_STOP = threading.Event()
# Set when a guardrail pauses the supervisor: the loop then spends nothing
# (no spawns, no metadata fetches) until resume_radio().
PAUSED = threading.Event()
SUBSCRIBER_EVENT = threading.Event()

CHUNK_SIZE = 8192
QUEUE_MAX_CHUNKS = 256
PLAYLIST_LOCK = threading.RLock()
PLAYLIST_REFRESH_INTERVAL_MINUTES = int(os.environ.get("PLAYLIST_REFRESH_INTERVAL_MINUTES", "60"))

def convert_playlist_to_links(link: str):
    # Loading a .radio file
    if link.endswith(".radio"):
        logger.info(".radio file specified, loading from local file")
        return _load_urls_from_file(link)

    # Pull YouTube list of URLs from YouTube Playlist
    ydl_opts = {
        "quiet": True,
        "extract_flat": True,
    }
    if PROXY_URL:
        # Playlist listing rides the proxy too, with the baseline's Python-API
        # equivalents (rotating session is fine — no per-track identity here).
        ydl_opts = proxied_ydl_opts(ydl_opts, PROXY_URL)
    urls = []
    logger.info("Starting conversion of playlist to links: %s", link)
    try:
        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(link, download=False)
    except Exception:
        logger.exception("yt-dlp failed to extract playlist info for %s", link)
        raise RuntimeError(f"yt-dlp failed to extract playlist info for {link}") from None

    entries = info.get("entries") if isinstance(info, dict) else None
    if isinstance(entries, list):
        logger.info("Playlist info returned %d entries", len(entries))
    else:
        logger.error("Could not find list of links. Are you providing a YouTube Playlist URL?")
        exit(1)

    for idx, entry in enumerate(entries, start=1):
        entry_id = None
        if isinstance(entry, dict):
            entry_id = entry.get("id") or entry.get("url")
        elif isinstance(entry, str):
            entry_id = entry
        if not entry_id:
            logger.debug("Skipping playlist entry #%d: no id/url present", idx)
            continue

        if isinstance(entry_id, str) and entry_id.startswith("http"):
            urls.append(entry_id)
            logger.debug("Playlist entry #%d: added direct URL %s", idx, entry_id)
        else:
            constructed = f"https://www.youtube.com/watch?v={entry_id}"
            urls.append(constructed)
            logger.debug("Playlist entry #%d: constructed URL %s from id %s", idx, constructed, entry_id)
    return urls

def refresh_playlist():
    """Reload the playlist from the configured source and reset metadata.
    This is safe to call from a background thread; it replaces the
    playlist and metadata atomically under PLAYLIST_LOCK.
    """
    global PLAYLIST, METADATA
    try:
        new_playlist = convert_playlist_to_links(PLAYLIST_URL)
    except Exception:
        logger.exception("Failed to refresh playlist from %s", PLAYLIST_URL)
        return

    with PLAYLIST_LOCK:
        PLAYLIST = new_playlist
        METADATA = {}
        logger.info("Playlist refreshed: %d tracks", len(PLAYLIST))

    # Preload metadata for the first few tracks so the UI isn't blank
    preload_count = min(4, len(PLAYLIST))
    if preload_count:
        indices = random.sample(range(len(PLAYLIST)), preload_count)
        for i in indices:
            with PLAYLIST_LOCK:
                if i >= len(PLAYLIST):
                    continue
                url = PLAYLIST[i]
            threading.Thread(target=fetch_metadata, args=(i, url), daemon=True).start()


def fetch_metadata(index, url):
    with _CACHE_LOCK:
        cached = _CACHE.get(url)
    if cached: # we can skip fetching from youtube if we've cached it before
        try:
            METADATA[index] = {
                "title": cached.get("title", f"Track {index+1}"),
                "artist": cached.get("uploader", "Unknown"),
                "duration": cached.get("duration", -1),
                "id": cached.get("id", ""),
            }
            return
        except Exception:
            logger.exception("Failed to use cached metadata for %s, will refetch", url)

    # Get metadata via yt-dlp
    try:
        # sticky_key pins this fetch to the track's sticky proxy session so
        # metadata extraction and the media download share one exit IP.
        result = TRANSPORT.run_ytdlp(["--dump-json", url], sticky_key=url)
        if result.returncode != 0 or not result.stdout:
            logger.error("Failed to get metadata from yt-dlp, you may or may not be throttled!")
            _maybe_log_cookies_recommendation(result.stderr or "")
            raise RuntimeError(f"yt-dlp failed for {url}: {result.stderr.strip()}")
        data = json.loads(result.stdout)
        METADATA[index] = {
            "title": data.get("title", f"Track {index+1}"),
            "artist": data.get("uploader", "Unknown"),
            "duration": data.get("duration", -1),
            "id": data.get("id", ""),
        }
        with _CACHE_LOCK: # get lock
            _CACHE[url] = {
                "title": data.get("title"),
                "uploader": data.get("uploader"),
                "duration": data.get("duration"),
                "id": data.get("id")
            }
        _save_cache(_CACHE, CACHE_FILE)
    except Exception:
        # Even if we fail to get meta we may be able to stream music still? So don't exit
        METADATA[index] = {
            "title": f"Track {index+1}",
            "artist": "Unknown",
            "duration": -1,
            "id": ""
        }
        logger.debug("Failed to fetch metadata for index %s, using fallback", index)

def _playlist_refresh_loop():
    if PLAYLIST_REFRESH_INTERVAL_MINUTES <= 0:
        return  # auto-refresh disabled; the bootstrap load handles the first fetch
    while not RADIO_STOP.is_set():
        # Wait for the refresh interval, but wake up quickly if the app stops
        RADIO_STOP.wait(timeout=PLAYLIST_REFRESH_INTERVAL_MINUTES * 60)
        if RADIO_STOP.is_set():
            break
        refresh_playlist()


# The app tolerates an empty PLAYLIST (routes return empty responses; the
# radio producer loop logs "Playlist is empty" and sleeps until tracks arrive).
# refresh_playlist() swallows fetch failures by logging and returning, so the
# worker always becomes ready regardless of YouTube's mood.
#
# refresh_playlist() REBINDS PLAYLIST (and resets METADATA) under the lock;
# nothing may hold a from-imported reference to it — consumers read it via
# playlist_snapshot(), which re-reads the global on every call.
PLAYLIST = []

def _initial_playlist_load():
    refresh_playlist()
    with PLAYLIST_LOCK:
        logger.info("Playlist loaded: %d tracks", len(PLAYLIST))
    logger.info("Stream available at %s/stream", BASE_URL)
    logger.info("M3U available at %s/playlist.m3u", BASE_URL)

_background_started = False

def start_background_work(force: bool = False):
    """Start the bootstrap playlist load and the auto-refresh loop.

    Invoked by the webapp factory when the real radio module is used — never
    at import time, so importing this module has no side effects. Idempotent;
    `force=True` re-runs the bootstrap load (integration tests use it to
    repoint PLAYLIST_URL).
    """
    global _background_started
    if not PLAYLIST_URL:
        raise RuntimeError("Please set PLAYLIST_URL environment variable")
    if _background_started and not force:
        return
    _background_started = True
    # Load the playlist in a background daemon thread so a slow or hung
    # YouTube fetch never blocks the gunicorn worker from booting / serving.
    threading.Thread(target=_initial_playlist_load, daemon=True).start()
    ensure_playlist_refresh_running()

def playlist_snapshot():
    """Live playlist copy under PLAYLIST_LOCK. The injection surface the
    webapp consumes instead of from-importing PLAYLIST."""
    with PLAYLIST_LOCK:
        return list(PLAYLIST)

def metadata_snapshot():
    """Live metadata copy under PLAYLIST_LOCK (same injection surface)."""
    with PLAYLIST_LOCK:
        return dict(METADATA)

def now_playing_snapshot():
    return dict(NOW_PLAYING)

def update_now_playing(chunk_index, meta):
    """Record the track a stream chunk belongs to (icy-metadata + /now_playing)."""
    NOW_PLAYING["index"] = chunk_index
    NOW_PLAYING["title"] = meta.get("title", "")
    NOW_PLAYING["artist"] = meta.get("artist", "")
    NOW_PLAYING["id"] = meta.get("id", "")

def radio_thread():
    """The radio producer thread, or None if it has never started."""
    return RADIO_THREAD



def ensure_metadata(index):
    if index in METADATA:
        return
    with PLAYLIST_LOCK:
        if index >= len(PLAYLIST):
            return
        url = PLAYLIST[index]
    fetch_metadata(index, url)



# --- Cost guardrails implementation ------------------------------------------

class TrackFailure(RuntimeError):
    """A track failed: yt-dlp exited nonzero (its exit code — NEVER ffmpeg's
    — decides). Carries yt-dlp's stderr so the failure budget and pause path
    can record why the track died."""

    def __init__(self, message, ytdlp_returncode=None, ytdlp_stderr=""):
        super().__init__(message)
        self.ytdlp_returncode = ytdlp_returncode
        self.ytdlp_stderr = ytdlp_stderr or ""


class FailureBudget:
    """The failure budget: the supervisor pauses once 5 consecutive track
    failures happen, or 5 failures land inside a 10-minute rolling window —
    even with successes in between. The clock is injectable so the window
    behaviour is deterministic under test."""

    def __init__(self, max_consecutive=FAILURE_BUDGET_CONSECUTIVE,
                 window_failures=FAILURE_BUDGET_WINDOW_FAILURES,
                 window_seconds=FAILURE_BUDGET_WINDOW_SECONDS,
                 clock=time.monotonic):
        self._max_consecutive = max_consecutive
        self._window_failures = window_failures
        self._window_seconds = window_seconds
        self._clock = clock
        self._consecutive = 0
        self._timestamps = deque()

    def record_failure(self):
        self._consecutive += 1
        now = self._clock()
        self._timestamps.append(now)
        self._expire(now)

    def record_success(self):
        self._consecutive = 0

    def exhausted(self):
        now = self._clock()
        self._expire(now)
        return (
            self._consecutive >= self._max_consecutive
            or len(self._timestamps) >= self._window_failures
        )

    def _expire(self, now):
        while self._timestamps and now - self._timestamps[0] > self._window_seconds:
            self._timestamps.popleft()

    def consecutive_failures(self):
        return self._consecutive

    def failures_in_window(self):
        return len(self._timestamps)

    def reset(self):
        self._consecutive = 0
        self._timestamps.clear()


def backoff_delay(consecutive_failures: int) -> float:
    """Exponential backoff before the nth consecutive retry: base * 2^(n-1),
    capped. Replaces the old fixed ~1s retry sleep — a run of failures must
    slow down, not retry-storm."""
    return min(
        BACKOFF_BASE_SECONDS * (2 ** max(consecutive_failures - 1, 0)),
        BACKOFF_MAX_SECONDS,
    )


# Guardrail state, reset by resume_radio() and by the test fixture.
_FAILURE_BUDGET = FailureBudget()
PAUSE_INFO = {}         # reason + counters + stderr excerpt for the pause path
LAST_TRACK_FAILURE = {}  # url / yt-dlp exit code / full stderr of the last failure
_PAUSED_AT_MONOTONIC = None  # time.monotonic() at pause; drives the auto-resume cooldown


def _traffic_exhausted(ytdlp_stderr) -> bool:
    """True when yt-dlp's stderr shows the proxy refusing with 407 (traffic
    exhausted / out of credit). Terminal: no retries of any kind."""
    stderr = ytdlp_stderr or ""
    return any(pattern.search(stderr) for pattern in TRAFFIC_EXHAUSTED_PATTERNS)


def _pause_radio(reason: str, stderr: str = "") -> None:
    """Pause the supervisor: the loop spends nothing until the cooldown
    auto-resumes it (or resume_radio() is called). Captures the counters and
    the failing spawn's stderr excerpt, then alerts exactly once."""
    global _PAUSED_AT_MONOTONIC
    if PAUSED.is_set():
        return
    PAUSE_INFO.clear()
    PAUSE_INFO.update({
        "reason": reason,
        "stderr_excerpt": (stderr or "").strip()[:STDERR_EXCERPT_CHARS],
        "consecutive_failures": _FAILURE_BUDGET.consecutive_failures(),
        "failures_in_window": _FAILURE_BUDGET.failures_in_window(),
        "paused_at": time.time(),
    })
    _PAUSED_AT_MONOTONIC = time.monotonic()
    PAUSED.set()
    logger.error("RADIO PAUSED: %s | last yt-dlp stderr: %s", reason, PAUSE_INFO["stderr_excerpt"])
    _send_pause_alert()


def _send_pause_alert() -> None:
    """Email one pause alert through the mailer seam: the reason, the failure
    counters, and the last yt-dlp stderr excerpt. A missing or failing mailer
    never un-guards the pause."""
    if MAILER is None:
        logger.warning(
            "Radio paused but alert email is not configured — "
            "set RESEND_API_KEY, ALERT_EMAIL_FROM and ALERT_EMAIL_TO to enable alerts"
        )
        return
    subject = "[yt-radio] radio paused: {}".format(PAUSE_INFO["reason"])
    body = "\n".join([
        "The radio supervisor paused and is spending nothing.",
        "",
        "Reason: {}".format(PAUSE_INFO["reason"]),
        "Consecutive failures: {}".format(PAUSE_INFO["consecutive_failures"]),
        "Failures in the last {} min: {}".format(
            FAILURE_BUDGET_WINDOW_MINUTES, PAUSE_INFO["failures_in_window"]
        ),
        "Paused at: {}".format(
            time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(PAUSE_INFO["paused_at"]))
        ),
        "",
        "Last yt-dlp stderr:",
        PAUSE_INFO["stderr_excerpt"] or "(none captured)",
        "",
        "The radio auto-resumes with a fresh failure budget after {} minutes.".format(
            PAUSE_RESUME_COOLDOWN_MINUTES
        ),
    ])
    try:
        if MAILER.send(subject, body):
            logger.info("Pause alert email sent")
        else:
            logger.error("Pause alert email could not be sent")
    except Exception:
        # Defensive only: the seam contract is send() never raises, but a
        # pause must never die over a broken mailer either way.
        logger.exception("Pause alert email failed")


def resume_radio() -> None:
    """Resume the supervisor loop with a fresh failure budget."""
    global _PAUSED_AT_MONOTONIC
    PAUSED.clear()
    PAUSE_INFO.clear()
    _FAILURE_BUDGET.reset()
    _PAUSED_AT_MONOTONIC = None
    logger.info("Radio resumed with a fresh failure budget")


def _record_track_success() -> None:
    """A track that completed with yt-dlp exit 0 resets the consecutive
    failure counter (the rolling window keeps counting)."""
    _FAILURE_BUDGET.record_success()


def _handle_track_failure(ytdlp_stderr="", *, ytdlp_returncode=None, url=None) -> None:
    """Record one track failure and apply the cost guardrails. Order matters:
    a traffic-exhausted proxy response pauses immediately (zero retries — no
    backoff sleep); otherwise the failure budget decides between exponential
    backoff and pausing."""
    _FAILURE_BUDGET.record_failure()
    LAST_TRACK_FAILURE.clear()
    LAST_TRACK_FAILURE.update({
        "url": url,
        "ytdlp_returncode": ytdlp_returncode,
        "stderr": ytdlp_stderr or "",
        "at": time.time(),
    })
    if _traffic_exhausted(ytdlp_stderr):
        _pause_radio(
            "traffic exhausted: proxy returned 407 TRAFFIC_EXHAUSTED — "
            "pausing immediately, zero retries",
            ytdlp_stderr,
        )
        return
    if _FAILURE_BUDGET.exhausted():
        _pause_radio(
            "failure budget exhausted "
            "({} consecutive / {} in {} min)".format(
                _FAILURE_BUDGET.consecutive_failures(),
                _FAILURE_BUDGET.failures_in_window(),
                FAILURE_BUDGET_WINDOW_MINUTES,
            ),
            ytdlp_stderr,
        )
        return
    _backoff_after_failure(_FAILURE_BUDGET.consecutive_failures())


def _backoff_after_failure(consecutive_failures: int) -> None:
    """Sleep out the exponential backoff delay (interruptible by shutdown)."""
    delay = backoff_delay(consecutive_failures)
    logger.warning(
        "Track failure %d: backing off %.0fs before the next attempt",
        consecutive_failures, delay,
    )
    RADIO_STOP.wait(delay)


def _media_format_selector():
    """Format selector for the media path. Proxied fetches cap the upstream
    audio at <=56 kbps (proxy egress is paid per GB) with a graceful fallback
    chain; direct mode keeps the legacy format chain."""
    if TRANSPORT.proxied:
        return PROXIED_FORMAT_CHAIN
    return YTDLP_FORMAT


def _stream_track(index, url=None):
    if url is None:
        with PLAYLIST_LOCK:
            if index >= len(PLAYLIST):
                logger.warning("Track index %d is out of range after playlist refresh", index)
                return
            url = PLAYLIST[index]
    ensure_metadata(index)
    meta = dict(METADATA.get(index, {"title": f"Track {index+1}", "artist": "Unknown", "duration": -1, "id": ""}))
    logger.info("Now playing [%d/%d]: %s - %s", index + 1, len(PLAYLIST), meta.get("artist", ""), meta.get("title", ""))

    # Single transport seam: both children (yt-dlp | ffmpeg) are spawned and
    # owned by the transport; we only read the transcoded stdout.
    pipeline = TRANSPORT.open_track_pipeline(
        url, ytdlp_format=_media_format_selector(), bitrate_kbps=BITRATE_KBPS
    )

    bytes_per_sec = (BITRATE_KBPS * 1000) // 8
    burst_bytes = bytes_per_sec * BURST_SECONDS
    bytes_sent = 0
    start_time = time.monotonic()
    # Set when the consumer abandons the stream early (shutdown, no
    # subscribers): the pipeline is then torn down, not classified.
    interrupted = False

    try:
        while True:
            if pipeline.stdout is None:
                logger.warning("No stdout available from FFMPEG")
                break
            chunk = pipeline.stdout.read(8192)
            if not chunk:
                break
            bytes_sent += len(chunk)
            yield chunk

            elapsed = time.monotonic() - start_time
            expected_bytes = elapsed * bytes_per_sec + burst_bytes
            if bytes_sent > expected_bytes:
                sleep_for = (bytes_sent - expected_bytes) / bytes_per_sec
                time.sleep(sleep_for)
    except GeneratorExit:
        interrupted = True
        raise
    finally:
        # close() waits out (then kills + reaps) both children and drains
        # their stderr. yt-dlp's exit code decides failure — ffmpeg's is
        # deliberately ignored.
        ffmpeg_err, ytdlp_err = pipeline.close()
        elapsed = time.monotonic() - start_time
        logger.info("Finished sending [%d/%d]: %r - %r (bytes_sent=%d, elapsed=%.2fs)", index + 1, len(PLAYLIST), meta.get("artist"), meta.get("title"), bytes_sent, elapsed)
        try:
            if ffmpeg_err:
                logger.warning("ffmpeg stderr for track %d: %s", index + 1, ffmpeg_err.strip())
            if ytdlp_err:
                logger.warning("yt-dlp stderr for track %d: %s", index + 1, ytdlp_err.strip())
                _maybe_log_cookies_recommendation(ytdlp_err)
        except Exception:
            logger.exception("Failed to log subprocess stderr")
        ytdlp_rc = pipeline.ytdlp_returncode
        # Only classify clean natural completions: if the read loop itself
        # raised, let the original exception propagate (sys.exc_info() is
        # set while an exception is in flight) — the loop still budgets it,
        # but the real traceback survives.
        if (
            not interrupted
            and sys.exc_info()[0] is None
            and ytdlp_rc not in (None, 0)
        ):
            raise TrackFailure(
                "yt-dlp exited {} for track {}".format(ytdlp_rc, index),
                ytdlp_returncode=ytdlp_rc,
                ytdlp_stderr=ytdlp_err,
            )


def add_subscriber():
    q = Queue(maxsize=QUEUE_MAX_CHUNKS)
    sid = uuid.uuid4().hex
    with SUBSCRIBERS_LOCK:
        SUBSCRIBERS[sid] = q
        SUBSCRIBER_EVENT.set()
    logger.info("Subscriber added sid=%s (total=%d)", sid, len(SUBSCRIBERS))
    return sid, q


def remove_subscriber(sid):
    with SUBSCRIBERS_LOCK:
        if sid in SUBSCRIBERS:
            SUBSCRIBERS.pop(sid, None)
            logger.info("Subscriber removed sid=%s (total=%d)", sid, len(SUBSCRIBERS))
        if not SUBSCRIBERS:
            SUBSCRIBER_EVENT.clear()


def broadcast_chunk(track_index: int, chunk: bytes):
    with SUBSCRIBERS_LOCK:
        subs = list(SUBSCRIBERS.items())
    for _, q in subs: # push the same chunk to all subscribers, we do nothing with sid for now
        if q.full():
            try:
                q.get_nowait()
            except Exception:
                pass
        try:
            q.put_nowait((track_index, chunk))
        except Exception:
            pass


def _radio_loop():
    played = []
    while not RADIO_STOP.is_set():
        if PAUSED.is_set():
            # Cost guardrail: while paused the supervisor spends nothing —
            # no spawns, no metadata fetches. After the cooldown it
            # auto-resumes with a fresh failure budget so transient proxy
            # outages self-heal; a still-broken proxy re-pauses (and
            # re-alerts) after at most one attempt.
            paused_at = _PAUSED_AT_MONOTONIC
            if (
                paused_at is not None
                and time.monotonic() - paused_at >= PAUSE_RESUME_COOLDOWN_SECONDS
            ):
                logger.info("Pause cooldown elapsed; auto-resuming with a fresh failure budget")
                resume_radio()
                continue
            if RADIO_STOP.wait(PAUSE_POLL_SECONDS):
                break
            continue
        if not SUBSCRIBER_EVENT.wait(timeout=1):
            continue
        with PLAYLIST_LOCK:
            playlist_snapshot = list(PLAYLIST)
        if not playlist_snapshot:
            logger.error("Playlist is empty, cannot stream")
            time.sleep(1)
            continue
        available = [i for i in range(len(playlist_snapshot)) if i not in played]
        if not available:
            played.clear()
            available = list(range(len(playlist_snapshot)))

        index = random.choice(available)
        played.append(index)
        url = playlist_snapshot[index]
        try:
            # stream.close() delivers GeneratorExit synchronously when the
            # loop abandons the track early, so the interrupted flag below is
            # set deterministically before the outcome is classified.
            interrupted = False
            stream = _stream_track(index, url)
            try:
                for chunk in stream:
                    if RADIO_STOP.is_set():
                        interrupted = True
                        break
                    with SUBSCRIBERS_LOCK:
                        if not SUBSCRIBERS:
                            logger.info("No subscribers remaining; stopping track early")
                            interrupted = True
                            break
                    broadcast_chunk(index, chunk)
            finally:
                stream.close()
            if interrupted:
                continue
            # Natural completion with yt-dlp exit 0: a real success.
            _record_track_success()
        except TrackFailure as failure:
            logger.warning(
                "Track failed (yt-dlp exit %s): %s", failure.ytdlp_returncode, failure
            )
            _handle_track_failure(
                failure.ytdlp_stderr,
                ytdlp_returncode=failure.ytdlp_returncode,
                url=url,
            )
        except Exception:
            # Spawn-level failure (e.g. the proxy is unreachable): no yt-dlp
            # exit code exists, but it costs exactly like a failed track.
            logger.exception("Error in radio producer, treating as a track failure")
            _handle_track_failure("", url=url)


def ensure_radio_running():
    global RADIO_THREAD
    if RADIO_THREAD and RADIO_THREAD.is_alive():
        return
    RADIO_STOP.clear()
    RADIO_THREAD = threading.Thread(target=_radio_loop, daemon=True)
    RADIO_THREAD.start()
    logger.info("Radio producer started; listeners will share the same track")
    ensure_playlist_refresh_running()

def ensure_playlist_refresh_running():
    for t in threading.enumerate():
        if getattr(t, "_yt_radio_refresh", False):
            return
    refresh_thread = threading.Thread(target=_playlist_refresh_loop, daemon=True)
    refresh_thread._yt_radio_refresh = True
    refresh_thread.start()
    logger.info("Playlist refresh thread started (interval=%d minutes)", PLAYLIST_REFRESH_INTERVAL_MINUTES)


# The radio producer is started lazily by the /stream route (ensure_radio_running);
# background playlist work is started by the webapp factory (start_background_work).

if __name__ == "__main__":
    from routes import app
    app.run(host="0.0.0.0", port=8000, threaded=True)
