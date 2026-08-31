# 11: Deployment wizard — guided, mostly-automated Oracle deploy

**What to build:** An interactive bash wizard (authored with the `/wizard` skill's template) that takes the operator from "fresh Oracle account" to "green smoke run" in one guided session. Genuinely human-only steps get stage-by-stage walkthroughs with URLs opened and hidden secret entry; everything scriptable on the OCI side is automated via the OCI CLI rather than console clicking. The wizard launches the VM, applies the ingress rule, captures the public IP, writes captured secrets into the VM's chmod-600 env file via the provisioning script, and finishes by running the smoke suite against the live deployment. Captured secrets land in the VM env file and local scratch only — never the repo. Demoable: run the wizard on a fresh machine, answer the prompts, end at a passing `pytest -m smoke --base-url=https://<domain>`.

**Blocked by:** 09, 10.

**Status:** ready-for-agent

- [ ] Wizard built from the /wizard template: `bash -n` and shellcheck clean; committed and linked from the README
- [ ] Stage: OCI account + API key setup (opens the console, walks user/policy/API-key creation, verifies `oci` CLI auth)
- [ ] Stage: PAYG-upgrade remedy guidance if "out of host capacity" occurs during instance launch
- [ ] Stage: DataImpulse signup + credentials captured via hidden entry
- [ ] Stage: SMTP app-password captured via hidden entry
- [ ] Stage: DNS guidance for the operator's setup (domain purchased via Vercel, DNS managed at Cloudflare): open the Cloudflare dashboard, create/point an A record at the VM's public IP, and set it **DNS-only (grey cloud)** so certbot's HTTP-01 challenge and the raw audio stream (icy-metadata, no CDN buffering) behave — note the orange-cloud option and why it's not used here; nameserver guidance for a Vercel-purchased domain pointed at Cloudflare
- [ ] Automated: OCI CLI launches the instance (`E2.1.Micro`, Oracle Linux 9, ≥47 GB boot volume, home region) and captures the public IP — no console clicking for the instance itself
- [ ] Automated: ingress rule (one stateful TCP 80+443 rule, NSG preferred) applied via OCI CLI
- [ ] Automated: provisioning script invoked by the wizard with the captured values; secrets written only to the VM env file / local scratch
- [ ] One wizard run ends at a passing smoke suite against `https://<domain>`
- [ ] Re-runnable against a reclaimed VM (idempotent upserts, instance-reuse path, no duplicate DNS/ingress writes)
