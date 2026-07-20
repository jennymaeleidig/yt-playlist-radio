from flask import Flask, Response, stream_with_context, jsonify, render_template, request
import random
from queue import Empty

from yt_radio import (
    BITRATE_KBPS,
    META_INTERVAL_SECONDS,
    PLAYLIST,
    PLAYLIST_LOCK,
    RANDOMIZE_PLAYLIST,
    RADIO_THREAD,
    SITE_IMAGE,
    SITE_TITLE,
    NOW_PLAYING,
    METADATA,
    _ensure_metadata,
    add_subscriber,
    ensure_radio_running,
    logger,
    remove_subscriber,
)

app = Flask(__name__)


@app.route("/")
def home():
    return render_template("index.html", title=SITE_TITLE, image_url=SITE_IMAGE)


@app.route("/playlist.m3u")
def playlist_route():
    with PLAYLIST_LOCK:
        playlist_snapshot = list(PLAYLIST)

    if not playlist_snapshot:
        return Response("#EXTM3U\n", mimetype="audio/x-mpegurl")
    indices = list(range(len(playlist_snapshot)))
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
            duration_int = (
                int(duration)
                if isinstance(duration, (int, float, str)) and str(duration).isdigit()
                else int(duration)
                if isinstance(duration, int)
                else -1
            )
        except Exception:
            duration_int = -1
        lines.append(f"#EXTINF:{duration_int},{artist} - {title}")
        lines.append(playlist_snapshot[i])
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
        meta_utf = meta_str.encode("utf-8", errors="replace")
        blocks = (len(meta_utf) + 15) // 16
        if blocks == 0:
            return b"\x00"
        padding = blocks * 16 - len(meta_utf)
        return bytes([blocks]) + meta_utf + (b"\x00" * padding)

    def generate():
        bytes_since_meta = 0
        current_index = None
        try:
            while True:
                try:
                    item = q.get(timeout=5)
                except Empty:
                    if RADIO_THREAD and not RADIO_THREAD.is_alive():
                        logger.warning("Producer stopped; restarting")
                        ensure_radio_running()
                    continue

                if item and len(item) == 2:
                    chunk_index, chunk = item
                else:
                    chunk_index, chunk = None, item

                if chunk_index is not None and chunk_index != current_index:
                    meta = METADATA.get(
                        chunk_index,
                        {
                            "title": f"Track {chunk_index+1}",
                            "artist": "Unknown",
                            "duration": -1,
                            "id": "",
                        },
                    )
                    NOW_PLAYING["index"] = chunk_index
                    NOW_PLAYING["title"] = meta.get("title", "")
                    NOW_PLAYING["artist"] = meta.get("artist", "")
                    NOW_PLAYING["id"] = meta.get("id", "")
                    current_index = chunk_index

                pos = 0
                chunk_len = len(chunk)
                if metaint <= 0:
                    yield chunk
                    continue

                while pos < chunk_len:
                    remaining = metaint - bytes_since_meta
                    take = min(remaining, chunk_len - pos)
                    if take > 0:
                        yield chunk[pos : pos + take]
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
        "icy-charset": "utf-8",
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
        return (
            f'<img src="{thumb_url}" alt="Cover" style="width:300px;height:300px;object-fit:cover;display:block;margin:0 auto 12px;">'
            f"<div>{artist} — {title}</div>"
        )
    return jsonify(NOW_PLAYING)


@app.route("/tracks")
def tracks():
    with PLAYLIST_LOCK:
        playlist_snapshot = list(PLAYLIST)
        metadata_snapshot = dict(METADATA)

    track_list = []
    for i, url in enumerate(playlist_snapshot):
        meta = metadata_snapshot.get(i, {"title": f"Track {i+1}", "artist": "Unknown", "duration": -1})
        track_list.append(
            {
                "index": i,
                "title": meta["title"],
                "artist": meta["artist"],
                "duration": meta["duration"],
                "url": url,
            }
        )
    return jsonify(track_list)
