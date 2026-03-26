"""
Caddy configuration manager.

Generates Caddyfile content from PlatformConfig and writes it
to a shared volume for the host-side watcher to pick up.
"""

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


def _build_service_domain_block(domain: str, upstream_host: str) -> str:
    """
    Build a Caddy site block for a service domain routed via Traefik.

    When `domain` differs from `upstream_host` (custom domain), Caddy rewrites
    the upstream Host header to the service's canonical public domain so Traefik
    can route immediately without requiring container label updates/redeploy.
    """
    lines = [f"{domain} {{"]

    if upstream_host and upstream_host != domain:
        lines.extend(
            [
                "    reverse_proxy localhost:8081 {",
                f"        header_up Host {upstream_host}",
                "    }",
            ]
        )
    else:
        lines.append("    reverse_proxy localhost:8081")

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

    Includes service.custom_domains (external domains needing their own blocks)
    but SKIPS service.public_domain entries that are subdomains of the platform
    wildcard (e.g. *.pcloud.linadeluxe.com) because those are already covered
    by the wildcard block.  Adding duplicate explicit blocks for them triggers
    conflicting ACME cert provisioning that breaks Caddy during reload.
    """
    blocks = []
    seen = set()
    try:
        from apps.deployments.models import Service

        for service in Service.objects.all().order_by("id"):
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

            # Skip public_domain if it's covered by the wildcard block.
            # e.g. "abc.pcloud.linadeluxe.com" is already routed by
            # "*.pcloud.linadeluxe.com" — adding an explicit block would
            # trigger a separate HTTP-01 cert and break existing SSL.
            if public_domain and public_domain not in seen:
                if wildcard_domain and public_domain.endswith(f".{wildcard_domain}"):
                    logger.debug(
                        "Skipping %s — covered by wildcard *.%s",
                        public_domain,
                        wildcard_domain,
                    )
                else:
                    seen.add(public_domain)
                    blocks.append(
                        _build_service_domain_block(public_domain, public_domain)
                    )

            for domain in (service.custom_domains or []):
                value = domain.strip() if isinstance(domain, str) else ""
                if not value:
                    continue
                try:
                    value = normalize_domain(value)
                except ValueError:
                    logger.warning(
                        "Skipping invalid custom domain %r for service %s",
                        domain,
                        service.id,
                    )
                    continue
                if value in seen:
                    continue
                # Also skip custom domains covered by the wildcard
                if wildcard_domain and value.endswith(f".{wildcard_domain}"):
                    logger.debug(
                        "Skipping custom domain %s — covered by wildcard *.%s",
                        value,
                        wildcard_domain,
                    )
                    continue
                seen.add(value)
                target_host = public_domain or value
                blocks.append(_build_service_domain_block(value, target_host))
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
                        "Skipping addon %s — covered by wildcard *.%s",
                        public_domain,
                        wildcard_domain,
                    )
                else:
                    blocks.append(
                        f"""{public_domain} {{
    reverse_proxy localhost:8081
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

        for addon in Addon.objects.exclude(public_domain__isnull=True).exclude(public_domain__exact="").only("id", "public_domain"):
            try:
                addon_domain = normalize_domain(addon.public_domain)
                if addon_domain.endswith(suffix):
                    hosts.add(addon_domain)
            except ValueError:
                logger.warning(
                    "Skipping invalid public domain %r for addon %s",
                    addon.public_domain,
                    addon.id,
                )

    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.warning("Could not load wildcard known hosts: %s", exc)
        return []

    return sorted(hosts)


def generate_caddyfile(config) -> str:
    """
    Generate Caddyfile content from a PlatformConfig instance.

    Modes:
    - IP-only: simple :80 reverse proxy
    - SSL (no wildcard): domain block with auto HTTPS + :80 fallback
    - SSL + wildcard: domain block + *.domain with Cloudflare DNS challenge

    Also includes per-service public and custom domain blocks.
    """
    sections = []
    domain = ""
    cloudflare_token = (getattr(config, "cloudflare_api_token", "") or "").strip()
    # Reject placeholder/dummy tokens
    _FAKE_TOKENS = {"fake", "changeme", "your_cloudflare_api_token", "test", ""}
    if cloudflare_token.lower() in _FAKE_TOKENS or cloudflare_token.startswith("your_"):
        cloudflare_token = ""

    if config.domain:
        try:
            domain = normalize_domain(config.domain)
        except ValueError:
            logger.warning("Ignoring invalid platform domain in config: %r", config.domain)

    if config.use_ssl and domain:
        # In wildcard mode, also use DNS challenge for the platform domain.
        # This avoids HTTP-01 edge cases when the domain is proxied by Cloudflare.
        platform_block = [f"{domain} {{"]
        if config.wildcard_subdomains and cloudflare_token:
            platform_block.extend(
                [
                    "    tls {",
                    "        dns cloudflare {env.CLOUDFLARE_API_TOKEN}",
                    "    }",
                ]
            )
        platform_block.extend(
            [
                "    reverse_proxy localhost:8090",
                "    encode gzip",
                "    log {",
                "        output file /var/log/caddy/access.log",
                "    }",
                "}",
            ]
        )
        sections.append("\n".join(platform_block))

        # Wildcard subdomains for deployed services.
        # Use {env.CLOUDFLARE_API_TOKEN} (Caddy env syntax) instead of the
        # raw token to match install.sh and avoid embedding secrets in files.
        if config.wildcard_subdomains and cloudflare_token:
            wildcard_known_hosts = _get_wildcard_known_hosts(domain)
            wildcard_lines = [
                f"*.{domain} {{",
                "    tls {",
                "        dns cloudflare {env.CLOUDFLARE_API_TOKEN}",
                "    }",
            ]

            if wildcard_known_hosts:
                wildcard_lines.extend(
                    [
                        f"    @known_hosts host {' '.join(wildcard_known_hosts)}",
                        "    handle @known_hosts {",
                        "        reverse_proxy localhost:8081",
                        "    }",
                    ]
                )

            wildcard_lines.extend(
                [
                    # Default: forward other *.domain traffic to Traefik so
                    # services stay reachable while DNS sync/verification catches up.
                    "    handle {",
                    "        reverse_proxy localhost:8081",
                    "    }",
                    "}",
                ]
            )
            sections.append("\n".join(wildcard_lines))

    # Always include :80 catch-all so the IP always works.
    # In SSL+domain mode this should only handle unmatched hosts and route to
    # a controlled notice page. In IP/HTTP mode it remains the primary route.
    # Never generate domain-specific HTTP blocks — they break IP access
    # because Caddy won't match requests by IP to a domain-named block.
    if config.use_ssl and domain:
        sections.append(
            """:80 {
    @has_host header_regexp host .+
    redir @has_host https://{host}{uri} 308
    handle {
        reverse_proxy localhost:8090
    }
}"""
        )
    else:
        sections.append(
            """:80 {
    reverse_proxy localhost:8090
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
            # Write Cloudflare token to shared volume for the host watcher
            # to sync into Caddy's systemd environment override.
            with open(CADDY_TOKEN_FILE, "w", encoding="utf-8") as handle:
                handle.write(cloudflare_token)
            os.chmod(CADDY_TOKEN_FILE, 0o600)
            # Persist a cache so future apply runs without an explicit token
            # do not unintentionally clear wildcard TLS.
            with open(CADDY_TOKEN_CACHE, "w", encoding="utf-8") as handle:
                handle.write(cloudflare_token)
            os.chmod(CADDY_TOKEN_CACHE, 0o600)
            if os.path.exists(CADDY_TOKEN_CLEAR_FILE):
                os.remove(CADDY_TOKEN_CLEAR_FILE)
        else:
            if os.path.exists(CADDY_TOKEN_FILE):
                os.remove(CADDY_TOKEN_FILE)
            if os.path.exists(CADDY_TOKEN_CACHE):
                os.remove(CADDY_TOKEN_CACHE)
            # Signal watcher to explicitly remove any stale systemd override.
            with open(CADDY_TOKEN_CLEAR_FILE, "w", encoding="utf-8") as handle:
                handle.write("clear")
            os.chmod(CADDY_TOKEN_CLEAR_FILE, 0o600)

        # Create reload flag - the host watcher will pick this up
        with open(CADDY_RELOAD_FLAG, "w", encoding="utf-8") as handle:
            handle.write("reload")

        result["ok"] = True
        result["message"] = "Caddyfile written and reload flag set"
        logger.info("Caddyfile written to %s", CADDY_FILE_PATH)

    except OSError as exc:
        result["message"] = f"Failed to write Caddyfile: {exc}"
        if isinstance(exc, PermissionError):
            result["message"] += (
                " | Fix host dir perms: sudo chown -R 1000:1000 /opt/smsly-hosting/caddy-config "
                "&& sudo chmod 775 /opt/smsly-hosting/caddy-config"
            )
        logger.error("Failed to write Caddyfile: %s", exc)

    return result
