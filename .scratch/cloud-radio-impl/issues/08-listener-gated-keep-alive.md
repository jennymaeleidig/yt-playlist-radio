# 08: Listener-gated keep-alive

**What to build:** A systemd service that protects the Always Free instance from Oracle's idle reclamation: it polls the app's loopback status endpoint and, only while zero listeners are connected, runs a throttled busy-loop targeting ~27% CPU (headroom over the 20% 95th-percentile threshold), cgroup-capped (`CPUQuota`) and low-priority (`Nice` / idle scheduling) so a live stream never contends with it. No fake network traffic. Wired into provisioning. Demoable: on the VM with zero listeners, observed CPU ≥20%; connect a listener → the busy-loop stops within seconds.

**Blocked by:** 07.

**Status:** ready-for-agent

- [ ] Keep-alive service runs only when the status endpoint reports zero listeners
- [ ] Targets ~27% CPU while active; verified on the VM (top / cgroup stats)
- [ ] `CPUQuota` cap and low priority confirmed; stream quality unaffected while a listener is connected
- [ ] Provisioned and enabled by the provisioning script (`WantedBy=multi-user.target`)
