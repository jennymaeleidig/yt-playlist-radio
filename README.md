# yt-playlist-radio
Takes a YouTube playlist and converts it to an audio stream, similar to internet radio
- `/playlist.m3u` provides a playlist of songs to be played, supports track skipping, like a traditional playlist
- `/stream` provides a buffered audio stream

## How to run?
First set the environment variables as per `.env.template`, then just run it with gunicorn or something else (gunicorn comes bundled as part of the deps here)
```bash
uv sync
uv run gunicorn yt_radio:app --bind 0.0.0.0:8000 -k gthread --threads 50 --workers 1 --timeout 0 --keep-alive 5
```

> Note that `--timeout 0` is a strict requirement if using `/stream` endpoint due to Gunicorn's default timeout policy
> Similarly, you should use a single persistent worker if you want everyone listening to the same stuff on `/stream`
