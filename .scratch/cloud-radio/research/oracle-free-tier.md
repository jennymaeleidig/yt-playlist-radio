# Research — Oracle Cloud Free Tier: VM.Standard.E2.1.Micro (Oracle Linux 9)

Researched for the yt-playlist-radio deployment ticket (Flask + gunicorn `--timeout 0`, single persistent worker, yt-dlp piped into ffmpeg). Target: OCI Free Tier, shape `VM.Standard.E2.1.Micro`, OS Oracle Linux 9. Research-only; no code changed.

Legend: **UNCERTAIN** marks claims that could not be confirmed from an official/primary source in this session, with the reason.

---

## 1. Always Free status of VM.Standard.E2.1.Micro

Source: [Oracle — Always Free Resources](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm) and [Oracle — Compute Shapes](https://docs.oracle.com/en-us/iaas/Content/Compute/References/computeshapes.htm).

- **Yes, Always Free.** "All tenancies get up to two Always Free VM instances using the VM.Standard.E2.1.Micro shape, which has an AMD processor."
- **Quota: 2 instances per tenancy.** They must be created in your **home region**, and in multi-AD regions, E2.1.Micro instances "can only be created in one availability domain."
- **Shape specs** (Compute Shapes table): 1 OCPU, 1 GB memory, no local disk (block storage only), max network bandwidth **480 Mbps**, max 1 VNIC, no Windows support. Oracle assigns one of several AMD EPYC processors (7551 / 7742 / 7J13) — you don't choose which.
- **"Out of host capacity" is a documented, acknowledged problem.** Oracle's own Always Free page: *"If you receive an 'out of host capacity' error when trying to create a Compute instance, this indicates a temporary lack of Always Free shapes in your home region."* The documented remediations are: (a) try a different availability domain (limited use for Micro, which is single-AD), (b) wait and retry, and (c) **upgrade the account to Pay As You Go** — *"Oracle doesn't charge for Always Free resources after you upgrade, and will only charge you for resource usage above the Always Free limits."* PAYG accounts get access to more compute capacity, which is the widely used practical workaround for the micro out-of-capacity problem. (Community reports that PAYG fixes the capacity problem are consistent with this doc; the doc itself only says PAYG "gives you access to more types of Compute resources" — the stronger "PAYG gets priority capacity" claim is **UNCERTAIN**: Oracle's official wording does not explicitly promise capacity priority, and forum reports could not be pulled in this session because search engines bot-walled the fetch.)
- **Boot volume:** minimum boot volume size for each instance is **47 GB**, regardless of shape; the account has **200 GB of Always Free block volume storage** total (e.g., two micros at 47 GB ≈ 94 GB of the 200 GB). Boot volumes beyond the Always Free allotment bill under PAYG.
- **Idle reclamation warning:** Oracle may **reclaim idle Always Free compute instances**. Over a 7-day period, an instance is deemed idle if: CPU utilization (95th percentile) < 20%, **network utilization < 20%**, and memory utilization < 20% (memory criterion applies to A1 shapes only). An internet radio that's actively streaming will blow through the network threshold, so reclamation risk is low for this workload — but a radio that sits unused for weeks could be reclaimed.

## 2. Oracle Linux 9 package story

### ffmpeg

Primary source: [RPM Fusion — Configuration](https://rpmfusion.org/Configuration) and [RPM Fusion — Multimedia Howto](https://rpmfusion.org/Howto/Multimedia).

- **EPEL 9 ships `ffmpeg-free`** (RPM Fusion's own docs distinguish it from the full build: "Fedora or EPEL ffmpeg-free works most of the time, but one will experience version mismatch from time to time"). `ffmpeg-free` strips patent-encumbered codecs (e.g., H.264/AAC decode live in `libavcodec-freeworld`), which matters if you transcode AAC audio; YouTube audio formats (opus/aac) are safer with the full build.
- **RPM Fusion officially supports EL9** and requires EPEL first: *"You need to enable EPEL on RHEL or compatible distributions like CentOS before you enable RPM Fusion for EL."*
- **Recipe (recommended):**
  1. Enable EPEL. On Oracle Linux 9 Oracle ships its own EPEL-mirror package, conventionally `sudo dnf install oracle-epel-release-el9` (alternative: Fedora's `epel-release-latest-9`). **UNCERTAIN:** the exact Oracle package name / official doc URL could not be fetched this session (docs.oracle.com EPEL chapter 404'd and search engines were bot-walled); the Oracle-hosted package is standard practice but verify with `dnf search epel` on the box.
  2. Enable RPM Fusion for EL9 (free repo is sufficient for ffmpeg):
     `sudo dnf install --nogpgcheck https://mirrors.rpmfusion.org/free/el/rpmfusion-free-release-9.noarch.rpm`
  3. Full ffmpeg (per RPM Fusion Multimedia page):
     `sudo dnf swap ffmpeg-free ffmpeg --allowerasing` (or `dnf install ffmpeg` on a system without ffmpeg-free).
- **Alternative — static build:** the johnvansickle.com static ffmpeg builds (https://johnvansickle.com/ffmpeg/) are the community-standard fallback and sidestep repo politics entirely; a static glibc-linked binary runs fine on OL9's glibc 2.34. **UNCERTAIN:** the site itself wasn't fetched this session; it is not an "official" distro source, but it's the option yt-dlp users most often cite. For a minimal-footprint box it also avoids pulling ~100+ MB of RPM Fusion dependency chains.
- yt-dlp's FAQ confirms ffmpeg is required for YouTube's separated audio formats and for piping (`yt-dlp -o - | player`, or in this app's case `| ffmpeg`).

### Python 3.13

- OL9's AppStream repo ships only older Pythons (python3.9/3.11/3.12-era SCL-style packages; **no 3.13**). **UNCERTAIN:** exact version set not re-verified from yum.oracle.com this session (page fetch truncated), but 3.13 in OL9 repos is not a thing — the ticket's premise ("AppStream only ships older Pythons") stands.
- **Route: uv-managed toolchain.** [uv installation](https://docs.astral.sh/uv/getting-started/installation/): `curl -LsSf https://astral.sh/uv/install.sh | sh`, then `uv python install 3.13`. uv-managed Pythons are self-contained builds from Astral's [python-build-standalone](https://docs.astral.sh/uv/concepts/python-versions/) fork ("self-contained, highly-portable"), so no OS Python build deps are needed.
- **glibc compatibility:** OL9 (RHEL 9 base) ships glibc **2.34** (standard RHEL9 platform fact; verify on the box with `ldd --version`). uv's Linux x86_64 `gnu` builds and python-build-standalone distributions target far older glibcs (≥ 2.17 / 2.28 era), so OL9 comfortably clears the floor. **UNCERTAIN:** uv's docs do not state a single official minimum glibc on the pages fetched this session; the exact floor wasn't confirmed — but 2.34 exceeds every known requirement, and the practical risk is negligible. Download the `x86_64-unknown-linux-gnu` flavor (the default), not the musl build.

## 3. Network: firewalld + OCI Security List / NSG

Primary sources: [OCI — Security Rules overview](https://docs.oracle.com/en-us/iaas/Content/Network/Concepts/securityrules.htm), [OCI — Security Lists](https://docs.oracle.com/en-us/iaas/Content/Network/Concepts/securitylists.htm).

- **Two independent firewall layers** must both allow inbound HTTP:
  1. **OCI-side** — Security List (subnet-wide) or NSG (per-VNIC). The VCN's default security list allows SSH (TCP 22) ingress from 0.0.0.0/0 and all egress, but **not** port 80/443. **UNCERTAIN (wording only):** the doc page confirms "A VCN automatically comes with a default security list that contains several default security rules" and that ping is *not* enabled by default; the exact default rule list (stateful TCP 22 from 0.0.0.0/0; stateful ICMP type 3 code 4 from 0.0.0.0/0; stateful ICMP type 3 from the VCN CIDR; stateful egress all to 0.0.0.0/0) is standard OCI behavior but the page's "Default" section body wasn't returned by the fetch tool this session — eyeball it in the console when adding rules.
  2. **In-OS** — Oracle docs explicitly warn: *"Confirm That OS Firewall Rules Align with Security Rules"*, and that on Oracle Linux images you use **firewalld** to interact with the iptables rules:
     ```
     sudo firewall-cmd --zone=public --permanent --add-port=80/tcp
     sudo firewall-cmd --reload
     ```
     (Oracle notes a known issue where `firewall-cmd --reload` can hang instances with iSCSI boot volumes — see their Compute known-issues page if that applies.)
- **To open for this app:** one stateful ingress rule — source CIDR `0.0.0.0/0`, TCP, destination port **80** (add **443** when TLS is added). Egress is already allow-all in the default security list, so outbound (YouTube via proxy, DNS) needs nothing.
- **Security List vs NSG:** SLs apply to all VNICs in a subnet (max 5 SLs/subnet); NSGs apply to chosen VNICs (max 5 NSGs/VNIC, rules can reference other NSGs). Oracle's stated recommendation: *"We recommend using NSGs instead of security lists."* Either works for a single-instance app; an NSG keeps the rule attached to the instance rather than the subnet.
- **gunicorn alone or reverse proxy?** Nothing in OCI networking requires a proxy. gunicorn with `--timeout 0` and one worker *can* serve the stream directly. A reverse proxy (nginx/caddy) remains advisable for: TLS termination (caddy auto-HTTPS), shielding the long-lived streaming connection from gunicorn's worker-management quirks, and low memory cost (~10 MB). **UNCERTAIN:** gunicorn's deployment docs (docs.gunicorn.org) repeatedly 404'd through the fetch tool this session, so the official "put nginx in front" guidance is cited from the docs site's canonical URL without re-verification; it is standard gunicorn guidance. Oracle's docs place no constraint either way. Given the app already pipes yt-dlp→ffmpeg and streams long responses, a proxy is recommended but not mandatory; if RAM is the binding constraint, gunicorn-direct on port 80 is workable.

## 4. Runtime posture on 1 GB RAM

Mostly operational judgment; official-source facts are flagged.

- **Footprint estimates (all UNCERTAIN — no official per-process numbers exist; based on typical community-reported values, not fetched this session):**
  - gunicorn master + 1 Flask worker: roughly **40–80 MB** RSS combined for a small app.
  - yt-dlp (fetch + parse): roughly **80–150 MB** peak, driven by playlist metadata and Python overhead.
  - ffmpeg (audio transcode/remux, single stream): roughly **50–150 MB** depending on codec and buffer sizes.
  - OL9 base + systemd + firewalld: **~200–300 MB**.
  - Total: fits in 1 GB but with little headroom; kernel page cache and transient spikes are the danger. This is the classic micro-shape profile where **swap is the safety net**.
- **Swap file for OL9** (UNCERTAIN as to OCI-image default — Oracle Linux OCI images typically ship with **no swap**; verify with `free -m`): create a 1–2 GB swapfile:
  ```
  sudo dd if=/dev/zero of=/swapfile bs=1M count=2048
  sudo chmod 600 /swapfile && sudo mkswap /swapfile && sudo swapon /swapfile
  echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
  ```
  A modest `vm.swappiness=10` keeps it as an emergency buffer rather than a performance drag. (General Linux practice, not Oracle-official.)
- **systemd unit shape** (directives are standard systemd; cite [gunicorn docs](https://docs.gunicorn.org/en/stable/deploy.html) — UNCERTAIN that specific page, which 404'd via fetch this session, but `Restart=always`, `MemoryMax`, etc. are core systemd.service directives):
  ```ini
  [Unit]
  Description=yt-playlist-radio
  After=network-online.target
  Wants=network-online.target

  [Service]
  User=radio
  WorkingDirectory=/opt/yt-playlist-radio
  ExecStart=/opt/yt-playlist-radio/.venv/bin/gunicorn --workers 1 --timeout 0 --bind 0.0.0.0:8080 app:app
  Restart=always
  RestartSec=5
  # keep the single worker from taking the whole box down:
  MemoryMax=700M
  MemoryHigh=600M
  # optional: restart on hang since --timeout 0 disables gunicorn's own worker kill
  # WatchdogSec=60  (requires gunicorn --timeout smaller than watchdog, see below)

  [Install]
  WantedBy=multi-user.target
  ```
- **`--timeout 0` interaction:** gunicorn's `--timeout` is a worker *heartbeat* kill; `0` disables it (needed for the long-lived streaming response). Consequence: a wedged worker is never restarted by gunicorn — that protection must come from systemd (`Restart=always` catches crashes; a healthcheck/watchdog catches hangs). This matches the app's stated requirement.

## 5. Datacenter-IP reality check (YouTube vs OCI IP ranges)

- **The block is real and general.** yt-dlp's own docs (primary source for the ecosystem) document the mechanics: without a PO token, requests "may return HTTP Error 403, or result in your account or IP address being blocked" ([yt-dlp PO Token Guide](https://github.com/yt-dlp/yt-dlp/wiki/PO-Token-Guide)); the [FAQ](https://github.com/yt-dlp/yt-dlp/wiki/FAQ) documents IP-based 429/403 soft blocks and the standard workarounds (cookies, `--proxy`, `--source-address`).
- **OCI-specific community reports** ("Sign in to confirm you're not a bot" from Oracle Cloud IPs): **UNCERTAIN** — I could not pull Reddit/forum threads this session (Bing and DuckDuckGo both bot-walled the fetcher). The prior deploy attempt's observed 403/bot-check from an OCI IP is consistent with the widely reported pattern that YouTube treats datacenter ranges (AWS/GCP/Azure/Oracle alike) with suspicion. Treat "OCI IPs are bot-flagged" as an empirical premise already validated by ticket 01's baseline, not something this session independently re-verified.
- **Verdict once all YouTube traffic is proxied (DataImpulse residential):** the OCI IP stops mattering **for YouTube**. yt-dlp's documented `--proxy` (and `HTTP(S)_PROXY` env) routes extractor metadata requests *and* media/range requests through the proxy, so YouTube sees the residential exit IP, not the OCI IP. The datacenter-IP variable is removed **if and only if** the proxying covers every request class — partial routing (e.g., proxying metadata but not media fragments) reintroduces the OCI IP. Two residual risks replace it:
  1. **Proxy path reliability** — a mid-stream proxy failure now kills the fetch (covered by sibling tickets 02/03/04).
  2. **PO tokens / bot checks are not purely IP-based.** YouTube's PO-token enforcement is keyed to client + session + (often) video ID, so a residential IP removes the *datacenter* penalty but does not guarantee zero bot-checking; cookies (COOKIES_FILE) may still be needed alongside the proxy. (PO Token Guide: enforcement is per-client and rolling out progressively — **UNCERTAIN** as to current exact state.)
  The OCI IP itself remains exposed only to the proxy vendor and to your own listeners — YouTube never sees it. Conclusion: routing through DataImpulse neutralizes the datacenter-IP problem for YouTube fetches; it does not by itself retire the PO-token/cookies dimension.

## Sources

- Oracle, Always Free Resources — https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm *(fetched; §1 all points)*
- Oracle, Compute Shapes (VM.Standard.E2.1.Micro specs) — https://docs.oracle.com/en-us/iaas/Content/Compute/References/computeshapes.htm *(fetched)*
- OCI, Security Rules / Security Lists vs NSGs — https://docs.oracle.com/en-us/iaas/Content/Network/Concepts/securityrules.htm *(fetched; firewalld alignment + `firewall-cmd` commands + default-security-list existence)*
- OCI, Security Lists — https://docs.oracle.com/en-us/iaas/Content/Network/Concepts/securitylists.htm *(fetched; Default-section body not returned — flagged UNCERTAIN)*
- RPM Fusion, Configuration (EL9 repos, EPEL prerequisite) — https://rpmfusion.org/Configuration *(fetched)*
- RPM Fusion, Multimedia (ffmpeg-free → ffmpeg swap, libavcodec-freeworld) — https://rpmfusion.org/Howto/Multimedia *(fetched)*
- uv, Installation — https://docs.astral.sh/uv/getting-started/installation/ *(fetched)*
- uv, Python versions (python-build-standalone provenance) — https://docs.astral.sh/uv/concepts/python-versions/ *(fetched)*
- yt-dlp, PO Token Guide (403/IP-block mechanics, PO token enforcement) — https://github.com/yt-dlp/yt-dlp/wiki/PO-Token-Guide *(fetched)*
- yt-dlp, FAQ (429/403 IP blocks, cookies, --proxy/--source-address, ffmpeg requirement) — https://github.com/yt-dlp/yt-dlp/wiki/FAQ *(fetched)*
- gunicorn, Deployment docs — https://docs.gunicorn.org/en/stable/deploy.html *(NOT fetched — 404 via tool all attempts; cited as canonical URL, flagged UNCERTAIN)*
- Oracle Linux 9 EPEL doc / yum.oracle.com repo index — NOT fetched (404 / truncated); exact `oracle-epel-release-el9` package name and OL9 AppStream Python version set flagged UNCERTAIN
- YouTube-vs-OCI community threads — NOT fetched (search engines bot-walled); OCI-IP bot-flag premise taken from prior deploy experience + ticket 01 baseline, flagged UNCERTAIN

## Open questions for the parent session

1. Confirm `oracle-epel-release-el9` availability on the box (`dnf search epel`) before scripting the install.
2. Decide gunicorn-direct vs reverse proxy (recommendation here: caddy if TLS is wanted soon, else gunicorn-direct on :80 to save RAM).
3. The PO-token/cookies question interacts with sibling tickets 02–04; this file only verdicts the OCI-IP variable.
