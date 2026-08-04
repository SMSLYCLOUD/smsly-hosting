import ipaddress
import logging
import socket
from urllib.parse import urlparse

import requests
from django.conf import settings
from django.core.exceptions import ValidationError

from apps.core.validators import validate_ssrf

logger = logging.getLogger(__name__)


_ALLOWED_NOTIFICATION_HOSTS = frozenset({
    'hooks.slack.com',
    'hooks.slack-gov.com',
    'discord.com',
    'discordapp.com',
})

_MAX_BODY_BYTES = 64 * 1024
_DEFAULT_REQUEST_TIMEOUT = 5.0
# Headroom for the JSON envelope so a truncated body never re-crosses
# the byte cap after serialization.
_JSON_ENVELOPE_HEADROOM = 1024


def _truncate_body(body: str) -> str:
    """Truncate a body to fit under the byte cap without splitting UTF-8."""
    max_message_bytes = _MAX_BODY_BYTES - _JSON_ENVELOPE_HEADROOM
    encoded = body.encode('utf-8')
    if len(encoded) <= max_message_bytes:
        return body
    logger.warning(
        "Notification body exceeds %d bytes; truncating", _MAX_BODY_BYTES
    )
    return encoded[:max_message_bytes].decode('utf-8', errors='ignore')


def _get_request_timeout() -> float:
    """Return the configured notification webhook timeout.

    Reads ``settings.NOTIFICATION_WEBHOOK_TIMEOUT`` (float seconds)
    and falls back to ``5.0`` when unset or non-positive. A
    configurable timeout lets operators tune the wait for slow
    downstream targets without code changes; the default preserves
    the original 5s behaviour.
    """
    value = getattr(settings, 'NOTIFICATION_WEBHOOK_TIMEOUT', _DEFAULT_REQUEST_TIMEOUT)
    try:
        value = float(value)
    except (TypeError, ValueError):
        value = _DEFAULT_REQUEST_TIMEOUT
    if value <= 0:
        value = _DEFAULT_REQUEST_TIMEOUT
    return value


def _validate_notification_url(url: str) -> str:
    """Validate a webhook URL against the notification host allowlist and
    reject any IP literal (or DNS resolution) that points to loopback,
    link-local, RFC1918 private, or otherwise reserved ranges.

    Returns the lowercased hostname on success. Raises ``ValueError`` on
    any violation. The caller is expected to surface this as a 4xx error
    to the user so SSRF attempts are visible.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ('http', 'https'):
        raise ValueError(
            f"Notification URL must use http(s); got {parsed.scheme!r}"
        )
    host = (parsed.hostname or '').lower()
    if not host:
        raise ValueError("Notification URL is missing a host.")
    if host not in _ALLOWED_NOTIFICATION_HOSTS:
        raise ValueError(
            f"Notification URL host {host!r} is not in the allowlist."
        )
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise ValueError(f"Cannot resolve notification URL host: {exc}")
    for _family, _type, _proto, _canon, sockaddr in infos:
        ip_str = sockaddr[0]
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            continue
        if (
            ip.is_loopback
            or ip.is_link_local
            or ip.is_private
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            raise ValueError(
                f"Notification URL resolves to disallowed IP {ip_str}"
            )
    return host


def _log_notification(provider: str, user, url: str) -> None:
    """Best-effort audit log of every outbound notification send.

    Only the URL host is persisted — never the full URL (which contains
    the webhook token). The recipient user id is included so the audit
    trail can attribute the send to a single account.
    """
    try:
        user_id = getattr(user, 'id', None) if user is not None else None
        username = getattr(user, 'username', None) if user is not None else None
        parsed = urlparse(url)
        logger.info(
            "notification.send provider=%s user_id=%s username=%s host=%s",
            provider,
            user_id,
            username,
            (parsed.hostname or '').lower(),
        )
    except Exception as exc:
        logger.warning("Failed to write notification audit log: %s", exc)


def _post_notification(url: str, payload: dict, user=None, provider: str = '') -> bool:
    """Validate ``url``, cap the payload, and POST it. Returns True on a
    successful 2xx response. Returns False (without raising) for any
    validation or transport error so callers can fail open.
    """
    try:
        _validate_notification_url(url)
    except ValueError as exc:
        logger.warning(
            "Rejected notification URL host=%s reason=%s",
            getattr(user, 'id', None),
            exc,
        )
        return False

    try:
        validate_ssrf(url)
    except ValidationError as exc:
        logger.warning(
            "Rejected notification URL host=%s reason=%s",
            getattr(user, 'id', None),
            exc,
        )
        return False

    body = str(payload).encode('utf-8')
    if len(body) > _MAX_BODY_BYTES:
        logger.warning(
            "Rejected notification body larger than %d bytes", _MAX_BODY_BYTES
        )
        return False

    _log_notification(provider, user, url)
    try:
        resp = requests.post(
            url,
            json=payload,
            timeout=_get_request_timeout(),
            allow_redirects=False,
        )
        return 200 <= resp.status_code < 300
    except requests.RequestException as exc:
        logger.error("Failed to send %s notification: %s", provider or 'notification', exc)
        return False
    except Exception as exc:
        logger.error("Unexpected error sending %s notification: %s", provider or 'notification', exc)
        return False


def send_slack_notification(message: str, webhook_url: str | None = None, user=None):
    """Send a notification to a Slack webhook."""
    url = webhook_url or getattr(settings, 'SLACK_WEBHOOK_URL', None)
    if not url:
        return

    body = _truncate_body(str(message))

    _post_notification(
        url,
        {"text": body},
        user=user,
        provider='slack',
    )


def send_discord_notification(message: str, webhook_url: str | None = None, user=None):
    """Send a notification to a Discord webhook."""
    url = webhook_url or getattr(settings, 'DISCORD_WEBHOOK_URL', None)
    if not url:
        return

    body = _truncate_body(str(message))

    _post_notification(
        url,
        {"content": body},
        user=user,
        provider='discord',
    )
