from yt_dlp import YoutubeDL
from file_util import _load_urls_from_file, _create_or_get_cache, _save_cache
import subprocess
import json
import threading
import random
import os
from dotenv import load_dotenv
import logging
import time
from queue import Queue
import uuid
import tempfile


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

# yt-dlp format selection with fallback chain for resilience against
# YouTube experiments that make pure audio-only formats unavailable.
YTDLP_FORMAT = os.environ.get(
    "YTDLP_FORMAT", "bestaudio[ext=webm]/bestaudio[ext=m4a]/bestaudio/best"
)

# Path to a cookies.txt file for YouTube auth. Required when YouTube bot-blocks
# the server's IP — export cookies from a logged-in browser session.
# When unset, yt-dlp runs without cookies (works from non-flagged IPs).
COOKIES_FILE = os.environ.get("COOKIES_FILE")

# Shared base argv for yt-dlp media extraction so metadata + stream paths stay in sync.
_YTDLP_BASE_ARGS = ["yt-dlp"] + (["--cookies", COOKIES_FILE] if COOKIES_FILE else [])
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
if not PLAYLIST_URL:
    raise RuntimeError("Please set PLAYLIST_URL environment variable")

METADATA = {}
NOW_PLAYING = {"index": None, "title": "Nothing", "artist": "Unknown", "id": ""}

_CACHE_LOCK = threading.Lock()
_CACHE = _create_or_get_cache(CACHE_FILE)

SUBSCRIBERS = {}  # sid -> Queue[bytes]
SUBSCRIBERS_LOCK = threading.Lock()
RADIO_THREAD = None
RADIO_STOP = threading.Event()
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
        result = subprocess.run(
            _YTDLP_BASE_ARGS + ["--dump-json", url],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
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


# Bootstrap: load the playlist in a background daemon thread so a slow or
# hung YouTube fetch never blocks the gunicorn worker from booting / serving.
# The app tolerates an empty PLAYLIST (routes return empty responses; the
# radio producer loop logs "Playlist is empty" and sleeps until tracks arrive).
# refresh_playlist() swallows fetch failures by logging and returning, so the
# worker always becomes ready regardless of YouTube's mood.
PLAYLIST = []

def _initial_playlist_load():
    refresh_playlist()
    with PLAYLIST_LOCK:
        logger.info("Playlist loaded: %d tracks", len(PLAYLIST))
    logger.info("Stream available at %s/stream", BASE_URL)
    logger.info("M3U available at %s/playlist.m3u", BASE_URL)

_bootstrap_thread = threading.Thread(target=_initial_playlist_load, daemon=True)
_bootstrap_thread._yt_radio_bootstrap = True
_bootstrap_thread.start()



def _ensure_metadata(index):
    if index in METADATA:
        return
    with PLAYLIST_LOCK:
        if index >= len(PLAYLIST):
            return
        url = PLAYLIST[index]
    fetch_metadata(index, url)



def _stream_track(index, url=None):
    if url is None:
        with PLAYLIST_LOCK:
            if index >= len(PLAYLIST):
                logger.warning("Track index %d is out of range after playlist refresh", index)
                return
            url = PLAYLIST[index]
    _ensure_metadata(index)
    meta = dict(METADATA.get(index, {"title": f"Track {index+1}", "artist": "Unknown", "duration": -1, "id": ""}))
    logger.info("Now playing [%d/%d]: %s - %s", index + 1, len(PLAYLIST), meta.get("artist", ""), meta.get("title", ""))

    ytdlp_err = tempfile.TemporaryFile()
    ffmpeg_err = tempfile.TemporaryFile()

    ytdlp = subprocess.Popen(
        _YTDLP_BASE_ARGS + ["-f", YTDLP_FORMAT, "-o", "-", url],
        stdout=subprocess.PIPE,
        stderr=ytdlp_err,
    )

    ffmpeg = subprocess.Popen(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel", "error",
            "-i", "pipe:0",
            "-f", "mp3",
            "-ab", f"{BITRATE_KBPS}k",
            "-ar", "44100",
            "-ac", "2",
            "pipe:1",
        ],
        stdin=ytdlp.stdout,
        stdout=subprocess.PIPE,
        stderr=ffmpeg_err,
    )
    if ytdlp.stdout:
        ytdlp.stdout.close()

    bytes_per_sec = (BITRATE_KBPS * 1000) // 8
    burst_bytes = bytes_per_sec * BURST_SECONDS
    bytes_sent = 0
    start_time = time.monotonic()

    try:
        while True:
            if ffmpeg.stdout is None:
                logger.warning("No stdout available from FFMPEG")
                break
            chunk = ffmpeg.stdout.read(8192)
            if not chunk:
                break
            bytes_sent += len(chunk)
            yield chunk

            elapsed = time.monotonic() - start_time
            expected_bytes = elapsed * bytes_per_sec + burst_bytes
            if bytes_sent > expected_bytes:
                sleep_for = (bytes_sent - expected_bytes) / bytes_per_sec
                time.sleep(sleep_for)
    finally:
        try:
            ffmpeg.kill()
        except Exception:
            pass
        try:
            ytdlp.kill()
        except Exception:
            pass
        ffmpeg.wait()
        ytdlp.wait()
        elapsed = time.monotonic() - start_time
        logger.info("Finished sending [%d/%d]: %r - %r (bytes_sent=%d, elapsed=%.2fs)", index + 1, len(PLAYLIST), meta.get("artist"), meta.get("title"), bytes_sent, elapsed)
        try:
            ffmpeg_err.seek(0)
            ferr = ffmpeg_err.read().decode("utf-8", errors="replace")
            if ferr:
                logger.warning("ffmpeg stderr for track %d: %s", index + 1, ferr.strip())
            ytdlp_err.seek(0)
            yerr = ytdlp_err.read().decode("utf-8", errors="replace")
            if yerr:
                logger.warning("yt-dlp stderr for track %d: %s", index + 1, yerr.strip())
                _maybe_log_cookies_recommendation(yerr)
        except Exception:
            logger.exception("Failed to read subprocess stderr")
        finally:
            try:
                ffmpeg_err.close()
            except Exception:
                pass
            try:
                ytdlp_err.close()
            except Exception:
                pass


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
            for chunk in _stream_track(index, url):
                if RADIO_STOP.is_set():
                    break
                with SUBSCRIBERS_LOCK:
                    if not SUBSCRIBERS:
                        logger.info("No subscribers remaining; stopping track early")
                        break
                broadcast_chunk(index, chunk)
        except Exception:
            logger.exception("Error in radio producer, skipping track")
            time.sleep(1)
            continue


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


ensure_radio_running()

if __name__ == "__main__":
    from routes import app
    app.run(host="0.0.0.0", port=8000, threaded=True)
