from flask import Flask, Response, stream_with_context, abort
from yt_dlp import YoutubeDL
import subprocess
import json
import threading
import random
import os
from dotenv import load_dotenv

# Load .env first
load_dotenv()

app = Flask(__name__)

BASE_URL = os.environ.get("BASE_URL", "http://localhost:8000")
PLAYLIST_URL = os.environ.get("PLAYLIST_URL")
RANDOMIZE_PLAYLIST = os.environ.get("RANDOMIZE_PLAYLIST", "False").lower() in ("1", "true", "yes")
if not PLAYLIST_URL:
    raise RuntimeError("Please set PLAYLIST_URL environment variable")
METADATA = {}

def convert_playlist_to_links(link: str):
    ydl_opts = {
        "quiet": True,
        "extract_flat": True,   # don't download, just metadata
    }
    urls = []
    with YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(link, download=False)
        for entry in info["entries"]:
            urls.append(f"https://www.youtube.com/watch?v={entry['id']}")
    if RANDOMIZE_PLAYLIST:
        random.shuffle(urls)
    return urls

def fetch_metadata(index, url):
    try:
        result = subprocess.run(
            ["yt-dlp", "--dump-json", url],
            capture_output=True,
            text=True,
            timeout=20
        )
        data = json.loads(result.stdout)
        METADATA[index] = {
            "title": data.get("title", f"Track {index+1}"),
            "artist": data.get("uploader", "Unknown"),
            "duration": data.get("duration", -1)
        }
    except Exception:
        METADATA[index] = {
            "title": f"Track {index+1}",
            "artist": "Unknown",
            "duration": -1
        }

PLAYLIST = convert_playlist_to_links(PLAYLIST_URL)
for i, url in enumerate(PLAYLIST):
    threading.Thread(target=fetch_metadata, args=(i, url), daemon=True).start()
print(f"Playlist loaded: {len(PLAYLIST)} tracks")
print(f"OK. Now serving, you can access the m3u at {BASE_URL}/playlist.m3u")


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
            process.kill()

    return Response(stream_with_context(stream()), mimetype="audio/mpeg")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, threaded=True)
