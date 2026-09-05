"""Where an alert actually goes.

Two channels ship: Telegram (a bot message) and a generic webhook (JSON POST,
which is how you would wire up WhatsApp via a provider, Slack, or a push
service). Adding a third means writing one function and registering it below.

Channels raise on failure. They must not swallow errors, because
``dispatch_pending`` relies on the exception to keep the alert retryable.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable

import httpx

from hawa.config import get_config
from hawa.models import AlertEvent, Subscription
from hawa.sources.base import build_client

log = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org"


class ChannelError(RuntimeError):
    """Delivery failed. The alert stays pending and will be retried."""


def format_message(event: AlertEvent) -> str:
    where = f" [{event.sector}]" if event.sector else ""
    heading = "Pollen alert" if event.kind == "pollen" else "Air quality alert"
    return (
        f"{heading}{where}\n"
        f"{event.trigger}\n\n"
        f"Islamabad, data via PMD / community sensors. "
        f"Source: https://github.com/abdullahbilal-y/islamabad-air"
    )


def send_telegram(sub: Subscription, event: AlertEvent) -> None:
    token = get_config().telegram_bot_token
    if not token:
        raise ChannelError("HAWA_TELEGRAM_BOT_TOKEN is not set")

    url = f"{TELEGRAM_API}/bot{token}/sendMessage"
    payload = {
        "chat_id": sub.target,
        "text": format_message(event),
        "disable_web_page_preview": True,
    }
    try:
        with build_client() as client:
            response = client.post(url, json=payload)
    except httpx.HTTPError as exc:
        raise ChannelError(f"telegram request failed: {exc}") from exc

    if response.status_code >= 400:
        # Telegram puts the real reason in the body; the status alone is not
        # enough to tell "bot blocked by user" from "token revoked".
        raise ChannelError(f"telegram returned HTTP {response.status_code}: {response.text[:300]}")

    try:
        body = response.json()
    except json.JSONDecodeError as exc:
        raise ChannelError("telegram returned a non-JSON body") from exc
    if not body.get("ok"):
        raise ChannelError(f"telegram rejected the message: {body}")


def send_webhook(sub: Subscription, event: AlertEvent) -> None:
    if not sub.target.startswith(("http://", "https://")):
        raise ChannelError(f"webhook target is not a URL: {sub.target!r}")

    payload = {
        "kind": event.kind,
        "sector": event.sector,
        "trigger": event.trigger,
        "value": event.value,
        "threshold": event.threshold,
        "created_at": event.created_at.isoformat(),
        "message": format_message(event),
    }
    try:
        with build_client() as client:
            response = client.post(sub.target, json=payload)
    except httpx.HTTPError as exc:
        raise ChannelError(f"webhook POST failed: {exc}") from exc
    if response.status_code >= 400:
        raise ChannelError(f"webhook returned HTTP {response.status_code}")


CHANNELS: dict[str, Callable[[Subscription, AlertEvent], None]] = {
    "telegram": send_telegram,
    "webhook": send_webhook,
}


def send_alert(sub: Subscription, event: AlertEvent) -> None:
    handler = CHANNELS.get(sub.channel)
    if handler is None:
        raise ChannelError(f"no handler for channel {sub.channel!r}")
    handler(sub, event)
