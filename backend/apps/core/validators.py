import ipaddress
import socket
import urllib.parse
from typing import Any

from django.core.exceptions import ValidationError


# Hostnames that must never be fetched, regardless of what they resolve
# to. Covers cloud metadata services on every major provider plus the
# platform's own control-plane names (a tenant webhook pointing at the
# backend's internal name would hit the zero-trust-EXEMPT routes and
# bypass auth entirely — e.g. /api/v1/webhooks/* paths).
BLOCKED_LITERAL_HOSTS = frozenset({
    'localhost',
    'localhost.localdomain',
    'metadata.google.internal',
    'metadata.goog',
    '169.254.169.254',
    '127.0.0.1',
    '::1',
    '0.0.0.0',
    # Platform internal service names (Docker network aliases) — the
    # webhook dispatcher runs in the backend container where these
    # resolve. Fetching them means fetching OURSELVES with any
    # zero-trust-exempt path the attacker chooses.
    'backend',
    'frontend',
    'caddy',
    'traefik',
    'socket-proxy',
    'postgres',
    'redis',
    'celery',
    'celery-beat',
    'rabbitmq',
    'registry',
    'route-fallback',
    'pgcat',
    'crowdsec',
    'spire-server',
    'spire-agent',
})

# Path prefixes that bypass the zero-trust middleware on THIS platform.
# A webhook URL pointing at the platform itself (even via a public
# hostname!) could target these unauthenticated endpoints — webhooks
# (forgeable body -> service creation triggers), check-domain, node
# token exchange. Never allow a tenant-controlled fetch to hit them.
_SELF_TARGET_PATH_DENYLIST = (
    '/api/v1/webhooks/',
    '/api/v1/services/webhook/',
    '/api/v1/services/check-domain',
    '/api/v1/auth/node-token-exchange',
    '/api/v1/servers/bootstrap/',
    '/api/v1/transfers/register-incoming/',
)

# DNS resolution cap: a webhook URL that fails to resolve is fine to
# reject; one resolving to MANY addresses where ANY is private is
# rejected (round-robin rebinding into the internal network).
_MAX_ADDRESSES = 16


def _bad_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> str | None:
    if ip.is_unspecified:
        return f"Unspecified IPs ({ip}) are not allowed."
    if ip.is_loopback:
        return f"Loopback IPs ({ip}) are not allowed."
    if ip.is_private:
        return f"Private IPs ({ip}) are not allowed."
    if ip.is_link_local:
        return f"Link-local IPs ({ip}) are not allowed."
    if ip.is_reserved:
        return f"Reserved IPs ({ip}) are not allowed."
    if ip.is_multicast:
        return f"Multicast IPs ({ip}) are not allowed."
    return None


def _check_ip_text(text: str) -> None:
    """Validate a textual IP (v4 or v6, incl. bracketed IPv6 host)."""
    raw = text.strip().strip('[]')
    try:
        ip = ipaddress.ip_address(raw)
    except ValueError:
        return  # not an IP literal — hostname path handles it
    reason = _bad_ip(ip)
    if reason:
        raise ValidationError(reason)


def _resolve_and_check(hostname: str) -> None:
    """Resolve *hostname* and require EVERY address to be public.

    DNS-BASED SSRF BYPASS: the old validator only inspected the literal
    string — 'internal.corp.example' or a DNS name resolving to
    169.254.169.254 sailed through. Worse, a name with MULTIPLE records
    (round-robin: one public + one internal) let the fetcher connect
    to the internal address by luck/attacker timing. We resolve ALL
    addresses and reject if ANY is non-public.
    """
    try:
        infos = socket.getaddrinfo(
            hostname, None, proto=socket.IPPROTO_TCP,
        )
    except (socket.gaierror, UnicodeError):
        # Unresolvable names are allowed at validation time (may be a
        # future/internal-only name the operator controls) — the fetch
        # itself will fail. Resolvable-but-private is the dangerous
        # case and that we catch below.
        return

    seen: set[str] = set()
    checked = 0
    for info in infos:
        if checked >= _MAX_ADDRESSES:
            break
        addr = info[4][0] if info[4] else ''
        if not addr or addr in seen:
            continue
        seen.add(addr)
        checked += 1
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            continue
        reason = _bad_ip(ip)
        if reason:
            raise ValidationError(
                f"Hostname '{hostname}' resolves to a blocked address: {reason}"
            )


def validate_ssrf(value: Any) -> None:
    """
    Validate a URL or domain to prevent SSRF attacks.

    Defense layers (in order):
      1. Literal-host blocklist (metadata services, loopback, Docker
         internal service names — 'backend' resolves inside the
         dispatcher container!).
      2. Path denylist when the URL targets THIS platform: any
         hostname/port combination is checked against the zero-trust
         exempt routes, so a self-pointing webhook cannot reach the
         unauthenticated endpoints.
      3. IP-literal checks in every textual encoding — decimal
         ('2130706433'), hex ('0x7f000001'), and octal ('0177.0.0.1')
         forms all parse via ipaddress after normalization attempts.
      4. DNS RESOLUTION: every address the hostname resolves to must
         be public. Defeats hostname-based bypasses and round-robin
         rebinding (public + private records).
    """
    if not value:
        return

    value = str(value).strip()

    parsed_path = ''
    if '://' in value:
        try:
            parsed = urllib.parse.urlparse(value)
            hostname = parsed.hostname
            parsed_path = parsed.path or ''
        except ValueError:
            raise ValidationError("Invalid URL.")
    else:
        if value.startswith('/'):
            return  # A pure path without host is fine
        hostname = value.split('/')[0].split(':')[0]

    if not hostname:
        return

    hostname = hostname.lower().strip('[]')

    # 1. Literal host blocklist
    if hostname in BLOCKED_LITERAL_HOSTS:
        raise ValidationError(
            f"Hostname '{hostname}' is not allowed for security reasons (SSRF protection)."
        )

    # 2. Self-platform targeting: deny the zero-trust-exempt paths on
    #    ANY hostname (the attacker may know our public name).
    norm_path = '/' + (parsed_path or '').lstrip('/')
    for deny in _SELF_TARGET_PATH_DENYLIST:
        if norm_path.startswith(deny):
            raise ValidationError(
                "URL targets a platform webhook/callback endpoint — "
                "self-referential webhooks are not allowed."
            )

    # 3. IP literal checks (v4/v6, incl. alternate decimal/hex forms)
    try:
        # Non-standard encodings: ipaddress only parses dotted/IPv6, so
        # convert integer forms first.
        if hostname.isdigit() and hostname != '':
            hostname = str(ipaddress.ip_address(int(hostname)))
    except (ValueError, OverflowError):
        pass
    _check_ip_text(hostname)

    # 4. Resolve and require ALL addresses public
    _resolve_and_check(hostname)
