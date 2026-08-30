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
import subprocess
import sys
import tempfile
from typing import NamedTuple

# Seconds before a synchronous metadata dump is abandoned. Matches the
# previously hard-coded timeout; a hung yt-dlp must not stall the producer.
YTDLP_METADATA_TIMEOUT = 30


class PipelineStderr(NamedTuple):
    """Captured stderr of the pipeline's children, in fixed order."""

    ffmpeg: str
    ytdlp: str


def _reap(proc):
    """Kill and wait out a child process, tolerating anything."""
    try:
        proc.kill()
    except Exception:
        pass
    try:
        proc.wait()
    except Exception:
        pass


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

    def close(self) -> PipelineStderr:
        """Kill and reap both children; return their captured stderr
        (empty strings when silent)."""
        if self._closed:
            return PipelineStderr("", "")
        self._closed = True
        _reap(self.ffmpeg)
        _reap(self.ytdlp)
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
    """The one place where subprocesses are spawned. See module docstring."""

    def __init__(self, cookies_file=None):
        self.cookies_file = cookies_file

    def yt_dlp_argv(self, args):
        """Full argv for a yt-dlp invocation: the venv interpreter running
        the yt_dlp module — never a PATH binary."""
        argv = [sys.executable, "-m", "yt_dlp"]
        if self.cookies_file:
            argv += ["--cookies", self.cookies_file]
        return argv + list(args)

    def run_ytdlp(self, args, timeout=YTDLP_METADATA_TIMEOUT):
        """Synchronous yt-dlp run (metadata dumps). Returns a CompletedProcess
        with text stdout/stderr; raises subprocess.TimeoutExpired on timeout."""
        return subprocess.run(
            self.yt_dlp_argv(args),
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
            self.yt_dlp_argv(["-f", ytdlp_format, "-o", "-", url]),
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
