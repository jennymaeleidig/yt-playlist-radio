"""Flask route module.

The app is built by create_app(radio): the radio module (or any object
exposing the same surface) is injected rather than from-imported. Route
handlers therefore always see live state — playlist refreshes included — and
tests can inject a fake radio wholesale.

This module has NO import-time side effects: importing it neither creates the
default Flask app nor starts the radio's background work. The WSGI entrypoint
lives in routes.py (`gunicorn routes:app`).
"""
import logging
import random
from queue import Empty

from flask import Flask, Response, jsonify, render_template, request, stream_with_context

logger = logging.getLogger(__name__)


def _default_meta(index):
    """Fallback metadata for a track whose real metadata hasn't arrived yet."""
    return {"title": f"Track {index+1}", "artist": "Unknown", "duration": -1, "id": ""}


def create_app(radio=None):
    """Build the Flask app against an injected radio.

    `radio` must expose the surface of the yt_radio module used below:
    playlist_snapshot / metadata_snapshot / now_playing_snapshot /
    update_now_playing / ensure_metadata / add_subscriber /
    remove_subscriber / ensure_radio_running / radio_thread, plus the config
    constants. When omitted, the real yt_radio module is used and its
    background work (bootstrap playlist load + auto-refresh) is started.
    """
    if radio is None:
        import yt_radio
        radio = yt_radio
        radio.start_background_work()

    app = Flask(__name__)

    @app.route("/")
    def home():
        return render_template("index.html", title=radio.SITE_TITLE, image_url=radio.SITE_IMAGE)

    @app.route("/playlist.m3u")
    def playlist_route():
        playlist_snapshot = radio.playlist_snapshot()

        if not playlist_snapshot:
            return Response("#EXTM3U\n", mimetype="audio/x-mpegurl")
        indices = list(range(len(playlist_snapshot)))
        if radio.RANDOMIZE_PLAYLIST:
            random.shuffle(indices)

        for i in indices:
            try:
                radio.ensure_metadata(i)
            except Exception:
                logger.debug("Failed to ensure metadata for index %s", i)

        metadata_snapshot = radio.metadata_snapshot()
        lines = ["#EXTM3U"]
        for i in indices:
            meta = metadata_snapshot.get(i, {})
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
        radio.ensure_radio_running()
        sid, q = radio.add_subscriber()

        bytes_per_sec = (radio.BITRATE_KBPS * 1000) // 8
        metaint = bytes_per_sec * radio.META_INTERVAL_SECONDS

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
            now_playing = {}
            try:
                while True:
                    try:
                        item = q.get(timeout=5)
                    except Empty:
                        producer = radio.radio_thread()
                        if producer and not producer.is_alive():
                            logger.warning("Producer stopped; restarting")
                            radio.ensure_radio_running()
                        continue

                    if item and len(item) == 2:
                        chunk_index, chunk = item
                    else:
                        chunk_index, chunk = None, item

                    if chunk_index is not None and chunk_index != current_index:
                        meta = radio.metadata_snapshot().get(chunk_index, _default_meta(chunk_index))
                        radio.update_now_playing(chunk_index, meta)
                        now_playing = radio.now_playing_snapshot()
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
                            title = (now_playing.get("title") or "").strip()
                            artist = (now_playing.get("artist") or "").strip()

                            meta_block = make_metadata_block(artist, title)
                            yield meta_block
                            bytes_since_meta = 0
            except GeneratorExit:
                logger.info("Client disconnected (sid=%s)", sid)
            finally:
                radio.remove_subscriber(sid)

        headers = {
            "icy-br": str(radio.BITRATE_KBPS),
            "icy-metaint": str(metaint),
            "icy-name": radio.SITE_TITLE or "yt_radio.py",
            "icy-charset": "utf-8",
        }
        return Response(stream_with_context(generate()), mimetype="audio/mpeg", headers=headers)

    @app.route("/now_playing")
    def now_playing():
        now_playing = radio.now_playing_snapshot()
        hx = request.headers.get("HX-Request")
        accept = request.headers.get("Accept", "")
        if (hx and hx.lower() == "true") or ("text/html" in accept and "application/json" not in accept):
            title = now_playing.get("title") or "Nothing"
            artist = now_playing.get("artist") or "Unknown"
            vid = now_playing.get("id") or ""
            thumb_url = f"https://img.youtube.com/vi/{vid}/maxresdefault.jpg" if vid else (radio.SITE_IMAGE or "")
            return (
                f'<img src="{thumb_url}" alt="Cover" style="width:300px;height:300px;object-fit:cover;display:block;margin:0 auto 12px;">'
                f"<div>{artist} — {title}</div>"
            )
        return jsonify(now_playing)

    @app.route("/tracks")
    def tracks():
        playlist_snapshot = radio.playlist_snapshot()
        metadata_snapshot = radio.metadata_snapshot()

        track_list = []
        for i, url in enumerate(playlist_snapshot):
            meta = metadata_snapshot.get(i, _default_meta(i))
            track_list.append(
                {
                    "index": i,
                    "title": meta["title"],
                    "artist": meta["artist"],
                    "duration": meta["duration"],
                    "id": meta.get("id", ""),
                    "url": url,
                }
            )
        return jsonify(track_list)

    return app
