"""Proxied-mode integration: real YouTube through the DataImpulse residential proxy.

⚠️ QUOTA WARNING — this module spends PAID proxy bandwidth (residential egress
is billed per GB). It is doubly opt-in:

1. deselected by default; run explicitly with `pytest -m proxied`
2. skips itself unless DATAIMPULSE_USER / DATAIMPULSE_PASS are configured
   (in the process env or the repo .env)

What it verifies, once, by hand (issue 03 evidence gate):
- the proxied transport fetches real per-track metadata through the proxy
- the proxied transport streams real media through the proxy
- no bot-wall is hit on the proxied path (evidence gate for the recorded
  future "cookies through the same proxy exit IP" path)

NOTE on test-env hygiene: tests/conftest.py pins DATAIMPULSE_* to empty so the
default run is always direct-mode; this module re-reads the repo .env itself
and reloads yt_radio with the proxy configured, then reloads it back.
"""
import importlib
import json
import os
import time
from pathlib import Path

import pytest

pytestmark = [pytest.mark.proxied, pytest.mark.timeout(300)]

PROJECT_ROOT = Path(__file__).resolve().parents[2]
STREAM_TARGET_BYTES = 256 * 1024


def _load_creds():
    """Proxy credentials from the process env or the repo .env (bools out; no
    values are printed)."""
    user = os.environ.get("DATAIMPULSE_USER")
    password = os.environ.get("DATAIMPULSE_PASS")
    if not (user and password):
        try:
            from dotenv import dotenv_values

            values = dotenv_values(PROJECT_ROOT / ".env")
            user = user or values.get("DATAIMPULSE_USER")
            password = password or values.get("DATAIMPULSE_PASS")
        except Exception:
            return None, None
    return user, password


@pytest.fixture(scope="module")
def proxied_radio():
    user, password = _load_creds()
    if not (user and password):
        pytest.skip(
            "DATAIMPULSE_USER/DATAIMPULSE_PASS not configured — proxied integration "
            "not opted in (this is the expected state for a plain test run)"
        )

    print(
        "\n⚠️  PROXIED INTEGRATION RUNNING: real YouTube traffic is riding the "
        "DataImpulse residential proxy and spending paid quota."
    )

    os.environ["DATAIMPULSE_USER"] = user
    os.environ["DATAIMPULSE_PASS"] = password
    import yt_radio

    importlib.reload(yt_radio)  # rebuild TRANSPORT with the proxy configured
    assert yt_radio.PROXY_URL, "reload must build the proxy URL from the credentials"
    assert yt_radio.TRANSPORT.proxied, "transport must be proxied after reload"
    yield yt_radio

    importlib.reload(yt_radio)  # restore direct-mode module state


def _first_track_url(radio):
    import file_util

    urls = file_util._load_urls_from_file(
        os.environ.get("PLAYLIST_URL", str(PROJECT_ROOT / "tests/fixtures/sample.radio"))
    )
    assert urls, "fixture playlist must yield at least one track URL"
    return urls[0]


def test_metadata_fetched_through_proxy(proxied_radio):
    radio = proxied_radio
    url = _first_track_url(radio)

    result = radio.TRANSPORT.run_ytdlp(["--dump-json", url], sticky_key=url)

    assert result.returncode == 0, f"proxied metadata fetch failed: {result.stderr[:400]}"
    data = json.loads(result.stdout)
    assert data.get("id"), "metadata through the proxy must carry the video id"
    assert data.get("title")


def test_media_streams_through_proxy(proxied_radio):
    radio = proxied_radio
    url = _first_track_url(radio)

    pipeline = radio.TRANSPORT.open_track_pipeline(
        url, ytdlp_format=radio.PROXIED_FORMAT_CHAIN, bitrate_kbps=radio.BITRATE_KBPS
    )
    total = 0
    start = time.monotonic()
    try:
        while total < STREAM_TARGET_BYTES and time.monotonic() - start < 120:
            chunk = pipeline.stdout.read(8192)
            if not chunk:
                break
            total += len(chunk)
    finally:
        ferr, yerr = pipeline.close()

    assert total >= 64 * 1024, (
        f"expected real media bytes through the proxy, got {total}; "
        f"ytdlp stderr: {yerr[:400]}"
    )
    # evidence gate: no bot-wall on the proxied path
    assert "Sign in to confirm" not in yerr and "403" not in yerr, (
        f"bot-wall hit on the proxied path — the recorded cookies-through-proxy "
        f"path would need building; stderr: {yerr[:400]}"
    )
