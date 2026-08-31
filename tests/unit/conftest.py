"""Shared fakes for unit tests: a wholesale replacement for the yt_radio
module surface that webapp.create_app consumes."""
import logging
from queue import Queue

import pytest

import yt_radio


@pytest.fixture
def radio_state():
    """Snapshot yt_radio's mutable module state (playlist, metadata, cache,
    guardrails, pause/alert state) and restore it afterwards."""
    saved = {
        "playlist": yt_radio.PLAYLIST,
        "metadata": dict(yt_radio.METADATA),
        "cache": dict(yt_radio._CACHE),
    }
    yt_radio.METADATA.clear()
    yt_radio._CACHE.clear()  # the temp cache file persists across runs
    yt_radio._cookies_recommended.clear()
    yt_radio.RADIO_STOP.clear()
    yt_radio.PAUSED.clear()
    yt_radio._FAILURE_BUDGET.reset()
    yt_radio.PAUSE_INFO.clear()
    yt_radio.LAST_TRACK_FAILURE.clear()
    yt_radio._PAUSED_AT_MONOTONIC = None
    yield
    yt_radio.PLAYLIST = saved["playlist"]
    yt_radio.METADATA.clear()
    yt_radio.METADATA.update(saved["metadata"])
    yt_radio._CACHE.clear()
    yt_radio._CACHE.update(saved["cache"])
    yt_radio._cookies_recommended.clear()
    yt_radio.RADIO_STOP.clear()
    yt_radio.PAUSED.clear()
    yt_radio._FAILURE_BUDGET.reset()
    yt_radio.PAUSE_INFO.clear()
    yt_radio.LAST_TRACK_FAILURE.clear()
    yt_radio._PAUSED_AT_MONOTONIC = None
    with yt_radio.SUBSCRIBERS_LOCK:
        yt_radio.SUBSCRIBERS.clear()
    yt_radio.SUBSCRIBER_EVENT.clear()


class FakeRadio:
    """In-memory stand-in for yt_radio.

    Mirrors exactly the surface webapp.create_app consumes; playlist state is
    mutated via set_playlist to simulate a background playlist refresh.
    """

    def __init__(self, playlist=None, bitrate_kbps=192, meta_interval_seconds=5):
        self._playlist = list(playlist or [])
        self._metadata = {}
        self._now_playing = {"index": None, "title": "Nothing", "artist": "Unknown", "id": ""}
        self.BITRATE_KBPS = bitrate_kbps
        self.META_INTERVAL_SECONDS = meta_interval_seconds
        self.RANDOMIZE_PLAYLIST = False
        self.SITE_TITLE = "test radio"
        self.SITE_IMAGE = ""

        self.queues = []  # queues handed out to subscribers
        self.next_queue = None  # if set, the next subscriber gets this queue
        self.ensure_radio_calls = 0
        self.ensure_metadata_calls = []
        self.removed_subscribers = []

    # -- playlist state -------------------------------------------------
    def set_playlist(self, playlist):
        self._playlist = list(playlist)

    def set_metadata(self, index, meta):
        self._metadata[index] = dict(meta)

    def playlist_snapshot(self):
        return list(self._playlist)

    def metadata_snapshot(self):
        return dict(self._metadata)

    def now_playing_snapshot(self):
        return dict(self._now_playing)

    def update_now_playing(self, chunk_index, meta):
        self._now_playing["index"] = chunk_index
        self._now_playing["title"] = meta.get("title", "")
        self._now_playing["artist"] = meta.get("artist", "")
        self._now_playing["id"] = meta.get("id", "")

    # -- radio lifecycle -------------------------------------------------
    def ensure_metadata(self, index):
        self.ensure_metadata_calls.append(index)

    def ensure_radio_running(self):
        self.ensure_radio_calls += 1

    def radio_thread(self):
        return None

    # -- subscribers -------------------------------------------------
    def add_subscriber(self):
        if self.next_queue is not None:
            q = self.next_queue
            self.next_queue = None
        else:
            q = Queue(maxsize=256)
        sid = f"sid-{len(self.queues)}"
        self.queues.append(q)
        return sid, q

    def remove_subscriber(self, sid):
        self.removed_subscribers.append(sid)


@pytest.fixture
def fake_radio():
    return FakeRadio()


@pytest.fixture
def client(fake_radio):
    from webapp import create_app

    app = create_app(fake_radio)
    app.testing = True
    return app.test_client()


@pytest.fixture
def quiet_logging():
    """Silence webapp/radio logging during tests that exercise failure paths."""
    logging.disable(logging.CRITICAL)
    yield
    logging.disable(logging.NOTSET)
