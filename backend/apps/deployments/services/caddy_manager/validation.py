import logging

from apps.domains.utils import normalize_domain

from .upstream import _service_proxy_upstream
from .utils import _normalize_caddy_site_label, _table_exists

logger = logging.getLogger(__name__)

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


def _known_service_route_domains() -> set[str]:
    domains: set[str] = set()
    try:
        from apps.deployments.models import Service
        from apps.deployments.models.addons import Addon

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

        for addon in Addon.objects.exclude(public_domain__isnull=True).exclude(public_domain="").only("id", "public_domain"):
            raw_domain = str(addon.public_domain or "").strip()
            if not raw_domain:
                continue
            try:
                domains.add(normalize_domain(raw_domain, allow_ip=True))
            except ValueError:
                logger.warning("Skipping invalid addon public domain in guard: %r", raw_domain)
    except Exception as exc:
        logger.warning("Could not load service route domains for Caddy guard: %s", exc)
    return domains


def _block_reverse_proxies_to_control_plane(block: str) -> list[str]:
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
