from flask import Flask, Response, stream_with_context, jsonify
from yt_dlp import YoutubeDL
import subprocess
import json
import threading
import random
import os
from dotenv import load_dotenv
import logging
import time

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

app = Flask(__name__)

BITRATE_KBPS = int(os.environ.get("BITRATE_KBPS", "192"))
BURST_SECONDS = int(os.environ.get("BURST_SECONDS", "10"))
BASE_URL = os.environ.get("BASE_URL", "http://localhost:8000")
PLAYLIST_URL = os.environ.get("PLAYLIST_URL")
CACHE_FILE = os.environ.get("CACHE_FILE", "cache.json")
if not PLAYLIST_URL:
    raise RuntimeError("Please set PLAYLIST_URL environment variable")

METADATA = {}
NOW_PLAYING = {"index": None, "title": "Nothing", "artist": "Unknown", "url": ""}

_CACHE_LOCK = threading.Lock()
try:
    if os.path.exists(CACHE_FILE):
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            _CACHE = json.load(f)
    else:
        _CACHE = {}
except Exception:
    logger.exception("Failed to load cache file, starting with empty cache")
    _CACHE = {}


def _save_cache():
    with _CACHE_LOCK:
        try:
            with open(CACHE_FILE, "w", encoding="utf-8") as f:
                json.dump(_CACHE, f, ensure_ascii=False, indent=2)
        except Exception:
            logger.exception("Failed to save cache to %s", CACHE_FILE)


def convert_playlist_to_links(link: str):
    ydl_opts = {
        "quiet": True,
        "extract_flat": True,
    }
    urls = []
    with YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(link, download=False)
        entries = info.get("entries") if isinstance(info, dict) else None
        if not entries:
            logger.warning("No entries found in playlist info for %s", link)
            return urls
        for entry in entries:
            entry_id = None
            if isinstance(entry, dict):
                entry_id = entry.get("id") or entry.get("url")
            elif isinstance(entry, str):
                entry_id = entry
            if not entry_id:
                continue
            if entry_id.startswith("http"):
                urls.append(entry_id)
            else:
                urls.append(f"https://www.youtube.com/watch?v={entry_id}")
    return urls


def fetch_metadata(index, url):
    with _CACHE_LOCK:
        cached = _CACHE.get(url)
    if cached:
        try:
            METADATA[index] = {
                "title": cached.get("title", f"Track {index+1}"),
                "artist": cached.get("uploader", "Unknown"),
                "duration": cached.get("duration", -1),
            }
            return
        except Exception:
            logger.exception("Failed to use cached metadata for %s, will refetch", url)

    try:
        result = subprocess.run(
            ["yt-dlp", "--dump-json", url],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0 or not result.stdout:
            raise RuntimeError(f"yt-dlp failed for {url}: {result.stderr.strip()}")
        data = json.loads(result.stdout)
        METADATA[index] = {
            "title": data.get("title", f"Track {index+1}"),
            "artist": data.get("uploader", "Unknown"),
            "duration": data.get("duration", -1),
        }
        with _CACHE_LOCK:
            _CACHE[url] = {
                "title": data.get("title"),
                "uploader": data.get("uploader"),
                "duration": data.get("duration"),
            }
        _save_cache()
    except Exception:
        METADATA[index] = {
            "title": f"Track {index+1}",
            "artist": "Unknown",
            "duration": -1,
        }
        logger.debug("Failed to fetch metadata for index %s, using fallback", index)


PLAYLIST = convert_playlist_to_links(PLAYLIST_URL)
PRELOAD_COUNT = min(4, len(PLAYLIST))
_preload_indices = random.sample(range(len(PLAYLIST)), PRELOAD_COUNT) if PLAYLIST else []
for i in _preload_indices:
    threading.Thread(target=fetch_metadata, args=(i, PLAYLIST[i]), daemon=True).start()
logger.info("Playlist loaded: %d tracks (preloading %d)", len(PLAYLIST), PRELOAD_COUNT)
logger.info("Stream available at %s/stream", BASE_URL)
logger.info("M3U available at %s/playlist.m3u", BASE_URL)


def _ensure_metadata(index):
    """Fetch metadata for a track if not already loaded. Called before playback."""
    if index not in METADATA:
        fetch_metadata(index, PLAYLIST[index])


def _stream_track(index):
    url = PLAYLIST[index]
    _ensure_metadata(index)
    meta = METADATA.get(index, {"title": f"Track {index+1}", "artist": "Unknown", "duration": -1})

    NOW_PLAYING["index"] = index
    NOW_PLAYING["title"] = meta["title"]
    NOW_PLAYING["artist"] = meta["artist"]
    NOW_PLAYING["url"] = url
    logger.info("Now playing [%d/%d]: %s - %s", index + 1, len(PLAYLIST), meta["artist"], meta["title"])

    ytdlp = subprocess.Popen(
        ["yt-dlp", "-f", "bestaudio", "-o", "-", url],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
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
        stderr=subprocess.DEVNULL,
    )
    ytdlp.stdout.close()

    bytes_per_sec = (BITRATE_KBPS * 1000) // 8
    burst_bytes = bytes_per_sec * BURST_SECONDS
    bytes_sent = 0
    start_time = time.monotonic()

    try:
        while True:
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
        logger.info("Finished sending [%d/%d]: %s - %s", index + 1, len(PLAYLIST), meta["artist"], meta["title"])


@app.route("/playlist.m3u")
def playlist_route():
    lines = [
        "#EXTM3U",
        f"#EXTINF:-1,Infinite Radio",
        f"{BASE_URL}/stream",
    ]
    return Response("\n".join(lines), mimetype="audio/x-mpegurl")


@app.route("/stream")
def stream():
    def generate():
        played = []
        while True:
            if not PLAYLIST:
                logger.error("Playlist is empty, cannot stream")
                break

            available = [i for i in range(len(PLAYLIST)) if i not in played]
            if not available:
                played.clear()
                available = list(range(len(PLAYLIST)))

            index = random.choice(available)
            played.append(index)

            try:
                yield from _stream_track(index)
            except GeneratorExit:
                logger.info("Client disconnected")
                return
            except Exception:
                logger.exception("Error streaming track %d, skipping", index)
                time.sleep(1)
                continue

    return Response(stream_with_context(generate()), mimetype="audio/mpeg")


@app.route("/now_playing")
def now_playing():
    return jsonify(NOW_PLAYING)


@app.route("/tracks")
def tracks():
    track_list = []
    for i, url in enumerate(PLAYLIST):
        meta = METADATA.get(i, {"title": f"Track {i+1}", "artist": "Unknown", "duration": -1})
        track_list.append({
            "index": i,
            "title": meta["title"],
            "artist": meta["artist"],
            "duration": meta["duration"],
            "url": url,
        })
    return jsonify(track_list)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, threaded=True)
