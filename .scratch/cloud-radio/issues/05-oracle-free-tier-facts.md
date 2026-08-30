# 05 — Oracle Cloud Free Tier: E2.1.Micro + Oracle Linux 9 facts

Type: research
Status: resolved

Docs: Oracle Cloud Free Tier official docs (docs.oracle.com), pulled first per the effort's plan.

## Question

What exactly does deploying this app on the fixed target require? Target is decided:
`VM.Standard.E2.1.Micro`, Oracle Linux 9. Answer with sources:

1. **Always Free confirmation**: is E2.1.Micro Always Free, how many per tenancy, and what are
   the known capacity-availability gotchas ("out of capacity" on free tier, upgrade-to-PAYG
   workaround)? Boot volume/shape limits.
2. **Oracle Linux 9 package story**: how to get `ffmpeg` on OL9 (EPEL? RPM Fusion el9? static
   build?) and Python 3.13 (uv-managed toolchain vs OL9 appstream) — note the app needs
   Python >= 3.13 and yt-dlp.
3. **Network**: firewalld defaults on OL9, plus the OCI Security List / NSG side — what must be
   opened for inbound HTTP (and is a reverse proxy needed, or is gunicorn alone fine behind the
   user's chosen DNS/CDN)?
4. **Runtime posture**: systemd unit shape for a single gunicorn worker (the app requires
   `--timeout 0` and one persistent worker), out-of-memory risk on 1 GB RAM with
   yt-dlp + ffmpeg resident, swap recommendation.
5. **Datacenter-IP reality check**: prior attempt (git history: "remove oracle b/c yt block it")
   hit YouTube bot-blocking; note what the docs/community say about media fetches from OCI IPs.

Write findings to `.scratch/cloud-radio/research/oracle-free-tier.md`; the ticket answer gists it.

## Answer

Full findings: [research/oracle-free-tier.md](../research/oracle-free-tier.md). Gist:

1. **Always Free confirmed**: up to **2× E2.1.Micro** per tenancy (home region, single AD); 1 OCPU / 1 GB / 480 Mbps; min boot 47 GB of the 200 GB free block-storage budget. "Out of host capacity" is real on free tier; documented workaround is upgrade to Pay As You Go (Always Free resources stay free). **Idle reclamation**: 7-day rules reclaim instances under ~20% CPU/network — an actively streamed radio is safe, an idle one is not.
2. **ffmpeg on OL9**: EPEL (`oracle-epel-release-el9`, UNCERTAIN package name — verify on-box) → RPM Fusion free el9 → `dnf swap ffmpeg-free ffmpeg` (ffmpeg-free lacks patented codecs). Static johnvansickle build is a viable fallback.
3. **Python 3.13**: not in OL9 repos; use uv (`uv python install 3.13`) — python-build-standalone is self-contained, OL9 glibc 2.34 clears uv's floor (exact floor UNCERTAIN, negligible risk).
4. **Network**: two layers — OCI security list (SSH-only by default; add stateful ingress TCP 80/443; Oracle recommends NSGs over SLs) and in-OS firewalld (`firewall-cmd --add-port=80/tcp --permanent --reload`). Reverse proxy advisable for TLS but not mandatory.
5. **Datacenter-IP verdict**: with all YouTube traffic proxied through DataImpulse, the OCI IP never reaches YouTube — the prior bot-block risk is neutralized, shifted to proxy reliability. Residual: PO-token/cookie enforcement is client-based, not just IP-based.
