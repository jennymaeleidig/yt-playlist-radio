# yt-playlist-radio
Takes a YouTube playlist and converts it to an audio stream, similar to internet radio
- `/playlist.m3u` provides a playlist of songs to be played, supports track skipping, like a traditional playlist
- `/stream` provides a buffered audio stream

## How to run?
First set the environment variables as per `.env.template`, then just run it with gunicorn or something else (gunicorn comes bundled as part of the deps here)
```bash
uv sync
uv run gunicorn routes:app --bind 0.0.0.0:8000 -k gthread --threads 50 --workers 1 --timeout 0 --keep-alive 5
```

> Note that `--timeout 0` is a strict requirement if using `/stream` endpoint due to Gunicorn's default timeout policy
> Similarly, you should use a single persistent worker if you want everyone listening to the same stuff on `/stream`

## Example Landing Page
<img width="936" height="992" alt="image" src="https://github.com/user-attachments/assets/e70879ad-bdff-46b0-8018-130211d950a1" />

## Local Playlist
In case when fetching from YouTube takes too long for some reason, you can also specify a local `.radio` file. An example file has been included in this repo.

The radio code will parse links from this file as the playlist instead. Set `PLAYLIST_URL="PATH_TO_.RADIO_FILE"`

The file must end with `.radio`
