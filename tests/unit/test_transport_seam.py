"""Unit tests for the transport seam (issue 02).

Every yt-dlp / ffmpeg subprocess spawn in the app flows through the single
`yt_radio.TRANSPORT` object. These tests exercise the supervisor
(fetch_metadata / _stream_track / _radio_loop) through a FAKE transport with
scripted exit codes, slow tracks, dead tracks, and spawn failures.

No test here asserts on raw argv strings: the fake transport ignores argv
entirely and scripts outcomes. (The one exception is TestRealTransportVenvPinning,
which pins that the REAL transport resolves yt-dlp from the venv interpreter —
the requirement itself is about how argv is built.)
"""
import io
import json
import subprocess
import sys
import threading
import time

import pytest

import yt_radio

pytestmark = pytest.mark.unit

TRACK_URL = "https://www.youtube.com/watch?v=aaaaaaaaaaa"


# -- fakes -----------------------------------------------------------------

class FakeCompleted:
    """Scripted subprocess.run result (exit codes / stdout / stderr)."""

    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class FakePipeline:
    """Scripted `yt-dlp | ffmpeg` pipeline.

    `chunks` is the media stream ffmpeg would emit; an empty list is a
    dead track (immediate EOF). `read_delay` simulates a slow track.
    `stderr_texts` is what close() reports as (ffmpeg_stderr, ytdlp_stderr).
    """

    def __init__(self, chunks=(), read_delay=0.0, stderr_texts=("", "")):
        self.stdout = _DelayedReader(io.BytesIO(b"".join(chunks)), read_delay)
        self._stderr_texts = stderr_texts
        self.close_calls = 0

    def close(self):
        self.close_calls += 1
        return self._stderr_texts


class _DelayedReader:
    def __init__(self, inner, delay):
        self._inner = inner
        self._delay = delay

    def read(self, size):
        if self._delay:
            time.sleep(self._delay)
        return self._inner.read(size)


class FakeTransport:
    """Wholesale fake for yt_radio.TRANSPORT.

    Ignores argv entirely — callers are scripted by outcome, never by
    command-line shape. `pipelines` hands out one scripted pipeline per
    open_track_pipeline call; `results` scripts run_ytdlp outcomes.
    """

    def __init__(self, pipelines=(), results=None, spawn_error=None, proxied=False):
        self._pipelines = list(pipelines)
        self._results = list(results or [])
        self._spawn_error = spawn_error
        self.proxied = proxied
        self.pipeline_opens = 0
        self.run_ytdlp_calls = 0

    def run_ytdlp(self, args, timeout=None, sticky_key=None):
        self.run_ytdlp_calls += 1
        if self._results:
            return self._results.pop(0)
        return FakeCompleted(returncode=0, stdout="{}")

    def open_track_pipeline(self, url, *, ytdlp_format, bitrate_kbps):
        self.pipeline_opens += 1
        if self._spawn_error is not None:
            raise self._spawn_error
        if self._pipelines:
            return self._pipelines.pop(0)
        return FakePipeline()


def _meta_json(**overrides):
    data = {"title": "Song A", "uploader": "Artist A", "duration": 100, "id": "aaaaaaaaaaa"}
    data.update(overrides)
    return json.dumps(data)


@pytest.fixture
def radio_state():
    """Snapshot yt_radio's mutable module state and restore it afterwards."""
    saved = {
        "playlist": yt_radio.PLAYLIST,
        "metadata": dict(yt_radio.METADATA),
        "cache": dict(yt_radio._CACHE),
    }
    yt_radio.METADATA.clear()
    yt_radio._CACHE.clear()  # the temp cache file persists across runs
    yt_radio._cookies_recommended.clear()
    yt_radio.RADIO_STOP.clear()
    yield
    yt_radio.PLAYLIST = saved["playlist"]
    yt_radio.METADATA.clear()
    yt_radio.METADATA.update(saved["metadata"])
    yt_radio._CACHE.clear()
    yt_radio._CACHE.update(saved["cache"])
    yt_radio._cookies_recommended.clear()
    yt_radio.RADIO_STOP.clear()
    with yt_radio.SUBSCRIBERS_LOCK:
        yt_radio.SUBSCRIBERS.clear()
    yt_radio.SUBSCRIBER_EVENT.clear()


# -- metadata path (scripted exit codes) ------------------------------------

class TestFetchMetadataThroughTransport:
    def test_success_populates_metadata_and_cache(self, radio_state, monkeypatch):
        transport = FakeTransport(results=[FakeCompleted(0, _meta_json(), "")])
        monkeypatch.setattr(yt_radio, "TRANSPORT", transport)

        yt_radio.fetch_metadata(0, TRACK_URL)

        meta = yt_radio.METADATA[0]
        assert meta == {"title": "Song A", "artist": "Artist A", "duration": 100, "id": "aaaaaaaaaaa"}
        # the fetched metadata is persisted to the cache for next time
        assert yt_radio._CACHE[TRACK_URL]["title"] == "Song A"
        assert transport.run_ytdlp_calls == 1

    def test_cached_metadata_skips_transport_entirely(self, radio_state, monkeypatch):
        transport = FakeTransport()
        monkeypatch.setattr(yt_radio, "TRANSPORT", transport)
        yt_radio._CACHE[TRACK_URL] = {"title": "Cached", "uploader": "C", "duration": 9, "id": "x"}

        yt_radio.fetch_metadata(0, TRACK_URL)

        assert transport.run_ytdlp_calls == 0
        assert yt_radio.METADATA[0]["title"] == "Cached"

    def test_nonzero_exit_degrades_to_fallback_metadata(self, radio_state, monkeypatch, caplog):
        # Scripted failure: yt-dlp exits 1 with a 403 bot-wall on stderr.
        transport = FakeTransport(results=[FakeCompleted(1, "", "ERROR: HTTP Error 403: Forbidden")])
        monkeypatch.setattr(yt_radio, "TRANSPORT", transport)

        with caplog.at_level("ERROR"):
            yt_radio.fetch_metadata(0, TRACK_URL)

        # streaming must not die over metadata: fallback meta is installed
        assert yt_radio.METADATA[0] == {"title": "Track 1", "artist": "Unknown", "duration": -1, "id": ""}

    def test_403_bot_wall_recommends_cookies_once(self, radio_state, monkeypatch, caplog):
        transport = FakeTransport(
            results=[
                FakeCompleted(1, "", "ERROR: HTTP Error 403: Forbidden"),
                FakeCompleted(1, "", "ERROR: Sign in to confirm you're not a bot"),
            ]
        )
        monkeypatch.setattr(yt_radio, "TRANSPORT", transport)

        with caplog.at_level("ERROR"):
            yt_radio.fetch_metadata(0, TRACK_URL)
            yt_radio.fetch_metadata(1, TRACK_URL)

        cookie_errors = [r for r in caplog.records if "COOKIES_FILE" in r.getMessage()]
        assert len(cookie_errors) == 1, "cookies recommendation must fire exactly once"


# -- media path (slow / dead tracks, spawn failures) -------------------------

class TestStreamTrackThroughTransport:
    def test_yields_scripted_chunks_unchanged(self, radio_state, monkeypatch):
        chunk_a, chunk_b = b"\xff\xfb" + b"a" * 8190, b"\xff\xfb" + b"b" * 8190
        transport = FakeTransport(pipelines=[FakePipeline(chunks=[chunk_a, chunk_b])])
        monkeypatch.setattr(yt_radio, "TRANSPORT", transport)
        yt_radio.PLAYLIST = [TRACK_URL]
        yt_radio.METADATA[0] = {"title": "t", "artist": "a", "duration": 1, "id": "x"}

        chunks = list(yt_radio._stream_track(0, TRACK_URL))

        assert chunks == [chunk_a, chunk_b]

    def test_dead_track_ends_stream_immediately(self, radio_state, monkeypatch):
        # Scripted dead track: pipeline EOFs with zero bytes.
        pipeline = FakePipeline(chunks=[])
        transport = FakeTransport(pipelines=[pipeline])
        monkeypatch.setattr(yt_radio, "TRANSPORT", transport)
        yt_radio.PLAYLIST = [TRACK_URL]
        yt_radio.METADATA[0] = {"title": "t", "artist": "a", "duration": 1, "id": "x"}

        assert list(yt_radio._stream_track(0, TRACK_URL)) == []
        assert pipeline.close_calls == 1, "supervisor must close (reap) the pipeline exactly once"

    def test_slow_track_still_streams_every_chunk(self, radio_state, monkeypatch):
        chunks = [b"\xff\xfb" + bytes([i]) * 8190 for i in range(3)]
        pipeline = FakePipeline(chunks=chunks, read_delay=0.05)
        transport = FakeTransport(pipelines=[pipeline])
        monkeypatch.setattr(yt_radio, "TRANSPORT", transport)
        yt_radio.PLAYLIST = [TRACK_URL]
        yt_radio.METADATA[0] = {"title": "t", "artist": "a", "duration": 1, "id": "x"}

        assert list(yt_radio._stream_track(0, TRACK_URL)) == chunks

    def test_close_reports_stderr_and_cookies_recommendation(self, radio_state, monkeypatch, caplog):
        pipeline = FakePipeline(
            chunks=[b"\xff\xfb"],
            stderr_texts=("ffmpeg: broken pipe", "ERROR: 403 Forbidden"),
        )
        transport = FakeTransport(pipelines=[pipeline])
        monkeypatch.setattr(yt_radio, "TRANSPORT", transport)
        yt_radio.PLAYLIST = [TRACK_URL]
        yt_radio.METADATA[0] = {"title": "t", "artist": "a", "duration": 1, "id": "x"}

        with caplog.at_level("WARNING"):
            list(yt_radio._stream_track(0, TRACK_URL))

        messages = [r.getMessage() for r in caplog.records]
        assert any("ffmpeg stderr" in m and "ffmpeg: broken pipe" in m for m in messages)
        assert any("yt-dlp stderr" in m and "403" in m for m in messages)
        assert any("COOKIES_FILE" in m for m in messages), "403 on stderr must trigger the cookies hint"

    def test_spawn_failure_propagates_to_supervisor(self, radio_state, monkeypatch):
        # Scripted proxy failure: the transport cannot even spawn the pipeline.
        transport = FakeTransport(spawn_error=OSError("proxy connection refused"))
        monkeypatch.setattr(yt_radio, "TRANSPORT", transport)
        yt_radio.PLAYLIST = [TRACK_URL]
        yt_radio.METADATA[0] = {"title": "t", "artist": "a", "duration": 1, "id": "x"}

        with pytest.raises(OSError):
            list(yt_radio._stream_track(0, TRACK_URL))


# -- supervisor loop (skips failed tracks, keeps playing) --------------------

class TestRadioLoopThroughTransport:
    def test_loop_skips_failing_track_and_keeps_playing(self, radio_state, monkeypatch):
        other = "https://www.youtube.com/watch?v=bbbbbbbbbbb"
        chunk = b"\xff\xfb" + b"x" * 8190
        transport = FakeTransport(pipelines=[FakePipeline(chunks=[chunk, chunk])])
        # First open fails (proxy), every later open succeeds — regardless of
        # which playlist index the loop picked.
        state = {"failed_once": False}

        def flaky_open(url, **kwargs):
            if not state["failed_once"]:
                state["failed_once"] = True
                raise OSError("proxy connection refused")
            return FakePipeline(chunks=[chunk, chunk])

        transport.open_track_pipeline = flaky_open
        monkeypatch.setattr(yt_radio, "TRANSPORT", transport)
        yt_radio.PLAYLIST = [TRACK_URL, other]
        yt_radio.METADATA[0] = {"title": "t", "artist": "a", "duration": 1, "id": "x"}
        yt_radio.METADATA[1] = {"title": "t", "artist": "a", "duration": 1, "id": "y"}

        sid, q = yt_radio.add_subscriber()
        received = []

        def consume():
            while len(received) < 2 and not yt_radio.RADIO_STOP.is_set():
                try:
                    received.append(q.get(timeout=0.5))
                except Exception:
                    pass

        consumer = threading.Thread(target=consume, daemon=True)
        consumer.start()
        loop = threading.Thread(target=yt_radio._radio_loop, daemon=True)
        loop.start()

        consumer.join(timeout=15)
        yt_radio.RADIO_STOP.set()
        loop.join(timeout=10)
        yt_radio.remove_subscriber(sid)

        assert not loop.is_alive(), "radio loop must terminate once RADIO_STOP is set"
        assert len(received) >= 2, "loop must recover from the failed spawn and keep streaming"
        assert state["failed_once"]


# -- wiring ------------------------------------------------------------------

class TestTransportWiring:
    def test_radio_module_has_no_direct_subprocess_access(self):
        """All spawns flow through the transport: yt_radio itself must not
        even hold the subprocess module — the transport is the only spawner."""
        assert not hasattr(yt_radio, "subprocess"), (
            "yt_radio must spawn exclusively via yt_radio.TRANSPORT"
        )

    def test_transport_is_a_single_shared_object(self):
        assert hasattr(yt_radio.TRANSPORT, "run_ytdlp")
        assert hasattr(yt_radio.TRANSPORT, "open_track_pipeline")


class TestRealTransportVenvPinning:
    """The real Transport must resolve yt-dlp from the venv interpreter
    (`python -m yt_dlp`), never a PATH binary — this is the one place where
    looking at argv IS the requirement, not an implementation detail."""

    def test_yt_dlp_runs_under_the_venv_interpreter(self):
        from transport import Transport

        argv = Transport().yt_dlp_argv(["--dump-json", TRACK_URL])

        assert argv[0] == sys.executable, "yt-dlp must run under the venv interpreter"
        assert argv[1:3] == ["-m", "yt_dlp"], "yt-dlp must be imported as a module, not a PATH binary"

    def test_run_ytdlp_passes_venv_argv_to_subprocess(self, monkeypatch):
        from transport import Transport

        captured = {}
        def fake_run(argv, **kwargs):
            captured["argv"] = argv
            return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

        monkeypatch.setattr("transport.subprocess.run", fake_run)
        Transport().run_ytdlp(["--dump-json", TRACK_URL])

        assert captured["argv"][0] == sys.executable
        assert captured["argv"][1:3] == ["-m", "yt_dlp"]
