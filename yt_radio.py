from flask import Flask, Response, stream_with_context, abort
from yt_dlp import YoutubeDL
import subprocess
import json
import threading
import random
import os
from dotenv import load_dotenv
import logging

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

app = Flask(__name__)

BASE_URL = os.environ.get("BASE_URL", "http://localhost:8000")
PLAYLIST_URL = os.environ.get("PLAYLIST_URL")
RANDOMIZE_PLAYLIST = os.environ.get("RANDOMIZE_PLAYLIST", "False").lower() in ("1", "true", "yes")
YTDLP_CACHE_FILE = os.environ.get("YTDLP_CACHE_FILE", "yt_dlp_cache.json")
if not PLAYLIST_URL:
    raise RuntimeError("Please set PLAYLIST_URL environment variable")
METADATA = {}

# Simple thread-safe cache for yt-dlp JSON outputs
_CACHE_LOCK = threading.Lock()
try:
    if os.path.exists(YTDLP_CACHE_FILE):
        with open(YTDLP_CACHE_FILE, "r", encoding="utf-8") as f:
            _CACHE = json.load(f)
    else:
        _CACHE = {}
except Exception:
    logger.exception("Failed to load cache file, starting with empty cache")
    _CACHE = {}

def _save_cache():
    # atomic write
    tmp = f"{YTDLP_CACHE_FILE}.tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(_CACHE, f, ensure_ascii=False, indent=2)
        os.replace(tmp, YTDLP_CACHE_FILE)
    except Exception:
        logger.exception("Failed to save cache to %s", YTDLP_CACHE_FILE)
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass

def convert_playlist_to_links(link: str):
    ydl_opts = {
        "quiet": True,
        "extract_flat": True,   # don't download, just metadata
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
    if RANDOMIZE_PLAYLIST:
        random.shuffle(urls)
    return urls

def fetch_metadata(index, url):
    cached = None
    with _CACHE_LOCK:
        cached = _CACHE.get(url)
    if cached:
        try:
            # cached may be the full yt-dlp JSON dict
            data = cached
            METADATA[index] = {
                "title": data.get("title", f"Track {index+1}"),
                "artist": data.get("uploader", "Unknown"),
                "duration": data.get("duration", -1)
            }
            logger.debug("Loaded metadata from cache for index %s: %s", index, METADATA[index])
            return
        except Exception:
            logger.exception("Failed to use cached metadata for %s, will refetch", url)

    # Not cached or failed to use cache: call yt-dlp to dump json
    try:
        result = subprocess.run(
            ["yt-dlp", "--dump-json", url],
            capture_output=True,
            text=True,
            timeout=30
        )
        if result.returncode != 0 or not result.stdout:
            raise RuntimeError(f"yt-dlp failed for {url}: {result.stderr.strip()}")
        data = json.loads(result.stdout)
        METADATA[index] = {
            "title": data.get("title", f"Track {index+1}"),
            "artist": data.get("uploader", "Unknown"),
            "duration": data.get("duration", -1)
        }
        # store full json in cache
        with _CACHE_LOCK:
            _CACHE[url] = data
            try:
                _save_cache()
            except Exception:
                logger.exception("Failed to persist cache after fetching %s", url)
        logger.debug("Fetched metadata for index %s: %s", index, METADATA[index])
    except Exception:
        # Fallback defaults
        METADATA[index] = {
            "title": f"Track {index+1}",
            "artist": "Unknown",
            "duration": -1
        }
        logger.debug("Failed to fetch metadata for index %s, using fallback", index)

PLAYLIST = convert_playlist_to_links(PLAYLIST_URL)
for i, url in enumerate(PLAYLIST):
    threading.Thread(target=fetch_metadata, args=(i, url), daemon=True).start()
print(f"Playlist loaded: {len(PLAYLIST)} tracks")
logger.info("Playlist loaded: %d tracks", len(PLAYLIST))
print(f"OK. Now serving, you can access the m3u at {BASE_URL}/playlist.m3u")
logger.info("OK. Now serving, you can access the m3u at %s/playlist.m3u", BASE_URL)


#  FLASK ROUTES
@app.route("/playlist.m3u")
def playlist_route():
    lines = ["#EXTM3U"]
    for i, url in enumerate(PLAYLIST):
        meta = METADATA.get(i, {
            "title": f"Track {i+1}",
            "artist": "Unknown",
            "duration": -1
        })
        lines.append(f"#EXTINF:{meta['duration']},{meta['artist']} - {meta['title']}")
        lines.append(f"{BASE_URL}/track/{i}")
    return Response("\n".join(lines), mimetype="audio/x-mpegurl")

@app.route("/track/<int:index>")
def track(index):
    if index < 0 or index >= len(PLAYLIST):
        abort(404)

    meta = METADATA.get(index, {
        "title": f"Track {index+1}",
        "artist": "Unknown",
        "duration": -1
    })
    logger.info("Now playing index %d: %s - %s (%s)", index, meta["artist"], meta["title"], PLAYLIST[index])

    process = subprocess.Popen(
        ["yt-dlp", "-f", "bestaudio", "-o", "-", PLAYLIST[index]],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL
    )

    def stream():
        try:
            while True:
                chunk = process.stdout.read(8192)
                if not chunk:
                    break
                yield chunk
        finally:
            try:
                process.kill()
            except Exception:
                pass
            logger.info("Finished playing index %d: %s - %s", index, meta["artist"], meta["title"])

    return Response(stream_with_context(stream()), mimetype="audio/mpeg")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, threaded=True)
