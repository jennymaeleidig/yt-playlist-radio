"""Unit tests for the cost guardrails (issue 04): exponential backoff on
consecutive track failures, the failure budget (5 consecutive / 5 in 10
minutes) that pauses the supervisor loop, and the terminal 407
TRAFFIC_EXHAUSTED response that pauses immediately with zero retries.

Everything is exercised through the fake transport (scripted exit codes and
stderr), so the behaviour is deterministic and hermetic. Failure decisions
key on yt-dlp's exit code — never ffmpeg's — so the fakes script both.
"""
import threading
import time

import pytest

import yt_radio
from tests.unit.test_transport_seam import FakePipeline, FakeTransport

pytestmark = pytest.mark.unit

TRACK_URL = "https://www.youtube.com/watch?v=aaaaaaaaaaa"


# -- fakes / helpers --------------------------------------------------------

class _StubStop:
    """Deterministic stand-in for RADIO_STOP: records every wait() timeout
    (so tests can assert the backoff schedule), never blocks for real, and
    ends the loop when the test calls set()."""

    def __init__(self):
        self.waits = []
        self._event = threading.Event()

    def is_set(self):
        return self._event.is_set()

    def set(self):
        self._event.set()

    def clear(self):
        self._event.clear()

    def wait(self, timeout=None):
        self.waits.append(timeout)
        return self._event.wait(0)


class _FakeClock:
    """Injectable monotonic clock for FailureBudget window tests."""

    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def _wait_until(predicate, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def _run_until_paused(stub_stop):
    """Run _radio_loop in a thread until the supervisor pauses; return the
    thread. The caller must join it via _stop_loop."""
    loop = threading.Thread(target=yt_radio._radio_loop, daemon=True)
    loop.start()
    assert _wait_until(yt_radio.PAUSED.is_set), "supervisor must pause"
    return loop


def _stop_loop(loop, stub_stop):
    stub_stop.set()
    loop.join(timeout=10)
    assert not loop.is_alive(), "radio loop must terminate once stopped"


def _fail_pipeline(stderr="ERROR: HTTP Error 403: Forbidden", rc=1):
    """A track whose yt-dlp exits nonzero: dead stream + real exit code."""
    return FakePipeline(chunks=[], ytdlp_returncode=rc, stderr_texts=("", stderr))


def _ok_pipeline(chunk=None):
    return FakePipeline(chunks=[chunk or (b"\xff\xfb" + b"x" * 100)])


@pytest.fixture
def radio_state():
    """Snapshot yt_radio's mutable module state (including guardrail state)
    and restore it afterwards."""
    saved = {
        "playlist": yt_radio.PLAYLIST,
        "metadata": dict(yt_radio.METADATA),
        "cache": dict(yt_radio._CACHE),
    }
    yt_radio.METADATA.clear()
    yt_radio._CACHE.clear()
    yt_radio._cookies_recommended.clear()
    yt_radio.RADIO_STOP.clear()
    yt_radio.PAUSED.clear()
    yt_radio._FAILURE_BUDGET.reset()
    yt_radio.PAUSE_INFO.clear()
    yt_radio.LAST_TRACK_FAILURE.clear()
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
    with yt_radio.SUBSCRIBERS_LOCK:
        yt_radio.SUBSCRIBERS.clear()
    yt_radio.SUBSCRIBER_EVENT.clear()


def _single_track_playlist():
    yt_radio.PLAYLIST = [TRACK_URL]
    yt_radio.METADATA[0] = {"title": "t", "artist": "a", "duration": 1, "id": "x"}


class _ExplodingReader:
    """A pipeline stdout whose reads fail mid-stream."""

    def read(self, size):
        raise OSError("read failed")


# -- exponential backoff -----------------------------------------------------

class TestBackoff:
    def test_backoff_delay_doubles_and_caps(self):
        assert yt_radio.backoff_delay(1) == 2
        assert yt_radio.backoff_delay(2) == 4
        assert yt_radio.backoff_delay(3) == 8
        assert yt_radio.backoff_delay(4) == 16
        assert yt_radio.backoff_delay(10) == yt_radio.BACKOFF_MAX_SECONDS

    def test_consecutive_failures_back_off_exponentially(self, radio_state, monkeypatch):
        stub = _StubStop()
        monkeypatch.setattr(yt_radio, "RADIO_STOP", stub)
        transport = FakeTransport(spawn_error=OSError("proxy connection refused"))
        monkeypatch.setattr(yt_radio, "TRANSPORT", transport)
        _single_track_playlist()
        sid, _ = yt_radio.add_subscriber()

        loop = _run_until_paused(stub)
        _stop_loop(loop, stub)
        yt_radio.remove_subscriber(sid)

        # Exactly five attempts, then pause — no retry storm. The four
        # non-terminal failures waited out the doubling schedule (the pause
        # polls show up as PAUSE_POLL_SECONDS waits and are not backoff).
        assert transport.pipeline_opens == 5
        backoff_waits = [w for w in stub.waits if w != yt_radio.PAUSE_POLL_SECONDS]
        assert backoff_waits == [2, 4, 8, 16], "backoff must double per consecutive failure"


# -- failure budget (pure logic, injectable clock) ---------------------------

class TestFailureBudget:
    def _budget(self, clock, **overrides):
        return yt_radio.FailureBudget(clock=clock, **overrides)

    def test_five_consecutive_failures_exhaust_budget(self):
        budget = self._budget(_FakeClock())
        for _ in range(4):
            budget.record_failure()
            assert not budget.exhausted()
        budget.record_failure()
        assert budget.exhausted()

    def test_success_resets_consecutive_counter(self):
        clock = _FakeClock()
        # Window disabled: this test isolates the consecutive counter.
        budget = self._budget(clock, window_failures=100)
        for _ in range(4):
            budget.record_failure()
        budget.record_success()
        assert not budget.exhausted()
        budget.record_failure()
        assert not budget.exhausted(), "one failure after a success must not pause"

    def test_five_failures_in_window_exhaust_even_with_successes(self):
        clock = _FakeClock()
        budget = self._budget(clock)
        # f s f s f s f s f — consecutive never exceeds 1, but five failures
        # land inside the 10-minute window.
        for _ in range(4):
            budget.record_failure()
            budget.record_success()
            clock.advance(60)
            assert not budget.exhausted()
        budget.record_failure()
        assert budget.exhausted()

    def test_failures_roll_out_of_window(self):
        clock = _FakeClock()
        budget = self._budget(clock)
        for _ in range(4):
            budget.record_failure()
        budget.record_success()
        clock.advance(601)  # the four failures are now outside the 10-minute window
        budget.record_failure()
        assert not budget.exhausted()
        clock.advance(1)
        budget.record_failure()
        assert not budget.exhausted()

    def test_reset_clears_everything(self):
        budget = self._budget(_FakeClock())
        for _ in range(5):
            budget.record_failure()
        assert budget.exhausted()
        budget.reset()
        assert not budget.exhausted()
        assert budget.consecutive_failures() == 0
        assert budget.failures_in_window() == 0


# -- 407 TRAFFIC_EXHAUSTED is terminal ---------------------------------------

class TestTrafficExhausted:
    def test_407_pauses_immediately_with_zero_retries(self, radio_state, monkeypatch):
        stub = _StubStop()
        monkeypatch.setattr(yt_radio, "RADIO_STOP", stub)
        transport = FakeTransport(
            pipelines=[
                _fail_pipeline(stderr="ERROR: unable to download video data: HTTP Error 407: TRAFFIC_EXHAUSTED")
            ]
        )
        monkeypatch.setattr(yt_radio, "TRANSPORT", transport)
        _single_track_playlist()
        sid, _ = yt_radio.add_subscriber()

        loop = _run_until_paused(stub)
        _stop_loop(loop, stub)
        yt_radio.remove_subscriber(sid)

        # Terminal: one attempt, no backoff sleep, no second track try.
        assert transport.pipeline_opens == 1
        backoff_waits = [w for w in stub.waits if w != yt_radio.PAUSE_POLL_SECONDS]
        assert backoff_waits == [], "a 407 must pause with zero retries, no backoff"
        assert "407" in yt_radio.PAUSE_INFO["reason"] or "traffic" in yt_radio.PAUSE_INFO["reason"].lower()
        assert "TRAFFIC_EXHAUSTED" in yt_radio.PAUSE_INFO["stderr_excerpt"]

    def test_traffic_exhausted_detection(self):
        assert yt_radio._traffic_exhausted("HTTP Error 407: TRAFFIC_EXHAUSTED")
        assert yt_radio._traffic_exhausted("ERROR: 407 Proxy Authentication Required")
        assert not yt_radio._traffic_exhausted("ERROR: HTTP Error 403: Forbidden")
        # A bare "407" inside unrelated stderr must NOT terminally pause.
        assert not yt_radio._traffic_exhausted("[download]   40.7% of 407.5MiB")
        assert not yt_radio._traffic_exhausted("")
        assert not yt_radio._traffic_exhausted(None)


# -- failure budget pauses the supervisor loop -------------------------------

class TestFailureBudgetPausesLoop:
    def test_five_consecutive_failures_pause_and_block_spawning(self, radio_state, monkeypatch):
        stub = _StubStop()
        monkeypatch.setattr(yt_radio, "RADIO_STOP", stub)
        transport = FakeTransport(spawn_error=OSError("proxy connection refused"))
        monkeypatch.setattr(yt_radio, "TRANSPORT", transport)
        _single_track_playlist()
        sid, _ = yt_radio.add_subscriber()

        loop = _run_until_paused(stub)
        time.sleep(0.3)  # while paused the loop must not spend on more attempts
        assert transport.pipeline_opens == 5, "paused supervisor must spawn nothing further"
        _stop_loop(loop, stub)
        yt_radio.remove_subscriber(sid)

    def test_successes_between_failures_defer_the_pause(self, radio_state, monkeypatch):
        stub = _StubStop()
        monkeypatch.setattr(yt_radio, "RADIO_STOP", stub)
        # f f ok ok f f — consecutive peaks at 2; the rolling window sees 4.
        transport = FakeTransport(
            pipelines=[
                _fail_pipeline(), _fail_pipeline(),
                _ok_pipeline(), _ok_pipeline(),
                _fail_pipeline(), _fail_pipeline(),
            ]
        )
        monkeypatch.setattr(yt_radio, "TRANSPORT", transport)
        _single_track_playlist()
        sid, _ = yt_radio.add_subscriber()

        loop = threading.Thread(target=yt_radio._radio_loop, daemon=True)
        loop.start()
        assert _wait_until(lambda: transport.pipeline_opens >= 6)
        assert not yt_radio.PAUSED.is_set(), "4 failures with successes between must not pause"
        _stop_loop(loop, stub)
        yt_radio.remove_subscriber(sid)

    def test_resume_clears_pause_and_budget_and_keeps_playing(self, radio_state, monkeypatch):
        stub = _StubStop()
        monkeypatch.setattr(yt_radio, "RADIO_STOP", stub)
        chunk = b"\xff\xfb" + b"y" * 100
        transport = FakeTransport(pipelines=[_fail_pipeline()] * 5)
        transport.add_pipeline(_ok_pipeline(chunk))
        monkeypatch.setattr(yt_radio, "TRANSPORT", transport)
        _single_track_playlist()
        sid, q = yt_radio.add_subscriber()

        loop = _run_until_paused(stub)
        assert yt_radio._FAILURE_BUDGET.consecutive_failures() == 5

        yt_radio.resume_radio()
        assert not yt_radio.PAUSED.is_set()
        assert yt_radio._FAILURE_BUDGET.consecutive_failures() == 0, "resume must grant a fresh budget"

        assert _wait_until(lambda: q.qsize() > 0), "after resume the loop must stream again"
        _stop_loop(loop, stub)
        yt_radio.remove_subscriber(sid)


# -- failure detection keys on yt-dlp's exit code, never ffmpeg's ------------

class TestFailureDetectionKeysOnYtdlpExitCode:
    def test_ytdlp_nonzero_exit_is_a_track_failure(self, radio_state, monkeypatch):
        transport = FakeTransport(pipelines=[_fail_pipeline(stderr="ERROR: boom")])
        monkeypatch.setattr(yt_radio, "TRANSPORT", transport)
        _single_track_playlist()

        with pytest.raises(yt_radio.TrackFailure) as excinfo:
            list(yt_radio._stream_track(0, TRACK_URL))

        assert excinfo.value.ytdlp_returncode == 1
        assert excinfo.value.ytdlp_stderr == "ERROR: boom"

    def test_ffmpeg_exit_code_never_causes_failure(self, radio_state, monkeypatch):
        chunk = b"\xff\xfb" + b"z" * 100
        # ffmpeg died ugly, but yt-dlp exited 0: not a failure.
        pipeline = FakePipeline(chunks=[chunk], ytdlp_returncode=0, ffmpeg_returncode=1)
        transport = FakeTransport(pipelines=[pipeline])
        monkeypatch.setattr(yt_radio, "TRANSPORT", transport)
        _single_track_playlist()

        assert list(yt_radio._stream_track(0, TRACK_URL)) == [chunk]

    def test_read_error_is_not_masked_by_track_failure(self, radio_state, monkeypatch):
        transport = FakeTransport(pipelines=[FakePipeline(chunks=[])])
        transport._pipelines[0].stdout = _ExplodingReader()
        monkeypatch.setattr(yt_radio, "TRANSPORT", transport)
        _single_track_playlist()

        # The read error propagates as itself — the yt-dlp exit-code
        # classification must not replace the original traceback.
        with pytest.raises(OSError, match="read failed"):
            list(yt_radio._stream_track(0, TRACK_URL))

    def test_loop_does_not_count_ffmpeg_failures(self, radio_state, monkeypatch):
        stub = _StubStop()
        monkeypatch.setattr(yt_radio, "RADIO_STOP", stub)
        transport = FakeTransport(
            pipelines=[
                FakePipeline(chunks=[b"\xff\xfb" + b"z" * 10], ytdlp_returncode=0, ffmpeg_returncode=1)
                for _ in range(5)
            ]
        )
        monkeypatch.setattr(yt_radio, "TRANSPORT", transport)
        _single_track_playlist()
        sid, _ = yt_radio.add_subscriber()

        loop = threading.Thread(target=yt_radio._radio_loop, daemon=True)
        loop.start()
        assert _wait_until(lambda: transport.pipeline_opens >= 5)
        assert not yt_radio.PAUSED.is_set(), "ffmpeg exit codes must never feed the failure budget"
        assert yt_radio._FAILURE_BUDGET.consecutive_failures() == 0
        _stop_loop(loop, stub)
        yt_radio.remove_subscriber(sid)


# -- stderr captured at failure time, available to the pause path ------------

class TestStderrCapture:
    def test_failure_captures_stderr_for_the_pause_path(self, radio_state, monkeypatch):
        stub = _StubStop()
        monkeypatch.setattr(yt_radio, "RADIO_STOP", stub)
        transport = FakeTransport(pipelines=[_fail_pipeline()] * 5)
        monkeypatch.setattr(yt_radio, "TRANSPORT", transport)
        _single_track_playlist()
        sid, _ = yt_radio.add_subscriber()

        loop = _run_until_paused(stub)
        _stop_loop(loop, stub)
        yt_radio.remove_subscriber(sid)

        assert "403" in yt_radio.LAST_TRACK_FAILURE["stderr"]
        assert "403" in yt_radio.PAUSE_INFO["stderr_excerpt"]
        assert yt_radio.LAST_TRACK_FAILURE["ytdlp_returncode"] == 1
        assert yt_radio.LAST_TRACK_FAILURE["url"] == TRACK_URL

    def test_pause_info_records_the_counters_that_triggered_it(self, radio_state, monkeypatch):
        stub = _StubStop()
        monkeypatch.setattr(yt_radio, "RADIO_STOP", stub)
        transport = FakeTransport(spawn_error=OSError("proxy down"))
        monkeypatch.setattr(yt_radio, "TRANSPORT", transport)
        _single_track_playlist()
        sid, _ = yt_radio.add_subscriber()

        loop = _run_until_paused(stub)
        _stop_loop(loop, stub)
        yt_radio.remove_subscriber(sid)

        assert yt_radio.PAUSE_INFO["consecutive_failures"] == 5
        assert yt_radio.PAUSE_INFO["failures_in_window"] == 5
        assert "budget" in yt_radio.PAUSE_INFO["reason"].lower()
