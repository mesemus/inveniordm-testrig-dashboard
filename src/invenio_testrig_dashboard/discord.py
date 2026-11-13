"""Post messages to a Discord webhook. Never raises: alerting must not break ingest."""

import logging
import time
from typing import Any

import httpx2

log = logging.getLogger(__name__)

TIMEOUT_SECONDS = 10.0
MAX_RETRY_AFTER = 10.0
# Suppress link previews, so a digest with many links stays compact.
SUPPRESS_EMBEDS = 4


def _retry_after(response: httpx2.Response) -> float:
    try:
        value = float(response.json().get("retry_after", 1.0))
    except Exception:
        return 1.0
    return min(max(value, 0.0), MAX_RETRY_AFTER)


def post(
    webhook_url: str | None, content: str, transport: httpx2.BaseTransport | None = None
) -> bool:
    """Post ``content``; returns whether it was delivered (or no webhook is configured).

    ``transport`` is for this module's own tests (mocked HTTP). Tests of callers patch
    ``discord.post`` itself instead; both are intended.
    """
    if webhook_url is None:
        log.warning("no Discord webhook configured; message not sent: %s", content)
        return True

    payload: dict[str, Any] = {
        "content": content,
        # Test names are untrusted text: without this, a test named @everyone pings the server.
        "allowed_mentions": {"parse": []},
        "flags": SUPPRESS_EMBEDS,
    }
    try:
        with httpx2.Client(timeout=TIMEOUT_SECONDS, transport=transport) as client:
            response = client.post(webhook_url, json=payload)
            if response.status_code == 429:
                time.sleep(_retry_after(response))
                response = client.post(webhook_url, json=payload)
            if response.status_code >= 400:
                log.error(
                    "Discord webhook returned HTTP %s: %s",
                    response.status_code,
                    response.text[:300],
                )
                return False
            return True
    except Exception as exc:
        # The exception text can contain the webhook URL, which is itself a secret.
        log.error("Posting to Discord failed: %s", type(exc).__name__)
        return False
