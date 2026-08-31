# 06: Base provisioning — OS, runtime, app user, swap

**What to build:** A single provisioning script, committed to the repo, that takes a fresh Oracle Linux 9 instance to "app deployable": EPEL → RPM Fusion → ffmpeg swap, uv + Python 3.13, a dedicated `radio` user with the repo cloned and dependencies synced, a 2 GB swapfile with low swappiness, journald as the log sink, and the chmod-600 env file template holding app config, DataImpulse credentials, base URL, and SMTP secrets. Idempotent enough to re-run on a reclaimed VM (~10-minute recovery). Demoable: run on a fresh instance, log in as the radio user, and start the app directly (no nginx yet).

**Blocked by:** 03 (env file needs the final proxy-credential shape).

**Status:** awaiting-vm-verification

- [x] Script provisions ffmpeg via EPEL + RPM Fusion on a fresh OL9 instance
- [x] uv + Python 3.13 installed; deps synced for the `radio` user
- [x] 2 GB swapfile active with low swappiness, persisted in fstab
- [x] Env file template written chmod 600, gitignored, covering app config + proxy + SMTP secrets
- [ ] App starts and streams when launched manually on the provisioned box
- [ ] Re-running the script on a provisioned box succeeds

## Comments

**Implementation notes**

- `provision.sh` (repo root, `sudo provision.sh [git-repo-url]`) is strict-mode bash
  (`set -euo pipefail`), refuses non-root, and is idempotent: every mutating step is
  guarded (`id -u radio` before useradd, `swapon --show` before swap creation,
  `grep -q` before the fstab append, `[[ -e .env ]]` before seeding the env file —
  a re-run never clobbers operator secrets). `dnf install` steps are naturally
  idempotent; the rpmfusion release RPM exits 0 when already installed.
- ffmpeg path: OL9-native `oracle-epel-release-el9` (fallback `epel-release`) →
  `ol9_codeready_builder` enabled → RPM Fusion free 9 release RPM → `dnf install ffmpeg`.
  Also installs `nodejs` (appstream): `yt-dlp-ejs` needs a JS runtime to solve YouTube
  signature challenges — the dev box got it from mise, OL9 gets it from dnf.
- Repo lands at `/opt/yt-radio` (chowned to `radio`). Source resolution: explicit URL
  arg → clone-or-`fetch`+`reset --hard origin/<branch>`; no arg → the checkout the script
  lives in, via its `origin` remote, else a local rsync of the checkout (excluding
  `.git/.venv/.env/cache.json/cookies*`). A non-git `/opt/yt-radio` under URL mode dies
  loudly rather than clobbering the deployed `.env`.
- `uv` installed system-wide (`UV_INSTALL_DIR=/usr/local/bin`, unmanaged); then as
  `radio`: `uv python install 3.13 && uv sync` in `/opt/yt-radio` (deps incl.
  gunicorn, yt-dlp, yt-dlp-ejs).
- Swap: `/swapfile` 2 GB (`dd`, `chmod 600`, `mkswap`, `swapon`), SELinux-labelled
  `swapfile_t` (idempotent `semanage fcontext`), persisted in `/etc/fstab`,
  `vm.swappiness=10` via `/etc/sysctl.d/99-radio-swap.conf` + `sysctl -w`.
- journald: drop-in `/etc/systemd/journald.conf.d/radio.conf` — `Storage=persistent`,
  `SystemMaxUse=500M`. In practice the app logs to journald once it runs under systemd
  (issue 07); until then its stderr is the sink, as the script's summary states.
- Env file: `/opt/yt-radio/.env` seeded from the committed `.env.template` — app config
  (PLAYLIST_URL, BASE_URL, …), DataImpulse proxy credentials, and Resend SMTP alert
  secrets are all in the template; `install -m 600` owned by `radio`; `.env` is
  gitignored already. Final summary prints the manual start (`sudo -iu radio`,
  `uv run python yt_radio.py` on :8000) and curl verify steps.

**Testing seam (agreed)**

- The sandbox cannot run dnf/systemd/root, so `tests/unit/test_provision.py` pins the
  checklist as static invariants + a `bash -n`/shellcheck gate: strict mode, non-root
  refusal, EPEL/rpmfusion/ffmpeg, uv + Python 3.13 + `uv sync` as radio, guarded
  useradd/swap/fstab/env-file, 2 GB swap, swappiness ≤10 (pinned to the config lines,
  not the header comment), journald persistent storage, 600 env file, and that the
  summary tells the operator how to start the app. Shellcheck `--severity=warning`
  exits clean (run by hand; the test skips when the binary is absent).
- Two checkboxes stay open pending hand-verification on a real VM (the issue's own
  demo criteria): fresh-instance run + manual start/stream, and a clean re-run.

**Code review outcomes (both axes)**

- Spec axis: the swappiness test was pinning the header *comment*, not the config —
  `SWAPPINESS=60` would have passed. Fixed: the test now requires the sysctl.d heredoc
  to interpolate `${SWAPPINESS}` and the variable itself to be ≤10 (mutation-checked:
  a doctored `SWAPPINESS=60` script now fails the suite). Also fixed a literal `\n` in
  a `die` message and hardened the git re-run branch resolution (empty/detached → main).
  Scope creep flagged (nodejs, rsync fallback, SELinux labelling) — all judged
  justified: nodejs is required by yt-dlp-ejs, the rest is idempotency/recovery
  hardening for the reclaimed-VM path.
- Standards axis (smell baseline; repo documents no coding standards): duplicate regex
  in the swappiness test, unused `script_text` fixture param in the syntax-gate tests,
  and redundant assertion alternations — all cleaned up. The regex-over-script-text
  seam is a documented, deliberate tradeoff, not flagged further.
