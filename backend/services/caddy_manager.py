"""
Caddy configuration manager.

Generates Caddyfile content from PlatformConfig and writes it
to a shared volume for the host-side watcher to pick up.
"""

import contextlib
import datetime
import ipaddress
import json
import logging
import os
import re
import secrets
import subprocess
import time

from apps.deployments.domain_utils import normalize_domain
from django.conf import settings

logger = logging.getLogger(__name__)

# Path inside the container where caddy-config volume is mounted
CADDY_CONFIG_DIR = os.environ.get("CADDY_CONFIG_DIR", "/caddy-config")
CADDY_FILE_PATH = os.path.join(CADDY_CONFIG_DIR, "Caddyfile")
CADDY_RELOAD_FLAG = os.path.join(CADDY_CONFIG_DIR, ".reload")
CADDY_TOKEN_FILE = os.path.join(CADDY_CONFIG_DIR, ".cloudflare_token")
CADDY_TOKEN_CLEAR_FILE = os.path.join(CADDY_CONFIG_DIR, ".cloudflare_token_clear")
# Cache the last known-good token so accidental empty writes (e.g. background
# tasks that don't load the token) do not wipe DNS-challenge capability.
CADDY_TOKEN_CACHE = os.path.join(CADDY_CONFIG_DIR, ".cloudflare_token_cache")
# Cache entries expire after this many seconds (default: 30 days).
# After expiry the cache is treated as missing and the operator must
# re-supply a token. This prevents the cache from effectively becoming
# permanent when combined with preserve_existing_token=True.
CADDY_TOKEN_CACHE_TTL_SECONDS = 30 * 24 * 60 * 60
SERVICE_PROXY_UPSTREAM = os.environ.get("SMSLY_SERVICE_PROXY_UPSTREAM", "traefik:80")
CONTROL_PLANE_UPSTREAMS = {
    "backend:8000",
    "http://backend:8000",
    "frontend:3000",
    "http://frontend:3000",
    "localhost:8090",
    "http://localhost:8090",
    "127.0.0.1:8090",
    "http://127.0.0.1:8090",
}


def caddy_disabled_mode() -> bool:
    """Return True when this runtime topology does not include Caddy."""
    mode = str(os.environ.get("MODE", "")).strip().lower()
    node_type = str(os.environ.get("NODE_TYPE", "")).strip().lower()
    return mode in {"agent", "agent-lite", "node"} or node_type in {
        "agent",
        "agent-lite",
        "node",
    }


def is_agent_lite() -> bool:
    """Backward-compatible helper for agent-lite checks."""
    mode = str(os.environ.get("MODE", "")).strip().lower()
    node_type = str(os.environ.get("NODE_TYPE", "")).strip().lower()
    return mode in {"agent", "agent-lite"} or node_type in {"agent", "agent-lite"}


def _generate_selfsigned_cert(cert_path: str, key_path: str, ip_address: str):
    """Generate a self-signed X.509 cert with the IP as SAN using cryptography.

    Always regenerates to ensure the cert stays current with the server's IP.
    (The RSA key generation + self-sign takes <100ms.)
    """
    try:
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID
    except ImportError:
        logger.warning("cryptography not available; skipping self-signed cert")
        return

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, ip_address or "localhost"),
    ])
    now = datetime.datetime.now(datetime.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        # Mask high bit to ensure positive serial number (RFC 5280 §4.1.2.2)
        .serial_number(int.from_bytes(secrets.token_bytes(8), "big") & 0x7FFFFFFFFFFFFFFF)
        .not_valid_before(now)
        .not_valid_after(now + datetime.timedelta(days=3650))
        .add_extension(
            x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address(ip_address))]),
            critical=False,
        )
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                key_encipherment=True,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=False,
                crl_sign=False,
                encipher_only=False,
                decipher_only=False,
                content_commitment=False,
            ),
            critical=True,
        )
        .add_extension(
            x509.ExtendedKeyUsage([x509.ExtendedKeyUsageOID.SERVER_AUTH]),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    os.makedirs(os.path.dirname(cert_path), exist_ok=True)
    with open(cert_path, "wb") as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))
    with open(key_path, "wb") as f:
        f.write(key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        ))
    # Caddy container runs as UID 1000; backend creates files as root.
    # chmod 644 ensures Caddy can read both cert and key.
    os.chmod(cert_path, 0o644)
    os.chmod(key_path, 0o644)
    logger.info("Generated self-signed cert for IP: %s", ip_address)


def _table_exists(table_name: str) -> bool:
    """Helper to check if a database table exists without triggering an exception."""
    from django.db import connection
    try:
        return table_name in connection.introspection.table_names()
    except Exception:  # pylint: disable=broad-exception-caught
        return False


def _is_ip(domain: str) -> bool:
    """Return True if the domain string is a raw IP address."""
    if not domain:
        return False
    try:
        ipaddress.ip_address(domain)
        return True
    except ValueError:
        return False


def _normalize_upstream_ip(value: str) -> str:
    """Return a bare IP from stored IP/CIDR values."""
    value = str(value or "").strip()
    if not value:
        return ""
    try:
        return str(ipaddress.ip_interface(value).ip)
    except ValueError:
        return value.split("/", 1)[0].strip()


def _remote_server_mesh_ip(server) -> str:
    """Return the best WireGuard address for a remote service server."""
    if not server or getattr(server, "is_primary", False):
        return ""

    # Try "default" mesh first to be extremely robust
    try:
        peer = server.wg_peers.filter(mesh__name="default", is_active=True).first()
        if peer and peer.wg_address:
            return _normalize_upstream_ip(peer.wg_address)
    except Exception:
        pass

    address = str(getattr(server, "wg_address", "") or "").strip()
    if address:
        return _normalize_upstream_ip(address)

    try:
        peer = server.wg_peers.filter(is_active=True).order_by("-updated_at").first()
        if peer and peer.wg_address:
            return _normalize_upstream_ip(peer.wg_address)
    except Exception:  # pylint: disable=broad-exception-caught
        return ""

    return ""


def _remote_upstream_url_for_service(service) -> str:
    """Return proxy upstreams for a remote service, with public fallback.

    Caddy accepts multiple upstreams on a single reverse_proxy line. Keeping
    the public node host after the mesh IP prevents stale WireGuard state from
    turning transferred services into hard 502s.
    """
    server = getattr(service, "server", None)
    if not server or getattr(server, "is_primary", False):
        return ""

    upstreams: list[str] = []

    def append(url: str):
        if url and url not in upstreams:
            upstreams.append(url)

    # Priority 1: WireGuard Mesh (Secure & Private)
    mesh_ip = _remote_server_mesh_ip(server)
    if mesh_ip:
        append(f"http://{mesh_ip}")

    # Priority 2: Public IP Fallback (Remote nodes listen on port 80 via Traefik)
    host = str(server.host or "").strip()
    if host:
        # SEC-ZT-005: Inter-server TLS enforcement is handled by the reverse_proxy
        # transport logic if the host supports HTTPS. For now, we proxy to port 80
        # as that is where Traefik expects incoming edge traffic on remote nodes.
        append(f"http://{host}")

    return " ".join(upstreams)


def _service_proxy_upstream() -> str:
    """Return the internal edge upstream used for deployed app domains."""
    return SERVICE_PROXY_UPSTREAM or "traefik:80"


def _append_reverse_proxy(lines: list[str], upstream_url: str, upstream_host: str = ""):
    """Append a Caddy reverse_proxy stanza, including remote TLS transport if needed."""
    upstream_url = upstream_url or _service_proxy_upstream()
    has_fallbacks = len(str(upstream_url).split()) > 1
    if upstream_host or has_fallbacks:
        lines.append(f"    reverse_proxy {upstream_url} {{")
        if has_fallbacks:
            lines.append("        lb_try_duration 5s")
            lines.append("        lb_try_interval 250ms")
        if upstream_host:
            lines.append(f"        header_up Host {upstream_host}")
        if upstream_url.startswith("https://") and upstream_host:
            lines.append("        transport http {")
            lines.append("            tls")
            lines.append(f"            tls_server_name {upstream_host}")
            lines.append("        }")
        lines.append("    }")
    else:
        lines.append(f"    reverse_proxy {upstream_url}")


def _build_service_domain_block(domain: str, upstream_host: str, upstream_url: str = "") -> str:
    """
    Build a Caddy site block for a service domain routed via Traefik.

    When `domain` differs from `upstream_host` (custom domain), Caddy rewrites
    the upstream Host header to the service's canonical public domain so Traefik
    can route immediately without requiring container label updates/redeploy.
    """
    lines = [f"{domain} {{"]

    if upstream_url:
        _append_reverse_proxy(lines, upstream_url, upstream_host or domain)
    elif upstream_host and upstream_host != domain:
        _append_reverse_proxy(lines, _service_proxy_upstream(), upstream_host)
    else:
        _append_reverse_proxy(lines, _service_proxy_upstream())

    lines.extend(
        [
            "    encode gzip",
            "    log {",
            "        output stdout",
            "    }",
            "}",
        ]
    )
    return "\n".join(lines)


def _get_service_domain_blocks(wildcard_domain: str = "") -> list:
    """
    Query all services and generate Caddy blocks for routable app domains.
    Uses On-Demand TLS for custom domains to minimize reloads.
    """
    blocks = []
    seen = set()
    # Note: Caddy global 'ask' endpoint is defined in generate_caddyfile.

    try:
        from apps.deployments.models import Service  # type: ignore[attr-defined]

        # Schema Guard: Check if tables exist before querying
        if not _table_exists(Service._meta.db_table):
            return []

        for service in Service.objects.all().select_related("server").order_by("id"):
            raw_public = (
                str(service.public_domain or "").strip().lower()
                if isinstance(service.public_domain, str)
                else ""
            )
            public_domain = ""
            if raw_public:
                try:
                    public_domain = normalize_domain(raw_public)
                except ValueError:
                    logger.warning(
                        "Skipping invalid public domain %r for service %s",
                        service.public_domain,
                        service.id,
                    )

            # Skip creating a public_domain block if it's hidden OR covered by wildcard.
            isHidden = getattr(service, "public_domain_hidden", False)
            if public_domain and public_domain not in seen:
                if isHidden:
                    logger.debug("Skipping hidden public domain block for %s", public_domain)
                    seen.add(public_domain)
                elif wildcard_domain and public_domain.endswith(f".{wildcard_domain}"):
                    logger.debug(
                        "Skipping %s \u2014 covered by wildcard *.%s",
                        public_domain,
                        wildcard_domain,
                    )
                    seen.add(public_domain)
                else:
                    seen.add(public_domain)
                    blocks.append(
                        _build_service_domain_block(
                            public_domain,
                            public_domain,
                            upstream_url=_remote_upstream_url_for_service(service),
                        )
                    )

            # Include ALL custom domain blocks in Caddyfile from the moment
            # they are added, gated by on_demand TLS. The check-domain ask
            # endpoint returns 200 only when DNS is verified, so Caddy will
            # not issue certificates for unverified domains. This eliminates
            # the timing gap between domain add and DNS verification where
            # a failed Caddy reload could leave the domain block missing.
            from apps.domains.models import Domain
            for domain_obj in Domain.objects.filter(service=service):
                value = domain_obj.domain_name.strip()
                if not value:
                    continue
                try:
                    value = normalize_domain(value)
                except ValueError:
                    continue
                if value in seen:
                    continue
                seen.add(value)
                target_host = public_domain if (public_domain and not isHidden) else value

                # Custom domains use direct on-demand TLS. Do not attach the
                # platform Cloudflare DNS challenge; customers may use any DNS
                # provider as long as public DNS points here.
                lines = [f"{value} {{"]
                lines.append("    tls {")
                lines.append("        on_demand")
                lines.append("    }")

                upstream_url = _remote_upstream_url_for_service(service)
                if upstream_url:
                    _append_reverse_proxy(lines, upstream_url, target_host or value)
                elif target_host and target_host != value:
                    _append_reverse_proxy(lines, _service_proxy_upstream(), target_host)
                else:
                    _append_reverse_proxy(lines, _service_proxy_upstream())
                lines.append("    encode gzip")
                lines.append("}")
                blocks.append("\n".join(lines))

        from apps.deployments.models_addons import Addon
        for addon in Addon.objects.exclude(public_domain__isnull=True).exclude(public_domain=""):
            public_domain = ""
            try:
                public_domain = normalize_domain(addon.public_domain.strip())
            except ValueError:
                continue

            if public_domain and public_domain not in seen:
                if wildcard_domain and public_domain.endswith(f".{wildcard_domain}"):
                    logger.debug(
                        "Skipping addon %s \u2014 covered by wildcard *.%s",
                        public_domain,
                        wildcard_domain,
                    )
                else:
                    blocks.append(
                        f"""{public_domain} {{
    reverse_proxy {_service_proxy_upstream()}
}}"""
                    )
                    seen.add(public_domain)

    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.warning("Could not load service domains for Caddyfile: %s", exc)
    return blocks


def _get_wildcard_known_hosts(wildcard_domain: str) -> list[str]:
    """
    Return service domains that should be routed through Traefik under wildcard TLS.

    Unknown wildcard subdomains are intentionally excluded so they can be
    redirected to the public notice page instead of exposing generic proxy 404s.
    """
    hosts: set[str] = set()
    if not wildcard_domain:
        return []

    try:
        from apps.deployments.models import Service  # type: ignore[attr-defined]
        from apps.deployments.models_addons import Addon

        # Schema Guard: Check if tables exist before querying
        if not _table_exists(Service._meta.db_table):
            return []

        suffix = f".{wildcard_domain}"
        for service in Service.objects.select_related("server").only("id", "public_domain", "custom_domains", "public_domain_hidden", "server__is_primary", "is_preview").all():
            # Skip services assigned to remote nodes — they are served via
            # _get_wildcard_remote_host_map and its @remote_hosts blocks.
            svr = getattr(service, "server", None)
            if svr and not svr.is_primary:
                continue
            # Skip local preview services - they are proxied directly to the container
            if getattr(service, "is_preview", False):
                continue
            public_domain = ""
            if getattr(service, "public_domain_hidden", False):
                public_domain = ""
            elif isinstance(service.public_domain, str) and service.public_domain.strip():
                try:
                    public_domain = normalize_domain(service.public_domain)
                except ValueError:
                    logger.warning(
                        "Skipping invalid public domain %r for service %s",
                        service.public_domain,
                        service.id,
                    )
            if public_domain.endswith(suffix):
                hosts.add(public_domain)

            for item in (service.custom_domains or []):
                value = item.strip() if isinstance(item, str) else ""
                if not value:
                    continue
                try:
                    value = normalize_domain(value)
                except ValueError:
                    logger.warning(
                        "Skipping invalid custom domain %r for service %s",
                        item,
                        service.id,
                    )
                    continue
                if value.endswith(suffix):
                    hosts.add(value)

        for addon in Addon.objects.exclude(public_domain__isnull=True).exclude(public_domain=""):
            public_domain = ""
            try:
                public_domain = normalize_domain(addon.public_domain.strip())
            except ValueError:
                logger.warning(
                    "Skipping invalid public domain %r for addon %s",
                    addon.public_domain,
                    addon.id,
                )
                continue
            if public_domain.endswith(suffix):
                hosts.add(public_domain)

    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.warning("Could not load wildcard known hosts: %s", exc)
        return []

    return sorted(hosts)


def _get_wildcard_remote_host_map(wildcard_domain: str) -> dict[str, list[str]]:
    """
    Return wildcard-covered service hosts that live on remote mesh nodes.

    The result maps upstream URL (https://wg-ip) to hostnames. Caddy can then
    keep public DNS pointed at the controller while proxying remote services
    over WireGuard.
    """
    remote_hosts: dict[str, set[str]] = {}
    if not wildcard_domain:
        return {}

    try:
        from apps.deployments.models import Service  # type: ignore[attr-defined]

        # Schema Guard: Check if tables exist before querying
        if not _table_exists(Service._meta.db_table):
            return {}

        suffix = f".{wildcard_domain}"
        for service in Service.objects.select_related("server").all():
            svr = getattr(service, "server", None)
            if not svr or svr.is_primary:
                continue
            upstream_url = _remote_upstream_url_for_service(service)
            if not upstream_url:
                continue

            service_hosts = set()
            if not getattr(service, "public_domain_hidden", False):
                raw_public = str(service.public_domain or "").strip()
                if raw_public:
                    try:
                        public_domain = normalize_domain(raw_public)
                        if public_domain.endswith(suffix):
                            service_hosts.add(public_domain)
                    except ValueError:
                        pass

            for item in (service.custom_domains or []):
                raw_value = item.strip() if isinstance(item, str) else ""
                if not raw_value:
                    continue
                try:
                    value = normalize_domain(raw_value)
                except ValueError:
                    continue
                if value.endswith(suffix):
                    service_hosts.add(value)

            if service_hosts:
                remote_hosts.setdefault(upstream_url, set()).update(service_hosts)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.warning("Could not load remote wildcard service domains: %s", exc)
        return {}

    return {upstream: sorted(hosts) for upstream, hosts in remote_hosts.items()}


def _normalize_caddy_site_label(label: str) -> str:
    """Normalize a Caddy site label for comparison with service domains."""
    value = str(label or "").strip().strip(",")
    value = re.sub(r"^https?://", "", value, flags=re.IGNORECASE)
    if value.startswith("[") and "]" in value:
        return value
    if ":" in value and not value.startswith(":"):
        value = value.split(":", 1)[0]
    return value.strip().lower().rstrip(".")


def _known_service_route_domains() -> set[str]:
    """Return service/addon domains that must never route to the control plane."""
    domains: set[str] = set()
    try:
        from apps.deployments.models import Service  # type: ignore[attr-defined]
        from apps.deployments.models_addons import Addon

        if not _table_exists(Service._meta.db_table):
            return domains

        for service in Service.objects.all().only("public_domain", "custom_domains", "public_domain_hidden"):
            if not getattr(service, "public_domain_hidden", False):
                raw_public = str(service.public_domain or "").strip()
                if raw_public:
                    try:
                        domains.add(normalize_domain(raw_public, allow_ip=True))
                    except ValueError:
                        logger.warning("Skipping invalid service public domain in guard: %r", raw_public)

            for item in service.custom_domains or []:
                raw_custom = item.strip() if isinstance(item, str) else ""
                if not raw_custom:
                    continue
                try:
                    domains.add(normalize_domain(raw_custom, allow_ip=True))
                except ValueError:
                    logger.warning("Skipping invalid service custom domain in guard: %r", raw_custom)

        for addon in Addon.objects.exclude(public_domain__isnull=True).exclude(public_domain=""):
            raw_domain = str(addon.public_domain or "").strip()
            if not raw_domain:
                continue
            try:
                domains.add(normalize_domain(raw_domain, allow_ip=True))
            except ValueError:
                logger.warning("Skipping invalid addon public domain in guard: %r", raw_domain)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.warning("Could not load service route domains for Caddy guard: %s", exc)
    return domains


def _block_reverse_proxies_to_control_plane(block: str) -> list[str]:
    """Return control-plane reverse_proxy lines from a Caddy block."""
    matches = []
    for raw_line in str(block or "").splitlines():
        line = raw_line.strip()
        if not line.startswith("reverse_proxy "):
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        upstream = parts[1].strip("{").strip()
        if upstream in CONTROL_PLANE_UPSTREAMS:
            matches.append(line)
    return matches


def validate_service_routes_do_not_hit_control_plane(content: str) -> list[str]:
    """
    Fail-closed guard for Caddyfile writes.

    Platform domains may proxy to the control plane, but service and addon domains must
    never do that. If they do, the deployed URL serves the PaaS homepage.
    """
    service_domains = _known_service_route_domains()
    if not service_domains:
        return []

    errors = []
    lines = str(content or "").splitlines()
    for index, raw_line in enumerate(lines):
        stripped = raw_line.strip()
        if not stripped.endswith("{"):
            continue

        labels = [
            _normalize_caddy_site_label(label)
            for label in stripped[:-1].strip().split()
            if label.strip()
        ]
        is_service_site = any(label in service_domains for label in labels)

        block_lines = [raw_line]
        for next_line in lines[index + 1:]:
            next_stripped = next_line.strip()
            if (
                next_line == next_line.lstrip()
                and next_stripped.endswith("{")
                and next_stripped != "{"
            ):
                break
            block_lines.append(next_line)
        block = "\n".join(block_lines)

        wildcard_service_hosts = False
        if not is_service_site and any(label.startswith("*.") for label in labels):
            for block_line in block_lines:
                block_stripped = block_line.strip()
                if not block_stripped.startswith("@") or " host " not in block_stripped:
                    continue
                _, _, host_list = block_stripped.partition(" host ")
                wildcard_hosts = {
                    _normalize_caddy_site_label(host)
                    for host in host_list.split()
                }
                if wildcard_hosts & service_domains:
                    wildcard_service_hosts = True
                    break

        if not is_service_site and not wildcard_service_hosts:
            continue

        bad_lines = _block_reverse_proxies_to_control_plane(block)
        if not bad_lines:
            continue

        route_name = ", ".join(label for label in labels if label) or f"block:{index + 1}"
        errors.append(
            f"{route_name} routes a service domain to the control plane: {bad_lines[0]}"
        )

    return errors


def generate_caddyfile(config) -> str:
    """
    Generate Caddyfile content from a PlatformConfig instance.
    Uses On-Demand TLS for custom domains.
    """
    if is_agent_lite():
        logger.debug("Agent-lite mode: skipping generate_caddyfile()")
        return ""

    sections = []
    domain = ""
    cloudflare_token = (getattr(config, "cloudflare_api_token", "") or "").strip()

    # Global options for On-Demand TLS
    # The secret is embedded as a query parameter (Caddy v2's on_demand_tls.ask
    # cannot send custom headers, but it CAN include query strings in the URL).
    _ask_secret = ""
    try:
        from apps.deployments.models_core import PlatformConfig
        _cfg = PlatformConfig.load()
        _ask_secret = str(getattr(_cfg, 'caddy_ask_secret', '') or '').strip()
    except Exception:
        pass
    if not _ask_secret:
        _ask_secret = str(getattr(settings, "CADDY_ASK_SECRET", "") or "")
    _ask_url = "http://backend:8000/api/v1/services/check-domain/"
    if _ask_secret:
        import urllib.parse
        _ask_url += f"?secret={urllib.parse.quote(_ask_secret, safe='')}"
    sections.append(f"""\u007b
    on_demand_tls \u007b
        ask {_ask_url}
    \u007d
\u007d""")

    # Reject placeholder/dummy tokens
    _FAKE_TOKENS = {
        "fake", "changeme", "your_cloudflare_api_token", "test", "",
        "dummy_token_for_testing",
    }
    if cloudflare_token.lower() in _FAKE_TOKENS or cloudflare_token.startswith("your_"):
        cloudflare_token = ""

    env_domain = os.environ.get("DOMAIN", "").strip()

    # 1. Determine Effective Domain/Protocol (Fallback to .env)
    effective_domain = config.domain if config.domain else env_domain
    use_ssl = config.use_ssl if config.domain else (os.environ.get("DEBUG", "False").lower() not in {"true", "1", "t"})

    if effective_domain:
        try:
            # Allow IP for platform domain normalization
            domain = normalize_domain(effective_domain, allow_ip=True)
            if _is_ip(domain):
                # IPs cannot have SSL certs (Let's Encrypt restriction)
                use_ssl = False
        except ValueError:
            logger.warning("Ignoring invalid platform domain in config: %r", effective_domain)

    if use_ssl and domain:
        # Keep the apex platform domain independent from wildcard DNS
        # validation. Wildcard routes need Cloudflare DNS-01, but the apex can
        # use Caddy's default HTTP-01 flow and should stay healthy even if the
        # wildcard token is temporarily invalid.
        platform_block = [f"{domain} {{"]
        platform_block.extend(
            [
                "    encode gzip",
                "    log {",
                "        output stdout",
                "    }",
                "    handle /api/* {",
                "        reverse_proxy backend:8000",
                "    }",
                "    handle /ws/* {",
                "        reverse_proxy backend:8000",
                "    }",
                "    handle /health* {",
                "        reverse_proxy backend:8000",
                "    }",
                "    # /auth/* covers the frontend's session->token exchange and",
                "    # /auth/user/ check used by AuthProvider. Without this handler",
                "    # the request falls through to the frontend, which 308-redirects",
                "    # the trailing-slash variant and 404s — causing AuthProvider to",
                "    # think every user is unauthenticated and triggering the protected-",
                "    # page -> /login redirect guard.",
                "    # /auth/* covers the frontend's session->token exchange and",
                "    # /auth/user/ check used by AuthProvider. Without this handler",
                "    # the request falls through to the frontend, which 308-redirects",
                "    # the trailing-slash variant and 404s — causing AuthProvider to",
                "    # think every user is unauthenticated and triggering the protected-",
                "    # page -> /login redirect guard.",
                "    #",
                "    # OAuth CONNECT flow: /auth/<provider>/callback goes to the",
                "    # FRONTEND (Next.js pages at app/auth/<provider>/callback/ extract",
                "    # the code and POST to /api/v1/integrations/...).  These matchers",
                "    # MUST come before the /auth/* catch-all below.",
                "    #",
                "    # OAuth LOGIN flow: the frontend links to /accounts/<provider>/login/",
                "    # which is already routed by handle /accounts/<provider>/* below.",
                "    # No rewrite is needed — allauth uses its standard callback URL",
                "    # /accounts/<provider>/login/callback/.",
                "    @oauth_github_connect   path /auth/github/callback*",
                "    @oauth_google_connect   path /auth/google/callback*",
                "    @oauth_gitlab_connect   path /auth/gitlab/callback* /auth/gitlab_oauth2/callback*",
                "    @oauth_bitbucket_connect path /auth/bitbucket/callback* /auth/bitbucket_oauth2/callback*",
                "    # Generic OAuth callback page (frontend reads ?auth_token=... or",
                "    # fetches session cookie). Must come BEFORE /auth/* catch-all.",
                "    @auth_callback_page     path /auth/callback*",
                "    handle @auth_callback_page {",
                "        reverse_proxy frontend:3000",
                "    }",
                "    handle @oauth_github_connect {",
                "        reverse_proxy frontend:3000",
                "    }",
                "    handle @oauth_google_connect {",
                "        reverse_proxy frontend:3000",
                "    }",
                "    handle @oauth_gitlab_connect {",
                "        reverse_proxy frontend:3000",
                "    }",
                "    handle @oauth_bitbucket_connect {",
                "        reverse_proxy frontend:3000",
                "    }",
                "    handle /auth/* {",
                "        reverse_proxy backend:8000",
                "    }",
                "    handle /health {",
                "        reverse_proxy backend:8000",
                "    }",
                "    handle /admin/* {",
                "        reverse_proxy backend:8000",
                "    }",
                "    handle /accounts/github/* {",
                "        reverse_proxy backend:8000",
                "    }",
                "    handle /accounts/google/* {",
                "        reverse_proxy backend:8000",
                "    }",
                "    handle /accounts/gitlab/* {",
                "        reverse_proxy backend:8000",
                "    }",
                "    handle /accounts/gitlab_oauth2/* {",
                "        reverse_proxy backend:8000",
                "    }",
                "    handle /accounts/bitbucket/* {",
                "        reverse_proxy backend:8000",
                "    }",
                "    handle /accounts/bitbucket_oauth2/* {",
                "        reverse_proxy backend:8000",
                "    }",
                "    handle /static/* {",
                "        reverse_proxy backend:8000",
                "    }",
                "    handle /media/* {",
                "        reverse_proxy backend:8000",
                "    }",
                "    handle /grafana {",
                "        reverse_proxy frontend:3000",
                "    }",
                "    handle /grafana/* {",
                "        reverse_proxy grafana:3000",
                "    }",
                "    handle /ui {",
                "        redir / 301",
                "    }",
                "    handle /ui/* {",
                "        redir / 301",
                "    }",
                "    handle /accounts/* {",
                "        reverse_proxy frontend:3000",
                "    }",
                "    handle {",
                "        reverse_proxy frontend:3000",
                "    }",
                "}",
            ]
        )
        sections.append("\n".join(platform_block))

        # Wildcard subdomains for deployed services.
        if config.wildcard_subdomains:
            wildcard_known_hosts = _get_wildcard_known_hosts(domain)
            wildcard_remote_hosts = _get_wildcard_remote_host_map(domain)
            wildcard_lines = [
                f"*.{domain} {{",
            ]
            # Require a valid Cloudflare token for DNS-01 (wildcard certs
            # can't use HTTP-01).  Without a token, skip the tls block so
            # Caddy doesn't error with "missing API token".
            if cloudflare_token:
                wildcard_lines.append("    tls {")
                wildcard_lines.append(f"        dns cloudflare {cloudflare_token}")
                wildcard_lines.append("    }")

            # Direct routing for local preview environments (bypass Traefik)
            local_previews = []
            try:
                from apps.deployments.models import Service  # type: ignore[attr-defined]
                if _table_exists(Service._meta.db_table):
                    local_previews = list(
                        Service.objects.select_related("server")
                        .filter(is_preview=True, server__is_primary=True, status=Service.Status.ACTIVE)
                        .only("name", "public_domain", "internal_port", "server__is_primary")
                    )
            except Exception as e:
                logger.warning("Failed to fetch local previews for Caddy: %s", e)

            for index, p_service in enumerate(local_previews):
                if not p_service.public_domain:
                    continue
                try:
                    p_domain = normalize_domain(p_service.public_domain)
                except ValueError:
                    continue
                if p_domain.endswith(f".{domain}"):
                    matcher = f"@local_preview_{index}"
                    port = getattr(p_service, "internal_port", 8000) or 8000
                    wildcard_lines.extend(
                        [
                            f"    {matcher} host {p_domain}",
                            f"    handle {matcher} {{",
                            f"        reverse_proxy {p_service.name}:{port}",
                            "    }",
                        ]
                    )

            for index, (upstream_url, hosts) in enumerate(sorted(wildcard_remote_hosts.items())):
                if not hosts:
                    continue
                matcher = f"@remote_hosts_{index}"
                upstream_has_fallbacks = len(str(upstream_url).split()) > 1
                wildcard_lines.extend(
                    [
                        f"    {matcher} host {' '.join(hosts)}",
                        f"    handle {matcher} {{",
                        f"        reverse_proxy {upstream_url} {{",
                    ]
                )
                if upstream_has_fallbacks:
                    wildcard_lines.extend(
                        [
                            "            lb_try_duration 5s",
                            "            lb_try_interval 250ms",
                        ]
                    )
                wildcard_lines.extend(
                    [
                        "            header_up Host {host}",
                        "        }",
                        "    }",
                    ]
                )

            if wildcard_known_hosts:
                wildcard_lines.extend(
                    [
                        f"    @known_hosts host {' '.join(wildcard_known_hosts)}",
                        "    handle @known_hosts {",
                        f"        reverse_proxy {_service_proxy_upstream()}",
                        "    }",
                    ]
                )

            wildcard_lines.extend(
                [
                    "    handle {",
                    "        respond \"Service Not Found\" 404",
                    "    }",
                    "}",
                ]
            )
            sections.append("\n".join(wildcard_lines))

    # Site block for the primary access point (Domain or IP)
    if domain and not use_ssl:
        sections.append(
            f"""http://{domain} {{
    encode gzip
    handle /api/* {{
        reverse_proxy backend:8000
    }}
    handle /ws/* {{
        reverse_proxy backend:8000
    }}
    handle /health {{
        reverse_proxy backend:8000
    }}
    handle /admin/* {{
        reverse_proxy backend:8000
    }}
    handle /static/* {{
        reverse_proxy backend:8000
    }}
    handle /media/* {{
        reverse_proxy backend:8000
    }}
    handle /grafana/* {{
        reverse_proxy grafana:3000
    }}
    handle /grafana {{
        reverse_proxy frontend:3000
    }}
    handle /accounts/github/* {{
        reverse_proxy backend:8000
    }}
    handle /accounts/google/* {{
        reverse_proxy backend:8000
    }}
    handle /ui {{
        redir / 301
    }}
    handle /ui/* {{
        redir / 301
    }}
    handle /accounts/* {{
        reverse_proxy frontend:3000
    }}
    handle {{
        reverse_proxy frontend:3000
    }}
}}"""
        )

    # Catch-all :443 with self-signed cert for IP redirect to HTTP.
    # The cert is generated here (at Caddyfile write time) so it's
    # guaranteed to exist regardless of install script timing.
    # Caddy's built-in tls internal does not support IP SANs, so we
    # generate a proper cert with Python's cryptography library.
    # Backend container sees volume at CADDY_CONFIG_DIR (default /caddy-config).
    # Caddy container sees same volume at /etc/caddy (fixed in docker-compose).
    _cert_dir = os.path.join(CADDY_CONFIG_DIR, "certs")
    _server_ip = str(getattr(config, "server_ip", "") or "").strip()
    _crt_path = os.path.join(_cert_dir, "ip.crt")
    _key_path = os.path.join(_cert_dir, "ip.key")
    # Paths from Caddy container's perspective
    # docker-compose mounts caddy_config at /config in the caddy container
    _caddy_crt = "/config/certs/ip.crt"
    _caddy_key = "/config/certs/ip.key"
    try:
        os.makedirs(_cert_dir, exist_ok=True)
        if _server_ip and ipaddress.ip_address(_server_ip):
            # Regenerate only if the IP in the cert doesn't match current IP
            # (avoids RSA keygen on every Caddyfile write for no benefit).
            _regenerate = True
            if os.path.exists(_crt_path):
                try:
                    from cryptography import x509
                    with open(_crt_path, "rb") as _cr:
                        _existing = x509.load_pem_x509_certificate(_cr.read())
                    _current_ip_obj = ipaddress.ip_address(_server_ip)
                    for _san in _existing.extensions.get_extension_for_class(x509.SubjectAlternativeName).value:
                        if isinstance(_san, x509.IPAddress) and _san.value == _current_ip_obj:
                            _regenerate = False
                            break
                except Exception:
                    _regenerate = True
            if _regenerate:
                _generate_selfsigned_cert(_crt_path, _key_path, _server_ip)
            # Fix permissions on existing certs that may have been created
            # with 600 by older install script versions (Caddy runs as UID 1000).
            for _f in (_crt_path, _key_path):
                if os.path.exists(_f):
                    try:
                        _mode = os.stat(_f).st_mode & 0o777
                        if _mode < 0o644:
                            os.chmod(_f, 0o644)
                    except OSError:
                        pass
        # if os.path.exists(_crt_path) and os.path.exists(_key_path) and _server_ip:
        #     # IP-specific HTTPS block using SNI routing.
        #     # Caddy routes TLS by SNI hostname: domain requests go to domain
        #     # blocks (Let's Encrypt), IP requests go here (self-signed + redirect).
        #     # WARNING: Defining an HTTPS block for the IP triggers Caddy's
        #     # Automatic HTTPS to aggressively redirect HTTP to HTTPS for the IP!
        #     sections.append(
        #         f"""{_server_ip} {{
        # tls {_caddy_crt} {_caddy_key}
        # redir http://{_server_ip}{{uri}} 308
        # }}"""
        #     )
    except Exception as _exc:
        logger.warning("Could not generate self-signed cert for IP redirect: %s", _exc)

    # Always include :80 catch-all. Caddy handles HTTP-01 ACME challenges
    # itself on port 80 (on_demand_tls) — DO NOT intercept /.well-known/
    # acme-challenge/* and proxy it to the backend, or Let's Encrypt
    # will never receive the challenge token and cert issuance silently fails.
    if use_ssl and domain:
        sections.append(
            """:80 {
    @redirectable {
        not header_regexp host ^([0-9]{1,3}[.]){3}[0-9]{1,3}(:[0-9]+)?$
        not host localhost
        not host 127.0.0.1
        not host *.local
        header_regexp host .+
    }
    redir @redirectable https://{host}{uri} 308

    handle /api/* {
        reverse_proxy backend:8000
    }
    handle /ws/* {
        reverse_proxy backend:8000
    }
    handle /health {
        reverse_proxy backend:8000
    }
    handle /admin/* {
        reverse_proxy backend:8000
    }
    handle /accounts/* {
        reverse_proxy backend:8000
    }
    handle /static/* {
        reverse_proxy backend:8000
    }
    handle /media/* {
        reverse_proxy backend:8000
    }
    handle /grafana/* {
        reverse_proxy grafana:3000
    }
    handle /grafana {
        reverse_proxy frontend:3000
    }
    handle /ui {
        redir / 301
    }
    handle /ui/* {
        redir / 301
    }
    handle {
        reverse_proxy frontend:3000
    }
    encode gzip
}"""
        )
    # Only add the generic :80 catch-all when there is no platform domain.
    # When a domain is configured (SSL or non-SSL), the domain-specific
    # http://domain block already handles port 80.
    elif not domain:
        sections.append(
            """:80 {
    handle /api/* {
        reverse_proxy backend:8000
    }
    handle /ws/* {
        reverse_proxy backend:8000
    }
    handle /health {
        reverse_proxy backend:8000
    }
    handle /admin/* {
        reverse_proxy backend:8000
    }
    handle /static/* {
        reverse_proxy backend:8000
    }
    handle /media/* {
        reverse_proxy backend:8000
    }
    handle /grafana/* {
        reverse_proxy grafana:3000
    }
    handle /grafana {
        reverse_proxy frontend:3000
    }
    handle {
        reverse_proxy frontend:3000
    }
    encode gzip
}"""
        )

    # Per-service custom domains routed to Traefik.
    # Skip subdomains already covered by the *.domain wildcard.
    # Custom domains (external) get their own blocks for HTTP-01 SSL.
    wildcard_base = domain if (config.use_ssl and config.wildcard_subdomains and cloudflare_token) else ""
    service_blocks = _get_service_domain_blocks(wildcard_domain=wildcard_base)
    sections.extend(service_blocks)

    header = "# CloudNeuron Caddyfile - Auto-generated by Settings UI\n"
    header += "# Do not edit manually; changes will be overwritten.\n\n"
    return header + "\n\n".join(sections) + "\n"


def _read_cached_token_payload() -> dict:
    """
    Read the raw token cache payload from disk.

    Returns an empty dict if the file is missing, unreadable, or in
    the legacy (unstructured) format that did not include a TTL.
    """
    try:
        if not os.path.exists(CADDY_TOKEN_CACHE):
            return {}
        with open(CADDY_TOKEN_CACHE, encoding="utf-8") as handle:
            raw = (handle.read() or "").strip()
    except OSError:
        return {}
    if not raw or not raw.startswith("{"):
        return {}
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return {}


def _load_cached_token() -> str:
    """
    Return a token from env or cache (best-effort, empty on failure).

    Cached entries are ignored (and the cache file is auto-invalidated)
    once they are older than CADDY_TOKEN_CACHE_TTL_SECONDS. Legacy cache
    files written before TTL support are treated as expired so we never
    silently resurrect a token that has been sitting on disk indefinitely.
    """
    token = (os.environ.get("CLOUDFLARE_API_TOKEN") or "").strip()
    if token:
        return token
    payload = _read_cached_token_payload()
    if not payload:
        return ""
    cached_token = (payload.get("token") or "").strip()
    expires_at = payload.get("expires_at")
    if not cached_token or not isinstance(expires_at, (int, float)):
        return ""
    now = time.time()
    if now >= expires_at:
        logger.warning(
            "Cloudflare token cache is stale (expired at %s, %s days old); ignoring.",
            datetime.datetime.fromtimestamp(expires_at, datetime.timezone.utc).isoformat(),
            int((now - expires_at) / 86400),
        )
        with contextlib.suppress(OSError):
            os.remove(CADDY_TOKEN_CACHE)
        return ""
    return cached_token


def clear_cached_token() -> bool:
    """
    Remove the Cloudflare token cache file from disk.

    Returns True if the file existed and was removed, False otherwise.
    Intended for explicit operator invalidation (admin CLI / endpoint).
    """
    try:
        if os.path.exists(CADDY_TOKEN_CACHE):
            os.remove(CADDY_TOKEN_CACHE)
            logger.info("Cloudflare token cache cleared by operator request")
            return True
    except OSError as exc:
        logger.warning("Failed to clear Cloudflare token cache: %s", exc)
    return False


def apply_caddyfile(content: str, cloudflare_token: str = "", preserve_existing_token: bool = True) -> dict:
    """
    Write Caddyfile to the shared volume and create a reload flag.
    The host-side watcher script picks up the flag and reloads Caddy.

    If cloudflare_token is provided, also write it to a token file so
    the host-side watcher can create the systemd environment override.
    This enables full SSL setup from the web UI without SSH access.

    Returns a status dict.
    If preserve_existing_token is True (default) and no token is provided,
    we attempt to reuse the last cached/override token to avoid forcing
    operators to re-enter the Cloudflare token after restarts or background
    sync jobs that didn't pass it through.
    """
    if caddy_disabled_mode():
        logger.debug("Caddy-disabled mode: skipping apply_caddyfile()")
        return {"ok": True, "message": "Skipped because Caddy is not part of this node"}

    result = {"ok": False, "message": ""}

    cloudflare_token = (cloudflare_token or "").strip()
    if not cloudflare_token and preserve_existing_token:
        cloudflare_token = _load_cached_token()

    try:
        route_errors = validate_service_routes_do_not_hit_control_plane(content)
        if route_errors:
            result["message"] = (
                "Refusing to apply Caddyfile because service routes would hit "
                f"the control plane: {'; '.join(route_errors[:5])}"
            )
            logger.error(result["message"])
            return result

        os.makedirs(CADDY_CONFIG_DIR, exist_ok=True)
        # Caddy container runs as uid 1000 and the host-side watcher also
        # touches the directory. If the volume was created by a different
        # process (e.g. a fresh `docker compose up` on a new host) the
        # permissions will silently block writes from this container.
        # Try a self-heal chmod first, and if that fails fall back to
        # chowning the dir so the Caddy container (uid 1000) can read it.
        try:
            os.chmod(CADDY_CONFIG_DIR, 0o775)
            # Touch a probe file to verify the dir is actually writable by
            # this process AND that the file mode allows the Caddy user to
            # read whatever we drop in.
            probe = os.path.join(CADDY_CONFIG_DIR, ".perm_probe")
            with open(probe, "w") as f:
                f.write("ok")
            os.remove(probe)
        except (OSError, PermissionError) as chmod_exc:
            # The dir is owned by another user (e.g. systemd-created).
            # Try to chown it so the Caddy container — which runs as
            # uid/gid 1000 — can read whatever we drop in. Do NOT
            # chown to os.getuid(); the backend runs as root, so that
            # would leave the dir root-owned and still unreadable by
            # Caddy.
            try:
                CADDY_UID = int(os.environ.get("CADDY_UID", "1000"))
                CADDY_GID = int(os.environ.get("CADDY_GID", "1000"))
                if hasattr(os, "chown"):
                    os.chown(CADDY_CONFIG_DIR, CADDY_UID, CADDY_GID)
                else:
                    logger.warning(
                        "os.chown unavailable on this platform; skipping ownership "
                        "change for %s (uid=%s gid=%s)",
                        CADDY_CONFIG_DIR, CADDY_UID, CADDY_GID,
                    )
                os.chmod(CADDY_CONFIG_DIR, 0o775)
                logger.warning(
                    "Self-healed caddy-config dir ownership to uid=%s gid=%s "
                    "(previous chmod failed: %s)",
                    CADDY_UID, CADDY_GID, chmod_exc,
                )
            except (OSError, PermissionError, ValueError) as chown_exc:
                raise PermissionError(
                    f"Cannot write to {CADDY_CONFIG_DIR}. "
                    "Fix host permissions: sudo chown -R 1000:1000 "
                    f"{CADDY_CONFIG_DIR} && sudo chmod 775 {CADDY_CONFIG_DIR}. "
                    f"chmod_error={chmod_exc} chown_error={chown_exc}"
                )

        with open(CADDY_FILE_PATH, "w", encoding="utf-8") as handle:
            handle.write(content)
        os.chmod(CADDY_FILE_PATH, 0o664)

        if cloudflare_token:
            # Caddy runs as uid 1000 — keep dir readable by group
            os.chmod(CADDY_CONFIG_DIR, 0o775)
            # Write Cloudflare token to shared volume for the host watcher
            # to sync into Caddy's systemd environment override.
            with os.fdopen(os.open(CADDY_TOKEN_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w", encoding="utf-8") as handle:
                handle.write(cloudflare_token)
            # Persist a cache so future apply runs without an explicit token
            # do not unintentionally clear wildcard TLS.
            # The cache stores a TTL so an old token is not silently
            # resurrected across rotations.
            cache_payload = json.dumps({
                "token": cloudflare_token,
                "expires_at": time.time() + CADDY_TOKEN_CACHE_TTL_SECONDS,
            })
            with os.fdopen(os.open(CADDY_TOKEN_CACHE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w", encoding="utf-8") as handle:
                handle.write(cache_payload)
            if os.path.exists(CADDY_TOKEN_CLEAR_FILE):
                os.remove(CADDY_TOKEN_CLEAR_FILE)
        else:
            if os.path.exists(CADDY_TOKEN_FILE):
                os.remove(CADDY_TOKEN_FILE)
            if os.path.exists(CADDY_TOKEN_CACHE):
                os.remove(CADDY_TOKEN_CACHE)
            # Signal watcher to explicitly remove any stale systemd override.
            with os.fdopen(os.open(CADDY_TOKEN_CLEAR_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w", encoding="utf-8") as handle:
                handle.write("clear")

        # Always write the .reload flag so the host-side watcher can pick it up.
        # This is the reliable reload path — docker exec may fail through the
        # socket-proxy (403), but the host-side watcher has direct socket access.
        try:
            with open(CADDY_RELOAD_FLAG, "w", encoding="utf-8") as f:
                f.write(str(int(__import__("time").time())))
            os.chmod(CADDY_RELOAD_FLAG, 0o664)
            logger.info("Wrote .reload flag to %s", CADDY_RELOAD_FLAG)
        except Exception as flag_exc:
            logger.warning("Failed to write .reload flag: %s", flag_exc)

        # Fire-and-forget: try docker exec for an immediate reload.
        # If it fails (e.g. socket-proxy 403), the host-side watcher will
        # handle the reload within a few seconds via the .reload flag.
        CONTAINER_NAME = "smsly-hosting-caddy-1"
        logger.info("Attempting fast-path Caddy reload via Docker exec %s...", CONTAINER_NAME)
        try:
            dock_res = subprocess.run(
                ["docker", "exec", CONTAINER_NAME, "caddy", "reload", "--config", "/etc/caddy/Caddyfile"],
                capture_output=True,
                text=True,
                timeout=15,
            )
            if dock_res.returncode == 0:
                result["ok"] = True
                result["message"] = "Caddyfile written and reloaded via Docker"
                logger.info("Caddy reloaded via docker exec on %s", CONTAINER_NAME)
            else:
                logger.info(
                    "Docker exec reload not available (%s) — host-side watcher will handle it",
                    dock_res.stderr.strip()[:200],
                )
                # Not a failure — the .reload flag ensures the host watcher applies it.
                result["ok"] = True
                result["message"] = "Caddyfile written; reload pending via host-side watcher"
        except FileNotFoundError:
            # docker CLI not installed in this container
            logger.info("Docker CLI not found in container — host-side watcher will handle reload")
            result["ok"] = True
            result["message"] = "Caddyfile written; reload pending via host-side watcher"
        except subprocess.TimeoutExpired:
            logger.info("Docker exec timed out — host-side watcher will handle reload")
            result["ok"] = True
            result["message"] = "Caddyfile written; reload pending via host-side watcher"
        except Exception as exec_exc:
            logger.info("Docker exec failed (%s) — host-side watcher will handle reload", exec_exc)
            result["ok"] = True
            result["message"] = "Caddyfile written; reload pending via host-side watcher"

    except Exception as exc:
        result["message"] = f"Failed to apply Caddyfile: {exc}"
        if isinstance(exc, PermissionError):
            result["message"] = str(result["message"]) + (
                " | Fix host dir perms: sudo chown -R 1000:1000 /opt/smsly-hosting/caddy-config "
                "&& sudo chmod 775 /opt/smsly-hosting/caddy-config"
            )
        logger.error("Failed to write Caddyfile: %s", exc)

    return result
