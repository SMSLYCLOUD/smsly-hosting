"""
Caddy configuration manager.

Generates Caddyfile content from PlatformConfig and writes it
to a shared volume for the host-side watcher to pick up.
"""

import ipaddress
import logging
import os

from apps.deployments.domain_utils import normalize_domain

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


def _remote_server_mesh_ip(server) -> str:
    """Return the best WireGuard address for a remote service server."""
    if not server or getattr(server, "is_primary", False):
        return ""

    address = str(getattr(server, "wg_address", "") or "").strip()
    if address:
        return address

    try:
        peer = server.wg_peers.filter(is_active=True).order_by("-updated_at").first()
        if peer and peer.wg_address:
            return str(peer.wg_address)
    except Exception:  # pylint: disable=broad-exception-caught
        return ""

    return ""


def _remote_upstream_url_for_service(service) -> str:
    mesh_ip = _remote_server_mesh_ip(getattr(service, "server", None))
    if not mesh_ip:
        return ""
    return f"https://{mesh_ip}"


def _append_reverse_proxy(lines: list[str], upstream_url: str, upstream_host: str = ""):
    """Append a Caddy reverse_proxy stanza, including remote TLS transport if needed."""
    upstream_url = upstream_url or "localhost:8081"
    if upstream_host:
        lines.append(f"    reverse_proxy {upstream_url} {{")
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
        _append_reverse_proxy(lines, "nginx:80", upstream_host)
    else:
        _append_reverse_proxy(lines, "nginx:80")

    lines.extend(
        [
            "    encode gzip",
            "    log {",
            "        output file /var/log/caddy/access.log",
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
        from apps.deployments.models import Service

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

            from apps.domains.models import Domain, DomainStatus
            from django.db.models import Q
            routed_domains = (
                Domain.objects
                .filter(
                    service=service,
                    status__in=[
                        DomainStatus.ACTIVE,
                        DomainStatus.DNS_VERIFIED,
                        DomainStatus.SSL_PROVISIONING,
                    ],
                )
                .filter(Q(verified=True) | Q(status=DomainStatus.ACTIVE))
            )
            for domain_obj in routed_domains:
                value = domain_obj.domain_name.strip()
                if not value:
                    continue
                try:
                    value = normalize_domain(value)
                except ValueError:
                    continue
                if value in seen:
                    continue
                # Also skip custom domains covered by the wildcard
                if wildcard_domain and value.endswith(f".{wildcard_domain}"):
                    continue
                seen.add(value)
                target_host = public_domain or value
                
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
                    _append_reverse_proxy(lines, "nginx:80", target_host)
                else:
                    _append_reverse_proxy(lines, "nginx:80")
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
    reverse_proxy nginx:80
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
        from apps.deployments.models import Service
        from apps.deployments.models_addons import Addon

        # Schema Guard: Check if tables exist before querying
        if not _table_exists(Service._meta.db_table):
            return []

        suffix = f".{wildcard_domain}"
        for service in Service.objects.all().only("id", "public_domain", "custom_domains", "public_domain_hidden"):
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
        from apps.deployments.models import Service

        # Schema Guard: Check if tables exist before querying
        if not _table_exists(Service._meta.db_table):
            return {}

        suffix = f".{wildcard_domain}"
        for service in Service.objects.select_related("server").all():
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


def generate_caddyfile(config) -> str:
    """
    Generate Caddyfile content from a PlatformConfig instance.
    Uses On-Demand TLS for custom domains.
    """
    sections = []
    domain = ""
    cloudflare_token = (getattr(config, "cloudflare_api_token", "") or "").strip()
    
    # Global options for On-Demand TLS
    sections.append("""{
    on_demand_tls {
        ask http://nginx:80/api/v1/services/check-domain/
    }
}""")

    # Reject placeholder/dummy tokens
    _FAKE_TOKENS = {"fake", "changeme", "your_cloudflare_api_token", "test", ""}
    if cloudflare_token.lower() in _FAKE_TOKENS or cloudflare_token.startswith("your_"):
        cloudflare_token = ""

    import os
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
                "    reverse_proxy nginx:80",
                "    encode gzip",
                "    log {",
                "        output file /var/log/caddy/access.log",
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
                "    tls {",
                f"        dns cloudflare {cloudflare_token}",
                "    }",
            ]

            for index, (upstream_url, hosts) in enumerate(sorted(wildcard_remote_hosts.items())):
                if not hosts:
                    continue
                matcher = f"@remote_hosts_{index}"
                wildcard_lines.extend(
                    [
                        f"    {matcher} host {' '.join(hosts)}",
                        f"    handle {matcher} {{",
                        f"        reverse_proxy {upstream_url} {{",
                        "            header_up Host {host}",
                        "            transport http {",
                        "                tls",
                        "                tls_server_name {host}",
                        "            }",
                        "        }",
                        "    }",
                    ]
                )

            if wildcard_known_hosts:
                wildcard_lines.extend(
                    [
                        f"    @known_hosts host {' '.join(wildcard_known_hosts)}",
                        "    handle @known_hosts {",
                        "        reverse_proxy nginx:80",
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
    if domain:
        if _is_ip(domain):
            # Explicitly use http:// for IP to prevent Caddy's auto-HTTPS loop
            sections.append(
                f"""http://{domain} {{
    reverse_proxy nginx:80
    encode gzip
}}"""
            )
        elif not use_ssl:
            sections.append(
                f"""http://{domain} {{
    reverse_proxy nginx:80
    encode gzip
}}"""
            )

    # Unified :443 block — handles both IPs (self-signed + HTTP redirect) and
    # real domains (on-demand Let's Encrypt). The @ip matcher runs first.
    sections.append(
        """:443 {
    @ip host `([0-9]{1,3}[.]){3}[0-9]{1,3}$`
    handle @ip {
        route {
            tls internal
            redir http://{host}{uri} 308
        }
    }
    handle {
        route {
            tls {
                on_demand
            }
            reverse_proxy nginx:80
        }
    }
}"""
    )

    # Always include :80 catch-all with ACME challenge exemption.
    if use_ssl and domain:
        sections.append(
            """:80 {
    @acme {
        path /.well-known/acme-challenge/*
    }
    handle @acme {
        reverse_proxy nginx:80
    }
    @redirectable {
        not header_regexp host ^([0-9]{1,3}[.]){3}[0-9]{1,3}$
        not host localhost
        not host 127.0.0.1
        not host *.local
        header_regexp host .+
    }
    redir @redirectable https://{host}{uri} 308
    handle {
        reverse_proxy nginx:80
    }
}"""
        )
    else:
        # Check if we already added a block for the domain/IP on :80
        # If we didn't, add the catch-all.
        if not domain or (not _is_ip(domain) and not use_ssl):
            sections.append(
                """:80 {
    reverse_proxy nginx:80
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


def _load_cached_token() -> str:
    """Return a token from env or cache (best-effort, empty on failure)."""
    token = (os.environ.get("CLOUDFLARE_API_TOKEN") or "").strip()
    if token:
        return token
    try:
        if os.path.exists(CADDY_TOKEN_CACHE):
            with open(CADDY_TOKEN_CACHE, "r", encoding="utf-8") as handle:
                return (handle.read() or "").strip()
    except OSError:
        return ""
    return ""


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
    result = {"ok": False, "message": ""}

    cloudflare_token = (cloudflare_token or "").strip()
    if not cloudflare_token and preserve_existing_token:
        cloudflare_token = _load_cached_token()

    try:
        os.makedirs(CADDY_CONFIG_DIR, exist_ok=True)

        with open(CADDY_FILE_PATH, "w", encoding="utf-8") as handle:
            handle.write(content)

        if cloudflare_token:
            # SEC-003: Restrict shared volume directory to owner-only read
            os.chmod(CADDY_CONFIG_DIR, 0o700)
            # Write Cloudflare token to shared volume for the host watcher
            # to sync into Caddy's systemd environment override.
            with os.fdopen(os.open(CADDY_TOKEN_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w", encoding="utf-8") as handle:
                handle.write(cloudflare_token)
            # Persist a cache so future apply runs without an explicit token
            # do not unintentionally clear wildcard TLS.
            with os.fdopen(os.open(CADDY_TOKEN_CACHE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w", encoding="utf-8") as handle:
                handle.write(cloudflare_token)
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

        # Trigger reload via Docker API
        import subprocess
        logger.info("Triggering Caddy reload via Docker...")
        res = subprocess.run(
            ["docker", "exec", "smsly-hosting-caddy-1", "caddy", "reload", "--config", "/etc/caddy/Caddyfile"],
            capture_output=True,
            text=True
        )
        if res.returncode != 0:
            logger.error("Caddy reload failed: %s", res.stderr)
            result["message"] = f"Caddy reload failed: {res.stderr}"
            return result

        result["ok"] = True
        result["message"] = "Caddyfile written and reloaded successfully"
        logger.info("Caddyfile written to %s", CADDY_FILE_PATH)

    except Exception as exc:
        result["message"] = f"Failed to apply Caddyfile: {exc}"
        if isinstance(exc, PermissionError):
            result["message"] += (
                " | Fix host dir perms: sudo chown -R 1000:1000 /opt/smsly-hosting/caddy-config "
                "&& sudo chmod 775 /opt/smsly-hosting/caddy-config"
            )
        logger.error("Failed to write Caddyfile: %s", exc)

    return result
