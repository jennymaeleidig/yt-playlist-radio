"""Direct-mode integration tests: real YouTube from the home IP.

Deselected by default — run explicitly with `pytest -m integration`.
These are the only tests allowed to touch the network; assertions are
deliberately shape-only (playlist non-empty, track fields present, stream
yields MP3 bytes with icy-metadata) so they survive unrelated upstream
changes.

NOTE: imports of routes/webapp-with-default-radio happen lazily inside
fixtures, because pytest imports test modules during collection even when
deselected — and importing routes.py boots the real radio (background work).
"""
import time

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.timeout(300)]

BOOTSTRAP_TIMEOUT_S = 60


@pytest.fixture(scope="module")
def app():
    import yt_radio
    from routes import app as flask_app  # starts bootstrap with tests/fixtures/sample.radio

    flask_app.testing = True

    deadline = time.monotonic() + BOOTSTRAP_TIMEOUT_S
    while time.monotonic() < deadline:
        if yt_radio.playlist_snapshot():
            break
        time.sleep(0.2)
    else:
        pytest.fail(f"playlist did not load within {BOOTSTRAP_TIMEOUT_S}s")
    return flask_app


@pytest.fixture(scope="module")
def client(app):
    return app.test_client()


def test_playlist_is_non_empty_with_track_fields(client):
    response = client.get("/tracks")
    assert response.status_code == 200
    tracks = response.get_json()
    assert len(tracks) >= 5, "fixture playlist should be fully loaded"
    for track in tracks:
        assert set(track) >= {"index", "title", "artist", "duration", "url"}
        assert track["title"]
        assert track["url"].startswith("https://www.youtube.com/")


def test_metadata_is_fetched_from_youtube(client):
    """Shape-only: at least one track carries real id/title/duration, proving
    the metadata path works end-to-end from the home IP.

    Metadata is fetched lazily via ensure_metadata — wired into
    /playlist.m3u and the stream producer, not /tracks — so warm it through
    the real endpoint before asserting."""
    client.get("/playlist.m3u")  # triggers ensure_metadata for every track
    tracks = client.get("/tracks").get_json()
    with_real_meta = [t for t in tracks if t["id"] and isinstance(t["duration"], (int, float)) and t["duration"] > 0]
    assert with_real_meta, "at least one track should have real metadata from YouTube"


def test_m3u_lists_every_track(client):
    body = client.get("/playlist.m3u").get_data(as_text=True)
    assert body.startswith("#EXTM3U\n")
    tracks = client.get("/tracks").get_json()
    for track in tracks:
        assert track["url"] in body


def test_stream_yields_mp3_bytes_with_icy_metadata(client):
    import yt_radio

    with client.get("/stream", buffered=False) as response:
        assert response.status_code == 200
        metaint = int(response.headers["icy-metaint"])
        assert metaint > 0
        assert int(response.headers["icy-br"]) > 0

        data = b""
        for chunk in response.response:
            data += chunk
            if len(data) >= metaint + 4096:
                break

    # MP3 frame sync (0xFF 0xEx / 0xFF 0xFx) within the first bytes
    assert any(
        data[i] == 0xFF and (data[i + 1] & 0xE0) == 0xE0
        for i in range(min(len(data) - 1, 4096))
    ), "no MP3 frame sync found in stream head"

    # icy metadata block at the metaint boundary
    n = data[metaint]
    meta = data[metaint + 1 : metaint + 1 + n * 16]
    assert meta.startswith(b"StreamTitle='"), f"expected StreamTitle in icy block, got {meta[:60]!r}"
    title = meta.split(b";")[0].decode("utf-8", errors="replace")
    assert title.removeprefix("StreamTitle='").removesuffix("'").strip(), "stream title should be non-empty"

    # the producer must have stopped feeding once the last listener left
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if not yt_radio.SUBSCRIBERS:
            break
        time.sleep(0.2)
    assert not yt_radio.SUBSCRIBERS, "subscriber not removed after disconnect"
