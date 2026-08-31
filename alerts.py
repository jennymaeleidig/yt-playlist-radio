"""Alert email via the Resend Email API — the mailer seam (issue 05).

Config is read from the environment (.env): RESEND_API_KEY (an API key from
https://resend.com/dashboard/api-keys), ALERT_EMAIL_FROM (a verified sender
on the Resend account) and ALERT_EMAIL_TO. When all are configured, yt_radio
builds one MAILER from them; otherwise it is None and pauses simply log
instead of emailing.

The API key never appears in logs; it travels only in the Authorization
header of the outbound request. Unit tests replace the MAILER object
wholesale with a fake (and pin the HTTP seam `_resend_post`), so no test
ever sends real email or touches the network.
"""
import json
import logging
import os
import urllib.request

logger = logging.getLogger(__name__)

RESEND_API_URL = "https://api.resend.com/emails"
SEND_TIMEOUT_SECONDS = 30


class Mailer:
    """The mailer seam: yt_radio depends only on `send(subject, body) -> bool`
    and never on Resend itself. Tests substitute fakes."""

    def send(self, subject: str, body: str) -> bool:
        raise NotImplementedError


def _resend_post(api_key: str, payload: dict) -> None:
    """POST one email payload to Resend. Raises on any HTTP/network failure.
    Kept as a module-level function so tests can pin the HTTP seam without
    patching urllib internals."""
    request = urllib.request.Request(
        RESEND_API_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=SEND_TIMEOUT_SECONDS) as response:
        response.read()


class ResendMailer(Mailer):
    """Sends alert emails through the Resend Email API."""

    def __init__(self, api_key, from_addr, to_addr):
        self.api_key = api_key
        self.from_addr = from_addr
        self.to_addr = to_addr

    @classmethod
    def from_env(cls, env=None):
        """Build a mailer from an env mapping (os.environ by default).
        Returns None when alerting is not configured — missing config must
        degrade to logging, never crash the pause path."""
        env = os.environ if env is None else env
        api_key = env.get("RESEND_API_KEY", "")
        from_addr = env.get("ALERT_EMAIL_FROM", "")
        to_addr = env.get("ALERT_EMAIL_TO", "")
        if not (api_key and from_addr and to_addr):
            return None
        return cls(api_key=api_key, from_addr=from_addr, to_addr=to_addr)

    def send(self, subject: str, body: str) -> bool:
        payload = {
            "from": self.from_addr,
            "to": [self.to_addr],
            "subject": subject,
            "text": body,
        }
        try:
            _resend_post(self.api_key, payload)
        except Exception as exc:
            # Never include the API key in the logged detail.
            logger.error("Alert email could not be sent: %s", exc)
            return False
        return True
