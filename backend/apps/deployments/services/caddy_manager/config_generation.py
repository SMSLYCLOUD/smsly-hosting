import ipaddress
import logging
import os

from apps.domains.utils import normalize_domain
from django.conf import settings

from .tls import _generate_selfsigned_cert
from .upstream import _remote_upstream_url_for_service, _service_proxy_upstream
from .utils import _is_ip, _table_exists, is_agent_lite

logger = logging.getLogger(__name__)

CADDY_CONFIG_DIR = os.environ.get("CADDY_CONFIG_DIR", "/caddy-config")


def _resolve_effective_server(service):
    """Resolve the effective server for a service.

    Checks the latest deployment's ``target_server`` first (set explicitly
    per-deploy), then falls back to the service's ``server`` FK.
    Returns the ``ManagedServer`` instance or ``None``.
    """
    from apps.deployments.models import Deployment
    from apps.deployments.models.core import ManagedServer
    try:
        latest = (
            Deployment.objects
            .filter(service=service)
            .order_by('-created_at')
            .only('target_server_id', 'target_is_local')
            .first()
        )
        if latest:
            if latest.target_is_local:
                return ManagedServer.get_primary()
            if latest.target_server_id:
                return latest.target_server
    except Exception:
        pass
    return getattr(service, 'server', None)


def ensure_ip_cert():
    """Generate the self-signed IP cert if it does not already exist.

    This is called from the backend entrypoint so the cert file is always
    present on the shared caddy-config volume BEFORE Caddy reads the
    Caddyfile. Without this, Caddy crash-loops with:
        loading certificates: open .../certs/ip.crt: no such file or directory
    because the Caddyfile references the cert but it was only generated
    on-demand by the domain-config signal (which may not have fired yet).
    """
    _cert_dir = os.path.join(CADDY_CONFIG_DIR, "certs")
    try:
        from apps.deployments.models.core import PlatformConfig
        _cfg = PlatformConfig.load()
        _server_ip = str(getattr(_cfg, "server_ip", "") or "").strip()
    except Exception as _exc:
        logger.debug("ensure_ip_cert: could not load PlatformConfig: %s", _exc)
        return
    if not _server_ip:
        return
    _crt_path = os.path.join(_cert_dir, "ip.crt")
    _key_path = os.path.join(_cert_dir, "ip.key")
    try:
        os.makedirs(_cert_dir, exist_ok=True)
        if not ipaddress.ip_address(_server_ip):
            return
        _regenerate = True
        if os.path.exists(_crt_path):
            try:
                from cryptography import x509
                with open(_crt_path, "rb") as _cr:
                    _existing = x509.load_pem_x509_certificate(_cr.read())
                _current_ip_obj = ipaddress.ip_address(_server_ip)
                for _san in _existing.extensions.get_extension_for_class(
                    x509.SubjectAlternativeName
                ).value:
                    if isinstance(_san, x509.IPAddress) and _san.value == _current_ip_obj:
                        _regenerate = False
                        break
            except Exception:
                _regenerate = True
        if _regenerate:
            _generate_selfsigned_cert(_crt_path, _key_path, _server_ip)
            logger.info("ensure_ip_cert: generated self-signed cert for %s", _server_ip)
        for _f in (_crt_path, _key_path):
            if os.path.exists(_f):
                try:
                    _mode = os.stat(_f).st_mode & 0o777
                    # Cert: 644 (readable by all), Key: 600 (owner-only)
                    _target = 0o600 if _f == _key_path else 0o644
                    if _mode < _target:
                        os.chmod(_f, _target)
                except OSError:
                    pass
    except Exception as _exc:
        logger.warning("ensure_ip_cert: failed: %s", _exc)


def _append_reverse_proxy(lines: list[str], upstream_url: str, upstream_host: str = ""):
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
            "        output file /var/log/caddy/access.log",
            "    }",
            "}",
        ]
    )
    return "\n".join(lines)


def _get_service_domain_blocks(wildcard_domain: str = "") -> list:
    blocks = []
    seen = set()

    try:
        from apps.deployments.models import Service

        if not _table_exists(Service._meta.db_table):
            return []

        for service in Service.objects.select_related("server").only(
            "id", "public_domain", "custom_domains", "public_domain_hidden", "staging_domain",
        ).order_by("id"):
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

            for domain_obj in service.domain_instances.all():
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

            # Custom staging domain: only route if this service has an active
            # STAGED deployment using the domain.
            if getattr(service, "staging_domain", None):
                staging_domain = str(service.staging_domain).strip()
                if staging_domain:
                    try:
                        staging_domain = normalize_domain(staging_domain)
                    except ValueError:
                        staging_domain = ""
                    if staging_domain and staging_domain not in seen:
                        from apps.deployments.models import Deployment
                        has_staged = Deployment.objects.filter(
                            service=service,
                            status__in=(
                                Deployment.Status.HEALTH_CHECK,
                                Deployment.Status.STAGED,
                            ),
                            staging_url__icontains=staging_domain,
                        ).exists()
                        if has_staged:
                            seen.add(staging_domain)
                            lines = [f"{staging_domain} {{"]
                            lines.append("    tls {")
                            lines.append("        on_demand")
                            lines.append("    }")
                            lines.append("    reverse_proxy traefik:80")
                            lines.append("    encode gzip")
                            lines.append("}")
                            blocks.append("\n".join(lines))

        from apps.deployments.models.addons import Addon
        for addon in Addon.objects.exclude(public_domain__isnull=True).exclude(public_domain="").only("id", "public_domain"):
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

    except Exception as exc:
        logger.warning("Could not load service domains for Caddyfile: %s", exc)
    return blocks


def _get_wildcard_known_hosts(wildcard_domain: str) -> list[str]:
    hosts: set[str] = set()
    if not wildcard_domain:
        return []

    try:
        from apps.deployments.models import Service
        from apps.deployments.models.addons import Addon

        if not _table_exists(Service._meta.db_table):
            return []

        suffix = f".{wildcard_domain}"
        for service in Service.objects.select_related("server").only("id", "public_domain", "custom_domains", "public_domain_hidden", "is_preview").all():
            svr = _resolve_effective_server(service)
            # @known_hosts routes to traefik:80 (master control plane).
            # Only local/primary services belong here — skip all remote targets.
            if svr and not svr.is_primary:
                continue
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

        for addon in Addon.objects.exclude(public_domain__isnull=True).exclude(public_domain="").only("id", "public_domain"):
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

        # Custom staging domains from Service.staging_domain
        # Only include if there's an active STAGED deployment for the service
        # using this custom staging domain
        for service in Service.objects.filter(
            staging_domain__isnull=False,
        ).exclude(staging_domain="").select_related("server").only("id", "staging_domain"):
            svr = _resolve_effective_server(service)
            if svr and not svr.is_primary:
                continue
            staging_domain = str(service.staging_domain).strip()
            if not staging_domain:
                continue
            try:
                staging_domain = normalize_domain(staging_domain)
            except ValueError:
                continue
            if staging_domain.endswith(suffix):
                # Only include if there's an active STAGED deployment using this domain
                from apps.deployments.models import Deployment
                has_staged = Deployment.objects.filter(
                    service=service,
                    status__in=(
                        Deployment.Status.HEALTH_CHECK,
                        Deployment.Status.STAGED,
                    ),
                    staging_url__icontains=staging_domain,
                ).exists()
                if has_staged:
                    hosts.add(staging_domain)

        from urllib.parse import urlparse
        from apps.deployments.models import Deployment
        for dep in Deployment.objects.filter(
            status__in=(
                Deployment.Status.HEALTH_CHECK,
                Deployment.Status.STAGED,
            ),
            staging_url__isnull=False,
        ).exclude(staging_url="").only("staging_url"):
            try:
                hostname = urlparse(dep.staging_url).hostname or ""
                if hostname.endswith(suffix):
                    hosts.add(hostname)
            except Exception:
                pass

    except Exception as exc:
        logger.warning("Could not load wildcard known hosts: %s", exc)
        return []

    return sorted(hosts)


def _get_wildcard_remote_host_map(wildcard_domain: str) -> dict[str, list[str]]:
    remote_hosts: dict[str, set[str]] = {}
    if not wildcard_domain:
        return {}

    try:
        from apps.deployments.models import Service

        if not _table_exists(Service._meta.db_table):
            return {}

        suffix = f".{wildcard_domain}"
        for service in Service.objects.select_related("server").only(
            "id", "public_domain", "custom_domains", "public_domain_hidden",
            "wildcard_url_enabled",
        ).all():
            if not getattr(service, "wildcard_url_enabled", True):
                continue
            svr = _resolve_effective_server(service)
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
    except Exception as exc:
        logger.warning("Could not load remote wildcard service domains: %s", exc)
        return {}

    return {upstream: sorted(hosts) for upstream, hosts in remote_hosts.items()}


def _get_node_subdomain_blocks(wildcard_domain: str, cloudflare_token: str) -> list[str]:
    """Generate Caddyfile blocks for node subdomains (node-{slug}.{domain}).

    These blocks reverse proxy to nodes via WireGuard mesh IP.
    Used as fallback when nodes are accessed through the master.
    """
    if not wildcard_domain:
        return []

    try:
        from apps.deployments.models.core import ManagedServer

        if not _table_exists(ManagedServer._meta.db_table):
            return []

        blocks: list[str] = []
        suffix = f".{wildcard_domain}"

        for server in ManagedServer.objects.filter(
            is_primary=False,
        ).only("id", "host", "wg_address", "node_components").all():
            node_slug = str(server.id).split("-")[0]
            node_domain = f"node-{node_slug}{suffix}"

            mesh_ip = str(getattr(server, "wg_address", "") or "").strip()
            if not mesh_ip:
                continue

            block = [
                f"{node_domain} {{",
                "    tls {",
            ]
            if cloudflare_token:
                block.append(f"        dns cloudflare {cloudflare_token}")
            else:
                block.append("        on_demand")
            block.extend([
                "    }",
                "    log {",
                "        output file /var/log/caddy/access.log",
                "    }",
                f"    reverse_proxy http://{mesh_ip}:80 {{",
                "        header_up Host {host}",
                "    }",
                "}",
            ])
            blocks.append("\n".join(block))

        return blocks
    except Exception as exc:
        logger.warning("Could not load node subdomains for Caddy: %s", exc)
        return []


def generate_node_caddyfile(node) -> str:
    """Generate a Caddyfile for a specific node.

    Uses grid{N} naming convention:
      Master wildcard: myservice.grid.{domain}
      Node direct:     myservice.grid{N}.{domain}

    The node's Caddy serves service-specific domains with on-demand TLS.
    """
    from apps.deployments.models import Service
    from apps.deployments.models.core import ManagedServer, PlatformConfig

    if not _table_exists(Service._meta.db_table):
        return ""

    config = PlatformConfig.load()
    base_domain = str(getattr(config, "domain", "") or "").strip()
    if not base_domain:
        return ""

    cloudflare_token = (getattr(config, "cloudflare_api_token", "") or "").strip()
    node_number = getattr(node, "node_number", None) or 1
    parts = base_domain.split(".")
    if len(parts) > 2:
        node_domain = f"grid{node_number}.{'.'.join(parts[1:])}"
    else:
        node_domain = f"grid{node_number}.{base_domain}"

    sections: list[str] = []

    # Node management block
    mgmt_block = [
        f"{node_domain} {{",
        "    tls {",
    ]
    if cloudflare_token:
        mgmt_block.append(f"        dns cloudflare {cloudflare_token}")
    else:
        mgmt_block.append("        on_demand")
    mgmt_block.extend([
        "    }",
        "    log {",
        "        output file /var/log/caddy/access.log",
        "    }",
        "    handle /api/* {",
        "        reverse_proxy backend:8000",
        "    }",
        "    handle /ws/* {",
        "        reverse_proxy backend:8000",
        "    }",
        "    handle {",
        "        reverse_proxy backend:8000",
        "    }",
        "    encode gzip",
        "}",
    ])
    sections.append("\n".join(mgmt_block))

    # Service blocks
    services = Service.objects.filter(
        node_url_enabled=True,
    ).order_by("id")

    for service in services:
        svr = _resolve_effective_server(service)
        if not svr or svr.id != node.id:
            continue

        slug = (service.slug or service.name.lower().replace(" ", "-")).strip()
        if len(parts) > 2:
            svc_domain = f"{slug}.grid{node_number}.{'.'.join(parts[1:])}"
        else:
            svc_domain = f"{slug}.grid{node_number}.{base_domain}"
        port = getattr(service, "internal_port", 8000) or 8000

        svc_block = [
            f"{svc_domain} {{",
            "    tls {",
        ]
        if cloudflare_token:
            svc_block.append(f"        dns cloudflare {cloudflare_token}")
        else:
            svc_block.append("        on_demand")
        svc_block.extend([
            "    }",
            "    log {",
            "        output file /var/log/caddy/access.log",
            "    }",
            f"    reverse_proxy http://localhost:{port} {{",
            "        header_up Host {host}",
            "    }",
            "    encode gzip",
            "}",
        ])
        sections.append("\n".join(svc_block))

    header = "# Node Caddyfile - Auto-generated by Grid Controller\n"
    header += f"# Node: {node.name} (grid{node_number}.{base_domain})\n"
    header += "# Do not edit manually; changes will be overwritten.\n\n"
    return header + "\n\n".join(sections) + "\n"


def generate_caddyfile(config) -> str:
    if is_agent_lite():
        logger.debug("Agent-lite mode: skipping generate_caddyfile()")
        return ""

    sections = []
    domain = ""
    cloudflare_token = (getattr(config, "cloudflare_api_token", "") or "").strip()

    _ask_secret = ""
    try:
        from apps.deployments.models.core import PlatformConfig
        _cfg = PlatformConfig.load()
        _ask_secret = str(getattr(_cfg, 'caddy_ask_secret', '') or '').strip()
    except Exception as exc:
        logger.debug("Failed to load PlatformConfig for Caddy ask secret: %s", exc)
    if not _ask_secret:
        _ask_secret = str(getattr(settings, "CADDY_ASK_SECRET", "") or "")
    _ask_url = "http://backend:8000/api/v1/services/check-domain/"
    if _ask_secret:
        # Pass the secret via Caddy env var interpolation to avoid
        # embedding it in plaintext in the Caddyfile.
        import os as _os
        _os.environ.setdefault("CADDY_ASK_SECRET", _ask_secret)
        _ask_url += "?secret={env.CADDY_ASK_SECRET}"
    sections.append(f"""\u007b
    on_demand_tls \u007b
        ask {_ask_url}
    \u007d
\u007d""")

    _FAKE_TOKENS = {
        "fake", "changeme", "your_cloudflare_api_token", "test", "",
        "dummy_token_for_testing",
    }
    if cloudflare_token.lower() in _FAKE_TOKENS or cloudflare_token.startswith("your_"):
        cloudflare_token = ""

    env_domain = os.environ.get("DOMAIN", "").strip()

    effective_domain = config.domain if config.domain else env_domain
    use_ssl = config.use_ssl if config.domain else (os.environ.get("DEBUG", "False").lower() not in {"true", "1", "t"})

    if effective_domain:
        try:
            domain = normalize_domain(effective_domain, allow_ip=True)
            if _is_ip(domain):
                use_ssl = False
        except ValueError:
            logger.warning("Ignoring invalid platform domain in config: %r", effective_domain)

    if use_ssl and domain:
        platform_block = [f"{domain} {{"]
        platform_block.extend(
            [
                "    encode gzip",
                "    log {",
                "        output file /var/log/caddy/access.log",
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

        if config.wildcard_subdomains:
            wildcard_known_hosts = _get_wildcard_known_hosts(domain)
            wildcard_remote_hosts = _get_wildcard_remote_host_map(domain)
            wildcard_lines = [
                f"*.{domain} {{",
            ]
            if cloudflare_token:
                wildcard_lines.append("    tls {")
                wildcard_lines.append(f"        dns cloudflare {cloudflare_token}")
                wildcard_lines.append("    }")

            wildcard_lines.append("    log {")
            wildcard_lines.append("        output file /var/log/caddy/access.log")
            wildcard_lines.append("    }")

            local_previews = []
            try:
                from apps.deployments.models import Service
                if _table_exists(Service._meta.db_table):
                    local_previews = list(
                        Service.objects.select_related("server")
                        .filter(is_preview=True, server__is_primary=True, status=Service.Status.ACTIVE)
                        .only("name", "public_domain", "internal_port")
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
                    "        reverse_proxy frontend:3000",
                    "    }",
                    "}",
                ]
            )
            sections.append("\n".join(wildcard_lines))

        # Node subdomains (node-{slug}.{domain}) — reverse proxy to nodes via WireGuard
        node_blocks = _get_node_subdomain_blocks(domain, cloudflare_token)
        for block in node_blocks:
            sections.append(block)

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
    handle /health* {{
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

    _cert_dir = os.path.join(CADDY_CONFIG_DIR, "certs")
    _server_ip = str(getattr(config, "server_ip", "") or "").strip()
    _crt_path = os.path.join(_cert_dir, "ip.crt")
    _key_path = os.path.join(_cert_dir, "ip.key")
    _caddy_crt = "/etc/caddy/certs/ip.crt"
    _caddy_key = "/etc/caddy/certs/ip.key"
    try:
        os.makedirs(_cert_dir, exist_ok=True)
        if _server_ip and ipaddress.ip_address(_server_ip):
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
            for _f in (_crt_path, _key_path):
                if os.path.exists(_f):
                    try:
                        _mode = os.stat(_f).st_mode & 0o777
                        # Cert: 644 (readable by all), Key: 600 (owner-only)
                        _target = 0o600 if _f == _key_path else 0o644
                        if _mode < _target:
                            os.chmod(_f, _target)
                    except OSError:
                        pass
    except Exception as _exc:
        logger.warning("Could not generate self-signed cert for IP redirect: %s", _exc)

    if os.path.exists(_crt_path) and os.path.exists(_key_path) and _server_ip:
        # Use :443 as the site address instead of the raw IP.
        # Caddy breaks TLS when the site address is a bare IP because
        # that IP doesn't exist on the container's network interface
        # (the container has e.g. 172.18.0.20).  :443 listens on all
        # interfaces and lets TLS negotiate via SNI.
        #
        # When the platform domain is a real hostname (not an IP), skip the
        # static IP cert in :443 — otherwise Caddy serves the self-signed IP
        # cert for ALL TLS connections (including named sites like
        # grid.smsly.cloud) instead of using the ACME-issued cert.
        _tls_line = ""
        if _is_ip(str(domain)):
            _tls_line = f"    tls {_caddy_crt} {_caddy_key}"
        sections.append(
            f""":443 {{
{_tls_line}
    handle /api/* {{
        reverse_proxy backend:8000
    }}
    handle /ws/* {{
        reverse_proxy backend:8000
    }}
    handle /health* {{
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
    handle {{
        reverse_proxy frontend:3000
    }}
    encode gzip
}}"""
        )

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
    elif not domain:
        sections.append(
            """:80 {
    handle /api/* {
        reverse_proxy backend:8000
    }
    handle /ws/* {
        reverse_proxy backend:8000
    }
    handle /health* {
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

    wildcard_base = domain if (config.use_ssl and config.wildcard_subdomains and cloudflare_token) else ""
    service_blocks = _get_service_domain_blocks(wildcard_domain=wildcard_base)
    sections.extend(service_blocks)

    header = "# Grid Caddyfile - Auto-generated by Settings UI\n"
    header += "# Do not edit manually; changes will be overwritten.\n\n"
    return header + "\n\n".join(sections) + "\n"
