# 06: Base provisioning — OS, runtime, app user, swap

**What to build:** A single provisioning script, committed to the repo, that takes a fresh Oracle Linux 9 instance to "app deployable": EPEL → RPM Fusion → ffmpeg swap, uv + Python 3.13, a dedicated `radio` user with the repo cloned and dependencies synced, a 2 GB swapfile with low swappiness, journald as the log sink, and the chmod-600 env file template holding app config, DataImpulse credentials, base URL, and SMTP secrets. Idempotent enough to re-run on a reclaimed VM (~10-minute recovery). Demoable: run on a fresh instance, log in as the radio user, and start the app directly (no nginx yet).

**Blocked by:** 03 (env file needs the final proxy-credential shape).

**Status:** ready-for-agent

- [ ] Script provisions ffmpeg via EPEL + RPM Fusion on a fresh OL9 instance
- [ ] uv + Python 3.13 installed; deps synced for the `radio` user
- [ ] 2 GB swapfile active with low swappiness, persisted in fstab
- [ ] Env file template written chmod 600, gitignored, covering app config + proxy + SMTP secrets
- [ ] App starts and streams when launched manually on the provisioned box
- [ ] Re-running the script on a provisioned box succeeds
