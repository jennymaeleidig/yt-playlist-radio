#!/usr/bin/env bash
#
# provision.sh — take a fresh Oracle Linux 9 instance to "app deployable"
# (cloud-radio issue 06).
#
#   sudo provision.sh [git-repo-url]
#
# What it does (idempotent — safe to re-run on a reclaimed VM, ~10 min):
#   1. EPEL (OL9-native) + CodeReady Builder + RPM Fusion free → ffmpeg
#      (plus nodejs, the JS runtime yt-dlp needs for YouTube challenges)
#   2. uv (system-wide) + Python 3.13, repo at /opt/yt-radio, `uv sync`
#      as the dedicated `radio` user
#   3. 2 GB swapfile + vm.swappiness=10, persisted in /etc/fstab
#   4. journald configured as the persistent log sink
#   5. /opt/yt-radio/.env seeded from the committed .env.template
#      (chmod 600, created only when missing — never clobbers secrets)
#
# Without an argument the repo source is taken from the checkout this
# script lives in: its `origin` remote if one exists, otherwise a local
# copy of the checkout itself. Pass a git URL to clone explicitly.
#
# After provisioning, fill in /opt/yt-radio/.env (playlist URL, DataImpulse
# credentials, base URL, SMTP/alert secrets) and start the app as the
# `radio` user — see the summary this script prints.

set -euo pipefail

APP_DIR="/opt/yt-radio"
APP_USER="radio"
SWAPFILE="/swapfile"
SWAP_SIZE_MB=2048
SWAPPINESS=10

log() { printf '\n==> %s\n' "$*"; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

[[ $EUID -ne 0 ]] && die "Run as root: sudo $0 [git-repo-url]"

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)

# --- Base tooling ------------------------------------------------------------
log "Installing base packages (git, rsync, curl, dnf-plugins-core)"
dnf install -y git rsync curl dnf-plugins-core

# --- EPEL + CodeReady Builder + RPM Fusion → ffmpeg ---------------------------
log "Enabling EPEL (OL9-native) and CodeReady Builder"
dnf install -y oracle-epel-release-el9 || dnf install -y epel-release
dnf config-manager --set-enabled ol9_codeready_builder || true

log "Enabling RPM Fusion free and installing ffmpeg + nodejs"
dnf install -y --nogpgcheck \
  https://mirrors.rpmfusion.org/free/el/rpmfusion-free-release-9.noarch.rpm
dnf install -y ffmpeg
# nodejs: the JS runtime yt-dlp-ejs uses to solve YouTube signature
# challenges (the dev box got this from mise; on OL9 it's an appstream dnf).
dnf install -y nodejs

# --- journald as the log sink -------------------------------------------------
log "Configuring journald as the persistent log sink"
mkdir -p /etc/systemd/journald.conf.d
cat > /etc/systemd/journald.conf.d/radio.conf <<'EOF'
[Journal]
Storage=persistent
SystemMaxUse=500M
EOF
systemctl try-restart systemd-journald

# --- Swap: 2 GB file, low swappiness, persisted -------------------------------
log "Provisioning ${SWAP_SIZE_MB} MB swap at ${SWAPFILE} (swappiness=${SWAPPINESS})"
if ! swapon --show=NAME --noheadings | grep -q "^${SWAPFILE}$"; then
  if [[ ! -f $SWAPFILE ]]; then
    dd if=/dev/zero of="$SWAPFILE" bs=1M count="$SWAP_SIZE_MB" status=progress
    chmod 600 "$SWAPFILE"
  fi
  mkswap "$SWAPFILE"
  # SELinux: label the file so swapon works under enforcing mode.
  dnf install -y policycoreutils-python-utils
  if command -v semanage > /dev/null 2>&1; then
    semanage fcontext -a -t swapfile_t "$SWAPFILE" 2> /dev/null || true
    restorecon -v "$SWAPFILE" || true
  fi
  swapon "$SWAPFILE"
fi
if ! grep -q "^${SWAPFILE}[[:space:]]" /etc/fstab; then
  printf '%s none swap sw 0 0\n' "$SWAPFILE" >> /etc/fstab
fi
cat > /etc/sysctl.d/99-radio-swap.conf <<EOF
vm.swappiness=${SWAPPINESS}
EOF
sysctl -w "vm.swappiness=${SWAPPINESS}"

# --- Dedicated app user --------------------------------------------------------
log "Creating app user '${APP_USER}'"
if ! id -u "$APP_USER" > /dev/null 2>&1; then
  useradd -m -s /bin/bash "$APP_USER"
fi
APP_USER_HOME=$(getent passwd "$APP_USER" | cut -d: -f6)

# --- Repo at ${APP_DIR} --------------------------------------------------------
log "Placing repo at ${APP_DIR}"
src="${1:-}"
if [[ -z $src ]]; then
  if origin=$(git -C "$SCRIPT_DIR" remote get-url origin 2> /dev/null); then
    src="$origin"
  else
    src="$SCRIPT_DIR"
  fi
fi

case $src in
  git@* | http://* | https://* | ssh://*)
    if [[ -d $APP_DIR/.git ]]; then
      branch=$(git -C "$APP_DIR" rev-parse --abbrev-ref HEAD 2> /dev/null || true)
      if [[ -z $branch || $branch == "HEAD" ]]; then
        branch=main
      fi
      git -C "$APP_DIR" fetch origin
      git -C "$APP_DIR" reset --hard "origin/${branch}"
    elif [[ -d $APP_DIR ]]; then
      die "${APP_DIR} exists but is not a git repo; remove it or re-run with a local-checkout source (no argument) — refusing to clobber the deployed .env"
    else
      git clone "$src" "$APP_DIR"
    fi
    ;;
  *)
    # Local checkout copy (no remote): rsync, keeping any existing .git and
    # never touching the deployed .env / runtime state.
    mkdir -p "$APP_DIR"
    rsync -a --delete \
      --exclude .git --exclude .venv --exclude .env \
      --exclude cache.json --exclude 'cookies*.txt' \
      "$src/" "$APP_DIR/"
    ;;
esac
chown -R "${APP_USER}:${APP_USER}" "$APP_DIR"

# --- uv + Python 3.13 + deps for the radio user --------------------------------
log "Installing uv (system-wide) if missing"
if ! command -v uv > /dev/null 2>&1; then
  curl -LsSf https://astral.sh/uv/install.sh |
    env UV_INSTALL_DIR=/usr/local/bin UV_UNMANAGED_INSTALL=1 sh
fi

log "Installing Python 3.13 and syncing dependencies as '${APP_USER}'"
runuser -u "$APP_USER" -- env HOME="$APP_USER_HOME" bash -lc \
  "cd '${APP_DIR}' && uv python install 3.13 && uv sync"

# --- Env file: chmod 600, seeded from the committed template --------------------
# Created only when missing — a re-run must never clobber operator secrets.
log "Seeding ${APP_DIR}/.env from .env.template (chmod 600) if missing"
if [[ ! -e $APP_DIR/.env ]]; then
  install -o "$APP_USER" -g "$APP_USER" -m 600 "$APP_DIR/.env.template" "$APP_DIR/.env"
fi

# --- Summary --------------------------------------------------------------------
log "Provisioning complete"
cat <<EOF

  Next steps:

    1. Fill in secrets/config (as root or ${APP_USER}):
         vi ${APP_DIR}/.env
       Required for streaming: PLAYLIST_URL, BASE_URL, DATAIMPULSE_USER/PASS.
       SMTP alert secrets (RESEND_API_KEY, ALERT_EMAIL_FROM/TO) enable the
       pause-alert email path.

    2. Start the app directly (no nginx yet — issue 07 adds the stack):
         sudo -iu ${APP_USER}
         cd ${APP_DIR}
         uv run python yt_radio.py

    3. Verify from the same box:
         curl -sSf http://localhost:8000/ > /dev/null && echo page-ok
         curl -sSf http://localhost:8000/stream --max-time 5 | head -c 64k > /dev/null \\
           && echo stream-ok

  Logs go to journald once the app runs under systemd (issue 07); until
  then the app logs to its own stderr.
EOF
