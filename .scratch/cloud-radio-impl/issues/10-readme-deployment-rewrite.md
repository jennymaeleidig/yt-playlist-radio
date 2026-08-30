# 10: README deployment rewrite

**What to build:** The README's Raspberry Pi + Cloudflare Tunnel deployment section is replaced by the Oracle Free Tier plan: target shape and region, the Pay-As-You-Go upgrade remedy for "out of host capacity", the OCI ingress rule (TCP 80/443, NSG preferred), provisioning usage, the smoke-check command, and the keep-alive rationale. Pi/Tunnel content is removed, not maintained further. The README's app-usage sections (env vars, running locally) are updated for the new env keys (proxy credentials, SMTP) — no other content churn.

**Blocked by:** 07.

**Status:** ready-for-agent

- [ ] Deployment section documents the Oracle plan end to end: shape, PAYG remedy, ingress, provisioning, smoke checks
- [ ] Pi + Cloudflare Tunnel deployment content removed
- [ ] Env var documentation covers the new proxy + SMTP keys
- [ ] Local development instructions still accurate (direct mode, test commands)
