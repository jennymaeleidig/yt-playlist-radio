"""Manual smoke: exercise the real transport end-to-end (metadata + short stream)."""
import json
import os
import sys
import time

os.environ.setdefault("PLAYLIST_URL", "tests/fixtures/sample.radio")
os.environ.setdefault("CACHE_FILE", "/tmp/yt-smoke-cache.json")
sys.path.insert(0, os.getcwd())

import yt_radio

yt_radio.PLAYLIST = yt_radio._load_urls_from_file(os.environ["PLAYLIST_URL"])
url = yt_radio.PLAYLIST[0]
print("URL:", url)

result = yt_radio.TRANSPORT.run_ytdlp(["--dump-json", url])
print("metadata returncode:", result.returncode)
print("metadata stderr:", result.stderr[:300].replace("\n", " | "))
if result.returncode == 0:
    d = json.loads(result.stdout)
    print("title:", d.get("title"), "| duration:", d.get("duration"))

pipeline = yt_radio.TRANSPORT.open_track_pipeline(
    url, ytdlp_format=yt_radio.YTDLP_FORMAT, bitrate_kbps=yt_radio.BITRATE_KBPS
)
total = 0
deadline = time.monotonic() + 60
start = time.monotonic()
while total < 256 * 1024 and time.monotonic() < deadline:
    chunk = pipeline.stdout.read(8192)
    if not chunk:
        break
    total += len(chunk)
print(f"streamed bytes: {total} in {time.monotonic()-start:.1f}s")
ferr, yerr = pipeline.close()
print("ffmpeg stderr:", ferr[:300].replace("\n", " | "))
print("yt-dlp stderr:", yerr[:300].replace("\n", " | "))
assert total > 64 * 1024, "expected to stream real media bytes"
print("SMOKE OK")
