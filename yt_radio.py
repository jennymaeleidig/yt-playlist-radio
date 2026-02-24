from flask import Flask, Response, stream_with_context, jsonify, render_template, request
from yt_dlp import YoutubeDL
import subprocess
import json
import threading
import random
import os
from dotenv import load_dotenv
import logging
import time
from queue import Queue, Empty
import uuid

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

app = Flask(__name__)

BITRATE_KBPS = int(os.environ.get("BITRATE_KBPS", "192"))
BURST_SECONDS = int(os.environ.get("BURST_SECONDS", "10")) # allow pre-buffering of 10 sec on /stream
BASE_URL = os.environ.get("BASE_URL", "http://localhost:8000")
PLAYLIST_URL = os.environ.get("PLAYLIST_URL")
CACHE_FILE = os.environ.get("CACHE_FILE", "cache.json")
RANDOMIZE_PLAYLIST = bool(os.environ.get("RANDOMIZE_PLAYLIST", "false"))
META_INTERVAL_SECONDS = int(os.environ.get("META_INTERVAL_SECONDS", "5"))

# optional / page
SITE_TITLE = os.environ.get("SITE_TITLE", "yt_radio.py")
SITE_IMAGE = os.environ.get("SITE_IMAGE", "")
if not PLAYLIST_URL:
    raise RuntimeError("Please set PLAYLIST_URL environment variable")

METADATA = {}
NOW_PLAYING = {"index": None, "title": "Nothing", "artist": "Unknown", "id": ""}

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


def load_urls_from_file(path: str):
    urls = []
    if not path:
        logger.debug("No path provided to load_urls_from_file")
        return urls
    try:
        with open(path, "r", encoding="utf-8") as f:
            for raw in f:
                line = raw.strip()
                if not line:
                    continue
                if line.startswith("#"):
                    continue
                if " #" in line:
                    line = line.split(" #", 1)[0].strip()
                urls.append(line)
        logger.info("Loaded %d URLs from %s", len(urls), path)
    except FileNotFoundError:
        logger.warning("URL file not found: %s", path)
    except Exception:
        logger.exception("Failed to read URL file: %s", path)
    return urls


def convert_playlist_to_links(link: str):
    if link.endswith(".radio"):
        logger.info(".radio file specified, loading from local file")
        return load_urls_from_file(link)
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
        return urls
    entries = info.get("entries") if isinstance(info, dict) else None
    if isinstance(entries, list):
        logger.info("Playlist info returned %d entries", len(entries))
    else:
        if isinstance(info, dict):
            vid = info.get("id") or info.get("url") or info.get("webpage_url")
            if vid:
                if isinstance(vid, str) and vid.startswith("http"):
                    urls.append(vid)
                    logger.info("Single video found for playlist URL, added: %s", vid)
                else:
                    constructed = f"https://www.youtube.com/watch?v={vid}"
                    urls.append(constructed)
                    logger.info("Single video id found for playlist URL, constructed: %s", constructed)
            else:
                logger.warning("No entries found in playlist info for %s", link)
        else:
            logger.warning("Unexpected playlist info format for %s: %r", link, type(info))
        return urls
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

    logger.info("Finished converting playlist: %d links generated", len(urls))
    return urls


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
            "id": data.get("id", ""),
        }
        with _CACHE_LOCK:
            _CACHE[url] = {
                "title": data.get("title"),
                "uploader": data.get("uploader"),
                "duration": data.get("duration"),
                "id": data.get("id")
            }
        _save_cache()
    except Exception:
        METADATA[index] = {
            "title": f"Track {index+1}",
            "artist": "Unknown",
            "duration": -1,
            "id": ""
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
    if index not in METADATA:
        fetch_metadata(index, PLAYLIST[index])


def _stream_track(index):
    url = PLAYLIST[index]
    _ensure_metadata(index)
    meta = METADATA.get(index, {"title": f"Track {index+1}", "artist": "Unknown", "duration": -1, "id": ""})

    NOW_PLAYING["index"] = index
    NOW_PLAYING["title"] = meta.get("title", "")
    NOW_PLAYING["artist"] = meta.get("artist", "")
    NOW_PLAYING["id"] = meta.get("id", "")
    logger.info("Now playing [%d/%d]: %s - %s", index + 1, len(PLAYLIST), meta.get("artist", ""), meta.get("title", ""))

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


SUBSCRIBERS = {}  # sid -> Queue[bytes]
SUBSCRIBERS_LOCK = threading.Lock()
RADIO_THREAD = None
RADIO_STOP = threading.Event()

CHUNK_SIZE = 8192
QUEUE_MAX_CHUNKS = 256


def add_subscriber():
    q = Queue(maxsize=QUEUE_MAX_CHUNKS)
    sid = uuid.uuid4().hex
    with SUBSCRIBERS_LOCK:
        SUBSCRIBERS[sid] = q
    logger.info("Subscriber added sid=%s (total=%d)", sid, len(SUBSCRIBERS))
    return sid, q


def remove_subscriber(sid):
    with SUBSCRIBERS_LOCK:
        if sid in SUBSCRIBERS:
            SUBSCRIBERS.pop(sid, None)
            logger.info("Subscriber removed sid=%s (total=%d)", sid, len(SUBSCRIBERS))


def broadcast_chunk(chunk: bytes):
    with SUBSCRIBERS_LOCK:
        subs = list(SUBSCRIBERS.items())
    for sid, q in subs: # push the same chunk to all subscribers
        if q.full():
            try:
                q.get_nowait()
            except Exception:
                pass
        try:
            q.put_nowait(chunk)
        except Exception:
            pass


def _radio_loop():
    played = []
    while not RADIO_STOP.is_set():
        if not PLAYLIST:
            logger.error("Playlist is empty, cannot stream")
            time.sleep(1)
            continue
        available = [i for i in range(len(PLAYLIST)) if i not in played]
        if not available:
            played.clear()
            available = list(range(len(PLAYLIST)))

        index = random.choice(available)
        played.append(index)

        try:
            for chunk in _stream_track(index):
                if RADIO_STOP.is_set():
                    break
                broadcast_chunk(chunk)
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


ensure_radio_running()

@app.route("/")
def home():
    return render_template("index.html", title=SITE_TITLE, image_url=SITE_IMAGE)


@app.route("/playlist.m3u")
def playlist_route():
    if not PLAYLIST:
        return Response("#EXTM3U\n", mimetype="audio/x-mpegurl")
    indices = list(range(len(PLAYLIST)))
    if RANDOMIZE_PLAYLIST:
        random.shuffle(indices)

    lines = ["#EXTM3U"]
    for i in indices:
        try:
            _ensure_metadata(i)
        except Exception:
            logger.debug("Failed to ensure metadata for index %s", i)

        meta = METADATA.get(i, {})
        title = meta.get("title", f"Track {i+1}")
        artist = meta.get("artist", "Unknown")
        duration = meta.get("duration", -1)
        try:
            duration_int = int(duration) if isinstance(duration, (int, float, str)) and str(duration).isdigit() else int(duration) if isinstance(duration, int) else -1
        except Exception:
            duration_int = -1
        lines.append(f"#EXTINF:{duration_int},{artist} - {title}")
        lines.append(PLAYLIST[i])
    body = "\n".join(lines) + "\n"
    return Response(body, mimetype="audio/x-mpegurl")


@app.route("/stream")
def stream():
    ensure_radio_running()
    sid, q = add_subscriber()

    bytes_per_sec = (BITRATE_KBPS * 1000) // 8
    metaint = bytes_per_sec * META_INTERVAL_SECONDS
    def make_metadata_block(artist: str, title: str) -> bytes:
        meta_str = f"StreamTitle='{artist} - {title}';"
        meta_iso = meta_str.encode("iso-8859-1", errors="replace")
        blocks = (len(meta_iso) + 15) // 16
        if blocks == 0:
            return b"\x00"
        padding = blocks * 16 - len(meta_iso)
        return bytes([blocks]) + meta_iso + (b"\x00" * padding)
    def generate():
        bytes_since_meta = 0
        try:
            while True:
                try:
                    chunk = q.get(timeout=5)
                except Empty:
                    if RADIO_THREAD and not RADIO_THREAD.is_alive():
                        logger.warning("Producer stopped; restarting")
                        ensure_radio_running()
                    continue
                pos = 0
                chunk_len = len(chunk)
                if metaint <= 0:
                    yield chunk
                    continue

                while pos < chunk_len:
                    remaining = metaint - bytes_since_meta
                    take = min(remaining, chunk_len - pos)
                    if take > 0:
                        yield chunk[pos:pos + take]
                        pos += take
                        bytes_since_meta += take

                    if bytes_since_meta >= metaint:
                        title = (NOW_PLAYING.get("title") or "").strip()
                        artist = (NOW_PLAYING.get("artist") or "").strip()

                        meta_block = make_metadata_block(artist, title)
                        yield meta_block
                        bytes_since_meta = 0
        except GeneratorExit:
            logger.info("Client disconnected (sid=%s)", sid)
        finally:
            remove_subscriber(sid)
    headers = {
        "icy-br": str(BITRATE_KBPS),
        "icy-metaint": str(metaint),
        "icy-name": SITE_TITLE or "yt_radio.py",
    }
    return Response(stream_with_context(generate()), mimetype="audio/mpeg", headers=headers)



@app.route("/now_playing")
def now_playing():
    hx = request.headers.get("HX-Request")
    accept = request.headers.get("Accept", "")
    if (hx and hx.lower() == "true") or ("text/html" in accept and "application/json" not in accept):
        title = NOW_PLAYING.get("title") or "Nothing"
        artist = NOW_PLAYING.get("artist") or "Unknown"
        vid = NOW_PLAYING.get("id") or ""
        thumb_url = f"https://img.youtube.com/vi/{vid}/maxresdefault.jpg" if vid else (SITE_IMAGE or "")
        return f'<img src="{thumb_url}" alt="Cover" style="width:300px;height:300px;object-fit:cover;display:block;margin:0 auto 12px;"><div>{artist} — {title}</div>'
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
