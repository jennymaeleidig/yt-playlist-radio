# yt-playlist-radio
Simple demo of YouTube playlist to m3u playlist, all in about 200 lines of Python.

## How to run?
First set the environment variables as per `.env.template`, then just run it with gunicorn or something else (gunicorn comes bundled as part of the deps here)
```bash
uv sync
uv run gunicorn -w 4 -b 0.0.0.0:8000 yt_radio:app
```
