"""Regression tests for the empty-playlist bug.

The old routes.py from-imported PLAYLIST at module load — before the
background bootstrap had populated it — so the playlist endpoints served a
stale, permanently-empty snapshot forever. The fix is injection: route
handlers read live state through the injected radio object. These tests pin
that both the injection seam and the real module's snapshot accessors reflect
playlist changes made after the app was created.
"""
import pytest


pytestmark = pytest.mark.unit


URLS = [
    "https://www.youtube.com/watch?v=aaaaaaaaaaa",
    "https://www.youtube.com/watch?v=bbbbbbbbbbb",
    "https://www.youtube.com/watch?v=ccccccccccc",
]


def test_tracks_reflect_playlist_added_after_app_creation(client, fake_radio):
    # App created while the playlist is still empty (pre-bootstrap).
    response = client.get("/tracks")
    assert response.status_code == 200
    assert response.get_json() == []

    # Background refresh lands new tracks AFTER app creation.
    fake_radio.set_playlist(URLS)
    fake_radio.set_metadata(
        0, {"title": "Song A", "artist": "Artist A", "duration": 100, "id": "aaaaaaaaaaa"}
    )
    fake_radio.set_metadata(1, {"title": "Song B", "artist": "Artist B", "duration": 200})

    response = client.get("/tracks")
    tracks = response.get_json()
    assert len(tracks) == 3
    assert tracks[0]["title"] == "Song A"
    assert tracks[0]["duration"] == 100
    assert tracks[0]["id"] == "aaaaaaaaaaa", "track id must be exposed (integration relies on it)"
    assert tracks[1]["id"] == "", "missing metadata degrades to an empty id, not a KeyError"
    assert tracks[2]["url"] == URLS[2]


def test_m3u_reflects_playlist_added_after_app_creation(client, fake_radio):
    assert client.get("/playlist.m3u").get_data(as_text=True) == "#EXTM3U\n"

    fake_radio.set_playlist(URLS)
    body = client.get("/playlist.m3u").get_data(as_text=True)
    assert body.startswith("#EXTM3U\n")
    for url in URLS:
        assert url in body


def test_real_radio_module_snapshots_reflect_refresh():
    """The real yt_radio module's playlist_snapshot() (the injection surface)
    reflects a playlist refresh — i.e. nothing holds a stale from-imported
    reference. Simulates refresh_playlist() exactly, including that it REBINDS
    PLAYLIST (a new list object) rather than mutating in place — rebinding is
    precisely where a from-imported reference would have gone stale.
    Mutates module state directly; no network."""
    import yt_radio
    from webapp import create_app

    app = create_app(yt_radio)  # explicit injection: no background work started
    app.testing = True
    client = app.test_client()

    original = yt_radio.playlist_snapshot()
    try:
        yt_radio.PLAYLIST = []  # bootstrap state: empty pre-refresh snapshot
        assert client.get("/tracks").get_json() == []

        # What refresh_playlist does on refresh: rebind to a new list object.
        yt_radio.PLAYLIST = list(URLS)

        tracks = client.get("/tracks").get_json()
        assert [t["url"] for t in tracks] == URLS
        assert all(URLS[i] in client.get("/playlist.m3u").get_data(as_text=True) for i in range(3))
    finally:
        yt_radio.PLAYLIST = original
