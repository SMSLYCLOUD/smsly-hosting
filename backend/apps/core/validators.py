import ipaddress
import urllib.parse
from typing import Any

from django.core.exceptions import ValidationError


def validate_ssrf(value: Any) -> None:
    """
    Validate a URL or domain to prevent SSRF attacks.
    Blocks RFC 1918 private IPs, cloud metadata endpoints, and localhost.
    """
    if not value:
        return

    value = value.strip()

    # Check if value contains an explicit scheme, implying a full URL.
    if '://' in value:
        parsed = urllib.parse.urlparse(value)
        hostname = parsed.hostname
    else:
        # Check if it looks like a path (e.g., /health)
        if value.startswith('/'):
            return # A pure path without host is fine

        hostname = value.split(':')[0]

    if not hostname:
        return

    hostname = hostname.lower()

    # Block literal matching rules
    blocked_hosts = [
        'localhost',
        'metadata.google.internal',
        '169.254.169.254',
        '127.0.0.1',
        '::1'
    ]
    if hostname in blocked_hosts:
        raise ValidationError(f"Hostname '{hostname}' is not allowed for security reasons (SSRF protection).")

    # Block IPs in reserved/private ranges
    try:
        ip = ipaddress.ip_address(hostname)
        if ip.is_unspecified:
            raise ValidationError(f"Unspecified IPs ({hostname}) are not allowed.")
        if ip.is_loopback:
            raise ValidationError(f"Loopback IPs ({hostname}) are not allowed.")
        if ip.is_private:
            raise ValidationError(f"Private IPs ({hostname}) are not allowed.")
        if ip.is_link_local:
            raise ValidationError(f"Link-local IPs ({hostname}) are not allowed.")
        if ip.is_reserved:
            raise ValidationError(f"Reserved IPs ({hostname}) are not allowed.")
    except ValueError:
        pass # Not a valid IP string, which is fine
