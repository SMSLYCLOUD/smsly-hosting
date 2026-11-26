import ipaddress
import logging
import os
import re

from apps.domains.utils import normalize_domain
from django.conf import settings

from .tls import _generate_selfsigned_cert, sanitize_cloudflare_token
from .upstream import _remote_upstream_url_for_service, _service_proxy_upstream
from .utils import _is_ip, _table_exists, is_agent_lite

logger = logging.getLogger(__name__)

CADDY_CONFIG_DIR = os.environ.get("CADDY_CONFIG_DIR", "/caddy-config")

# User-configurable per-service path redirects: /segment -> https://target/...
_PATH_REDIRECT_SEGMENT_RE = re.compile(r"^/[a-z0-9_-]{1,63}$")
_MAX_PATH_REDIRECTS_PER_SERVICE = 50

SECURE_HEADERS_SNIPPET_NAME = "secure_headers"
SECURE_HEADERS_SNIPPET = """(secure_headers) {
    header {
        Strict-Transport-Security "max-age=31536000; includeSubDomains; preload"
        X-Content-Type-Options nosniff
        X-Frame-Options DENY
        Content-Security-Policy "frame-ancestors 'none'"
        Referrer-Policy strict-origin-when-cross-origin
        Permissions-Policy "camera=(), microphone=(), geolocation=()"
        -Server
    }
}"""

CORAZA_SNIPPET_NAME = "coraza_waf"
CORAZA_SNIPPET = """(coraza_waf) {
    coraza_waf {
        load_owasp_crs
        directives `
            Include @coraza.conf-recommended
            Include @crs-setup.conf.example
            Include @owasp_crs/*.conf
            SecRuleEngine On
            # Platform inter-server sync bypass: master<->node control
            # traffic (service sync, deploy triggers, heartbeats) carries
            # X-SMSLY-Remote-Sync and is HMAC-authenticated downstream in
            # Django. CRS method-enforcement (e.g. PATCH) and anomaly
            # scoring otherwise 403 every node sync with an empty body,
            # breaking all remote deploys (2026-10-13: 949110 on PATCH
            # /api/v1/services/<uuid>/). Scoped strictly to /api/v1/ URIs;
            # tenant blocks also strip X-SMSLY-Remote-Sync inside a route
            # block before Coraza runs.
            SecRule REQUEST_HEADERS:X-SMSLY-Remote-Sync "@streq 1" "id:1000000,phase:1,deny,status:403,log,t:none,chain"
                SecRule &REQUEST_HEADERS:X-Gateway-Signature-V2 "@eq 0" "t:none,chain"
                    SecRule &REQUEST_HEADERS:Authorization "@eq 0" "t:none"
            SecRule REQUEST_HEADERS:X-SMSLY-Remote-Sync "@streq 1" "id:1000001,phase:1,pass,log,t:none,chain"
                SecRule &REQUEST_HEADERS:X-Gateway-Signature-V2 "@gt 0" "t:none,chain"
                    SecRule REQUEST_URI "@beginsWith /api/v1/" "t:none,t:urlDecodeUni,t:normalizePath,ctl:ruleRemoveById=911100,ctl:ruleRemoveById=920170,ctl:ruleRemoveById=920171"
        `
    }
}"""

# Edge JWT gating via the host sidecar (smsly-edge-sidecar), NOT via the
# caddy-security plugin: Caddy has no safe native HS256 gate matching our
# edge tokens, and a second verification path would drift from the sidecar
# (the single verifier shared with Traefik forwardAuth). forward_auth
# passes the client's Authorization header through and only proxies when
# the sidecar answers 2xx, copying the identity headers downstream.
EDGE_SIDECAR_HOST = "host.docker.internal:8971"
EDGE_JWT_FORWARD_AUTH_LINES = [
    f"    forward_auth {EDGE_SIDECAR_HOST} {{",
    "        uri /auth-verify",
    "        copy_headers X-User-Id X-Edge-Scope",
    "    }",
]

# Host aliases (accounts.google.com pattern): extra hostnames that serve the app
_ALIAS_REWRITE_ROOT_RE = re.compile(r"^/[A-Za-z0-9/_.-]{0,100}$")
_REDIRECT_TARGET_PATH_RE = re.compile(r"^/[A-Za-z0-9/_.-]{0,200}$")
_MAX_HOST_ALIASES_PER_SERVICE = 10

_CLOUDFLARE_TRUSTED_CIDRS: tuple[str, ...] = (
    # Cloudflare IPv4
    "173.245.48.0/20", "103.21.244.0/22", "103.22.200.0/22", "103.31.4.0/22",
    "141.101.64.0/18", "108.162.192.0/18", "190.93.240.0/20", "188.114.96.0/20",
    "197.234.240.0/22", "198.41.128.0/17", "162.158.0.0/15", "104.16.0.0/13",
    "172.64.0.0/13", "131.0.72.0/22",
    # Cloudflare IPv6
    "2400:cb00::/32", "2606:4700::/32", "2803:f800::/32", "2405:b500::/32",
    "2405:8100::/32", "2a06:98c0::/29", "2c0f:f248::/32",
)


def _format_host_cidr(raw_ip: str) -> str:
    raw = str(raw_ip or "").strip()
    if not raw:
        return ""
    try:
        addr = ipaddress.ip_address(raw)
        return f"{addr}/32" if addr.version == 4 else f"{addr}/128"
    except ValueError:
        try:
            return str(ipaddress.ip_network(raw, strict=False))
        except ValueError:
            return ""


def _trusted_proxy_cidrs(include_cloudflare: bool = True, config=None) -> list[str]:
    """Return ordered Caddy trusted_proxies CIDRs for master & node Caddyfiles.

    When Cloudflare edge proxying is enabled, trusts private_ranges,
    100.64.0.0/10, known ManagedServer IPs, and Cloudflare edge CIDRs.
    When Cloudflare edge proxying is NOT enabled, narrows trusted proxies
    to private_ranges, the WireGuard mesh 10.100.0.0/24, and known
    ManagedServer host/WireGuard IPs.
    """
    cidrs: list[str] = (
        ["private_ranges", "100.64.0.0/10"]
        if include_cloudflare
        else ["private_ranges", "10.100.0.0/24"]
    )
    if config is not None:
        srv_cidr = _format_host_cidr(getattr(config, "server_ip", "") or "")
        if srv_cidr:
            cidrs.append(srv_cidr)
    try:
        from django.db.models import Q
        from apps.deployments.models.core import ManagedServer
        if _table_exists(ManagedServer._meta.db_table):
            for host_ip, wg_ip in ManagedServer.objects.filter(
                Q(is_primary=True) | Q(owner__is_staff=True) | Q(owner__is_superuser=True)
            ).values_list("host", "wg_address"):
                for raw in (host_ip, wg_ip):
                    cidr = _format_host_cidr(raw or "")
                    if cidr:
                        cidrs.append(cidr)
    except Exception as exc:
        logger.debug("ManagedServer lookup skipped for trusted_proxies: %s", exc)
    if include_cloudflare:
        cidrs.extend(_CLOUDFLARE_TRUSTED_CIDRS)
    return list(dict.fromkeys(cidrs))


def _build_servers_trusted_proxies_lines(include_cloudflare: bool = True, config=None) -> list[str]:
    proxies = " ".join(_trusted_proxy_cidrs(include_cloudflare=include_cloudflare, config=config))
    ip_headers = "Cf-Connecting-Ip X-Forwarded-For" if include_cloudflare else "X-Forwarded-For"
    return [
        "    servers {",
        f"        trusted_proxies static {proxies}",
        f"        client_ip_headers {ip_headers}",
        "    }",
    ]


def _resolve_crowdsec_bouncer_key(config) -> str:
    try:
        key = str(getattr(config, "crowdsec_bouncer_key", "") or "").strip()
        if key:
            return key
    except Exception:
        pass
    return str(os.environ.get("CROWDSEC_BOUNCER_KEY", "") or "").strip()


def node_service_domain(slug: str, node_number, base_domain: str) -> str:
    """Flat per-node service hostname: ``{slug}-node{N}.{zone}``.

    Second-level under the platform zone on purpose: Cloudflare
    Universal SSL covers second-level names, so these ride the
    platform wildcard behind CF protection (orange-cloud capable)
    with zero per-host DNS. The deep ``{slug}.node{N}...`` form is
    third-level and cannot be proxied on Cloudflare free plans
    (kept grey by default; orange only with paid Advanced Certificates).
    """
    slug = (slug or "").strip().lower().replace(" ", "-")
    try:
        node_number = int(node_number or 1)
    except (TypeError, ValueError):
        node_number = 1
    base = (base_domain or "").strip()
    parts = base.split(".")
    rest = ".".join(parts[1:]) if len(parts) > 2 else base
    return f"{slug}-node{node_number}.{rest}"


def node_service_domain_legacy_flat(slug: str, node_number, base_domain: str) -> str:
    """Legacy flat hostname (``{slug}-grid{N}.{zone}``) — served for compat."""
    slug = (slug or "").strip().lower().replace(" ", "-")
    try:
        node_number = int(node_number or 1)
    except (TypeError, ValueError):
        node_number = 1
    base = (base_domain or "").strip()
    parts = base.split(".")
    rest = ".".join(parts[1:]) if len(parts) > 2 else base
    return f"{slug}-grid{node_number}.{rest}"


def node_service_domain_nested(slug: str, node_number, base_domain: str) -> str:
    """Direct-access hostname: ``{slug}.node{N}.{zone}`` (grey by default)."""
    slug = (slug or "").strip().lower().replace(" ", "-")
    try:
        node_number = int(node_number or 1)
    except (TypeError, ValueError):
        node_number = 1
    base = (base_domain or "").strip()
    parts = base.split(".")
    rest = ".".join(parts[1:]) if len(parts) > 2 else base
    return f"{slug}.node{node_number}.{rest}"


def node_service_domain_legacy_nested(slug: str, node_number, base_domain: str) -> str:
    """Legacy direct-access hostname: ``{slug}.grid{N}.{zone}`` (grey)."""
    slug = (slug or "").strip().lower().replace(" ", "-")
    try:
        node_number = int(node_number or 1)
    except (TypeError, ValueError):
        node_number = 1
    base = (base_domain or "").strip()
    parts = base.split(".")
    rest = ".".join(parts[1:]) if len(parts) > 2 else base
    return f"{slug}.grid{node_number}.{rest}"


def node_domain_proxy_info(slug: str, node_number, base_domain: str) -> dict:
    """Return the 3-domain set with Cloudflare proxy guidance (fail-closed).

    - master: platform-issued public_domain (orange capable).
    - flat: ``{slug}-node{N}.{zone}`` — second-level, orange capable.
    - deep: ``{slug}.node{N}.{zone}`` — third-level, grey-only on free
      plans; orange only when the operator enables paid certs
      (PlatformConfig.edge_proxy_wildcards).
    """
    try:
        from apps.deployments.models import PlatformConfig
        cfg = PlatformConfig.load()
        paid_proxy = bool(getattr(cfg, "edge_proxy_wildcards", False))
    except Exception:
        paid_proxy = False
    return {
        "flat_proxiable": True,
        "deep_proxiable": paid_proxy,
        "deep_default": "dns-only",
    }


def node_service_domains_for_service(service, node=None) -> list[str]:
    """Return the node-scoped hostnames (flat/deep + legacy) for *service*.

    Accepts an optional explicit *node* (e.g. an overflow target node);
    otherwise resolves the effective server via ``_resolve_effective_server``.
    Returns ``[]`` for primary/local services or when base domain is unset.
    """
    try:
        from apps.deployments.models.service import Service as _Svc

        svr = node or _resolve_effective_server(service)
        if svr is None or getattr(svr, "is_primary", True):
            return []
        node_num = getattr(svr, "node_number", None)
        if node_num is None or not isinstance(node_num, int):
            return []
        base = _Svc.default_public_base_domain()
        raw_slug = getattr(service, "slug", "") or getattr(service, "name", "") or ""
        if not isinstance(raw_slug, str):
            return []
        slug = raw_slug.strip().lower().replace(" ", "-")
        if not slug or not base:
            return []
        hosts = [
            node_service_domain(slug, node_num, base),
            node_service_domain_nested(slug, node_num, base),
            node_service_domain_legacy_flat(slug, node_num, base),
            node_service_domain_legacy_nested(slug, node_num, base),
        ]
        return [h for h in dict.fromkeys(hosts) if h]
    except Exception:
        return []


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


# Retry budget for backend-bound proxies. A backend container recreate
# leaves a dial gap (old container gone, gunicorn still booting —
# 2026-09-25: ~1 min of 502s on every backend restart, surfaced as user
# 503s). Retries bridge the gap for idempotent requests (Caddy never
# retries a request whose body was already sent); single upstream, so
# this only re-dials backend:8000. Keep the window above the worst
# observed cold boot (~60s for this Django app).
BACKEND_PROXY_RETRY_OPTIONS = (
    "lb_try_duration 60s",
    "lb_try_interval 1s",
    "lb_retries 8",
)


def _apply_backend_retry_budget(caddyfile: str) -> str:
    """Give every `reverse_proxy backend:8000` a retry budget.

    Single choke point (called once by generate_caddyfile): a backend
    container recreate leaves a dial gap (old container gone, gunicorn
    still booting — 2026-09-25: ~1 min of 502s on every backend restart,
    surfaced as user 503s). Retries bridge the gap for idempotent
    requests (Caddy never retries a request whose body was already
    sent); single upstream, so this only re-dials backend:8000.

    Two cases: bare one-liners become full blocks; existing blocks
    (e.g. challenge handles with header_up) get the options injected
    when absent. Idempotent — a second run changes nothing.
    """
    import re as _re

    bare_re = _re.compile(r"^([ \t]*)reverse_proxy backend:8000[ \t]*$")
    block_re = _re.compile(r"^([ \t]*)reverse_proxy backend:8000 \{$")

    out: list[str] = []
    lines = caddyfile.split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]
        bare = bare_re.match(line)
        if bare:
            indent = bare.group(1)
            out.append(f"{indent}reverse_proxy backend:8000 {{")
            for opt in BACKEND_PROXY_RETRY_OPTIONS:
                out.append(f"{indent}    {opt}")
            out.append(f"{indent}}}")
            i += 1
            continue
        opened = block_re.match(line)
        if opened:
            indent = opened.group(1)
            buf = [line]
            i += 1
            depth = 1
            has_lb = False
            while i < len(lines) and depth > 0:
                current = lines[i]
                if "lb_try_duration" in current or "lb_retries" in current:
                    has_lb = True
                depth += current.count("{") - current.count("}")
                buf.append(current)
                i += 1
            if not has_lb:
                buf.insert(
                    -1,
                    f"{indent}    "
                    + f"\n{indent}    ".join(BACKEND_PROXY_RETRY_OPTIONS),
                )
            out.extend(buf)
            continue
        out.append(line)
        i += 1
    return "\n".join(out)


def _strip_cf_connecting_ip_in_proxies(caddyfile: str) -> str:
    """Strip client-supplied CF-Connecting-IP on non-Cloudflare edges."""
    import re as _re

    bare_re = _re.compile(r"^([ \t]*)reverse_proxy ([^\s{]+)[ \t]*$")
    block_re = _re.compile(r"^([ \t]*)reverse_proxy (.+?) \{$")

    out: list[str] = []
    lines = caddyfile.split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]
        bare = bare_re.match(line)
        if bare:
            indent, upstream = bare.group(1), bare.group(2)
            if upstream == "frontend:3000":
                out.append(line)
            else:
                out.append(f"{indent}reverse_proxy {upstream} {{")
                out.append(f"{indent}    header_up -CF-Connecting-IP")
                out.append(f"{indent}}}")
            i += 1
            continue
        opened = block_re.match(line)
        if opened:
            indent = opened.group(1)
            buf = [line]
            i += 1
            depth = 1
            has_cf = False
            while i < len(lines) and depth > 0:
                current = lines[i]
                if "cf-connecting-ip" in current.lower():
                    has_cf = True
                depth += current.count("{") - current.count("}")
                buf.append(current)
                i += 1
            if not has_cf:
                buf.insert(-1, f"{indent}    header_up -CF-Connecting-IP")
            out.extend(buf)
            continue
        out.append(line)
        i += 1
    return "\n".join(out)


def _append_reverse_proxy(
    lines: list[str],
    upstream_url: str,
    upstream_host: str = "",
    *,
    sablier_enabled: bool = False,
):
    upstream_url = upstream_url or _service_proxy_upstream()
    has_fallbacks = len(str(upstream_url).split()) > 1
    is_remote_upstream = "://" in str(upstream_url)
    is_fallback_route = str(upstream_url).strip() == "route-fallback:80"
    sab_retry = bool(sablier_enabled) and not is_fallback_route
    if upstream_host or has_fallbacks or is_remote_upstream or sab_retry:
        lines.append(f"    reverse_proxy {upstream_url} {{")
        if has_fallbacks:
            lines.append("        lb_policy round_robin")
            lines.append("        fail_duration 15s")
            lines.append("        max_fails 2")
            if sab_retry:
                lines.append("        lb_try_duration 60s")
                lines.append("        lb_try_interval 1s")
            else:
                lines.append("        lb_try_duration 5s")
                lines.append("        lb_try_interval 250ms")
        elif sab_retry:
            lines.append("        lb_try_duration 60s")
            lines.append("        lb_try_interval 1s")
        if upstream_host:
            lines.append(f"        header_up Host {upstream_host}")
        if is_remote_upstream:
            lines.append("        header_up Cf-Connecting-Ip {client_ip}")
        if upstream_url.startswith("https://") and upstream_host:
            lines.append("        transport http {")
            lines.append("            tls")
            lines.append(f"            tls_server_name {upstream_host}")
            lines.append("        }")
        lines.append("    }")
    else:
        lines.append(f"    reverse_proxy {upstream_url}")


def _service_path_redirect_rules(service) -> list[tuple[str, str, str, str]]:
    """Sanitized (source_host, path_segment, target_host, target_path) rules.
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
    rules: list[tuple[str, str, str, str]] = []
    seen: set[tuple[str, str]] = set()
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
        source_host = ""
        if not path.startswith("/"):
            try:
                source_host, path = split_host_and_path(path)
                source_host = normalize_domain(source_host)
            except ValueError:
                continue
        if path != "/" and not _PATH_REDIRECT_SEGMENT_RE.match(path):
            continue
        if path == "/" and not source_host:
            # Unscoped root rule (no source host): fail safe, skip. The
            # serializer never produces these; emitting one would redirect
            # a whole site root on every site block it lands in.
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
        if target_path and (
            not _REDIRECT_TARGET_PATH_RE.match(target_path) or ".." in target_path
        ):
            continue
        rule_key = (source_host, path)
        if rule_key in seen:
            continue
        seen.add(rule_key)
        rules.append((source_host, path, target_host, target_path))
        if len(rules) >= _MAX_PATH_REDIRECTS_PER_SERVICE:
            break
    return rules


def _path_redirect_site_lines(
    rules: list[tuple[str, str, str, str]],
    site_domain: str = "",
    indent: str = "    ",
) -> list[str]:
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
    for index, (source_host, segment, target, target_path) in enumerate(rules):
        if source_host and source_host != site_domain:
            continue
        if segment == "/":
            # Whole-host root redirect (e.g. accounts.trulay.co =>
            # accounts.trulay.co/login): exact `/` ONLY. Sub-paths must
            # keep proxying - matching them too would swallow the site
            # (and loop when target shares the host).
            if not source_host:
                # Unscoped root rule: fail safe, skip. The serializer
                # never produces these; emitting one would redirect a
                # whole site root on every site block it lands in.
                continue
            dest = f"https://{target}{target_path if target_path and target_path != '/' else '/'}"
            lines.extend([
                f"{indent}@path_redir_{index}_root path /",
                f"{indent}handle @path_redir_{index}_root {{",
                f"{indent}    redir {dest} 301",
                f"{indent}}}",
            ])
            continue
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
        if rewrite_root and (not _ALIAS_REWRITE_ROOT_RE.match(rewrite_root) or ".." in rewrite_root):
            continue
        seen_hosts.add(host)
        rules.append((host, rewrite_root))
        if len(rules) >= _MAX_HOST_ALIASES_PER_SERVICE:
            break
    return rules


def _alias_scoped_redirects(rules: list[tuple[str, str, str, str]],
                            alias_host: str) -> list[tuple[str, str, str, str]]:
    """Redirect rules eligible for an alias host's site block.

    Only domain-scoped rules naming THIS alias host. Plain-path rules
    stay out (alias blocks never had them; adding them could hijack an
    alias rewrite_root), and foreign-host rules stay dropped.
    """
    return [r for r in (rules or []) if r and r[0] == alias_host]


def _build_host_alias_block(alias_host: str, rewrite_root: str, upstream_url: str, host_header: str,
                           path_redirect_rules: list[tuple[str, str, str, str]] | None = None,
                           edge_jwt_required: bool = False, waf_opt_out: bool = False,
                           crowdsec_enabled: bool = False,
                           sablier_enabled: bool = False) -> str:
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
        "    import secure_headers",
    ])
    if crowdsec_enabled:
        lines.append("    crowdsec")
    if not waf_opt_out:
        lines.extend([
            "    route {",
            "        request_header -X-SMSLY-Remote-Sync",
            "        request_header -X-User-Id",
            "        request_header -X-Edge-Scope",
            "        import coraza_waf",
            "    }",
        ])
    else:
        lines.extend([
            "    request_header -X-SMSLY-Remote-Sync",
            "    request_header -X-User-Id",
            "    request_header -X-Edge-Scope",
        ])
    # Aliases proxy DIRECTLY to the upstream (bypassing Traefik), so the
    # edge gate must be emitted here too — otherwise a gated service's
    # alias hosts serve the whole app unauthenticated.
    if edge_jwt_required:
        lines.extend([
            "    request_header -X-User-Id",
            "    request_header -X-Edge-Scope",
        ])
        lines.extend(EDGE_JWT_FORWARD_AUTH_LINES)
    lines.extend([
        "    log {",
        "        output file /var/log/caddy/access.log {",
        "            mode 0644",
        "        }",
        "    }",
    ])
    # Domain-scoped path redirects for THIS alias host (e.g. source
    # ``accounts.example.com/login``). Redirect handles come first: Caddy
    # `handle` blocks are exclusive, first match wins, and a redirect must
    # beat the rewrite/proxy below. Plain-path rules are deliberately NOT
    # included — alias blocks never had them.
    if path_redirect_rules:
        lines.extend(_path_redirect_site_lines(path_redirect_rules, site_domain=alias_host))
    if rewrite_root and rewrite_root != "/":
        lines.extend([
            "    @alias_root path /",
            "    handle @alias_root {",
            f"        rewrite * {rewrite_root}",
        ])
        before = len(lines)
        _append_reverse_proxy(lines, upstream_url, host_header, sablier_enabled=sablier_enabled)
        for i in range(before, len(lines)):
            lines[i] = "    " + lines[i]
        lines.append("    }")
    lines.append("    handle {")
    _append_reverse_proxy(lines, upstream_url, host_header, sablier_enabled=sablier_enabled)
    lines.append("    }")
    lines.extend(["    encode gzip", "}"])
    return "\n".join(lines)


def _build_service_domain_block(
    domain: str,
    upstream_host: str,
    upstream_url: str = "",
    path_redirect_rules: list[tuple[str, str, str, str]] | None = None,
    edge_jwt_required: bool = False,
    waf_opt_out: bool = False,
    crowdsec_enabled: bool = False,
    sablier_enabled: bool = False,
) -> str:
    lines = [f"{domain} {{"]

    lines.extend(_path_redirect_site_lines(path_redirect_rules or [], site_domain=domain))
    lines.append("    import secure_headers")
    if crowdsec_enabled:
        lines.append("    crowdsec")
    if not waf_opt_out:
        lines.extend([
            "    route {",
            "        request_header -X-SMSLY-Remote-Sync",
            "        request_header -X-User-Id",
            "        request_header -X-Edge-Scope",
            "        import coraza_waf",
            "    }",
        ])
    else:
        lines.extend([
            "    request_header -X-SMSLY-Remote-Sync",
            "    request_header -X-User-Id",
            "    request_header -X-Edge-Scope",
        ])
    if edge_jwt_required:
        lines.extend([
            "    request_header -X-User-Id",
            "    request_header -X-Edge-Scope",
        ])
        lines.extend(EDGE_JWT_FORWARD_AUTH_LINES)

    if upstream_url:
        _append_reverse_proxy(lines, upstream_url, upstream_host or domain, sablier_enabled=sablier_enabled)
    elif upstream_host and upstream_host != domain:
        _append_reverse_proxy(lines, _service_proxy_upstream(), upstream_host, sablier_enabled=sablier_enabled)
    else:
        _append_reverse_proxy(lines, _service_proxy_upstream(), sablier_enabled=sablier_enabled)

    lines.extend(
        [
            "    encode gzip",
            "    log {",
            "        output file /var/log/caddy/access.log {",
            "            mode 0644",
            "        }",
            "    }",
            "}",
        ]
    )
    return "\n".join(lines)


def _www_apex_redirect_target(value: str, verified_set: set[str]) -> str:
    """Return the apex hostname when a www custom domain must 301 to it.

    Canonicalization applies only when BOTH www.X and X are verified
    customs of the same service. www-only setups (apex attached
    elsewhere or nowhere) must keep proxying, so anything else yields
    ``""`` (no redirect).
    """
    if value.startswith("www.") and value[4:] in verified_set:
        return value[4:]
    return ""


def _get_service_domain_blocks(wildcard_domain: str = "", crowdsec_enabled: bool = False) -> list:
    blocks = []
    seen = set()

    try:
        from apps.deployments.models import Service
        from apps.domains.models import Domain, DomainStatus

        if not _table_exists(Service._meta.db_table):
            return []

        all_verified_domains: set[str] = set()
        if _table_exists(Domain._meta.db_table):
            for raw_dom in Domain.objects.filter(
                status__in=[
                    DomainStatus.ACTIVE,
                    DomainStatus.DNS_VERIFIED,
                    DomainStatus.SSL_PROVISIONING,
                ],
                verified=True,
            ).values_list("domain_name", flat=True):
                try:
                    all_verified_domains.add(normalize_domain(str(raw_dom or "").strip()))
                except ValueError:
                    continue

        services = list(
            Service.objects.only(
                "id", "status", "sablier_enabled",
                "public_domain", "custom_domains", "public_domain_hidden", "staging_domain",
                "host_aliases", "path_redirects", "edge_jwt_required", "waf_opt_out",
            ).order_by("id")
        )
        svc_context: dict = {}

        # Pass 1: process all public_domain and verified Domain blocks first so
        # no service's host_aliases can shadow another service's verified Domain.
        for service in services:
            sablier_stopped = (
                str(getattr(service, "status", "") or "").upper() == "STOPPED"
                and bool(getattr(service, "sablier_enabled", False))
            )
            effective_remote_upstream = (
                "route-fallback:80" if sablier_stopped
                else _remote_upstream_url_for_service(service)
            )
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
            sab_active = bool(getattr(service, "sablier_enabled", False)) and not sablier_stopped
            svc_context[service.id] = (effective_remote_upstream, public_domain, isHidden, sab_active)

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
                            upstream_url=effective_remote_upstream,
                            path_redirect_rules=_service_path_redirect_rules(service),
                            edge_jwt_required=bool(getattr(service, "edge_jwt_required", False)),
                            waf_opt_out=bool(getattr(service, "waf_opt_out", False)),
                            crowdsec_enabled=crowdsec_enabled,
                            sablier_enabled=sab_active,
                        )
                    )

            # VISIBILITY GATE: a Private service (is_public=False) or a
            # Hidden default-domain (public_domain_hidden=True) must not
            # keep custom domains publicly reachable — the toggle's
            # promise is honored end to end (Railway behavior for
            # private services). Skip the entire custom-domain loop.
            if (
                getattr(service, 'is_public', True) is False
                or getattr(service, 'public_domain_hidden', False)
            ):
                pass  # private/hidden: no public custom-domain routes
            else:
                # Canonicalize www -> apex (301) when BOTH are verified
                # customs of this same service: one canonical hostname per
                # site avoids split sessions/cookies and duplicate SEO
                # content. www-only setups (apex not attached here) keep
                # proxying untouched.
                _verified_ordered: list[str] = []
                for domain_obj in service.domain_instances.filter(
                    status__in=[
                        DomainStatus.ACTIVE,
                        DomainStatus.DNS_VERIFIED,
                        DomainStatus.SSL_PROVISIONING,
                    ],
                    verified=True,
                ):
                    _raw = domain_obj.domain_name.strip()
                    if not _raw:
                        continue
                    try:
                        _norm = normalize_domain(_raw)
                    except ValueError:
                        continue
                    if _norm and _norm not in _verified_ordered:
                        _verified_ordered.append(_norm)
                _verified_set = set(_verified_ordered)
                for value in _verified_ordered:
                    if value in seen:
                        continue
                    seen.add(value)
                    _apex = _www_apex_redirect_target(value, _verified_set)
                    if _apex:
                        lines = [f"{value} {{"]
                        lines.append("    tls {")
                        lines.append("        on_demand")
                        lines.append("    }")
                        lines.append("    import secure_headers")
                        if crowdsec_enabled:
                            lines.append("    crowdsec")
                        # Challenge handle BEFORE the apex redirect: the
                        # verifier fetches over http (308 to https) and the
                        # www host must answer the token itself, not bounce
                        # to the apex (which has a different row/token).
                        lines.append("    handle /.well-known/smsly-verify/* {")
                        lines.append("        reverse_proxy backend:8000 {")
                        lines.append("            header_up Host localhost")
                        lines.append("        }")
                        lines.append("    }")
                        lines.append("    handle {")
                        lines.append(f"        redir https://{_apex}{{uri}} 301")
                        lines.append("    }")
                        lines.append("}")
                        blocks.append("\n".join(lines))
                        continue
                    target_host = public_domain if (public_domain and not isHidden) else value

                    lines = [f"{value} {{"]
                    lines.extend(_path_redirect_site_lines(
                        _service_path_redirect_rules(service), site_domain=value,
                    ))
                    lines.append("    import secure_headers")
                    if crowdsec_enabled:
                        lines.append("    crowdsec")
                    if not bool(getattr(service, "waf_opt_out", False)):
                        lines.extend([
                            "    route {",
                            "        request_header -X-SMSLY-Remote-Sync",
                            "        request_header -X-User-Id",
                            "        request_header -X-Edge-Scope",
                            "        import coraza_waf",
                            "    }",
                        ])
                    else:
                        lines.extend([
                            "    request_header -X-SMSLY-Remote-Sync",
                            "    request_header -X-User-Id",
                            "    request_header -X-Edge-Scope",
                        ])
                    if bool(getattr(service, "edge_jwt_required", False)):
                        lines.extend([
                            "    request_header -X-User-Id",
                            "    request_header -X-Edge-Scope",
                        ])
                        lines.extend(EDGE_JWT_FORWARD_AUTH_LINES)
                    lines.append("    tls {")
                    lines.append("        on_demand")
                    lines.append("    }")
                    # Per-domain challenge handle: Caddy routes Host-based
                    # sites (both :80 with auto-https-redir and :443) ahead
                    # of the generic :80 site, so without this the token
                    # 308s to https and 404s in the app — breaking
                    # orange-clouded verification permanently.
                    lines.append("    handle /.well-known/smsly-verify/* {")
                    lines.append("        reverse_proxy backend:8000 {")
                    lines.append("            header_up Host localhost")
                    lines.append("        }")
                    lines.append("    }")

                    upstream_url = effective_remote_upstream
                    if upstream_url:
                        _append_reverse_proxy(lines, upstream_url, target_host or value, sablier_enabled=sab_active)
                    elif target_host and target_host != value:
                        _append_reverse_proxy(lines, _service_proxy_upstream(), target_host, sablier_enabled=sab_active)
                    else:
                        _append_reverse_proxy(lines, _service_proxy_upstream(), sablier_enabled=sab_active)
                    lines.append("    encode gzip")
                    lines.append("}")
                    blocks.append("\n".join(lines))

        # Pass 2: host_aliases and staging domains (after all verified Domains).
        for service in services:
            effective_remote_upstream, public_domain, _is_hidden, sab_active = svc_context[service.id]
            # Host aliases (accounts.google.com pattern): dedicated site
            # blocks that serve this app under extra hostnames. Exact host
            # matches take precedence over the platform wildcard block.
            upstream_url = effective_remote_upstream
            redirect_rules = _service_path_redirect_rules(service)
            for alias_host, rewrite_root in _service_host_alias_rules(service):
                if alias_host in seen or alias_host in all_verified_domains:
                    continue
                try:
                    from apps.deployments.views._helpers import _is_platform_owned_domain
                    if _is_platform_owned_domain(alias_host):
                        continue
                except Exception:
                    pass
                seen.add(alias_host)
                # Only domain-scoped rules naming THIS alias host are
                # emitted here — they had no site block before and were
                # silently dropped. Plain-path rules stay out (existing
                # behavior preserved); foreign-host rules stay dropped.
                alias_redirects = _alias_scoped_redirects(redirect_rules, alias_host)
                blocks.append(_build_host_alias_block(
                    alias_host,
                    rewrite_root,
                    upstream_url,
                    public_domain or alias_host,
                    path_redirect_rules=alias_redirects,
                    edge_jwt_required=bool(getattr(service, "edge_jwt_required", False)),
                    waf_opt_out=bool(getattr(service, "waf_opt_out", False)),
                    crowdsec_enabled=crowdsec_enabled,
                    sablier_enabled=sab_active,
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
                            lines.append("    import secure_headers")
                            if crowdsec_enabled:
                                lines.append("    crowdsec")
                            lines.extend([
                                "    route {",
                                "        request_header -X-SMSLY-Remote-Sync",
                                "        request_header -X-User-Id",
                                "        request_header -X-Edge-Scope",
                                "        import coraza_waf",
                                "    }",
                            ])
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
                    _addon_cs = "\n    crowdsec" if crowdsec_enabled else ""
                    blocks.append(
                        f"""{public_domain} {{
    import secure_headers{_addon_cs}
    route {{
        request_header -X-SMSLY-Remote-Sync
        request_header -X-User-Id
        request_header -X-Edge-Scope
        import coraza_waf
    }}
    reverse_proxy {_service_proxy_upstream()}
}}"""
                    )
                    seen.add(public_domain)

    except Exception as exc:
        logger.warning("Could not load service domains for Caddyfile: %s", exc)
    return blocks


def _get_wildcard_waf_optout_hosts(wildcard_domain: str) -> list[str]:
    """Public domains under the wildcard whose service opted out of WAF.

    The wildcard @known_hosts handle runs Coraza; opted-out hosts get
    their own earlier handle that proxies straight to Traefik so the
    opt-out promise holds end to end. Empty (usual) = no extra handle.
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
            "id", "public_domain", "waf_opt_out",
        ).filter(waf_opt_out=True):
            raw = service.public_domain or ""
            if not isinstance(raw, str) or not raw.strip():
                continue
            try:
                domain = normalize_domain(raw.strip())
            except ValueError:
                continue
            if domain.endswith(suffix):
                hosts.add(domain)
    except Exception as exc:
        logger.debug("WAF opt-out host lookup skipped: %s", exc)
    return sorted(hosts)


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

        active_wildcard_redirects = set(_get_wildcard_redirect_map(wildcard_domain).keys())

        for service in Service.objects.select_related('server').only(
            "id", "status", "sablier_enabled",
            "public_domain", "custom_domains", "public_domain_hidden",
            "is_preview", "server",
            "wildcard_url_enabled", "is_public",
            "wildcard_redirect_custom_domain", "wildcard_internal_only",
        ).all():
            if getattr(service, "is_preview", False):
                continue
            if (
                str(getattr(service, "status", "") or "").upper() == "STOPPED"
                and bool(getattr(service, "sablier_enabled", False))
            ):
                continue

            routing_off = getattr(service, "wildcard_url_enabled", True) is False
            private = getattr(service, "is_public", True) is False
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
                and public_domain not in active_wildcard_redirects
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
            "id", "status", "sablier_enabled",
            "public_domain", "custom_domains", "public_domain_hidden",
            "wildcard_url_enabled", "server", "wildcard_internal_only",
        ).all():
            if not getattr(service, "wildcard_url_enabled", True):
                continue
            if bool(getattr(service, "wildcard_internal_only", False)):
                # Handled by the dedicated internal-only section instead.
                continue
            if (
                str(getattr(service, "status", "") or "").upper() == "STOPPED"
                and bool(getattr(service, "sablier_enabled", False))
            ):
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


def _get_wildcard_edge_jwt_hosts(wildcard_domain: str) -> set[str]:
    """Wildcard hosts whose service requires edge JWT (forward_auth)."""
    hosts: set[str] = set()
    if not wildcard_domain:
        return hosts
    try:
        from apps.deployments.models import Service

        if not _table_exists(Service._meta.db_table):
            return hosts
        suffix = f".{wildcard_domain}"
        for service in Service.objects.only(
            "id", "public_domain", "custom_domains", "edge_jwt_required",
        ).filter(edge_jwt_required=True):
            raw_public = str(service.public_domain or "").strip()
            if raw_public:
                try:
                    pub = normalize_domain(raw_public)
                    if pub.endswith(suffix):
                        hosts.add(pub)
                except ValueError:
                    pass
            for item in (service.custom_domains or []):
                raw_c = item.strip() if isinstance(item, str) else ""
                if not raw_c:
                    continue
                try:
                    c = normalize_domain(raw_c)
                    if c.endswith(suffix):
                        hosts.add(c)
                except ValueError:
                    pass
    except Exception as exc:
        logger.debug("Wildcard edge_jwt host lookup skipped: %s", exc)
    return hosts


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
            "id", "status", "sablier_enabled", "custom_domains",
            "public_domain", "public_domain_hidden",
            "wildcard_url_enabled", "is_public",
        ).all():
            sablier_stopped = (
                str(getattr(service, "status", "") or "").upper() == "STOPPED"
                and bool(getattr(service, "sablier_enabled", False))
            )
            if sablier_stopped:
                for item in (service.custom_domains or []):
                    raw_c = item.strip() if isinstance(item, str) else ""
                    if not raw_c:
                        continue
                    try:
                        cdom = normalize_domain(raw_c)
                    except ValueError:
                        continue
                    if cdom.endswith(suffix):
                        hosts.add(cdom)
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
            if hidden or routing_off or private or sablier_stopped:
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
            "id", "status", "sablier_enabled",
            "public_domain", "public_domain_hidden",
            "wildcard_url_enabled", "wildcard_internal_only",
            "is_public", "server",
        ).all():
            if not getattr(service, "wildcard_internal_only", False):
                continue
            if (
                str(getattr(service, "status", "") or "").upper() == "STOPPED"
                and bool(getattr(service, "sablier_enabled", False))
            ):
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
    """Map wildcard public domains to their first verified custom domain (301 target).

    Services with ``wildcard_redirect_custom_domain=True`` and at least one
    verified custom domain (while publicly routable on their wildcard URL)
    get their auto-generated wildcard domain permanently redirected instead
    of proxied. Unverified/pending custom domains never hijack a working
    wildcard URL into a TLS 525 failure.
    """
    redirects: dict[str, str] = {}
    if not wildcard_domain:
        return {}

    try:
        from apps.deployments.models import Service
        from apps.domains.models import Domain, DomainStatus

        if not _table_exists(Service._meta.db_table):
            return {}

        verified_by_service: dict = {}
        if _table_exists(Domain._meta.db_table):
            for sid, raw_dom in Domain.objects.filter(
                status__in=[
                    DomainStatus.ACTIVE,
                    DomainStatus.DNS_VERIFIED,
                    DomainStatus.SSL_PROVISIONING,
                ],
                verified=True,
            ).values_list("service_id", "domain_name"):
                raw = str(raw_dom or "").strip()
                if not raw or sid is None:
                    continue
                try:
                    norm = normalize_domain(raw)
                except ValueError:
                    continue
                if norm:
                    verified_by_service.setdefault(sid, set()).add(norm)

        suffix = f".{wildcard_domain}"
        for service in Service.objects.only(
            "id", "status", "sablier_enabled",
            "public_domain", "custom_domains", "public_domain_hidden",
            "wildcard_url_enabled", "is_public",
            "wildcard_redirect_custom_domain",
        ).all():
            if not getattr(service, "wildcard_redirect_custom_domain", False):
                continue
            if (
                getattr(service, "is_public", True) is False
                or getattr(service, "public_domain_hidden", False)
                or getattr(service, "wildcard_url_enabled", True) is False
                or (
                    str(getattr(service, "status", "") or "").upper() == "STOPPED"
                    and bool(getattr(service, "sablier_enabled", False))
                )
            ):
                continue

            service_verified = verified_by_service.get(service.id, set())
            if not service_verified:
                continue

            target = ""
            for item in (service.custom_domains or []):
                value = item.strip() if isinstance(item, str) else ""
                if not value:
                    continue
                try:
                    # Normalize (lowercase, no scheme/path/port): the map
                    # value is interpolated into `redir https://{target}`,
                    # so a raw "https://X/" or "X:8443" would emit a
                    # broken redirect. First verified *valid* custom wins.
                    candidate = normalize_domain(value)
                except ValueError:
                    continue
                if candidate in service_verified:
                    target = candidate
                    break
            if not target:
                continue
            apex_target = _www_apex_redirect_target(target, service_verified)
            if apex_target:
                target = apex_target

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

    # Loop guard: if A redirects to B and B redirects back to A (mutual
    # first-customs), browsers spin until their hop limit. Drop the cycle
    # — both hosts keep proxying instead of redirect-looping.
    for _source in [k for k, v in redirects.items() if v in redirects]:
        logger.warning(
            "Dropping wildcard redirect %s -> %s (mutual redirect cycle)",
            _source, redirects[_source],
        )
        del redirects[_source]

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
            for r_index, (source_host, segment, target, target_path) in enumerate(rules):
                if source_host and source_host != public_domain:
                    continue
                matcher = f"@wpr_{svc_alias}_{r_index}"
                rest_re = f"wpr_{svc_alias}_{r_index}"
                if target_path and target_path != "/":
                    tp = target_path.rstrip('/')
                else:
                    tp = ""
                if segment == "/":
                    # Whole-host root redirect scoped to this service's
                    # hostname: exact `/` only, sub-paths keep proxying.
                    lines.append(f"    {matcher} {{")
                    lines.append(f"        host {public_domain}")
                    lines.append("        path /")
                    lines.append("    }")
                    lines.append(f"    handle {matcher} {{")
                    lines.append(f"        redir https://{target}{tp or '/'} 301")
                    lines.append("    }")
                    continue
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


def _get_node_subdomain_blocks(
    wildcard_domain: str,
    cloudflare_token: str,
    crowdsec_enabled: bool = False,
) -> list[str]:
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
                "    import secure_headers",
                *(["    crowdsec"] if crowdsec_enabled else []),
                "    log {",
                "        output file /var/log/caddy/access.log {",
                "            mode 0644",
                "        }",
                "    }",
                f"    reverse_proxy http://{mesh_ip}:80 {{",
                "        header_up Host {host}",
                "        header_up Cf-Connecting-Ip {client_ip}",
                "    }",
                "}",
            ])
            blocks.append("\n".join(block))

        return blocks
    except Exception as exc:
        logger.warning("Could not load node subdomains for Caddy: %s", exc)
        return []


def _node_secure_header_lines() -> list[str]:
    """Inline security headers for node site blocks (no snippet/import).

    The node Caddyfile ships without snippet definitions: older node
    binaries rejected the file at the snippet's `header` line while the
    identical construct adapts on newer builds, and the emergency path
    must parse on whatever binary the node runs. Same headers as
    SECURE_HEADERS_SNIPPET, written inline.
    """
    return [
        "    header {",
        '        Strict-Transport-Security "max-age=31536000; includeSubDomains; preload"',
        "        X-Content-Type-Options nosniff",
        "        X-Frame-Options DENY",
        '        Content-Security-Policy "frame-ancestors \'none\'"',
        "        Referrer-Policy strict-origin-when-cross-origin",
        '        Permissions-Policy "camera=(), microphone=(), geolocation()"',
        "        -Server",
        "    }",
    ]


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

    cloudflare_token = sanitize_cloudflare_token(getattr(config, "cloudflare_api_token", ""))
    node_number = getattr(node, "node_number", None) or 1
    parts = base_domain.split(".")
    if len(parts) > 2:
        node_domain = f"grid{node_number}.{'.'.join(parts[1:])}"
    else:
        node_domain = f"grid{node_number}.{base_domain}"

    sections: list[str] = []

    # Node edge WAF: same Coraza CRS as master (the node image ships the
    # module). Global order + snippet must precede all sites — this file
    # otherwise carries no global block. :80 blocks stay clean: that path
    # arrives from master already inspected.
    _node_edge_proxied = bool(getattr(config, "edge_proxy_records", False))
    _node_ask_secret = str(os.environ.get("CADDY_ASK_SECRET", "") or getattr(config, "caddy_ask_secret", "") or "").strip()
    if _node_ask_secret:
        os.environ.setdefault("CADDY_ASK_SECRET", _node_ask_secret)
    sections.append(
        "{\n"
        "    on_demand_tls {\n"
        "        ask http://host.docker.internal:8971/ask?secret={env.CADDY_ASK_SECRET}\n"
        "    }\n"
        "    order coraza_waf first\n"
        + "\n".join(
            _build_servers_trusted_proxies_lines(
                include_cloudflare=_node_edge_proxied,
                config=config,
            )
        )
        + "\n}"
    )
    sections.append(CORAZA_SNIPPET)

    # NOTE: no SECURE_HEADERS_SNIPPET here on purpose — node binaries
    # must parse this file cold (see _node_secure_header_lines); every
    # site block below carries the headers inline instead.

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
        *_node_secure_header_lines(),
        "    import coraza_waf",
        *(["    request_header -CF-Connecting-IP"] if not _node_edge_proxied else []),
        "    log {",
        "        output file /var/log/caddy/access.log {",
        "            mode 0644",
        "        }",
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

    # Service blocks (primary-owned node services + remote overflow replicas).
    from django.db.models import Q as _Q
    replica_service_ids: set = set()
    running_replica_service_ids: set = set()
    try:
        from apps.autoscaler.models.replica import ServiceReplica
        if _table_exists(ServiceReplica._meta.db_table):
            for sid, st in ServiceReplica.objects.filter(
                node=node,
                status__in=["RUNNING", "SPAWNING"],
            ).values_list("service_id", "status"):
                replica_service_ids.add(sid)
                if st == "RUNNING":
                    running_replica_service_ids.add(sid)
    except Exception:
        pass

    services = (
        Service.objects.filter(
            _Q(node_url_enabled=True) | _Q(id__in=replica_service_ids),
        )
        .distinct()
        .order_by("id")
    )

    for service in services:
        svr = _resolve_effective_server(service)
        is_owner = bool(svr and svr.id == node.id)
        is_replica_host = service.id in replica_service_ids
        if not is_owner and not is_replica_host:
            continue

        slug = (service.slug or service.name.lower().replace(" ", "-")).strip()
        flat_domain = node_service_domain(slug, node_number, base_domain)
        nested_domain = node_service_domain_nested(slug, node_number, base_domain)
        legacy_flat = node_service_domain_legacy_flat(slug, node_number, base_domain)
        legacy_nested = node_service_domain_legacy_nested(slug, node_number, base_domain)
        # App container name follows the service name (same convention
        # as _local_upstream_for_service on master). When Sablier, Canary, or
        # horizontal/overflow replicas are present on this node, proxy through
        # node Traefik (traefik:80) so Traefik pools primary + replicas and
        # applies WRR/Sablier middlewares instead of pinning one container.
        container = (service.name or "").strip() or slug
        port = getattr(service, "internal_port", 8000) or 8000
        sab = bool(getattr(service, "sablier_enabled", False))
        is_canary = str(getattr(service, "deploy_strategy", "") or "").upper() == "CANARY"
        has_node_replicas = (
            service.id in running_replica_service_ids
            or not is_owner
        )
        use_traefik = sab or is_canary or has_node_replicas
        sablier_manually_stopped = (
            sab and str(getattr(service, "status", "") or "").upper() == "STOPPED"
        )
        edge_jwt_required = bool(getattr(service, "edge_jwt_required", False))
        proxy_target = "traefik:80" if use_traefik else f"{container}:{port}"
        if sab:
            proxy_retry_lines = ["        lb_try_duration 60s", "        lb_try_interval 1s"]
        elif has_node_replicas:
            proxy_retry_lines = ["        lb_try_duration 5s", "        lb_try_interval 250ms"]
        else:
            proxy_retry_lines = []

        def _node_proxy_lines(target: str, retry: list[str]) -> list[str]:
            if sablier_manually_stopped:
                return ['    respond "Service is stopped" 503']
            auth_lines = (
                [
                    "    request_header -X-User-Id",
                    "    request_header -X-Edge-Scope",
                    *EDGE_JWT_FORWARD_AUTH_LINES,
                ]
                if edge_jwt_required
                else []
            )
            return [
                *auth_lines,
                f"    reverse_proxy {target} {{",
                *retry,
                "        header_up Host {host}",
                "    }",
            ]

        # Every public hostname that can arrive here: the flat node
        # name (+ legacy grid alias), the service's public/custom wildcard
        # domains (master proxies preview hostnames like x-e8ac13... to
        # the node), and the nested direct name is covered by its own
        # block below.
        # Unmatched :80 on Caddy 308-redirects by default — a missing
        # hostname here becomes an infinite master↔node redirect loop
        # (2026-10-01: preview domain looped 6+ hops).
        suffix = f".{base_domain}"
        extra_hosts: list[str] = []
        if not getattr(service, "public_domain_hidden", False):
            for raw_value in [service.public_domain, *(service.custom_domains or [])]:
                raw = str(raw_value or "").strip()
                if not raw:
                    continue
                try:
                    value = normalize_domain(raw)
                except ValueError:
                    continue
                if value and value.endswith(suffix) and value not in (flat_domain, nested_domain, legacy_flat, legacy_nested):
                    extra_hosts.append(value)
            # Verified EXTERNAL customs (e.g. ignite.trulay.co on a
            # trulay.site platform): DNS may point them straight at the
            # node IP for emergency/direct serving. Without a site block
            # the handshake has no SNI match and dies (fail-closed TLS).
            # Only verified names — never raw user input.
            try:
                from apps.domains.models import DomainStatus as _DS
                _verified: list[str] = []
                for d in service.domain_instances.filter(
                    status__in=[_DS.ACTIVE, _DS.DNS_VERIFIED, _DS.SSL_PROVISIONING],
                    verified=True,
                ):
                    try:
                        _verified.append(normalize_domain(str(d.domain_name or "").strip()))
                    except ValueError:
                        continue
                for value in dict.fromkeys(_verified):
                    if value and not value.endswith(suffix) and value not in (
                        flat_domain, nested_domain, legacy_flat, legacy_nested,
                    ):
                        extra_hosts.append(value)
            except Exception as exc:
                logger.debug("Node extra customs skipped for %s: %s", service.id, exc)
        http_hosts = [flat_domain, legacy_flat, *extra_hosts]

        tls_lines = [
            "    tls {",
        ]
        if cloudflare_token:
            tls_lines.append(f"        dns cloudflare {cloudflare_token}")
        else:
            tls_lines.append("        on_demand")

        # Flat/public hostnames over plain HTTP, ONE BLOCK PER HOST
        # with an EXPLICIT :80 port (2026-10-01: scheme-only
        # `http://a, b` lines split per-address defaults unpredictably
        # and bare `http://a` blocks still migrated to the TLS server
        # in this Caddy build). An explicit :80 address deterministically
        # opts out of automatic HTTPS: served plainly with NO https
        # redirect — this is the master→node proxy path.
        # Sablier services ride node Traefik here too: a direct
        # container:port upstream 503s while asleep and never wakes
        # (the sablier plugin only runs in the Traefik chain).
        _plain_target = proxy_target if use_traefik else f"{container}:{port}"
        _plain_retry = proxy_retry_lines if use_traefik else []
        for http_host in http_hosts:
            flat_block = [
                f"{http_host}:80 {{",
                "    request_header -X-SMSLY-Remote-Sync",
                "    request_header -X-User-Id",
                "    request_header -X-Edge-Scope",
                "    log {",
                "        output file /var/log/caddy/access.log {",
                "            mode 0644",
                "        }",
                "    }",
                *_node_proxy_lines(_plain_target, _plain_retry),
                "    encode gzip",
                "}",
            ]
            sections.append("\n".join(flat_block))

        # Nested hostname over HTTPS with on-demand certs: direct grey
        # access to the node without going through Cloudflare/master.
        # Deep names stay DNS-only on free plans; orange only with paid
        # Advanced Certificates (edge_proxy_wildcards).
        # Per-service WAF (direct grey access bypasses master inspection).
        if bool(getattr(service, "waf_opt_out", False)):
            _waf_lines = [
                "    request_header -X-SMSLY-Remote-Sync",
                "    request_header -X-User-Id",
                "    request_header -X-Edge-Scope",
            ]
        else:
            _waf_lines = [
                "    route {",
                "        request_header -X-SMSLY-Remote-Sync",
                "        request_header -X-User-Id",
                "        request_header -X-Edge-Scope",
                "        import coraza_waf",
                "    }",
            ]
        for _nested in dict.fromkeys([nested_domain, legacy_nested]):
            nested_block = [
                f"{_nested} {{",
                *tls_lines,
                "    }",
                *_node_secure_header_lines(),
                *_waf_lines,
                "    log {",
                "        output file /var/log/caddy/access.log {",
                "            mode 0644",
                "        }",
                "    }",
                *_node_proxy_lines(proxy_target, proxy_retry_lines),
                "    encode gzip",
                "}",
            ]
            sections.append("\n".join(nested_block))

        # Flat hostname over HTTPS: exact DNS records point flat names
        # at the NODE IP (orange), so Cloudflare terminates browsers
        # and talks TLS to the node directly (525 if this block is
        # missing — 2026-10-01). Same app, DNS-challenge certs.
        flat_tls_block = [
            f"{flat_domain} {{",
            *tls_lines,
            "    }",
            *_node_secure_header_lines(),
            *_waf_lines,
            "    log {",
            "        output file /var/log/caddy/access.log {",
            "            mode 0644",
            "        }",
            "    }",
            *_node_proxy_lines(proxy_target, proxy_retry_lines),
            "    encode gzip",
            "}",
        ]
        sections.append("\n".join(flat_tls_block))

        # Master-proxied hostnames (public + customs) over HTTPS too:
        # master now dials the node over TLS with SNI, so the node must
        # terminate these names itself. DNS-01 works regardless of where
        # the A records point. Without these blocks an https-forcing app
        # behind an http upstream 301-loops forever (marketer 2026-10-03).
        # EXTERNAL customs (verified, non-base-domain) get BARE on_demand:
        # the Cloudflare token only covers our own zones, so DNS-01 dies
        # with "expected 1 zone, got 0" for foreign zones — HTTP-01 /
        # TLS-ALPN (automatic, no dns line) is the only issuance path
        # that works when DNS points straight at the node. Shape matches
        # tls_lines exactly (open block, NO closing brace — the shared
        # literal below closes it).
        _bare_tls_lines = [
            "    tls {",
            "        on_demand",
        ]
        for _extra in dict.fromkeys(extra_hosts):
            _is_external = not _extra.endswith(suffix)
            _block_tls = _bare_tls_lines if _is_external else tls_lines
            extra_tls_block = [
                f"{_extra} {{",
                *_block_tls,
                "    }",
                *_node_secure_header_lines(),
                *_waf_lines,
                "    log {",
                "        output file /var/log/caddy/access.log {",
                "            mode 0644",
                "        }",
                "    }",
                *_node_proxy_lines(proxy_target, proxy_retry_lines),
                "    encode gzip",
                "}",
            ]
            sections.append("\n".join(extra_tls_block))

    header = "# Node Caddyfile - Auto-generated by Grid Controller\n"
    header += f"# Node: {node.name} (grid{node_number}.{base_domain})\n"
    header += "# Do not edit manually; changes will be overwritten.\n\n"
    rendered_node = header + "\n\n".join(sections) + "\n"
    if not _node_edge_proxied:
        rendered_node = _strip_cf_connecting_ip_in_proxies(rendered_node)
    return rendered_node


def generate_caddyfile(config) -> str:
    if is_agent_lite():
        logger.debug("Agent-lite mode: skipping generate_caddyfile()")
        return ""

    sections = []
    domain = ""
    cloudflare_token = sanitize_cloudflare_token(getattr(config, "cloudflare_api_token", ""))

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

    _crowdsec_key = _resolve_crowdsec_bouncer_key(config)
    _crowdsec_active = bool(getattr(config, "enable_crowdsec_waf", False) and _crowdsec_key)

    _ask_secret = str(os.environ.get("CADDY_ASK_SECRET", "") or "").strip()
    if not _ask_secret:
        try:
            from apps.deployments.models.core import PlatformConfig
            _cfg = PlatformConfig.load()
            _ask_secret = str(getattr(_cfg, 'caddy_ask_secret', '') or '').strip()
        except Exception as exc:
            logger.debug("Failed to load PlatformConfig for Caddy ask secret: %s", exc)
    if not _ask_secret:
        _ask_secret = str(getattr(settings, "CADDY_ASK_SECRET", "") or "").strip()
    # Caddy runs containerized; the sidecar is a host systemd unit, so the
    # ask URL must use host.docker.internal (extra_hosts: host-gateway on
    # the caddy service). 127.0.0.1 here would be the Caddy container
    # itself and TLS issuance would break for every domain.
    _ask_url = "http://host.docker.internal:8971/ask"
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
        "    # Coraza WAF is a custom HTTP handler with no built-in order —",
        "    # without this, any site importing coraza_waf fails to adapt",
        "    # ('not an ordered HTTP handler'). Upstream requires first:",
        "    # on routes without a proxy, relative order left WAF after",
        "    # the responder and rules never executed.",
        "    order coraza_waf first",
    ]
    if _crowdsec_active:
        global_lines.extend([
            "    order crowdsec before coraza_waf",
            "    crowdsec {",
            "        api_url http://smsly-crowdsec:8080",
            f"        api_key {_crowdsec_key}",
            "        ticker_interval 1s",
            "    }",
        ])

    # Edge Shield & Mesh Proxy Trust: always trust internal/private/WG and
    # known ManagedServer IPs so multi-hop master->node traffic never logs the
    # master IP as client_ip; add Cloudflare ranges when edge_proxy_records is on.
    try:
        _edge_proxied = bool(getattr(config, "edge_proxy_records", False))
    except Exception:
        _edge_proxied = False
    global_lines.extend(
        _build_servers_trusted_proxies_lines(
            include_cloudflare=bool(_edge_proxied and domain),
            config=config,
        )
    )

    global_lines.append("}")
    sections.append("\n".join(global_lines))

    sections.append(SECURE_HEADERS_SNIPPET)
    sections.append(CORAZA_SNIPPET)

    # Single source of truth for "the *.domain site exists". The
    # wildcard site block, the per-host block skip, known-hosts, and the
    # redirect map must ALL agree — Caddy prefers exact-host sites, so
    # any host with both an explicit block and a wildcard redirect
    # handle never redirects (silent dead 301, 2026-09-12). The
    # Cloudflare token is deliberately NOT part of this condition: it
    # only controls the dns-challenge TLS lines, while serving works
    # via on-demand either way.
    _wildcard_site_on = bool(use_ssl and domain and config.wildcard_subdomains)

    if use_ssl and domain:
        platform_block = [f"{domain} {{"]
        platform_block.extend(
            [
                "    encode gzip",
                "    import secure_headers",
                *(["    crowdsec"] if _crowdsec_active else []),
                *(["    request_header -CF-Connecting-IP"] if not _edge_proxied else []),
                "    log {",
                "        output file /var/log/caddy/access.log {",
                "            mode 0644",
                "        }",
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

        if _wildcard_site_on:
            wildcard_known_hosts = _get_wildcard_known_hosts(domain)
            wildcard_remote_hosts = _get_wildcard_remote_host_map(domain)
            remote_edge_jwt_hosts = _get_wildcard_edge_jwt_hosts(domain)
            wildcard_lines = [
                f"*.{domain} {{",
            ]
            if cloudflare_token:
                wildcard_lines.append("    tls {")
                wildcard_lines.append(f"        dns cloudflare {cloudflare_token}")
                wildcard_lines.append("    }")

            wildcard_lines.extend([
                "    log {",
                "        output file /var/log/caddy/access.log {",
                "            mode 0644",
                "        }",
                "    }",
            ])
            wildcard_lines.append("    import secure_headers")
            if _crowdsec_active:
                wildcard_lines.append("    crowdsec")
            wildcard_lines.append("    request_header -X-SMSLY-Remote-Sync")
            wildcard_lines.append("    request_header -X-User-Id")
            wildcard_lines.append("    request_header -X-Edge-Scope")
            if not _edge_proxied:
                wildcard_lines.append("    request_header -CF-Connecting-IP")

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
                    edge_jwt_required = bool(getattr(p_service, "edge_jwt_required", False))
                    use_edge_traefik = (
                        bool(getattr(p_service, "sablier_enabled", False))
                        or str(getattr(p_service, "deploy_strategy", "") or "").upper() == "CANARY"
                        or edge_jwt_required
                    )
                    if use_edge_traefik:
                        proxy_lines = [
                            f"        reverse_proxy {_service_proxy_upstream()} {{",
                            "            header_up Host {host}",
                            "        }",
                        ]
                    else:
                        proxy_lines = [
                            f"        reverse_proxy {p_service.name}:{port}",
                        ]
                    preview_auth_lines = [
                        "        request_header -X-User-Id",
                        "        request_header -X-Edge-Scope",
                    ]
                    if edge_jwt_required:
                        preview_auth_lines.extend(
                            "    " + line for line in EDGE_JWT_FORWARD_AUTH_LINES
                        )
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
                                    *preview_auth_lines,
                                    *proxy_lines,
                                    "    }",
                                ]
                            )
                            continue
                    wildcard_lines.extend(
                        [
                            f"    {matcher} host {p_domain}",
                            f"    handle {matcher} {{",
                            *preview_auth_lines,
                            *proxy_lines,
                            "    }",
                        ]
                    )

            # Internal-only wildcard domains: public visitors get the 503
            # fallback page, internal/mesh clients are routed normally.
            # Defined BEFORE redirects/disabled/known handlers.
            internal_only_upstreams = _get_wildcard_internal_only_upstreams(domain)
            if internal_only_upstreams:
                internal_edge_jwt_hosts = _get_wildcard_edge_jwt_hosts(domain)
                wildcard_lines.extend(
                    [
                        f"    @internal_only_hosts host {' '.join(sorted(h for hosts in internal_only_upstreams.values() for h in hosts))}",
                        "    @internal_src client_ip 10.0.0.0/8 172.16.0.0/12 192.168.0.0/16 100.64.0.0/10",
                        "    handle @internal_only_hosts {",
                        "        request_header -X-User-Id",
                        "        request_header -X-Edge-Scope",
                        "        handle @internal_src {",
                    ]
                )

                def _emit_internal_upstream(group_matcher: str, group_hosts: list[str], require_jwt: bool, upstream_url: str, upstream_has_fallbacks: bool) -> None:
                    if not group_hosts:
                        return
                    auth_lines = (
                        [
                            "            request_header -X-User-Id",
                            "            request_header -X-Edge-Scope",
                            *["        " + line for line in EDGE_JWT_FORWARD_AUTH_LINES],
                        ]
                        if require_jwt
                        else [
                            "            request_header -X-User-Id",
                            "            request_header -X-Edge-Scope",
                        ]
                    )
                    wildcard_lines.extend(
                        [
                            f"        {group_matcher} host {' '.join(group_hosts)}",
                            f"        handle {group_matcher} {{",
                            *auth_lines,
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

                for index, (upstream_url, hosts) in enumerate(internal_only_upstreams.items()):
                    matcher = f"@internal_upstream_{index}"
                    upstream_has_fallbacks = len(str(upstream_url).split()) > 1
                    jwt_hosts = [h for h in hosts if h in internal_edge_jwt_hosts]
                    open_hosts = [h for h in hosts if h not in internal_edge_jwt_hosts]
                    _emit_internal_upstream(f"{matcher}_jwt", jwt_hosts, True, upstream_url, upstream_has_fallbacks)
                    _emit_internal_upstream(matcher, open_hosts, False, upstream_url, upstream_has_fallbacks)
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

            def _emit_remote_group(group_matcher: str, group_hosts: list[str], require_jwt: bool) -> None:
                if not group_hosts:
                    return
                auth_lines = (
                    [
                        "        request_header -X-User-Id",
                        "        request_header -X-Edge-Scope",
                        *["    " + line for line in EDGE_JWT_FORWARD_AUTH_LINES],
                    ]
                    if require_jwt
                    else []
                )
                wildcard_lines.extend(
                    [
                        f"    {group_matcher} host {' '.join(group_hosts)}",
                        f"    handle {group_matcher} {{",
                        *auth_lines,
                        f"        reverse_proxy {upstream_url} {{",
                    ]
                )
                if upstream_has_fallbacks:
                    wildcard_lines.extend(
                        [
                            "            lb_policy round_robin",
                            "            fail_duration 15s",
                            "            max_fails 2",
                            "            lb_try_duration 5s",
                            "            lb_try_interval 250ms",
                        ]
                    )
                wildcard_lines.extend(
                    [
                        "            header_up Host {host}",
                        "            header_up Cf-Connecting-Ip {client_ip}",
                        "        }",
                        "    }",
                    ]
                )

            for index, (upstream_url, hosts) in enumerate(sorted(wildcard_remote_hosts.items())):
                if not hosts:
                    continue
                matcher = f"@remote_hosts_{index}"
                upstream_has_fallbacks = len(str(upstream_url).split()) > 1
                https_upstream = str(upstream_url).strip().startswith("https://")
                if https_upstream:
                    # Per-host handles so each gets its own tls_server_name:
                    # a shared block cannot present the right SNI for every
                    # name behind it, and the node would fail the handshake.
                    for hi, host in enumerate(sorted(hosts)):
                        hmatch = f"{matcher}_{hi}"
                        auth_lines = (
                            [
                                "        request_header -X-User-Id",
                                "        request_header -X-Edge-Scope",
                                *["    " + line for line in EDGE_JWT_FORWARD_AUTH_LINES],
                            ]
                            if host in remote_edge_jwt_hosts
                            else []
                        )
                        wildcard_lines.extend(
                            [
                                f"    {hmatch} host {host}",
                                f"    handle {hmatch} {{",
                                *auth_lines,
                                f"        reverse_proxy {upstream_url} {{",
                            ]
                        )
                        if upstream_has_fallbacks:
                            wildcard_lines.extend(
                                [
                                    "            lb_policy round_robin",
                                    "            fail_duration 15s",
                                    "            max_fails 2",
                                    "            lb_try_duration 5s",
                                    "            lb_try_interval 250ms",
                                ]
                            )
                        wildcard_lines.extend(
                            [
                                "            header_up Host {host}",
                                "            header_up Cf-Connecting-Ip {client_ip}",
                                "            transport http {",
                                "                tls",
                                f"                tls_server_name {host}",
                                "            }",
                                "        }",
                                "    }",
                            ]
                        )
                    continue
                jwt_hosts = [h for h in hosts if h in remote_edge_jwt_hosts]
                open_hosts = [h for h in hosts if h not in remote_edge_jwt_hosts]
                _emit_remote_group(f"{matcher}_jwt", jwt_hosts, require_jwt=True)
                _emit_remote_group(matcher, open_hosts, require_jwt=False)

            if wildcard_known_hosts:
                # WAF opt-outs bypass Coraza: their handle comes FIRST
                # (first match wins) and proxies straight through.
                try:
                    _waf_optouts = [
                        h for h in _get_wildcard_waf_optout_hosts(domain)
                        if h in set(wildcard_known_hosts)
                    ]
                except Exception:
                    _waf_optouts = []
                if _waf_optouts:
                    wildcard_lines.extend(
                        [
                            f"    @waf_optout host {' '.join(_waf_optouts)}",
                            "    handle @waf_optout {",
                            "        request_header -X-SMSLY-Remote-Sync",
                            "        request_header -X-User-Id",
                            "        request_header -X-Edge-Scope",
                            f"        reverse_proxy {_service_proxy_upstream()}",
                            "    }",
                        ]
                    )
                wildcard_lines.extend(
                    [
                        f"    @known_hosts host {' '.join(wildcard_known_hosts)}",
                        "    handle @known_hosts {",
                        "        route {",
                        "            request_header -X-SMSLY-Remote-Sync",
                        "            request_header -X-User-Id",
                        "            request_header -X-Edge-Scope",
                        "            import coraza_waf",
                        f"            reverse_proxy {_service_proxy_upstream()}",
                        "        }",
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
        node_blocks = _get_node_subdomain_blocks(domain, cloudflare_token, crowdsec_enabled=_crowdsec_active)
        for block in node_blocks:
            sections.append(block)

    if domain and not use_ssl:
        _http_cs = "\n    crowdsec" if _crowdsec_active else ""
        _http_cf = "\n    request_header -CF-Connecting-IP" if not _edge_proxied else ""
        sections.append(
            f"""http://{domain} {{
    encode gzip
    import secure_headers{_http_cs}{_http_cf}
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
                        if _f == _key_path:
                            if (_mode & 0o007) != 0 or _mode < 0o600:
                                os.chmod(_f, 0o640)
                        elif _mode < 0o644:
                            os.chmod(_f, 0o644)
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
        _cs_443 = "    crowdsec\n" if _crowdsec_active else ""
        _cf_443 = "    request_header -CF-Connecting-IP\n" if not _edge_proxied else ""
        sections.append(
            f""":443 {{
{_tls_line}
    import secure_headers
{_cs_443}{_cf_443}    handle /api/* {{
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
    handle /ui {{
        redir / 301
    }}
    handle /ui/* {{
        redir / 301
    }}
    handle {{
        reverse_proxy frontend:3000
    }}
    encode gzip
}}"""
        )

    if use_ssl and domain:
        _cs_80_ssl = "    crowdsec\n" if _crowdsec_active else ""
        _cf_80_ssl = "    request_header -CF-Connecting-IP\n" if not _edge_proxied else ""
        sections.append(
            ":80 {\n"
            + _cs_80_ssl
            + _cf_80_ssl
            + """    @redirectable {
        not header_regexp host ^([0-9]{1,3}[.]){3}[0-9]{1,3}(:[0-9]+)?$
        not host localhost
        not host 127.0.0.1
        not host *.local
        not path /.well-known/smsly-verify/*
        header_regexp host .+
    }
    redir @redirectable https://{host}{uri} 308

    # HTTP-proof domain verification (orange-compatible): per-domain
    # challenge tokens for ANY host. Must live in THIS :80 site (Caddy
    # rejects duplicate :80 sites as ambiguous) and be excluded from the
    # https redirect above (directive order runs redir before handles).
    # Tokens are unguessable per-domain secrets; unknown tokens 404. Host
    # is rewritten to localhost so Django ALLOWED_HOSTS (strict by
    # design) never blocks a pending custom hostname.
    handle /.well-known/smsly-verify/* {
        reverse_proxy backend:8000 {
            header_up Host localhost
        }
    }

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
        _cs_80_plain = "    crowdsec\n" if _crowdsec_active else ""
        _cf_80_plain = "    request_header -CF-Connecting-IP\n" if not _edge_proxied else ""
        sections.append(
            ":80 {\n"
            + _cs_80_plain
            + _cf_80_plain
            + """    # HTTP-proof domain verification challenge (see the use_ssl branch
    # above). First so the catch-all below never swallows it.
    handle /.well-known/smsly-verify/* {
        reverse_proxy backend:8000 {
            header_up Host localhost
        }
    }
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

    # Which wildcard-covered hosts skip their explicit site block:
    # exactly the hosts served by the wildcard site above
    # (_wildcard_site_on). See the flag definition for why these must
    # never disagree (silent dead 301s, 2026-09-12).
    wildcard_base = domain if _wildcard_site_on else ""
    service_blocks = _get_service_domain_blocks(
        wildcard_domain=wildcard_base,
        crowdsec_enabled=_crowdsec_active,
    )
    sections.extend(service_blocks)

    header = "# Grid Caddyfile - Auto-generated by Settings UI\n"
    header += "# Do not edit manually; changes will be overwritten.\n\n"
    rendered = _apply_backend_retry_budget(header + "\n\n".join(sections) + "\n")
    if not _edge_proxied:
        rendered = _strip_cf_connecting_ip_in_proxies(rendered)
    return rendered
