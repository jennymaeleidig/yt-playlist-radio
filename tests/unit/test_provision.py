"""Provisioning-script invariants (cloud-radio issue 06).

provision.sh targets Oracle Linux 9 and cannot be executed in the dev
sandbox (dnf, systemd, root). The agreed seam is therefore the script
itself: these tests pin the checklist facts as static assertions plus a
bash -n / shellcheck syntax gate, so a regression that drops an
idempotency guard, the 600 env-file mode, or the swap persistence fails
here before it fails on a fresh VM.
"""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
PROVISION = REPO_ROOT / "provision.sh"

REQUIRED_ENV_KEYS = (
    "PLAYLIST_URL",
    "BASE_URL",
    "RANDOMIZE_PLAYLIST",
    "DATAIMPULSE_USER",
    "DATAIMPULSE_PASS",
    "RESEND_API_KEY",
    "ALERT_EMAIL_FROM",
    "ALERT_EMAIL_TO",
)


@pytest.fixture(scope="module")
def script_text() -> str:
    assert PROVISION.exists(), "provision.sh missing from repo root"
    return PROVISION.read_text()


def test_bash_syntax():
    result = subprocess.run(
        ["bash", "-n", str(PROVISION)], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr


def test_shellcheck():
    if shutil.which("shellcheck") is None:
        pytest.skip("shellcheck not installed")
    result = subprocess.run(
        ["shellcheck", "--severity=warning", str(PROVISION)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_strict_mode(script_text):
    assert "set -euo pipefail" in script_text


def test_refuses_non_root(script_text):
    assert re.search(r"\[\[\s*\$EUID\s*-ne\s*0\s*\]\]", script_text), (
        "script must refuse to run as non-root"
    )


def test_ffmpeg_via_epel_and_rpmfusion(script_text):
    assert "oracle-epel-release-el9" in script_text, "OL9-native EPEL enablement missing"
    assert "rpmfusion-free-release-9" in script_text, "RPM Fusion free missing"
    assert re.search(r"dnf install -y .*ffmpeg", script_text), "ffmpeg install missing"


def test_uv_and_python_313(script_text):
    assert "uv" in script_text
    assert "uv python install 3.13" in script_text


def test_deps_synced_for_radio_user(script_text):
    # uv sync runs as the radio user inside the app dir (heredoc or inline).
    assert "uv sync" in script_text
    assert re.search(
        r"runuser -u \"?\$APP_USER\"? --|runuser -u radio --|sudo -u radio", script_text
    )


def test_radio_user_created_once(script_text):
    # Idempotency: useradd is guarded so a re-run doesn't collide.
    assert re.search(r'id -u "?\$APP_USER\"?|id -u radio|id radio', script_text), (
        "useradd must be guarded by an existence check"
    )
    assert "useradd" in script_text


def test_swapfile_two_gb_and_persisted(script_text):
    assert re.search(r"SWAP_SIZE_MB=2048|count=2048", script_text), (
        "2 GB swapfile missing"
    )
    assert re.search(r"mkswap", script_text)
    assert re.search(r"swapon", script_text)
    # fstab persistence is guarded so re-runs don't append a duplicate line.
    assert re.search(r"grep -q[^\n]*fstab", script_text), (
        "fstab append must be guarded by grep"
    )


def test_low_swappiness_persisted(script_text):
    # Pin the actual config lines, not the header comment: the sysctl.d
    # heredoc must interpolate SWAPPINESS and SWAPPINESS itself must be low.
    assert re.search(r"vm\.swappiness=\$\{SWAPPINESS\}", script_text), (
        "sysctl.d heredoc must interpolate SWAPPINESS"
    )
    match = re.search(r"^SWAPPINESS=(\d+)\s*$", script_text, re.MULTILINE)
    assert match, "SWAPPINESS must be set as a script variable"
    assert int(match.group(1)) <= 10, "swappiness must be low (<=10)"


def test_journald_is_log_sink(script_text):
    assert "Storage=persistent" in script_text, (
        "journald must be configured as the persistent log sink"
    )


def test_env_file_600_only_when_missing(script_text):
    # The env file is created from the committed template, mode 600, and only
    # when absent — a re-run must never clobber operator secrets.
    assert re.search(r"\[\[\s*!?\s*-e\s+\$APP_DIR/\.env\s*\]\]", script_text), (
        "env-file creation must be guarded by an existence check"
    )
    assert re.search(r"install\s+[^&]*-m\s+600", script_text), (
        "env file must be installed with mode 600"
    )


def test_app_dir_and_env_template_source(script_text):
    assert ".env.template" in script_text, "env file must be seeded from .env.template"


def test_env_template_covers_required_keys():
    text = (REPO_ROOT / ".env.template").read_text()
    for key in REQUIRED_ENV_KEYS:
        assert re.search(rf"^#?\s*{key}=", text, re.MULTILINE), (
            f".env.template missing {key}"
        )


def test_app_starts_directly_hint(script_text):
    # The demo is "log in as radio and start the app"; the final summary must
    # say how (uv run python yt_radio.py on :8000).
    assert "uv run python yt_radio.py" in script_text
