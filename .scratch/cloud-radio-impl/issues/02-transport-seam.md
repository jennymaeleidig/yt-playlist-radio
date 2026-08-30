# 02: Transport seam

**What to build:** All yt-dlp and ffmpeg subprocess spawns flow through a single transport object. Unit tests fake the transport wholesale — scripted exit codes, fake slow/dead tracks, fake proxy failures — with no test asserting on raw argv strings. yt-dlp is pinned to the venv's copy (invoked via the venv interpreter), never a PATH binary, so version skew can't cause ghost failures. Pure "make the change easy": app behaviour is unchanged and the app still streams.

**Blocked by:** 01.

**Status:** ready-for-agent

- [ ] Every yt-dlp/ffmpeg spawn in the app goes through the one transport object
- [ ] Unit tests exercise the supervisor through a fake transport with scripted exit codes
- [ ] yt-dlp is resolved from the venv exclusively; no PATH lookup
- [ ] Full unit suite green; manual smoke: stream still plays direct
