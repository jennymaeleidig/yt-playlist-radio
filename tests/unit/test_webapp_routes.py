"""Behavioural unit tests for the route module against a fake radio."""
from queue import Queue

import pytest


pytestmark = pytest.mark.unit


def open_stream(client, fake_radio, chunks=(b"\x00\x00\x00",)):
    """Open /stream with `chunks` pre-loaded in the subscriber queue and return
    the (unbuffered) TestResponse.

    Werkzeug's test client eagerly pulls exactly one yield inside client.get(),
    so the queue must be non-empty at request time; the eagerly-pulled first
    chunk is still part of response.response, shifted to the front.
    """
    q = Queue(maxsize=256)
    for c in chunks:
        q.put(c)
    fake_radio.next_queue = q
    return client.get("/stream", buffered=False)


def stream_chunks(client, fake_radio, chunks, extra_reads=0):
    """open_stream + read len(chunks)+extra_reads delivered chunks."""
    with open_stream(client, fake_radio, chunks) as response:
        it = iter(response.response)
        return [next(it) for _ in range(len(chunks) + extra_reads)]


def test_home_renders_template_with_site_config(client, fake_radio):
    response = client.get("/")
    assert response.status_code == 200
    assert fake_radio.SITE_TITLE in response.get_data(as_text=True)


def test_stream_calls_ensure_radio_running_once(client, fake_radio):
    with open_stream(client, fake_radio):
        pass
    assert fake_radio.ensure_radio_calls == 1
    assert fake_radio.removed_subscribers, "subscriber removed when the client disconnects"


def test_stream_sets_icy_headers(client, fake_radio):
    metaint = (192 * 1000 // 8) * 5
    with open_stream(client, fake_radio) as response:
        assert response.headers["icy-br"] == "192"
        assert response.headers["icy-metaint"] == str(metaint)
        assert response.headers["icy-name"] == "test radio"
        assert response.headers["icy-charset"] == "utf-8"
        assert response.headers["Content-Type"].startswith("audio/mpeg")


def test_stream_yields_scripted_chunks_and_updates_now_playing(client, fake_radio):
    chunk_a = b"\xff\xfb\x90\x00" + b"\x00" * 252
    chunk_b = b"\xff\xfb\x90\x01" + b"\x01" * 252
    fake_radio.set_metadata(0, {"title": "Song A", "artist": "Artist A", "duration": 100, "id": "aaaaaaaaaaa"})

    delivered = stream_chunks(client, fake_radio, [(0, chunk_a), (0, chunk_b)])

    assert delivered[0] == chunk_a
    assert delivered[1] == chunk_b
    assert fake_radio.now_playing_snapshot()["title"] == "Song A"
    assert fake_radio.removed_subscribers, "subscriber removed when the client disconnects"


def test_stream_emits_icy_metadata_block_at_metaint(client, fake_radio):
    fake_radio.BITRATE_KBPS = 8  # bytes_per_sec = 1000
    fake_radio.META_INTERVAL_SECONDS = 5
    metaint = (8 * 1000 // 8) * 5  # 5000 bytes

    dummy = b"\x00" * 10
    half = b"\xff\xfb" + b"\x00" * (2495 - 2)  # 10 + 2495 + 2495 == metaint
    fake_radio.set_metadata(0, {"title": "Song A", "artist": "Artist A", "duration": 100, "id": "aaaaaaaaaaa"})

    delivered = stream_chunks(client, fake_radio, [(0, dummy), (0, half), (0, half)], extra_reads=1)

    assert len(b"".join(delivered[:3])) == metaint, "audio bytes before the metadata block == metaint"
    meta_block = delivered[3]
    blocks = meta_block[0]
    text = meta_block[1 : 1 + blocks * 16]
    assert text.startswith(b"StreamTitle='Artist A - Song A';")
    assert len(meta_block) == 1 + blocks * 16


def test_now_playing_json_and_hx_variants(client, fake_radio):
    response = client.get("/now_playing")
    assert response.status_code == 200
    assert response.get_json()["title"] == "Nothing"

    fake_radio.update_now_playing(0, {"title": "Song A", "artist": "Artist A", "id": "aaaaaaaaaaa"})
    json_now = client.get("/now_playing").get_json()
    assert json_now["title"] == "Song A" and json_now["id"] == "aaaaaaaaaaa"

    html_now = client.get("/now_playing", headers={"HX-Request": "true"})
    body = html_now.get_data(as_text=True)
    assert "Song A" in body and "img.youtube.com/vi/aaaaaaaaaaa" in body


def test_playlist_m3u_empty_playlist(client):
    response = client.get("/playlist.m3u")
    assert response.status_code == 200
    assert response.get_data(as_text=True) == "#EXTM3U\n"
    assert response.mimetype == "audio/x-mpegurl"


def test_playlist_m3u_ensure_metadata_called_for_every_track(client, fake_radio):
    fake_radio.set_playlist(["u1", "u2", "u3"])
    body = client.get("/playlist.m3u").get_data(as_text=True)
    assert "u1" in body and "u3" in body
    assert sorted(fake_radio.ensure_metadata_calls) == [0, 1, 2]
