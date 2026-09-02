import ipaddress
import logging
import os
import re

from apps.domains.utils import normalize_domain
from django.conf import settings

from .tls import _generate_selfsigned_cert
from .upstream import _remote_upstream_url_for_service, _service_proxy_upstream
from .utils import _is_ip, _table_exists, is_agent_lite

logger = logging.getLogger(__name__)

CADDY_CONFIG_DIR = os.environ.get("CADDY_CONFIG_DIR", "/caddy-config")

# User-configurable per-service path redirects: /segment -> https://target/...
_PATH_REDIRECT_SEGMENT_RE = re.compile(r"^/[a-z0-9_-]{1,63}$")
_MAX_PATH_REDIRECTS_PER_SERVICE = 50

# Host aliases (accounts.google.com pattern): extra hostnames that serve the app
_ALIAS_REWRITE_ROOT_RE = re.compile(r"^/[A-Za-z0-9/_.-]{0,100}$")
_MAX_HOST_ALIASES_PER_SERVICE = 10


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


def _service_path_redirect_rules(service) -> list[tuple[str, str, str]]:
    """Sanitized (path_segment, target_host, target_path) triples from
    service.path_redirects.

    Fully user-configurable — invalid entries are skipped defensively at
    generation time.  Each rule 301-redirects ``/segment`` and ``/segment/*``
    on the service's own domains to ``https://target`` (prefix stripped,
    query preserved).

    Targets may include a path: ``accounts.trulay.co/login`` redirects
    ``/login`` on the service's domain to ``https://accounts.trulay.co/login``.
    The UI accepts host/path, the serializer stores it verbatim, so the
    generator must split it here (previously it ran normalize_domain on the
    whole string, which rejects paths and silently dropped every rule with
    a path — the user's /login and /register redirects never generated).
    Bare-host targets (``accounts.trulay.co``) keep the existing semantics:
    ``/segment`` goes to the target root and ``/segment/*`` preserves the
    remainder.
    """
    from apps.domains.utils import normalize_domain, split_host_and_path
    rules: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    raw_entries = getattr(service, "path_redirects", None)
    if not isinstance(raw_entries, list):
        return []
    for entry in raw_entries:
        if not isinstance(entry, dict):
            continue
        path = str(entry.get("path") or "").strip().lower()
        target = str(entry.get("target") or "").strip().lower()
        if not path or not target:
            continue
        if not _PATH_REDIRECT_SEGMENT_RE.match(path):
            continue
        # Strip a URL scheme if the operator pasted a full URL.
        if "://" in target:
            target = target.split("://", 1)[1]
        try:
            target_host, target_path = split_host_and_path(target)
        except ValueError:
            continue
        try:
            target_host = normalize_domain(target_host)
        except ValueError:
            continue
        if path in seen:
            continue
        seen.add(path)
        rules.append((path, target_host, target_path))
        if len(rules) >= _MAX_PATH_REDIRECTS_PER_SERVICE:
            break
    return rules


def _path_redirect_site_lines(rules: list[tuple[str, str, str]], indent: str = "    ") -> list[str]:
    """Caddyfile lines implementing 301 path redirects inside a site block.

    Two target shapes:
      - Bare host (``accounts.trulay.co``): exact ``/seg`` → target root;
        ``/seg/*`` preserves the remainder (``/seg/x`` →
        ``https://target/x`` — the old prefix-strip semantics).
      - Host with path (``accounts.trulay.co/login``): exact ``/seg`` →
        ``https://host/login``; ``/seg/x`` → ``https://host/login/x``
        (the target path replaces the matched segment, the remainder is
        appended). This is the 'domain/path' format the UI accepts.
    """
    lines: list[str] = []
    for index, (segment, target, target_path) in enumerate(rules):
        if target_path and target_path != "/":
            # Host/path target: /seg -> https://host/path, /seg/x -> https://host/path/x
            # Caddy's {http.request.uri.path} excludes the query; {uri} keeps it.
            lines.extend([
                f"{indent}@path_redir_{index}_exact path {segment}",
                f"{indent}handle @path_redir_{index}_exact {{",
                f"{indent}    redir https://{target}{target_path} 301",
                f"{indent}}}",
                f"{indent}handle_path {segment}/* {{",
                f"{indent}    redir https://{target}{target_path}{{uri}} 301",
                f"{indent}}}",
            ])
        else:
            # Bare-host target: /seg -> https://host/, /seg/x -> https://host/x
            lines.extend([
                f"{indent}@path_redir_{index}_exact path {segment}",
                f"{indent}handle @path_redir_{index}_exact {{",
                f"{indent}    redir https://{target}/ 301",
                f"{indent}}}",
                f"{indent}handle_path {segment}/* {{",
                f"{indent}    redir https://{target}{{uri}} 301",
                f"{indent}}}",
            ])
    return lines


def _service_host_alias_rules(service) -> list[tuple[str, str]]:
    """Sanitized (host, rewrite_root) pairs from service.host_aliases.

    Each alias serves the app directly; ``/`` is rewritten to ``rewrite_root``
    (e.g. ``/login``) so account.example.com shows the login page.  Empty
    rewrite_root means a pure alias with no path rewriting.  Aliases that
    duplicate the public domain or custom domains are skipped — those are
    already routed.
    """
    rules: list[tuple[str, str]] = []
    seen_hosts: set[str] = set()
    raw_entries = getattr(service, "host_aliases", None)
    if not isinstance(raw_entries, list):
        return []

    already_routed = {str(getattr(service, "public_domain", "") or "").strip().lower()}
    for item in (service.custom_domains or []):
        if isinstance(item, str) and item.strip():
            already_routed.add(item.strip().lower())

    for entry in raw_entries:
        if not isinstance(entry, dict):
            continue
        host = str(entry.get("host") or "").strip().lower()
        if not host or host in seen_hosts or host in already_routed:
            continue
        try:
            host = normalize_domain(host)
        except ValueError:
            continue
        rewrite_root = str(entry.get("rewrite_root") or "").strip()
        if rewrite_root and not _ALIAS_REWRITE_ROOT_RE.match(rewrite_root):
            continue
        seen_hosts.add(host)
        rules.append((host, rewrite_root))
        if len(rules) >= _MAX_HOST_ALIASES_PER_SERVICE:
            break
    return rules


def _build_host_alias_block(alias_host: str, rewrite_root: str, upstream_url: str, host_header: str) -> str:
    """Caddyfile site block for a host alias (accounts.google.com pattern).

    Semantics (from Service.host_aliases help_text):
      - Visiting the alias's ROOT (/) is rewritten to ``rewrite_root``
        (e.g. /login), so https://accounts.example.com/ shows the app's
        login page.
      - All other paths (/login, /dashboard, /api/...) pass through
        UNCHANGED — the alias serves the whole app, not just one path.

    Therefore the matcher must be exactly ``/`` — the root only. An
    earlier revision changed the matcher to ``path {rewrite_root}*``,
    which matched only requests already under the rewrite_root; the
    root itself fell through to the catch-all handle and served the
    unrewritten homepage instead of /login (and the /login rewrite
    rewrote /login to itself — a no-op). Root-only matcher, rewrite
    to rewrite_root, everything else unchanged.
    """
    lines = [f"{alias_host} {{"]
    lines.extend([
        "    tls {",
        "        on_demand",
        "    }",
        "    log {",
        "        output file /var/log/caddy/access.log",
        "    }",
    ])
    if rewrite_root and rewrite_root != "/":
        lines.extend([
            "    @alias_root path /",
            "    handle @alias_root {",
            f"        rewrite * {rewrite_root}",
        ])
        before = len(lines)
        _append_reverse_proxy(lines, upstream_url, host_header)
        for i in range(before, len(lines)):
            lines[i] = "    " + lines[i]
        lines.append("    }")
    lines.append("    handle {")
    _append_reverse_proxy(lines, upstream_url, host_header)
    lines.append("    }")
    lines.extend(["    encode gzip", "}"])
    return "\n".join(lines)


def _build_service_domain_block(
    domain: str,
    upstream_host: str,
    upstream_url: str = "",
    path_redirect_rules: list[tuple[str, str, str]] | None = None,
) -> str:
    lines = [f"{domain} {{"]

    lines.extend(_path_redirect_site_lines(path_redirect_rules or []))

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

        for service in Service.objects.only(
            "id", "public_domain", "custom_domains", "public_domain_hidden", "staging_domain",
            "host_aliases", "path_redirects",
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
                            path_redirect_rules=_service_path_redirect_rules(service),
                        )
                    )

            from apps.domains.models import DomainStatus
            for domain_obj in service.domain_instances.filter(
                status__in=[
                    DomainStatus.ACTIVE,
                    DomainStatus.DNS_VERIFIED,
                    DomainStatus.SSL_PROVISIONING,
                ],
                verified=True,
            ):
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
                lines.extend(_path_redirect_site_lines(_service_path_redirect_rules(service)))
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

            # Host aliases (accounts.google.com pattern): dedicated site
            # blocks that serve this app under extra hostnames. Exact host
            # matches take precedence over the platform wildcard block.
            upstream_url = _remote_upstream_url_for_service(service)
            for alias_host, rewrite_root in _service_host_alias_rules(service):
                if alias_host in seen:
                    continue
                seen.add(alias_host)
                blocks.append(_build_host_alias_block(
                    alias_host,
                    rewrite_root,
                    upstream_url,
                    public_domain or alias_host,
                ))

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
        from apps.deployments.models import Service, Deployment
        from apps.deployments.models.addons import Addon

        if not _table_exists(Service._meta.db_table):
            return []

        suffix = f".{wildcard_domain}"

        # Pre-fetch latest deployment target_is_local per service to avoid
        # N+1 queries.  A service is considered LOCAL if:
        #   1. It has no deployments (fresh service, server FK is authoritative)
        #   2. Its latest deployment has target_is_local=True
        #   3. Its latest deployment has no target_server_id and the service's
        #      own server FK is primary (legacy deployments)
        service_ids = [
            s.id for s in Service.objects.only("id").all()
        ]
        latest_local_map: dict[str, bool] = {}
        if service_ids:
            from django.db.models import OuterRef, Subquery
            latest_dep = (
                Deployment.objects.filter(service_id=OuterRef('id'))
                .order_by('-created_at')
                .values('target_is_local', 'target_server_id')[:1]
            )
            for row in Service.objects.filter(id__in=service_ids).annotate(
                _latest_target_is_local=Subquery(latest_dep.values('target_is_local')),
                _latest_target_server_id=Subquery(latest_dep.values('target_server_id')),
            ).values('id', '_latest_target_is_local', '_latest_target_server_id'):
                sid = str(row['id'])
                target_is_local = row['_latest_target_is_local']
                target_server_id = row['_latest_target_server_id']
                if target_is_local is True:
                    latest_local_map[sid] = True
                elif target_server_id is not None:
                    latest_local_map[sid] = False
                else:
                    # No deployment or deployment has no target info —
                    # fall through to service.server FK check below.
                    latest_local_map.pop(sid, None)

        for service in Service.objects.select_related('server').only(
            "id", "public_domain", "custom_domains", "public_domain_hidden",
            "is_preview", "server",
            "wildcard_url_enabled", "is_public",
            "wildcard_redirect_custom_domain", "wildcard_internal_only",
        ).all():
            if getattr(service, "is_preview", False):
                continue

            routing_off = getattr(service, "wildcard_url_enabled", True) is False
            private = getattr(service, "is_public", True) is False
            redirect_flag = getattr(service, "wildcard_redirect_custom_domain", False)
            internal_only = bool(getattr(service, "wildcard_internal_only", False))

            # Determine if this service is local:
            # 1. Check latest deployment target from pre-fetched map
            # 2. Fall back to service.server FK
            sid = str(service.id)
            is_local = False
            if sid in latest_local_map:
                is_local = latest_local_map[sid]
            else:
                # No deployment data — use service.server FK
                svr = getattr(service, 'server', None)
                is_local = (svr is None) or (getattr(svr, 'is_primary', False) is True)

            if not is_local:
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
            if (
                public_domain.endswith(suffix)
                and not routing_off
                and not private
                and not redirect_flag
                and not internal_only
            ):
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
        ).exclude(staging_domain="").only("id", "staging_domain"):
            sid = str(service.id)
            is_local = False
            if sid in latest_local_map:
                is_local = latest_local_map[sid]
            else:
                svr = getattr(service, 'server', None)
                is_local = (svr is None) or (getattr(svr, 'is_primary', False) is True)
            if not is_local:
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
        from apps.deployments.models import Service, Deployment

        if not _table_exists(Service._meta.db_table):
            return {}

        suffix = f".{wildcard_domain}"

        # Pre-fetch latest deployment target per service (same logic as _get_wildcard_known_hosts)
        service_ids = [
            s.id for s in Service.objects.only("id").all()
        ]
        latest_local_map: dict[str, bool] = {}
        if service_ids:
            from django.db.models import OuterRef, Subquery
            latest_dep = (
                Deployment.objects.filter(service_id=OuterRef('id'))
                .order_by('-created_at')
                .values('target_is_local', 'target_server_id')[:1]
            )
            for row in Service.objects.filter(id__in=service_ids).annotate(
                _latest_target_is_local=Subquery(latest_dep.values('target_is_local')),
                _latest_target_server_id=Subquery(latest_dep.values('target_server_id')),
            ).values('id', '_latest_target_is_local', '_latest_target_server_id'):
                sid = str(row['id'])
                target_is_local = row['_latest_target_is_local']
                target_server_id = row['_latest_target_server_id']
                if target_is_local is True:
                    latest_local_map[sid] = True
                elif target_server_id is not None:
                    latest_local_map[sid] = False
                else:
                    latest_local_map.pop(sid, None)

        for service in Service.objects.select_related('server').only(
            "id", "public_domain", "custom_domains", "public_domain_hidden",
            "wildcard_url_enabled", "server", "wildcard_internal_only",
        ).all():
            if not getattr(service, "wildcard_url_enabled", True):
                continue
            if bool(getattr(service, "wildcard_internal_only", False)):
                # Handled by the dedicated internal-only section instead.
                continue

            # Determine if this service is remote using same logic as _get_wildcard_known_hosts
            sid = str(service.id)
            is_local = False
            if sid in latest_local_map:
                is_local = latest_local_map[sid]
            else:
                svr = getattr(service, 'server', None)
                is_local = (svr is None) or (getattr(svr, 'is_primary', False) is True)

            if is_local:
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


def _get_wildcard_disabled_hosts(wildcard_domain: str) -> list[str]:
    """Public wildcard domains that must serve the 503 fallback page.

    A host lands here when Public Domain Routing is off
    (``wildcard_url_enabled=False``), domain visibility is private
    (``is_public=False``), or the domain is hidden
    (``public_domain_hidden=True``).  Applies to local AND remote services —
    master Caddy answers these itself so traffic never falls through to the
    platform landing page.
    """
    hosts: set[str] = set()
    if not wildcard_domain:
        return []

    try:
        from apps.deployments.models import Service

        if not _table_exists(Service._meta.db_table):
            return []

        suffix = f".{wildcard_domain}"
        for service in Service.objects.only(
            "id", "public_domain", "public_domain_hidden",
            "wildcard_url_enabled", "is_public",
        ).all():
            raw_public = str(service.public_domain or "").strip()
            if not raw_public:
                continue
            try:
                public_domain = normalize_domain(raw_public)
            except ValueError:
                continue
            if not public_domain.endswith(suffix):
                continue
            hidden = bool(getattr(service, "public_domain_hidden", False))
            routing_off = getattr(service, "wildcard_url_enabled", True) is False
            private = getattr(service, "is_public", True) is False
            if hidden or routing_off or private:
                hosts.add(public_domain)
    except Exception as exc:
        logger.warning("Could not load disabled wildcard hosts: %s", exc)
        return []

    return sorted(hosts)


def _get_wildcard_internal_only_upstreams(wildcard_domain: str) -> dict[str, list[str]]:
    """Wildcard public domains that are INTERNAL-ONLY, grouped by upstream.

    Public visitors get the 503 fallback page; requests originating from
    private/mesh networks (RFC1918, CGNAT) are routed normally.  Custom
    domains are unaffected.
    """
    grouped: dict[str, set[str]] = {}
    if not wildcard_domain:
        return {}

    try:
        from apps.deployments.models import Service, Deployment

        if not _table_exists(Service._meta.db_table):
            return {}

        suffix = f".{wildcard_domain}"

        service_ids = [s.id for s in Service.objects.only("id").all()]
        latest_local_map: dict[str, bool] = {}
        if service_ids:
            from django.db.models import OuterRef, Subquery
            latest_dep = (
                Deployment.objects.filter(service_id=OuterRef('id'))
                .order_by('-created_at')
                .values('target_is_local', 'target_server_id')[:1]
            )
            for row in Service.objects.filter(id__in=service_ids).annotate(
                _latest_target_is_local=Subquery(latest_dep.values('target_is_local')),
                _latest_target_server_id=Subquery(latest_dep.values('target_server_id')),
            ).values('id', '_latest_target_is_local', '_latest_target_server_id'):
                sid = str(row['id'])
                if row['_latest_target_is_local'] is True:
                    latest_local_map[sid] = True
                elif row['_latest_target_server_id'] is not None:
                    latest_local_map[sid] = False
                else:
                    latest_local_map.pop(sid, None)

        for service in Service.objects.select_related('server').only(
            "id", "public_domain", "public_domain_hidden",
            "wildcard_url_enabled", "wildcard_internal_only",
            "is_public", "server",
        ).all():
            if not getattr(service, "wildcard_internal_only", False):
                continue
            if getattr(service, "is_preview", False):
                continue
            if getattr(service, "public_domain_hidden", False):
                continue
            if getattr(service, "wildcard_url_enabled", True) is False:
                continue
            if getattr(service, "is_public", True) is False:
                continue

            raw_public = str(service.public_domain or "").strip()
            if not raw_public:
                continue
            try:
                public_domain = normalize_domain(raw_public)
            except ValueError:
                continue
            if not public_domain.endswith(suffix):
                continue

            sid = str(service.id)
            if sid in latest_local_map:
                is_local = latest_local_map[sid]
            else:
                svr = getattr(service, 'server', None)
                is_local = (svr is None) or (getattr(svr, 'is_primary', False) is True)

            if is_local:
                upstream = _service_proxy_upstream()
            else:
                upstream = _remote_upstream_url_for_service(service)
                if not upstream:
                    continue
            grouped.setdefault(upstream, set()).add(public_domain)
    except Exception as exc:
        logger.warning("Could not load internal-only wildcard hosts: %s", exc)
        return {}

    return {upstream: sorted(hosts) for upstream, hosts in grouped.items()}


def _get_wildcard_redirect_map(wildcard_domain: str) -> dict[str, str]:
    """Map wildcard public domains to their first custom domain (301 target).

    Services with ``wildcard_redirect_custom_domain=True`` and at least one
    custom domain get their auto-generated wildcard domain permanently
    redirected instead of proxied.
    """
    redirects: dict[str, str] = {}
    if not wildcard_domain:
        return {}

    try:
        from apps.deployments.models import Service

        if not _table_exists(Service._meta.db_table):
            return {}

        suffix = f".{wildcard_domain}"
        for service in Service.objects.only(
            "id", "public_domain", "custom_domains",
            "wildcard_redirect_custom_domain",
        ).all():
            if not getattr(service, "wildcard_redirect_custom_domain", False):
                continue
            target = ""
            for item in (service.custom_domains or []):
                value = item.strip() if isinstance(item, str) else ""
                if value:
                    target = value
                    break
            if not target:
                continue
            raw_public = str(service.public_domain or "").strip()
            if not raw_public:
                continue
            try:
                public_domain = normalize_domain(raw_public)
            except ValueError:
                continue
            if public_domain.endswith(suffix):
                redirects[public_domain] = target
    except Exception as exc:
        logger.warning("Could not load wildcard redirect map: %s", exc)
        return {}

    return dict(sorted(redirects.items()))


def _preview_bcrypt_hash(p_service) -> str:
    """Return a Caddy basic_auth bcrypt hash for a preview service.

    Auto-generates + persists a per-service preview password on first
    use. Caddy's basic_auth only accepts bcrypt hashes; we bcrypt the
    stored plaintext with the platform's SECRET_KEY-derived salt... no —
    bcrypt salts internally; we simply hash the stored password.
    """
    import secrets as _secrets

    password = (getattr(p_service, "preview_password", "") or "").strip()
    if not password:
        # First preview deploy for this service: mint a password. 32 hex
        # chars — strong enough for a preview gate, short enough for a
        # human to read from the dashboard.
        password = _secrets.token_hex(16)
        try:
            from apps.deployments.models import Service as _S
            _S.objects.filter(pk=p_service.pk).update(preview_password=password)
            p_service.preview_password = password
        except Exception as exc:
            logger.warning("Could not persist preview password for %s: %s",
                           p_service.id, exc)
    try:
        import bcrypt as _bcrypt
        return _bcrypt.hashpw(password.encode(), _bcrypt.gensalt()).decode()
    except Exception as exc:
        logger.warning("preview bcrypt failed for %s: %s", p_service.id, exc)
        return ""


def _get_wildcard_path_redirect_lines(wildcard_domain: str) -> list[str]:
    """Per-service path-redirect handles for wildcard-covered public domains.

    Services whose public_domain is a subdomain of the wildcard (e.g.
    ``smsly-frontend-j51qi-801dc1.grid.smsly.cloud`` under
    ``*.grid.smsly.cloud``) get their whole domain block skipped in
    ``_get_service_domain_blocks`` — which also skips their
    ``path_redirects``. This emits those redirects into the wildcard
    site block, scoped to the specific service host so two services
    with a ``/login`` redirect don't collide.

    Each rule is a single ``handle`` with a combined host+path matcher
    covering both the exact segment and sub-paths, using
    ``uri strip_prefix <segment>`` so ``/login/x`` redirects to the
    target with the remainder preserved (matching the per-domain block
    semantics from _path_redirect_site_lines).
    """
    lines: list[str] = []
    if not wildcard_domain:
        return []
    try:
        from apps.deployments.models import Service

        if not _table_exists(Service._meta.db_table):
            return []

        suffix = f".{wildcard_domain}"
        for service in Service.objects.only(
            "id", "public_domain", "path_redirects",
        ).order_by("id"):
            raw_public = str(getattr(service, "public_domain", "") or "").strip().lower()
            if not raw_public:
                continue
            try:
                public_domain = normalize_domain(raw_public)
            except ValueError:
                continue
            if not public_domain.endswith(suffix):
                continue
            rules = _service_path_redirect_rules(service)
            if not rules:
                continue
            svc_alias = str(service.id).replace("-", "")[:8]
            # Scope every redirect to this service's own hostname so
            # the wildcard block can host rules for many services.
            # A single named matcher with host AND path conditions
            # (Caddyfile block-matcher syntax) — `handle` accepts
            # exactly one matcher argument, so ANDing must happen
            # inside the matcher definition itself.
            # The remainder is extracted with path_regexp so the target
            # path is exact for /seg and /seg/rest maps to
            # https://host/target-path/rest (handle_path-style stripping
            # without needing a second matcher).
            for r_index, (segment, target, target_path) in enumerate(rules):
                matcher = f"@wpr_{svc_alias}_{r_index}"
                rest_re = f"wpr_{svc_alias}_{r_index}"
                seg_q = segment.replace('/', r'\/')
                # Named matcher combining host + path_regexp. The regexp
                # captures the remainder (empty for the exact /seg match,
                # /rest for sub-paths) into {re.<rest_re>.1} — used
                # directly in the redir below. path_regexp lives INSIDE
                # the matcher block (it's a matcher directive, not a
                # handler directive).
                lines.append(f"    {matcher} {{")
                lines.append(f"        host {public_domain}")
                lines.append(f"        path_regexp {rest_re} ^{seg_q}(\\/.*)?$")
                lines.append("    }")
                lines.append(f"    handle {matcher} {{")
                if target_path and target_path != "/":
                    tp = target_path.rstrip('/')
                    lines.append(f"        redir https://{target}{tp}{{re.{rest_re}.1}} 301")
                else:
                    lines.append(f"        redir https://{target}{{re.{rest_re}.1}} 301")
                lines.append("    }")
    except Exception as exc:
        logger.warning("Could not load wildcard path redirects: %s", exc)
        return []
    return lines


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

    # ── SINGLE global options block (MUST be the first section) ─────────
    # Caddyfile syntax allows exactly ONE keyless (global) block and it
    # must precede every site block. An earlier Edge Shield revision
    # appended a SECOND global block at the END of the file — Caddy
    # rejected the whole file ("server block without any key is global
    # configuration, and if used, it must be first"), every reload
    # silently failed, and the running process kept an empty TLS app:
    # on-demand issuance stopped working for ALL custom domains
    # (TLS alert 80 / no peer certificate — the distinctionlabs.org
    # outage). Everything global lives HERE and ONLY here.
    global_lines = [
        "{",
        "    on_demand_tls {",
        f"        ask {_ask_url}",
        "    }",
    ]

    # Edge Shield: when records are Cloudflare-proxied, ALL inbound
    # connections arrive from Cloudflare IPs and X-Forwarded-For carries
    # the true client. Without this, Caddy logs/rate-limits see only CF
    # edge IPs — CrowdSec bans single Cloudflare nodes and every client
    # shares one rate-limit bucket. trusted_proxies restricts
    # X-Forwarded-For honoring to Cloudflare's published ranges only
    # (a non-CF source cannot spoof its forwarding chain).
    try:
        _edge_proxied = bool(getattr(config, "edge_proxy_records", False))
    except Exception:
        _edge_proxied = False
    if _edge_proxied and domain:
        global_lines.extend([
            "    servers {",
            "        trusted_proxies static 173.245.48.0/20 103.21.244.0/22 103.22.200.0/22 103.31.4.0/22 141.101.64.0/18 108.162.192.0/18 190.93.240.0/20 188.114.96.0/20 197.234.240.0/22 198.41.128.0/17 162.158.0.0/15 104.16.0.0/13 172.64.0.0/13 131.0.72.0/22",
            "    }",
        ])

    global_lines.append("}")
    sections.append("\n".join(global_lines))

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
            preview_auth_on = True
            try:
                from apps.deployments.models import PlatformConfig as _PC
                preview_auth_on = bool(getattr(_PC.load(), "preview_auth_required", True))
            except Exception:
                preview_auth_on = True
            try:
                from apps.deployments.models import Service
                if _table_exists(Service._meta.db_table):
                    local_previews = list(
                        Service.objects.select_related("server")
                        .filter(is_preview=True, server__is_primary=True, status=Service.Status.ACTIVE)
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
                    # PREVIEW ACCESS GATE: previews often run against full
                    # clones of the production database. Without this
                    # block anyone who scraped the PR hostname browsed the
                    # clone. basic_auth with a per-service password
                    # (username: preview) whenever the platform gate is on.
                    if preview_auth_on:
                        p_hash = _preview_bcrypt_hash(p_service)
                        if p_hash:
                            wildcard_lines.extend(
                                [
                                    f"    {matcher} host {p_domain}",
                                    f"    handle {matcher} {{",
                                    "        basic_auth {",
                                    f"            preview {p_hash}",
                                    "        }",
                                    f"        reverse_proxy {p_service.name}:{port}",
                                    "    }",
                                ]
                            )
                            continue
                    wildcard_lines.extend(
                        [
                            f"    {matcher} host {p_domain}",
                            f"    handle {matcher} {{",
                            f"        reverse_proxy {p_service.name}:{port}",
                            "    }",
                        ]
                    )

            # Internal-only wildcard domains: public visitors get the 503
            # fallback page, internal/mesh clients are routed normally.
            # Defined BEFORE redirects/disabled/known handlers.
            internal_only_upstreams = _get_wildcard_internal_only_upstreams(domain)
            if internal_only_upstreams:
                wildcard_lines.extend(
                    [
                        f"    @internal_only_hosts host {' '.join(sorted(h for hosts in internal_only_upstreams.values() for h in hosts))}",
                        "    @internal_src client_ip 10.0.0.0/8 172.16.0.0/12 192.168.0.0/16 100.64.0.0/10",
                        "    handle @internal_only_hosts {",
                        "        handle @internal_src {",
                    ]
                )
                for index, (upstream_url, hosts) in enumerate(internal_only_upstreams.items()):
                    matcher = f"@internal_upstream_{index}"
                    upstream_has_fallbacks = len(str(upstream_url).split()) > 1
                    wildcard_lines.extend(
                        [
                            f"        {matcher} host {' '.join(hosts)}",
                            f"        handle {matcher} {{",
                            f"            reverse_proxy {upstream_url} {{",
                        ]
                    )
                    if upstream_has_fallbacks:
                        wildcard_lines.extend(
                            [
                                "                lb_try_duration 5s",
                                "                lb_try_interval 250ms",
                            ]
                        )
                    wildcard_lines.extend(
                        [
                            "                header_up Host {host}",
                            "            }",
                            "        }",
                        ]
                    )
                wildcard_lines.extend(
                    [
                        "            handle {",
                        "                reverse_proxy route-fallback:80 {",
                        "                    header_up X-SMSLY-Fallback-Reason disabled",
                        "                }",
                        "            }",
                        "        }",
                        "    }",
                    ]
                )

            # Wildcard -> custom domain 301 redirects (opt-in per service).
            # Defined BEFORE the proxy handlers so redirected hosts never hit
            # @known_hosts or the landing catch-all.
            wildcard_redirects = _get_wildcard_redirect_map(domain)
            for index, (redirect_host, redirect_target) in enumerate(wildcard_redirects.items()):
                matcher = f"@wildcard_redirect_{index}"
                wildcard_lines.extend(
                    [
                        f"    {matcher} host {redirect_host}",
                        f"    handle {matcher} {{",
                        f"        redir https://{redirect_target}{{uri}} 301",
                        "    }",
                    ]
                )

            # Per-service path redirects on wildcard-covered public domains.
            # A service whose public_domain is a subdomain of this wildcard
            # gets its own domain block skipped above, so its path_redirects
            # (/login -> accounts.trulay.co/login) must be emitted here,
            # scoped to that service's hostname. BEFORE the proxy handlers
            # so the redirect wins over proxying to the app.
            wildcard_lines.extend(_get_wildcard_path_redirect_lines(domain))

            # Disabled/private/hidden wildcard domains serve the 503 fallback
            # page from route-fallback — NEVER the platform landing page.
            disabled_hosts = _get_wildcard_disabled_hosts(domain)
            if disabled_hosts:
                wildcard_lines.extend(
                    [
                        f"    @disabled_wildcard_hosts host {' '.join(disabled_hosts)}",
                        "    handle @disabled_wildcard_hosts {",
                        "        reverse_proxy route-fallback:80 {",
                        "            header_up X-SMSLY-Fallback-Reason disabled",
                        "        }",
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
                    # Wildcard catch-all: forward unmatched subdomains to
                    # Traefik (which has the per-service routers). Traefik
                    # returns 404 for hosts it doesn't know — much better
                    # than silently serving the PaaS landing page. The
                    # 'header_up Host {host}' preserves the original Host
                    # so Traefik can do its Host() match correctly.
                    "    handle {",
                    "        reverse_proxy traefik:80 {",
                    "            header_up Host {host}",
                    "        }",
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
