# Supplying YouTube cookies (`COOKIES_FILE`)

YouTube occasionally returns **"Sign in to confirm you're not a bot"** for per-video yt-dlp requests (the calls `yt-playlist-radio` makes for both `/stream` audio and track metadata). This commonly happens from cloud/datacenter IPs and shared home IPs under load. Passing authenticated browser cookies to yt-dlp is the documented remedy.

This guide is the app-specific version of the official yt-dlp docs:

- [FAQ: How do I pass cookies to yt-dlp?](https://github.com/yt-dlp/yt-dlp/wiki/FAQ#how-do-i-pass-cookies-to-yt-dlp)
- [Extractors: Exporting YouTube cookies](https://github.com/yt-dlp/yt-dlp/wiki/Extractors#exporting-youtube-cookies)

> ⚠️ **Account ban risk.** Using a real account with yt-dlp can get it banned (temporarily or permanently). Use a throwaway account, and only enable cookies when you actually need them — the app works fine without them on non-flagged IPs.

---

## 1. Tell the app where the cookies are

`yt_radio.py` reads a `COOKIES_FILE` environment variable and injects `--cookies <path>` into **both** yt-dlp invocations (metadata fetch and stream download). Add it to `.env`:

```env
COOKIES_FILE=/home/pi/yt-playlist-radio/cookies.txt
```

Leave it unset (or blank) on IPs that aren't bot-blocked — yt-dlp then runs without cookies.

## 2. Export YouTube cookies (the stable way)

YouTube rotates cookies on open browser tabs, so you must export from a session that is **never re-opened**. Follow this exactly — the generic `--cookies-from-browser` export does **not** work for this (it captures your regular browser cookies, not the isolated session, per the yt-dlp wiki).

1. Open a **new private/incognito window** and log into YouTube (throwaway account).
2. In that **same tab**, navigate to `https://www.youtube.com/robots.txt`. (This should be the **only** incognito tab open.)
3. Export `youtube.com` cookies with a browser extension:
   - **Chrome:** [Get cookies.txt LOCALLY](https://chrome.google.com/webstore/detail/get-cookiestxt-locally/cclelndahbckbenkjhflpdbgdldlbecc)
   - **Firefox:** [cookies.txt](https://addons.mozilla.org/en-US/firefox/addon/cookies-txt/)
   - ⚠️ The non-"LOCALLY" "Get cookies.txt" Chrome extension is reported malware — make sure you use the **LOCALLY** one.
4. **Close the incognito window immediately** and never reopen that session (reopening rotates the cookies and invalidates your export).
5. Confirm the exported file's **first line** is exactly `# HTTP Cookie File` or `# Netscape HTTP Cookie File` — yt-dlp rejects files without this header.

## 3. Put the file on the server

```bash
# from your laptop
scp cookies.txt pi@YOUR_PI_HOST:~/yt-playlist-radio/cookies.txt
```

```bash
# on the server
chmod 600 /home/pi/yt-playlist-radio/cookies.txt      # keep it private
head -1 /home/pi/yt-playlist-radio/cookies.txt        # confirm the header line
```

## 4. Test before restarting the app

Run the exact command the app uses for metadata, now with cookies:

```bash
cd /home/pi/yt-playlist-radio
PLAYLIST_URL=$(grep -E '^PLAYLIST_URL=' .env | cut -d= -f2- | tr -d '"')
FIRST_URL=$(.venv/bin/yt-dlp --flat-playlist --skip-download -J "$PLAYLIST_URL" 2>/dev/null \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['entries'][0]['url'])")

.venv/bin/yt-dlp --cookies /home/pi/yt-playlist-radio/cookies.txt --dump-json "$FIRST_URL"
echo "exit: $?"
```

- **JSON output + `exit 0`** → cookies cleared the bot wall. Add `COOKIES_FILE=...` to `.env` and restart the service.
- **Still "Sign in to confirm you're not a bot"** → the session itself is challenged; see [Troubleshooting](#troubleshooting) below.

## 5. Restart and verify

```bash
# ensure .env has: COOKIES_FILE=/home/pi/yt-playlist-radio/cookies.txt
sudo systemctl restart yt-radio
sudo journalctl -u yt-radio -n 20 --no-pager
# expect "Playlist loaded: N tracks" and NO "Failed to get metadata from yt-dlp"

curl -I http://127.0.0.1:8000/stream         # should stream audio, not error
```

---

## Troubleshooting

- **Cookies exported but still bot-blocked** — confirm the file header (`# HTTP Cookie File`), confirm you exported from the incognito + robots.txt session (not your regular browser), and confirm the incognito window is closed. If it still fails, the account/session itself may be rate-limited; wait ~1 hour and re-export from a fresh incognito session.
- **"This content isn't available, try again later"** — this is the per-session video rate limit (not an IP block; see the yt-dlp wiki: "YT rate limit is not bound to IP"). Guest sessions get ~300 videos/hour, accounts ~2000/hour. The app's auto-refresh + multiple listeners sharing one stream keep requests low; if you still hit it, the recommendation is a delay between downloads.
- **Cookies expired / stopped working** — YouTube rotates them over time. Re-export from a fresh incognito session following step 2.
- **Security** — `cookies.txt` grants account-level access to your YouTube account. Keep it `chmod 600`, never commit it (`.gitignore` should exclude `cookies.txt` and `*.env`), and prefer a throwaway account.

## Why not `--cookies-from-browser` on the server?

The `--cookies-from-browser BROWSER` flag reads cookies from a local browser's profile on the same machine. It's convenient for local dev, but on a headless server there's no browser session to read, and per the yt-dlp wiki it does **not** capture the incognito session this procedure uses. Use the exported `cookies.txt` file + `COOKIES_FILE` instead.
