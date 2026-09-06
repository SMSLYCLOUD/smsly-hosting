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


def _wildcard_has_safe_service_handlers(block_lines: list[str], service_domains: set[str]) -> bool:
    """Check if a wildcard block has @host matchers that safely route service
    domains to non-control-plane upstreams (e.g. traefik:80).

    When this is true, the catch-all ``handle { reverse_proxy frontend:3000 }``
    never fires for those service domains, so the block is safe.
    """
    # Collect @host matcher names and their upstreams
    matchers: dict[str, str] = {}  # matcher_name -> upstream
    in_handle_for: str | None = None
    for raw_line in block_lines:
        stripped = raw_line.strip()
        # Detect @name host ... lines
        if stripped.startswith("@") and " host " in stripped:
            name = stripped.split()[0]
            _, _, host_list = stripped.partition(" host ")
            hosts = {_normalize_caddy_site_label(h) for h in host_list.split()}
            if hosts & service_domains:
                matchers[name] = "_pending_"  # will be filled when we see its handler
        # Detect handle @name { lines and the next reverse_proxy
        if stripped.startswith("handle @") and stripped.endswith("{"):
            handle_name = stripped.split()[1].lstrip("@").rstrip("{").strip()
            in_handle_for = handle_name
            continue
        if in_handle_for and stripped.startswith("reverse_proxy "):
            parts = stripped.split()
            if len(parts) >= 2:
                upstream = parts[1].strip("{").strip()
                if upstream not in CONTROL_PLANE_UPSTREAMS:
                    matchers[in_handle_for] = upstream
            in_handle_for = None
            continue
        if in_handle_for and (stripped == "}" or (not stripped.startswith("reverse_proxy") and stripped.endswith("}"))):
            in_handle_for = None

    # If any matcher that references a service domain routes safely, the block is safe
    for name, upstream in matchers.items():
        if upstream and upstream != "_pending_" and upstream not in CONTROL_PLANE_UPSTREAMS:
            return True
    return False


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

        # If the wildcard block has @host matchers that route service domains
        # to non-control-plane upstreams (e.g. traefik:80), the catch-all
        # ``handle { reverse_proxy frontend:3000 }`` never fires for those
        # domains — the block is safe.
        if wildcard_service_hosts and not is_service_site:
            if _wildcard_has_safe_service_handlers(block_lines, service_domains):
                continue

        bad_lines = _block_reverse_proxies_to_control_plane(block)
        if not bad_lines:
            continue

        route_name = ", ".join(label for label in labels if label) or f"block:{index + 1}"
        errors.append(
            f"{route_name} routes a service domain to the control plane: {bad_lines[0]}"
        )

    return errors


def extract_site_labels(content: str) -> set[str]:
    """Return the normalized site labels (hostnames) of every site block.

    Skips the keyless global options block and port-only addresses
    like ``:80`` (those carry no hostname). Used by the no-regression
    guard to compare the generated content against the live file.
    """
    labels: set[str] = set()
    for line in str(content or "").splitlines():
        stripped = line.strip()
        if not stripped.endswith("{"):
            continue
        for label in stripped[:-1].strip().split():
            label = (label or "").strip()
            if not label or label.startswith(":"):
                continue
            norm = _normalize_caddy_site_label(label)
            if norm:
                labels.add(norm)
    return labels


def validate_no_site_block_regression(
    new_content: str,
    live_content: str,
    platform_domain: str = "",
) -> list[str]:
    """Refuse new content that drops site blocks present in the live file.

    OUTAGE GUARD (2026-09-06): a restart-race generated a stub Caddyfile
    (global block + bare ``:80`` only) while the platform config read
    empty; every site block vanished, Caddy dropped its 443 listener,
    and all proxied traffic 521'd. The domain-based control-plane
    guard cannot catch this when the domain itself is unreadable — so
    compare against what's actually live.

    Scoping (so legitimate removals keep working): a tenant removing a
    custom domain regenerates content that drops exactly that block —
    allowed. Refusal fires only when (a) the KNOWN platform domain is
    among the dropped, or (b) the domain is unknown AND every live
    label disappears (the total-wipe stub signature). Fresh installs
    (no live file) are unaffected.
    """
    if not live_content or not live_content.strip():
        return []
    live_labels = extract_site_labels(live_content)
    if not live_labels:
        return []
    new_labels = extract_site_labels(new_content or "")
    dropped = sorted(live_labels - new_labels)
    if not dropped:
        return []

    platform_domain = str(platform_domain or "").strip().lower().rstrip(".")
    total_wipe = not new_labels
    platform_dropped = bool(platform_domain) and platform_domain in dropped
    if not (platform_dropped or total_wipe):
        return []

    return [
        f"Refusing to apply Caddyfile that drops live site block(s): "
        f"{', '.join(dropped)}. The previous good Caddyfile stays live. "
        f"Likely cause: generation ran with an unreadable/empty platform "
        f"config (backend restart race) — check PlatformConfig.domain."
    ]


def validate_control_plane_block_present(content: str, platform_domain: str) -> list[str]:
    """Refuse to apply a Caddyfile that drops the control plane site block.

    ROOT CAUSE GUARD (2026-09-02 outage): during a backend restart race,
    a Caddyfile was generated + applied WITHOUT the platform's own site
    block (grid.smsly.cloud). Every proxied API request then failed with
    525 (CF -> origin TLS handshake had no cert), including the PATCHes
    that would have fixed it — the operator was locked out of the UI.

    This runs inside apply_caddyfile BEFORE the file is written. If the
    platform domain block is missing, the apply is refused and the
    previous good Caddyfile stays live.
    """
    if not platform_domain:
        return []
    platform_domain = str(platform_domain).strip().lower().rstrip(".")
    if not platform_domain:
        return []

    for line in str(content or "").splitlines():
        stripped = line.strip()
        if not stripped.endswith("{"):
            continue
        labels = [
            _normalize_caddy_site_label(label)
            for label in stripped[:-1].strip().split()
            if label.strip()
        ]
        if platform_domain in labels:
            return []

    return [
        f"Platform control-plane site block for '{platform_domain}' is "
        "MISSING from the generated Caddyfile. Refusing to apply — this "
        "would lock the operator out of the API (525 on every proxied "
        "request). Likely cause: PlatformConfig.domain/use_ssl were "
        "unreadable during generation (backend restart race)."
    ]
