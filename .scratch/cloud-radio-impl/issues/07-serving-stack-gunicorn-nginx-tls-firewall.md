# 07: Serving stack — gunicorn, nginx, TLS, firewall

**What to build:** The provisioned VM serves the radio properly: a gunicorn systemd unit (single worker, worker heartbeat timeout disabled for the long-lived stream, `Restart=always`, systemd memory caps ~600M/700M, starts on boot), nginx on 80/443 proxying to it with proxy buffering disabled on the stream location, certbot TLS with HTTP→HTTPS redirect, and firewalld opening 80/443. OCI-side ingress (one stateful rule, TCP 80+443 from anywhere, NSG preferred) is documented in the script's output or README runbook. Demoable: run the extended provisioning on a fresh instance → `https://<domain>` plays the proxied stream, and alert email flows through msmtp.

**Blocked by:** 06, 05 (SMTP/msmtp configured during provisioning).

**Status:** claimed

- [ ] gunicorn systemd unit: single worker, streaming-safe timeout, `Restart=always` + `RestartSec`, memory caps, `After=network-online.target`
- [ ] nginx proxies loopback → public; buffering disabled on the stream location
- [ ] certbot issues a cert; HTTP redirects to HTTPS; renewal enabled
- [ ] firewalld opens 80/443 permanently
- [ ] msmtp configured from the env file; a test alert email arrives from the VM
- [ ] Full stream session (multiple tracks, icy-metadata) works over HTTPS
