"""Unit tests for proxied fetch routing (issue 03).

Two layers are tested:

1. Transport level — the REAL Transport with subprocess monkeypatched, asserting
   on the built argv. This is where the mandated flag baseline, the cookies
   exclusion, and the fail-closed proxy invariant live, so here argv assertions
   ARE the spec (mirroring the venv-pinning tests from issue 02).
2. Supervisor level — yt_radio through the FAKE transport, scripting outcomes;
   these tests verify which format chain / sticky key the supervisor requests
   without looking at argv at all.

DataImpulse semantics (docs.dataimpulse.com): sticky sessions are PORT-based —
ports 10000-20000 hold one exit IP for 1-120 min; port 823 is the rotating
HTTP gateway. There is no sessid username parameter, so a per-track sticky
session is a deterministic sticky port derived from the track URL.
"""
import subprocess
import urllib.parse

import pytest

import transport as transport_mod
import yt_radio
from transport import Transport, build_dataimpulse_proxy_url

pytestmark = pytest.mark.unit

PROXY_URL = build_dataimpulse_proxy_url("user@example.com", "p@ss w/rd")
TRACK_URL = "https://www.youtube.com/watch?v=aaaaaaaaaaa"
OTHER_URL = "https://www.youtube.com/watch?v=bbbbbbbbbbb"

# the mandated flag baseline (issue 03), spelled out for assertion
BASELINE = [
    "--http-chunk-size", "10M",
    "--retries", "3",
    "--fragment-retries", "3",
    "--retry-sleep", "linear=1:30",
    "--socket-timeout", "20",
]


def _captured_run(monkeypatch):
    captured = {}

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        return subprocess.CompletedProcess(argv, 0, stdout="{}", stderr="")

    monkeypatch.setattr(transport_mod.subprocess, "run", fake_run)
    return captured


def _captured_popen(monkeypatch):
    captured = {"argvs": []}

    class FakeProc:
        def __init__(self):
            self.stdout = None

        def kill(self):
            pass

        def wait(self):
            return 0

    def fake_popen(argv, **kwargs):
        captured["argvs"].append(argv)
        return FakeProc()

    monkeypatch.setattr(transport_mod.subprocess, "Popen", fake_popen)
    return captured


# -- proxy URL construction ---------------------------------------------------

class TestProxyUrlConstruction:
    def test_none_when_credentials_missing(self):
        assert build_dataimpulse_proxy_url(None, None) is None
        assert build_dataimpulse_proxy_url("user", "") is None
        assert build_dataimpulse_proxy_url("", "pass") is None

    def test_built_from_credentials_not_hardcoded(self):
        url = build_dataimpulse_proxy_url("someuser", "somepass")
        assert url.startswith("http://someuser:somepass@")
        assert url.endswith("@gw.dataimpulse.com:823")

    def test_credentials_are_url_quoted(self):
        url = build_dataimpulse_proxy_url("user@example.com", "p@ss w/rd")
        # the URL must parse; special chars in creds must not break it
        parts = urllib.parse.urlsplit(url)
        assert parts.hostname == "gw.dataimpulse.com"
        assert parts.username == "user%40example.com" or parts.username == "user@example.com"
        assert parts.password is not None


# -- fail-closed invariant + mandated baseline (proxied argv) ------------------

class TestProxiedArgv:
    def test_proxy_present_and_cookies_excluded_on_every_proxied_spawn(self, monkeypatch):
        """Fail-closed: when the proxy is configured, EVERY spawn rides it and
        cookies are structurally unreachable — there is no argv this transport
        can build that is both proxied and cookied, or unproxied."""
        t = Transport(cookies_file="/tmp/cookies.txt", proxy_url=PROXY_URL)
        captured = _captured_run(monkeypatch)

        t.run_ytdlp(["--dump-json", TRACK_URL], sticky_key=TRACK_URL)
        argv = captured["argv"]
        assert "--proxy" in argv
        assert "--cookies" not in argv
        assert argv[argv.index("--proxy") + 1].startswith("http://")

    def test_baseline_flags_on_metadata_spawn(self, monkeypatch):
        t = Transport(cookies_file="/tmp/cookies.txt", proxy_url=PROXY_URL)
        captured = _captured_run(monkeypatch)

        t.run_ytdlp(["--dump-json", TRACK_URL], sticky_key=TRACK_URL)
        argv = captured["argv"]
        for flag in BASELINE:
            assert flag in argv, f"mandated baseline flag missing: {flag}"
        assert argv[argv.index("--http-chunk-size") + 1] == "10M"
        # metadata phase gets the extractor retry budget
        assert argv[argv.index("--extractor-retries") + 1] == "5"

    def test_baseline_flags_on_media_spawn_without_extractor_retries(self, monkeypatch):
        t = Transport(proxy_url=PROXY_URL)
        captured = _captured_popen(monkeypatch)

        t.open_track_pipeline(TRACK_URL, ytdlp_format="bestaudio", bitrate_kbps=192)
        media_argv = captured["argvs"][0]
        for flag in BASELINE:
            assert flag in media_argv, f"mandated baseline flag missing: {flag}"
        assert "--extractor-retries" not in media_argv, "media phase must not get the metadata retry budget"
        assert "--cookies" not in media_argv

    def test_proxied_spawn_without_sticky_key_is_refused(self):
        """Fail-closed: a proxied spawn without a sticky key must fail loudly,
        never silently fall back to the rotating gateway (which would break
        the metadata+media-share-one-exit-IP guarantee)."""
        t = Transport(proxy_url=PROXY_URL)
        with pytest.raises(ValueError, match="sticky_key"):
            t.yt_dlp_argv(["--dump-json", TRACK_URL])

    def test_direct_mode_argv_unchanged_by_baseline(self, monkeypatch):
        """Without credentials, the app runs direct exactly as before: no
        --proxy, no baseline flags, cookies preserved."""
        t = Transport(cookies_file="/tmp/cookies.txt", proxy_url=None)
        captured = _captured_run(monkeypatch)

        t.run_ytdlp(["--dump-json", TRACK_URL])
        argv = captured["argv"]
        assert "--proxy" not in argv
        assert "--cookies" in argv
        for flag in BASELINE + ["--extractor-retries"]:
            assert flag not in argv


# -- per-track sticky session (port-based) --------------------------------------

class TestStickySessionPerTrack:
    def test_metadata_and_media_share_one_exit_ip_per_track(self, monkeypatch):
        """The sticky port (hence exit IP) for a track must be identical for
        its metadata fetch and its media download."""
        t = Transport(proxy_url=PROXY_URL)
        run_captured = _captured_run(monkeypatch)
        popen_captured = _captured_popen(monkeypatch)

        t.run_ytdlp(["--dump-json", TRACK_URL], sticky_key=TRACK_URL)
        t.open_track_pipeline(TRACK_URL, ytdlp_format="bestaudio", bitrate_kbps=192)

        meta_proxy = run_captured["argv"][run_captured["argv"].index("--proxy") + 1]
        media_proxy = popen_captured["argvs"][0][popen_captured["argvs"][0].index("--proxy") + 1]
        assert _port(meta_proxy) == _port(media_proxy), "metadata and media must share the track's sticky session"

    def test_sticky_ports_land_in_the_sticky_range(self, monkeypatch):
        t = Transport(proxy_url=PROXY_URL)
        captured = _captured_run(monkeypatch)

        for url in (TRACK_URL, OTHER_URL, "https://www.youtube.com/watch?v=ccccccccccc"):
            t.run_ytdlp(["--dump-json", url], sticky_key=url)
            proxy = captured["argv"][captured["argv"].index("--proxy") + 1]
            assert 10000 <= _port(proxy) <= 20000, "sticky sessions are port-based (10000-20000) on DataImpulse"

    def test_different_tracks_get_sticky_ports_not_the_rotating_port(self, monkeypatch):
        t = Transport(proxy_url=PROXY_URL)
        captured = _captured_run(monkeypatch)

        ports = set()
        for url in (TRACK_URL, OTHER_URL):
            t.run_ytdlp(["--dump-json", url], sticky_key=url)
            proxy = captured["argv"][captured["argv"].index("--proxy") + 1]
            ports.add(_port(proxy))
        assert 823 not in ports, "per-track fetches must not ride the rotating port"

    def test_sticky_port_is_deterministic_per_track(self):
        from transport import sticky_port_for_track
        assert sticky_port_for_track(TRACK_URL) == sticky_port_for_track(TRACK_URL)
        assert 10000 <= sticky_port_for_track(TRACK_URL) <= 20000

    def test_proxied_property(self):
        assert Transport(proxy_url=PROXY_URL).proxied is True
        assert Transport(proxy_url=None).proxied is False


def _port(proxy_url):
    return urllib.parse.urlsplit(proxy_url).port


# -- supervisor layer: format chain + sticky key via the FAKE transport ---------

class FakeTransport:
    def __init__(self, proxied=False):
        self.proxied = proxied
        self.requests = []

    def run_ytdlp(self, args, timeout=None, sticky_key=None):
        self.requests.append(("run_ytdlp", list(args), sticky_key))
        return subprocess.CompletedProcess(args, 0, stdout="{}", stderr="")

    def open_track_pipeline(self, url, *, ytdlp_format, bitrate_kbps):
        self.requests.append(("open_track_pipeline", url, ytdlp_format, bitrate_kbps))
        from tests.unit.test_transport_seam import FakePipeline
        return FakePipeline(chunks=[])


class TestSupervisorProxiedRouting:
    @pytest.fixture
    def radio_state(self):
        saved_playlist = yt_radio.PROXY_URL
        saved_cache = dict(yt_radio._CACHE)
        yt_radio.PROXY_URL = None
        yt_radio._CACHE.clear()  # the temp cache file persists across runs
        yield
        yt_radio.PROXY_URL = saved_playlist
        yt_radio._CACHE.clear()
        yt_radio._CACHE.update(saved_cache)

    def test_proxied_stream_uses_abr_capped_fallback_chain(self, radio_state, monkeypatch):
        fake = FakeTransport(proxied=True)
        monkeypatch.setattr(yt_radio, "TRANSPORT", fake)
        yt_radio.PLAYLIST = [TRACK_URL]
        yt_radio.METADATA[0] = {"title": "t", "artist": "a", "duration": 1, "id": "x"}

        list(yt_radio._stream_track(0, TRACK_URL))

        media_calls = [r for r in fake.requests if r[0] == "open_track_pipeline"]
        assert media_calls, "supervisor must request the pipeline through the transport"
        requested_format = media_calls[0][2]
        assert requested_format == yt_radio.PROXIED_FORMAT_CHAIN
        # the chain caps upstream audio at <=56 kbps first, then degrades gracefully
        assert requested_format.startswith("bestaudio[abr<=56]/")

    def test_direct_stream_keeps_legacy_format_chain(self, radio_state, monkeypatch):
        fake = FakeTransport(proxied=False)
        monkeypatch.setattr(yt_radio, "TRANSPORT", fake)
        yt_radio.PLAYLIST = [TRACK_URL]
        yt_radio.METADATA[0] = {"title": "t", "artist": "a", "duration": 1, "id": "x"}

        list(yt_radio._stream_track(0, TRACK_URL))

        media_calls = [r for r in fake.requests if r[0] == "open_track_pipeline"]
        assert media_calls[0][2] == yt_radio.YTDLP_FORMAT

    def test_metadata_fetch_passes_track_as_sticky_key(self, radio_state, monkeypatch):
        fake = FakeTransport(proxied=True)
        monkeypatch.setattr(yt_radio, "TRANSPORT", fake)

        yt_radio.fetch_metadata(0, TRACK_URL)

        run_calls = [r for r in fake.requests if r[0] == "run_ytdlp"]
        assert run_calls, "supervisor must fetch metadata through the transport"
        assert run_calls[0][2] == TRACK_URL, "sticky key must be the track URL so metadata + media share one exit IP"


# -- playlist listing rides the proxy -------------------------------------------

class TestPlaylistListingProxied:
    def test_youtube_dl_api_gets_proxy_option_when_configured(self, monkeypatch):
        captured = {}

        class FakeYoutubeDL:
            def __init__(self, opts):
                captured["opts"] = opts

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def extract_info(self, link, download=False):
                return {"entries": [{"id": "aaaaaaaaaaa"}]}

        monkeypatch.setattr(yt_radio, "YoutubeDL", FakeYoutubeDL)
        monkeypatch.setattr(yt_radio, "PROXY_URL", PROXY_URL)

        urls = yt_radio.convert_playlist_to_links("https://www.youtube.com/playlist?list=PL123")

        assert urls == ["https://www.youtube.com/watch?v=aaaaaaaaaaa"]
        opts = captured["opts"]
        assert opts.get("proxy") == PROXY_URL
        # the baseline's Python-API equivalents must reach the playlist path too
        assert opts["http_chunk_size"] <= 10 * 1024 * 1024
        assert opts["socket_timeout"] == 20
        assert opts["retries"] == 3
        assert opts["extractor_retries"] == 5

    def test_youtube_dl_api_has_no_proxy_option_in_direct_mode(self, monkeypatch):
        captured = {}

        class FakeYoutubeDL:
            def __init__(self, opts):
                captured["opts"] = opts

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def extract_info(self, link, download=False):
                return {"entries": [{"id": "aaaaaaaaaaa"}]}

        monkeypatch.setattr(yt_radio, "YoutubeDL", FakeYoutubeDL)
        monkeypatch.setattr(yt_radio, "PROXY_URL", None)

        yt_radio.convert_playlist_to_links("https://www.youtube.com/playlist?list=PL123")

        opts = captured["opts"]
        assert "proxy" not in opts
        assert "http_chunk_size" not in opts and "socket_timeout" not in opts
