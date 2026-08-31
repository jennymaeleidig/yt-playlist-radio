"""Unit tests for pause semantics (issue 05): the alert email (through the
mailer seam — a FakeMailer here and a pinned HTTP seam for the real Resend
mailer, never real email), auto-resume after the cooldown with a fresh
failure budget, and re-pause + re-alert when the proxy is still broken after
resuming.

SMTP/Resend config is read from the environment by the real mailer
(alerts.py); the API key must never appear in logs.
"""
import time

import pytest

import yt_radio
from tests.unit.test_cost_guardrails import (
    _fail_pipeline,
    _ok_pipeline,
    _run_until_paused,
    _single_track_playlist,
    _stop_loop,
    _StubStop,
    _wait_until,
)
from tests.unit.test_transport_seam import FakeTransport

pytestmark = pytest.mark.unit


class FakeMailer:
    """The mailer seam, faked: records sends, never sends real email."""

    def __init__(self, fail=False):
        self.sent = []
        self.fail = fail

    def send(self, subject, body):
        if self.fail:
            return False
        self.sent.append((subject, body))
        return True


def _stop_and_remove(loop, stub, sid):
    """Stop the loop thread and clean up the subscriber it streamed to."""
    _stop_loop(loop, stub)
    yt_radio.remove_subscriber(sid)


# -- pause triggers exactly one alert email -----------------------------------

class TestPauseAlertEmail:
    def test_pause_sends_exactly_one_alert_with_stderr_and_counters(
        self, radio_state, monkeypatch
    ):
        mailer = FakeMailer()
        monkeypatch.setattr(yt_radio, "MAILER", mailer)
        stub = _StubStop()
        monkeypatch.setattr(yt_radio, "RADIO_STOP", stub)
        transport = FakeTransport(pipelines=[_fail_pipeline()] * 5)
        monkeypatch.setattr(yt_radio, "TRANSPORT", transport)
        _single_track_playlist()
        sid, _ = yt_radio.add_subscriber()

        loop = _run_until_paused(stub)
        # A second pause call while already paused must not re-alert.
        yt_radio._pause_radio("duplicate pause should be ignored")
        _stop_and_remove(loop, stub, sid)

        assert len(mailer.sent) == 1, "one alert email per pause"
        subject, body = mailer.sent[0]
        assert "paused" in subject.lower()
        assert "failure budget" in body
        assert "403" in body, "alert must contain the last yt-dlp stderr excerpt"
        assert "Consecutive failures: 5" in body
        assert "Failures in the last 10 min: 5" in body
        assert "auto-resumes" in body.lower()

    def test_pause_without_configured_mailer_still_pauses(self, radio_state, monkeypatch):
        monkeypatch.setattr(yt_radio, "MAILER", None)
        stub = _StubStop()
        monkeypatch.setattr(yt_radio, "RADIO_STOP", stub)
        transport = FakeTransport(spawn_error=OSError("proxy down"))
        monkeypatch.setattr(yt_radio, "TRANSPORT", transport)
        _single_track_playlist()
        sid, _ = yt_radio.add_subscriber()

        loop = _run_until_paused(stub)
        _stop_and_remove(loop, stub, sid)

        assert yt_radio.PAUSED.is_set()

    def test_mailer_failure_does_not_prevent_the_pause(self, radio_state, monkeypatch):
        monkeypatch.setattr(yt_radio, "MAILER", FakeMailer(fail=True))
        stub = _StubStop()
        monkeypatch.setattr(yt_radio, "RADIO_STOP", stub)
        transport = FakeTransport(spawn_error=OSError("proxy down"))
        monkeypatch.setattr(yt_radio, "TRANSPORT", transport)
        _single_track_playlist()
        sid, _ = yt_radio.add_subscriber()

        loop = _run_until_paused(stub)
        _stop_and_remove(loop, stub, sid)

        assert yt_radio.PAUSED.is_set(), "a mailer outage must never un-guard the supervisor"


# -- auto-resume after the cooldown -------------------------------------------

class TestAutoResume:
    def test_auto_resume_after_cooldown_with_fresh_budget(self, radio_state, monkeypatch):
        mailer = FakeMailer()
        monkeypatch.setattr(yt_radio, "MAILER", mailer)
        stub = _StubStop()
        monkeypatch.setattr(yt_radio, "RADIO_STOP", stub)
        transport = FakeTransport(pipelines=[_fail_pipeline()] * 5)
        transport.add_pipeline(_ok_pipeline(b"\xff\xfb" + b"y" * 100))
        monkeypatch.setattr(yt_radio, "TRANSPORT", transport)
        _single_track_playlist()
        sid, q = yt_radio.add_subscriber()

        loop = _run_until_paused(stub)
        # Make the cooldown look elapsed; a working pipeline is queued so the
        # resumed supervisor streams again.
        yt_radio._PAUSED_AT_MONOTONIC = time.monotonic() - 10**9

        assert _wait_until(lambda: not yt_radio.PAUSED.is_set()), "cooldown must auto-resume"
        assert _wait_until(lambda: q.qsize() > 0), "resumed supervisor must stream again"
        assert yt_radio._FAILURE_BUDGET.consecutive_failures() == 0, "auto-resume grants a fresh budget"
        _stop_and_remove(loop, stub, sid)
        assert len(mailer.sent) == 1

    def test_cooldown_not_elapsed_keeps_the_pause(self, radio_state, monkeypatch):
        monkeypatch.setattr(yt_radio, "MAILER", FakeMailer())
        stub = _StubStop()
        monkeypatch.setattr(yt_radio, "RADIO_STOP", stub)
        transport = FakeTransport(pipelines=[_fail_pipeline()] * 5)
        monkeypatch.setattr(yt_radio, "TRANSPORT", transport)
        _single_track_playlist()
        sid, _ = yt_radio.add_subscriber()

        loop = _run_until_paused(stub)
        yt_radio._PAUSED_AT_MONOTONIC = time.monotonic()  # cooldown just started

        time.sleep(0.3)
        assert yt_radio.PAUSED.is_set(), "pause must hold for the full cooldown"
        assert transport.pipeline_opens == 5
        _stop_and_remove(loop, stub, sid)

    def test_resuming_on_still_broken_proxy_repauses_and_realerts(
        self, radio_state, monkeypatch
    ):
        mailer = FakeMailer()
        monkeypatch.setattr(yt_radio, "MAILER", mailer)
        stub = _StubStop()
        monkeypatch.setattr(yt_radio, "RADIO_STOP", stub)
        # Budget pause on 403s, then — after the cooldown — the proxy answers
        # with a terminal 407 again.
        transport = FakeTransport(pipelines=[_fail_pipeline()] * 5)
        transport.add_pipeline(
            _fail_pipeline(stderr="ERROR: HTTP Error 407: TRAFFIC_EXHAUSTED")
        )
        monkeypatch.setattr(yt_radio, "TRANSPORT", transport)
        _single_track_playlist()
        sid, _ = yt_radio.add_subscriber()

        loop = _run_until_paused(stub)
        assert len(mailer.sent) == 1
        yt_radio._PAUSED_AT_MONOTONIC = time.monotonic() - 10**9

        # Resume and re-pause happen within one loop iteration, so poll for
        # the re-pause's evidence: the extra attempt and the rewritten reason.
        assert _wait_until(
            lambda: transport.pipeline_opens >= 6
            and "407" in yt_radio.PAUSE_INFO.get("reason", "")
        ), "still-broken proxy must re-pause after resuming"
        _stop_and_remove(loop, stub, sid)

        # Re-pause is terminal-407: exactly one more attempt (not a hot loop),
        # and a second alert went out.
        assert transport.pipeline_opens == 6
        assert len(mailer.sent) == 2
        assert "407" in mailer.sent[1][1] or "traffic" in mailer.sent[1][0].lower()


# -- the Resend mailer seam (alerts.py) ----------------------------------------

class TestResendMailer:
    def _configured(self, **overrides):
        from alerts import ResendMailer

        env = {
            "RESEND_API_KEY": "re_sekret_api_key",
            "ALERT_EMAIL_FROM": "radio@example.com",
            "ALERT_EMAIL_TO": "ops@example.com",
        }
        env.update(overrides)
        return ResendMailer.from_env(env), env

    def test_from_env_requires_key_sender_and_recipient(self):
        from alerts import ResendMailer

        assert ResendMailer.from_env({}) is None
        assert ResendMailer.from_env({"RESEND_API_KEY": "k"}) is None
        assert ResendMailer.from_env({"RESEND_API_KEY": "k", "ALERT_EMAIL_FROM": "f"}) is None
        mailer, _ = self._configured()
        assert mailer is not None
        assert mailer.to_addr == "ops@example.com"

    def test_send_posts_json_to_resend_api(self, monkeypatch):
        import alerts

        mailer, _ = self._configured()
        captured = {}

        def fake_post(api_key, payload):
            captured["api_key"] = api_key
            captured["payload"] = payload

        monkeypatch.setattr(alerts, "_resend_post", fake_post)

        assert mailer.send("subj", "body text") is True

        assert captured["api_key"] == "re_sekret_api_key"
        assert captured["payload"]["from"] == "radio@example.com"
        assert captured["payload"]["to"] == ["ops@example.com"]
        assert captured["payload"]["subject"] == "subj"
        assert captured["payload"]["text"] == "body text"

    def test_send_returns_false_on_api_error_and_never_logs_key(
        self, monkeypatch, caplog
    ):
        import alerts

        mailer, _ = self._configured()

        def boom(api_key, payload):
            raise alerts.urllib.error.HTTPError(
                alerts.RESEND_API_URL, 422,
                "validation error", {}, None,
            )

        monkeypatch.setattr(alerts, "_resend_post", boom)

        with caplog.at_level("ERROR"):
            assert mailer.send("subj", "body") is False

        assert all("re_sekret_api_key" not in r.getMessage() for r in caplog.records)

    def test_send_swallows_network_errors(self, monkeypatch):
        import alerts

        mailer, _ = self._configured()

        def boom(api_key, payload):
            raise OSError("network unreachable")

        monkeypatch.setattr(alerts, "_resend_post", boom)

        assert mailer.send("subj", "body") is False
