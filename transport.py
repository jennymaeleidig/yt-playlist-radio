"""The transport seam: every yt-dlp / ffmpeg subprocess spawn in the app
flows through a single Transport object.

yt_radio never touches `subprocess` directly — it calls
`TRANSPORT.run_ytdlp(...)` for synchronous metadata dumps and
`TRANSPORT.open_track_pipeline(...)` for the media path. Unit tests replace
the transport wholesale with a fake that scripts exit codes, slow/dead
tracks, and spawn failures, so no test has to assert on raw argv.

yt-dlp is resolved from the venv exclusively: it is invoked as
`<venv interpreter> -m yt_dlp` (i.e. `sys.executable -m yt_dlp`), never as a
PATH binary, so a stray system yt-dlp of a different vintage cannot cause
version-skew ghost failures.

ffmpeg is still looked up on PATH (it is a system binary provisioned with the
app, not a Python dependency) — only yt-dlp is venv-pinned.
"""
import hashlib
import subprocess
import sys
import tempfile
import urllib.parse
from typing import NamedTuple, Optional

# Seconds before a synchronous metadata dump is abandoned. Matches the
# previously hard-coded timeout; a hung yt-dlp must not stall the producer.
YTDLP_METADATA_TIMEOUT = 30

# --- Residential proxy (DataImpulse) ---------------------------------------
# docs.dataimpulse.com: the HTTP gateway is gw.dataimpulse.com:823 (rotating);
# sticky sessions are PORT-based — ports 10000-20000 hold one exit IP for
# 1-120 minutes (default ~30). There is no sessid username parameter, so a
# per-track sticky session is a deterministic sticky port derived from the
# track URL: metadata extraction and the media download for one track use the
# same port, hence the same exit IP.
DATAIMPULSE_HOST = "gw.dataimpulse.com"
DATAIMPULSE_ROTATING_PORT = 823
STICKY_PORT_MIN = 10000
STICKY_PORT_MAX = 20000

# Mandated flag baseline for proxied invocations (issue 03): bounded chunk
# size, modest retry budgets with backoff (never immediate retry loops), and
# a bounded socket timeout. Applied only when the proxy is configured —
# without credentials the app runs direct exactly as before.
PROXIED_FLAG_BASELINE = [
    "--http-chunk-size", "10M",
    "--retries", "3",
    "--fragment-retries", "3",
    "--retry-sleep", "linear=1:30",
    "--socket-timeout", "20",
]
# Metadata phase only: extraction gets a wider retry budget than downloads.
PROXIED_EXTRACTOR_RETRIES = ["--extractor-retries", "5"]

# Python-API (YoutubeDL) equivalents of the baseline, for the in-process
# playlist listing. `linear=1:30` backoff has no clean API equivalent; the
# retry budgets and timeouts still apply.
PROXIED_YDL_API_OPTS = {
    "http_chunk_size": 10 * 1024 * 1024,
    "socket_timeout": 20,
    "retries": 3,
    "extractor_retries": 5,
}


def build_dataimpulse_proxy_url(user, password) -> Optional[str]:
    """Build the rotating-gateway proxy URL from env credentials.

    Returns None when either credential is missing — the caller then runs
    direct. Credentials are URL-quoted so special characters cannot break
    the URL. Never hardcode credentials anywhere else.
    """
    if not user or not password:
        return None
    quoted_user = urllib.parse.quote(str(user), safe="")
    quoted_password = urllib.parse.quote(str(password), safe="")
    return f"http://{quoted_user}:{quoted_password}@{DATAIMPULSE_HOST}:{DATAIMPULSE_ROTATING_PORT}"


def sticky_port_for_track(track_key: str) -> int:
    """Deterministic sticky port for a track (10000-20000). Same track key
    always maps to the same port, so metadata + media share one exit IP."""
    digest = hashlib.sha256(track_key.encode("utf-8")).digest()
    value = int.from_bytes(digest[:8], "big")
    return STICKY_PORT_MIN + value % (STICKY_PORT_MAX - STICKY_PORT_MIN + 1)


def proxied_ydl_opts(opts: dict, proxy_url: str) -> dict:
    """Apply the proxy and the mandated baseline's Python-API equivalents to
    a YoutubeDL opts dict (returns a copy)."""
    out = dict(opts)
    out.update(PROXIED_YDL_API_OPTS)
    out["proxy"] = proxy_url
    return out


def _sticky_proxy_url(proxy_url: str, track_key: str) -> str:
    """Rewrite the proxy URL's port to the track's sticky port, preserving
    credentials and host."""
    parts = urllib.parse.urlsplit(proxy_url)
    netloc = parts.hostname
    if parts.username:
        userinfo = parts.username
        if parts.password:
            userinfo += f":{parts.password}"
        netloc = f"{userinfo}@{netloc}"
    netloc = f"{netloc}:{sticky_port_for_track(track_key)}"
    return urllib.parse.urlunsplit(parts._replace(netloc=netloc))


class PipelineStderr(NamedTuple):
    """Captured stderr of the pipeline's children, in fixed order."""

    ffmpeg: str
    ytdlp: str


# Seconds to wait for yt-dlp to exit on its own at close() before killing it.
# Its stdout EOF is what normally ends the stream, so it has usually exited
# already; the grace wait lets close() capture its REAL exit code instead of
# a kill signal. Cost guardrails key on this code (see yt_radio.TrackFailure).
YTDLP_EXIT_GRACE_SECONDS = 2.0


def _reap(proc, wait_timeout=0.0):
    """Wait out a child (up to `wait_timeout` seconds), then kill and wait.
    Returns the child's exit code: the real one if it exited inside the wait
    window, a negative signal value if it had to be killed, or None if the
    code could not be obtained. Tolerates anything."""
    if wait_timeout > 0:
        try:
            return proc.wait(timeout=wait_timeout)
        except Exception:
            pass
    try:
        proc.kill()
    except Exception:
        pass
    try:
        proc.wait(timeout=10)
    except Exception:
        pass
    return proc.returncode


class TrackPipeline:
    """A live `yt-dlp | ffmpeg` pipeline for one track.

    `stdout` is ffmpeg's transcoded MP3 stream — the supervisor reads it in
    chunks; an EOF means the track is done (or died). The pipeline owns the
    child processes and their captured stderr; `close()` kills and reaps the
    children, drains stderr, and releases the temp files. Call it exactly
    once (it is idempotent and safe).
    """

    def __init__(self, ytdlp_proc, ffmpeg_proc, ytdlp_err, ffmpeg_err):
        self.ytdlp = ytdlp_proc
        self.ffmpeg = ffmpeg_proc
        self.stdout = ffmpeg_proc.stdout
        self._ytdlp_err = ytdlp_err
        self._ffmpeg_err = ffmpeg_err
        self._closed = False
        self._ytdlp_returncode = None
        self._ffmpeg_returncode = None

    @property
    def ytdlp_returncode(self):
        """yt-dlp's exit code, meaningful after close(). Failure decisions
        key on THIS code — never on ffmpeg's."""
        return self._ytdlp_returncode

    @property
    def ffmpeg_returncode(self):
        """ffmpeg's exit code, meaningful after close(). Deliberately not
        used for failure decisions: ffmpeg's failures mirror yt-dlp's or are
        irrelevant (early teardown)."""
        return self._ffmpeg_returncode

    def close(self) -> PipelineStderr:
        """Kill and reap both children; return their captured stderr
        (empty strings when silent)."""
        if self._closed:
            return PipelineStderr("", "")
        self._closed = True
        # Reap yt-dlp first with a short grace wait so its real exit code is
        # captured; ffmpeg is killed outright (nothing keys on its code).
        self._ytdlp_returncode = _reap(self.ytdlp, wait_timeout=YTDLP_EXIT_GRACE_SECONDS)
        self._ffmpeg_returncode = _reap(self.ffmpeg)
        texts = []
        for err_file in (self._ffmpeg_err, self._ytdlp_err):
            text = ""
            try:
                err_file.seek(0)
                text = err_file.read().decode("utf-8", errors="replace")
            except Exception:
                pass
            finally:
                try:
                    err_file.close()
                except Exception:
                    pass
            texts.append(text)
        return PipelineStderr(texts[0], texts[1])


class Transport:
    """The one place where subprocesses are spawned. See module docstring.

    When `proxy_url` is set, every yt-dlp spawn rides the proxy and the
    mandated flag baseline is applied; COOKIES_FILE is structurally excluded
    (the argv builder has no branch that combines --proxy with --cookies).
    When it is None, behaviour is exactly the pre-proxy direct mode.
    """

    def __init__(self, cookies_file=None, proxy_url=None):
        self.cookies_file = cookies_file
        self.proxy_url = proxy_url

    @property
    def proxied(self) -> bool:
        return self.proxy_url is not None

    def yt_dlp_argv(self, args, sticky_key=None):
        """Full argv for a yt-dlp invocation: the venv interpreter running
        the yt_dlp module — never a PATH binary.

        Fail-closed by construction: with a proxy configured, --proxy is on
        every spawn, --cookies is unreachable, and a spawn without a
        sticky_key is refused — per-track sticky sessions are the exit-IP
        guarantee, so a silent fall back to the rotating gateway is not
        allowed. Without a proxy, this is the direct argv exactly as before.
        """
        argv = [sys.executable, "-m", "yt_dlp"]
        if self.proxy_url:
            if sticky_key is None:
                raise ValueError(
                    "sticky_key is required for proxied yt-dlp spawns — "
                    "refusing to fetch without the track's sticky session"
                )
            argv += ["--proxy", _sticky_proxy_url(self.proxy_url, sticky_key)]
            argv += PROXIED_FLAG_BASELINE
        elif self.cookies_file:
            argv += ["--cookies", self.cookies_file]
        return argv + list(args)

    def run_ytdlp(self, args, timeout=YTDLP_METADATA_TIMEOUT, sticky_key=None):
        """Synchronous yt-dlp run (metadata dumps). Returns a CompletedProcess
        with text stdout/stderr; raises subprocess.TimeoutExpired on timeout.
        `sticky_key` (a track URL) pins the spawn to that track's sticky
        session so metadata and media share one exit IP."""
        argv = self.yt_dlp_argv(args, sticky_key=sticky_key)
        if self.proxy_url:
            argv += PROXIED_EXTRACTOR_RETRIES
        return subprocess.run(
            argv,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )

    def open_track_pipeline(self, url, *, ytdlp_format, bitrate_kbps):
        """Spawn yt-dlp (media to stdout) piped into ffmpeg (MP3 transcode to
        stdout) and return the live TrackPipeline.

        The caller must close() the pipeline exactly once. If either spawn
        fails, both children (if any) are cleaned up and the exception
        propagates — the supervisor treats it as a skipped track.
        """
        ytdlp_err = tempfile.TemporaryFile()
        ffmpeg_err = tempfile.TemporaryFile()

        ytdlp = subprocess.Popen(
            self.yt_dlp_argv(["-f", ytdlp_format, "-o", "-", url], sticky_key=url),
            stdout=subprocess.PIPE,
            stderr=ytdlp_err,
        )
        try:
            ffmpeg = subprocess.Popen(
                [
                    "ffmpeg",
                    "-hide_banner",
                    "-loglevel", "error",
                    "-i", "pipe:0",
                    "-f", "mp3",
                    "-ab", f"{bitrate_kbps}k",
                    "-ar", "44100",
                    "-ac", "2",
                    "pipe:1",
                ],
                stdin=ytdlp.stdout,
                stdout=subprocess.PIPE,
                stderr=ffmpeg_err,
            )
        except BaseException:
            _reap(ytdlp)
            if ytdlp.stdout:
                ytdlp.stdout.close()
            ytdlp_err.close()
            ffmpeg_err.close()
            raise
        # The parent keeps its own copy of yt-dlp's stdout open only so ffmpeg
        # can inherit it; close it here so EOF propagates when yt-dlp exits.
        if ytdlp.stdout:
            ytdlp.stdout.close()

        return TrackPipeline(ytdlp, ffmpeg, ytdlp_err, ffmpeg_err)
