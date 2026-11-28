"""Urls module."""
from apps.addons.views_crud import toggle_bucket_public_api
from django.conf import settings
from django.contrib import admin
from django.http import HttpResponse
from django.urls import include, path, re_path
from drf_spectacular.views import (
    SpectacularAPIView,
    SpectacularRedocView,
    SpectacularSwaggerView,
)

from config.health import health_check, health_check_verbose, liveness_check, readiness_check
from apps.core.views import ContactView
from apps.core.views.observability import grafana_wake_fallback_view
from apps.domains.views.challenge import domain_challenge


def bootstrap_view(request, token):
    """Serve the install script with baked-in env vars for self-provisioning.

    Called by the target server: curl -fsSL <master>/api/v1/servers/bootstrap/<token>/
    Returns a bash script that installs Grid and calls back to the master.
    """
    import base64
    import hashlib
    import hmac as hmac_mod
    import json as json_mod
    import os
    import time

    try:
        payload_b64, sig = token.split(".", 1)
    except ValueError:
        return HttpResponse("Invalid token format", status=400)

    secret = settings.SECRET_KEY.encode()
    expected_sig = hmac_mod.new(secret, payload_b64.encode(), hashlib.sha256).hexdigest()
    if not hmac_mod.compare_digest(expected_sig, sig):
        return HttpResponse("Invalid token signature", status=403)

    try:
        payload_data = json_mod.loads(base64.urlsafe_b64decode(payload_b64))
    except Exception:
        return HttpResponse("Corrupt token payload", status=400)

    if payload_data.get("exp", 0) < time.time():
        return HttpResponse(
            "Token expired — re-issue via POST /api/v1/servers/<id>/reissue-token/",
            status=403,
        )

    # Bound reuse: bearer-usable for curl retry, but capped (10 fetches)
    # so a leaked URL cannot bootstrap an unlimited fleet within 1h.
    try:
        from django.core.cache import cache as _cache
        _use_key = f"bootstrap_used:{payload_data.get('nonce', '')}"
        try:
            _uses = _cache.get(_use_key) or 0
        except Exception:
            _uses = 0
        if int(_uses) >= 10:
            return HttpResponse("Token fetch limit reached", status=429)
        try:
            try:
                _cache.incr(_use_key)
            except ValueError:
                _cache.set(_use_key, 1, timeout=3600)
        except Exception:
            pass
    except Exception:
        pass

    # Bind token to a live server row: deleted or DONE nodes must not
    # re-bootstrap from a leaked URL (re-install would wipe a healthy node).
    try:
        from apps.deployments.models.core import ManagedServer as _MS
        _srv = _MS.objects.filter(id=payload_data.get("server_id", "")).first()
        if _srv is None:
            return HttpResponse("Unknown server (deleted?)", status=404)
        if getattr(_srv, "provision_status", "") == _MS.ProvisionStatus.DONE:
            return HttpResponse("Server already provisioned", status=409)
    except HttpResponse:
        raise
    except Exception:
        pass

    host = payload_data["host"]
    name = payload_data["name"]
    server_id = payload_data.get("server_id", "")
    is_lite_agent = payload_data.get("is_lite_agent", False)
    is_media_node = payload_data.get("is_media_node", False)
    is_primary = payload_data.get("is_primary", False)
    allow_user_workloads = payload_data.get("allow_user_workloads", True)
    # Component defaults mirror the dashboard DEFAULT_NODE_COMPONENTS
    # and provision_server.py: absent keys mean "default", never
    # "everything off". An explicit false always wins (opt-out).
    node_components = {
        "observability": True,
        "security": True,
        "crowdsec": False,
        "falco": False,
        "spire": False,
        "log_shipping": True,
        **(payload_data.get("node_components", {}) or {}),
    }
    node_number = payload_data.get("node_number", "")
    node_domain = payload_data.get("node_domain", "")
    wg_address = payload_data.get("wg_address", "")
    wg_private_key = payload_data.get("wg_private_key", "")
    wg_public_key = payload_data.get("wg_public_key", "")
    gateway_secret = payload_data.get("gateway_secret", "")
    master_wg_pubkey = payload_data.get("master_wg_pubkey", "")
    master_wg_endpoint = payload_data.get("master_wg_endpoint", "")

    master_url = os.environ.get("PUBLIC_URL", "")
    if not master_url:
        try:
            from apps.deployments.models.core import PlatformConfig as _PC
            _dom = (_PC.get_config_value("domain") or "").strip()
            if _dom:
                master_url = f"https://{_dom}"
        except Exception:
            pass
    master_url = master_url or "https://grid.smsly.cloud"
    master_ip = os.environ.get("PUBLIC_IP", "")

    if not gateway_secret:
        return HttpResponse("Token missing gateway_secret (re-issue provision token)", status=400)

    # Resolve install mode — never stack multiple --mode flags
    if is_media_node:
        install_mode = "media-node"
    elif is_lite_agent:
        install_mode = "agent-lite"
    else:
        install_mode = "node"

    # Resolve platform config for compose file (full nodes only)
    compose_file = ""
    if install_mode == "node":
        compose_file = "infrastructure/docker/docker-compose.node.yml"

    def _comp_flag(key):
        return "1" if node_components.get(key) else "0"

    def _env_line(key, value):
        return f"{key}={value}"

    env_lines = [
        _env_line("NON_INTERACTIVE", "1"),
        _env_line("SKIP_REBOOT", "1"),
        _env_line("SMSLY_STRICT_VERIFY", "1"),
        _env_line("MASTER_IP", master_ip),
        _env_line("MASTER_URL", master_url),
        _env_line("GATEWAY_SECRET", gateway_secret),
        _env_line("NODE_NAME", name),
        _env_line("NODE_HOST", host),
        _env_line("NODE_OBSERVABILITY", _comp_flag("observability")),
        _env_line("NODE_SECURITY", _comp_flag("security")),
        _env_line("NODE_CROWDSEC", _comp_flag("crowdsec")),
        _env_line("NODE_FALCO", _comp_flag("falco")),
        _env_line("NODE_SPIRE", _comp_flag("spire")),
        _env_line("NODE_LOG_SHIPPING", _comp_flag("log_shipping")),
        _env_line("SMSLY_NODE_HOST", host),
    ]
    if server_id:
        env_lines.append(_env_line("SERVER_ID", server_id))
    if node_number:
        env_lines.append(_env_line("NODE_NUMBER", str(node_number)))
    if node_domain:
        env_lines.append(_env_line("NODE_DOMAIN", node_domain))
    if wg_address:
        env_lines.append(_env_line("WG_ADDRESS", wg_address))
    if install_mode == "node" and wg_address:
        # Node CoreDNS replica binds the mesh IP only (never public):
        # compose fails fast with a clear message when this is unset.
        # NODE_MESH_IP must match: the installer configures wg0 with it,
        # and a placeholder/default (.2) here while coredns binds the real
        # address fails with "cannot assign requested address".
        env_lines.append(_env_line("NODE_MESH_BIND_IP", wg_address))
        env_lines.append(_env_line("NODE_MESH_IP", wg_address))
    if wg_private_key:
        env_lines.append(_env_line("WG_PRIVATE_KEY", wg_private_key))
    if master_wg_pubkey:
        env_lines.append(_env_line("MASTER_WG_PUBKEY", master_wg_pubkey))
    if master_wg_endpoint:
        env_lines.append(_env_line("MASTER_WG_ENDPOINT", master_wg_endpoint))
    if install_mode == "node" and compose_file:
        env_lines.append(_env_line("COMPOSE_FILE", compose_file))
    if is_media_node:
        # Media code for private repos must come via the SSH path
        # (App-token tarball → file:// staging, no cred on node).
        # Baking MEDIA_REPO_TOKEN here would leak it into the node env,
        # so bootstrap carries the URL only; private repos fail fast
        # with a clear message telling the operator to use SSH provisioning.
        try:
            from apps.deployments.models.platform import PlatformConfig
            pc = PlatformConfig.objects.first()
            if pc:
                if pc.media_repo_url:
                    env_lines.append(_env_line("MEDIA_REPO_URL", pc.media_repo_url))
        except Exception:
            pass
    _needs_mesh_ip = is_lite_agent or install_mode == "node"
    if _needs_mesh_ip:
        master_mesh_ip = os.environ.get("MASTER_MESH_IP", "")
        if not master_mesh_ip:
            try:
                from apps.deployments.services.provisioner.helpers.server_config import _get_master_mesh_ip
                master_mesh_ip = _get_master_mesh_ip()
            except Exception:
                master_mesh_ip = ""
        # Never fall back to the public IP for the mesh address: internal
        # ports (5432 etc.) are firewalled there, and lib/agent-lite.sh
        # refuses to build DATABASE_URL from it. Omit instead so the
        # agent fails fast with a clear message.
        _mesh_ok = False
        if master_mesh_ip:
            try:
                import ipaddress as _ipmod
                _parsed = _ipmod.ip_address(master_mesh_ip.strip())
                _mesh_ok = (
                    _parsed.is_private or _parsed.is_link_local
                ) and not _parsed.is_loopback
            except ValueError:
                _mesh_ok = False
        if _mesh_ok:
            env_lines.append(_env_line("MASTER_MESH_IP", master_mesh_ip))
            # Full nodes need it too: install_registry_docker_certs
            # installs the registry CA for ${MASTER_MESH_IP}:5000, without
            # which node pulls from the mesh registry die on x509
            # unknown-authority (2026-09-30: every pull to a fresh node
            # failed this way).
        # Bootstrap must carry everything lib/agent-lite.sh hard-fails
        # without (MASTER_DB_PASSWORD etc.) plus the node identity/queue.
        # build_agent_lite_install_env() provisions the dedicated lite DB
        # user — the single owner of these values. Lite-only: full nodes
        # run their own DB and must not receive these credentials.
        if is_lite_agent and server_id:
            try:
                from apps.deployments.models.servers import ManagedServer
                from apps.deployments.services.provisioner.helpers.server_config import (
                    build_agent_lite_install_env,
                )
                _srv = ManagedServer.objects.select_related("owner").filter(id=server_id).first()
                if _srv is not None:
                    _owner = getattr(_srv, "owner", None)
                    if not _owner or not (getattr(_owner, "is_staff", False) or getattr(_owner, "is_superuser", False)):
                        return HttpResponse(
                            "Lite-agent bootstrap requires a staff or superuser server owner",
                            status=403,
                        )
                    _lite_env, _ = build_agent_lite_install_env(_srv)
                    for _k in (
                        "MASTER_DB_USER", "MASTER_DB_PASSWORD",
                        "MASTER_MQ_PASSWORD", "MASTER_REDIS_PASSWORD",
                        "MASTER_FIELD_ENCRYPTION_KEY",
                        "SMSLY_NODE_ID", "SMSLY_NODE_QUEUE",
                    ):
                        _v = str(_lite_env.get(_k) or "").strip()
                        if _v:
                            env_lines.append(_env_line(_k, _v))
            except Exception:
                pass

    env_block = "\n".join(env_lines)

    # Node CoreDNS replica seed: render the CURRENT mesh zone so the
    # installer can write zone files before compose up (works with the
    # already-minted token — wg_address is in the payload). Refresh
    # afterwards comes from the 5-min cron (same payload mechanism).
    # Empty on render failure: installer warns, cron backfills.
    import base64 as _b64mod
    zone_hosts_b64 = ""
    zone_corefile_b64 = ""
    if install_mode == "node":
        try:
            from apps.deployments.services.mesh_dns import (
                build_corefile as _build_corefile,
                build_mesh_hosts as _build_mesh_hosts,
            )
            _hosts_content, _record_count = _build_mesh_hosts()
            if _record_count > 0:
                zone_hosts_b64 = _b64mod.b64encode(
                    _hosts_content.encode()
                ).decode()
                zone_corefile_b64 = _b64mod.b64encode(
                    _build_corefile().encode()
                ).decode()
        except Exception:
            pass

    # ── Bash-injection guard (defense in depth) ──────────────────────────
    # The payload is HMAC-signed so only this master can mint it, but
    # every value below is interpolated into a bash heredoc and
    # double-quoted echo lines on the TARGET host. A value carrying
    # newlines, the heredoc terminator, or command-substitution syntax
    # would execute there if a poisoned token were ever minted (master
    # compromise, an operator tricked into provisioning a hostile name).
    # Reject at render time so a bad token cannot produce a bad script.
    def _assert_bash_safe(key, value):
        text = str(value or "")
        for bad in ("\n", "\r", "`", "$(", "${", '"', "'", "ENVEOF"):
            if bad in text:
                return HttpResponse(
                    f"Token field '{key}' contains characters not allowed "
                    "in bootstrap payloads.",
                    status=400,
                )
        return None

    for _key, _value in (
        ("host", host), ("name", name), ("server_id", server_id),
        ("node_number", node_number), ("node_domain", node_domain),
        ("master_url", master_url), ("master_ip", master_ip),
    ):
        _resp = _assert_bash_safe(_key, _value)
        if _resp is not None:
            return _resp
    for _line in env_lines:
        _resp = _assert_bash_safe("env", _line)
        if _resp is not None:
            return _resp
    for _b64_key, _b64_val in (
        ("zone_hosts", zone_hosts_b64), ("zone_corefile", zone_corefile_b64),
    ):
        _resp = _assert_bash_safe(_b64_key, _b64_val)
        if _resp is not None:
            return _resp

    script = f"""#!/bin/bash
set -euo pipefail

# Grid Auto-Provisioning Script
# Generated by {master_url}
# Target: {name} ({host})
# Server: {server_id}
# Mode: {install_mode}
# Node: {node_domain} (grid{node_number})

echo "=== Grid Auto-Provisioning ==="
echo "Server: {name}"
echo "Host:   {host}"
echo "Mode:   {install_mode}"
echo "Node:   {node_domain}"
echo ""

if ! id -u grid &>/dev/null; then
    useradd -r -m -d /opt/smsly-hosting -s /bin/bash grid
    echo "Created grid user"
fi

if [ -d "/opt/smsly-hosting/.git" ]; then
    cd /opt/smsly-hosting
    git pull --ff-only || true
else
    rm -rf /opt/smsly-hosting
    git clone https://github.com/SMSLYCLOUD/smsly-hosting.git /opt/smsly-hosting
    cd /opt/smsly-hosting
fi

cat > .env << 'ENVEOF'
{env_block}
ENVEOF

chmod 600 .env 2>/dev/null || true
# Node CoreDNS replica seed (full nodes only): zone files must exist
# BEFORE compose up mounts ./coredns-config (a missing bind source
# would poison the mount). Refresh via cron; CoreDNS auto-reloads.
if [ -n "{zone_hosts_b64}" ]; then
  mkdir -p infrastructure/docker/coredns-config
  echo "{zone_hosts_b64}" | base64 -d > infrastructure/docker/coredns-config/mesh.hosts
  echo "{zone_corefile_b64}" | base64 -d > infrastructure/docker/coredns-config/Corefile
  chmod 644 infrastructure/docker/coredns-config/mesh.hosts infrastructure/docker/coredns-config/Corefile
  echo "Mesh DNS zone seeded."
else
  echo "WARNING: mesh zone seed unavailable — first cron sync will backfill."
fi
if ! grep -q 'smsly-mesh-dns-sync' /etc/crontab 2>/dev/null; then
  echo '*/5 * * * * root [ -x /opt/smsly-hosting/scripts/sync-node-mesh-dns.sh ] && /opt/smsly-hosting/scripts/sync-node-mesh-dns.sh >>/var/log/smsly-mesh-dns.log 2>&1 || true # smsly-mesh-dns-sync' >> /etc/crontab 2>/dev/null || echo "WARNING: could not install mesh-dns cron."
fi
echo ""
echo "=== Starting installer in background... ==="
echo "The installation will continue even if your SSH connection drops."
echo "Tail logs with: tail -f /opt/smsly-hosting/install.log"
nohup bash install.sh --mode={install_mode} </dev/null >/opt/smsly-hosting/install.log 2>&1 &
"""
    return HttpResponse(script, content_type="text/x-shellscript")

urlpatterns = [
    # ─── CRITICAL: Direct Addon Actions (Greedy Regex bypass for router shadowing) ───
    re_path(r'^api/v1/addons/(?P<pk>[^/.]+)/toggle_bucket_public/?$', toggle_bucket_public_api, name='addon-toggle-bucket-public-root'),
    # Bootstrap endpoint for self-provisioning (must be before router)
    re_path(r'^api/v1/servers/bootstrap/(?P<token>[^/]+)/?$', bootstrap_view, name='server-bootstrap'),
    # path('admin/', admin.site.urls), # Moved to conditional block below


    # Health probes
    path('health', health_check, name='health-check'),
    path('health/live', liveness_check, name='health-liveness'),
    path('health/ready', readiness_check, name='health-readiness'),
    path('health/verbose', health_check_verbose, name='health-verbose'),
    path('healthz', health_check, name='healthz-check'),

    # HTTP-proof custom-domain verification (orange-compatible): serves the
    # row's per-domain token for ANY host. Caddy's :80 catch-all proxies
    # here with Host rewritten to localhost (ALLOWED_HOSTS stays strict).
    # Unknown tokens 404; no auth by design (same exposure as ACME HTTP-01).
    # Accept both slashless and trailing-slash paths without 301 APPEND_SLASH.
    re_path(r'^\.well-known/smsly-verify/(?P<token>[^/]+)/?$', domain_challenge,
            name='domain-challenge'),
    path('', include('django_prometheus.urls')),

    # ─── Frontend compatibility aliases (MUST come before the broad
    # ``path('api/v1/', include('apps.deployments.urls'))`` so the
    # /api/v1/<anything>/ matchers in deployments.urls do not eat
    # these paths first and 404 the frontend.
    path('api/v1/dashboard/overview/',
         include('apps.core.urls.dashboard_alias')),
    path('api/v1/contact/', ContactView.as_view(), name='public-contact'),
    path('api/v1/system/health/',
         include('apps.core.urls.system_health_alias')),
    path('api/v1/system/resources/',
         include('apps.core.urls.system_resources_alias')),
    path('api/v1/preferences/',
         include('apps.notifications.urls.preferences_alias')),
    path('api/v1/ecosystem/bulk-update-environment/',
         include('apps.cloud.urls.ecosystem_bulk_env_alias')),
    path('api/v1/ecosystem/cached-scan/',
         include('apps.cloud.urls.ecosystem_cached_scan_alias')),
    path('api/v1/resource-alerts/',
         include('apps.notifications.urls.resource_alerts_alias')),
    path('api/v1/api-keys/',
         include('apps.core.urls.api_keys_alias')),
    path('api/v1/admin/users/',
         include('apps.core.urls.admin_users_alias')),
    path('api/v1/observability/',
         include('apps.core.urls.observability_alias')),
    # OAuth callback aliases — the frontend uses
    # /api/v1/accounts/<provider>/login/ but allauth is mounted
    # at /accounts/<provider>/login/. The alias re-exports
    # allauth.urls under the /api/v1/accounts/ prefix.
    path('api/v1/accounts/',
         include('apps.deployments.urls.accounts_alias')),

    # API — mTLS must come before deployments.urls to avoid route shadowing
    path('api/v1/', include('apps.mtls.urls')),
    path('api/v1/', include('apps.mcp.urls')),
    path('api/v1/', include('apps.deployments.urls')),
    path('api/v1/cloud/', include('apps.cloud.urls')),
    path('api/v1/crowdsec/', include('apps.crowdsec.urls')),
    path('api/v1/teams/', include('apps.teams.urls')),
    path('api/v1/autoscaler/', include('apps.autoscaler.urls')),
    path('api/v1/notifications/', include('apps.notifications.urls')),
    path('api/v1/core/', include('apps.core.urls')),
    path('api/v1/domains/', include('apps.domains.urls')),
    path('api/v1/organizations/', include('apps.organizations.urls')),
    path('api/v1/security/', include('apps.security.urls')),

    # Auth — the three brute-force-sensitive endpoints
    # (login, password reset, registration) are mounted from
    # a throttled URL conf that subclasses dj_rest_auth's
    # views and applies the platform's narrow auth throttles.
    # The remaining dj_rest_auth URLs (logout, user details,
    # password change) are mounted via the existing include
    # below and keep their default global throttle. The
    # throttled URLs MUST come first so they win the URL
    # resolution race.
    path('api/v1/auth/', include('apps.core.urls.throttled_auth')),
    path('api/v1/auth/', include('dj_rest_auth.urls')),
    path('api/v1/auth/2fa/', include('apps.deployments.urls.2fa')),
    path(
        'api/v1/auth/registration/',
        include('dj_rest_auth.registration.urls')),
    path('accounts/', include('allauth.urls')),
    re_path(r'^grafana/(?P<subpath>.*)$', grafana_wake_fallback_view, name='grafana-wake-fallback'),
]

# ─── Conditional App Routes (Agent Mode Resiliency) ───────────────────────
if 'django.contrib.admin' in settings.INSTALLED_APPS:
    # Defensively check if admin is already in urlpatterns to avoid W005 warning
    if not any(getattr(p, 'app_name', None) == 'admin' for p in urlpatterns):
        urlpatterns.insert(1, path('admin/', admin.site.urls))



if 'apps.billing' in settings.INSTALLED_APPS:
    urlpatterns.append(path('api/v1/billing/', include('apps.billing.urls')))

if 'apps.licensing' in settings.INSTALLED_APPS:
    urlpatterns.append(path('api/v1/licensing/', include('apps.licensing.urls')))

if 'apps.addons' in settings.INSTALLED_APPS:
    urlpatterns.append(path('api/v1/addons/', include('apps.addons.urls')))

if 'apps.intelligence' in settings.INSTALLED_APPS:
    urlpatterns.append(path('api/v1/ai/', include('apps.intelligence.urls')))
    urlpatterns.append(path('api/v1/', include('apps.intelligence.urls.openai')))

if 'apps.media' in settings.INSTALLED_APPS:
    urlpatterns.append(path('api/v1/media/', include('apps.media.urls')))

# ─── Server Identity Attestation (Zero-Trust challenge-response) ──────────
try:
    from apps.deployments.views.attestation import (
        attestation_challenge,
        attestation_verify,
    )
    urlpatterns += [
        path('api/v1/internal/attest/challenge/', attestation_challenge, name='attest-challenge'),
        path('api/v1/internal/attest/verify/', attestation_verify, name='attest-verify'),
    ]
except ImportError:
    pass

# ─── Tunnel API (function-based views, not DRF router) ────────────────────
try:
    if getattr(settings, 'ENABLE_LEGACY_TUNNEL_API', False):
        from apps.deployments.services.tunnels.api import get_urlpatterns as tunnel_urls
        urlpatterns += [path('api/v1/legacy/', include(tunnel_urls()))]
except ImportError:
    pass  # tunnels module not installed

# ─── OpenAPI Schema & Docs ────────────────────────────────────────────
# SECURITY: Only expose schema/docs in DEBUG mode to prevent API surface
# enumeration in production. Set DEBUG=True temporarily to access docs.
if settings.DEBUG:
    urlpatterns += [
        path('api/docs/schema/', SpectacularAPIView.as_view(), name='schema'),
        path('api/docs/swagger/', SpectacularSwaggerView.as_view(url_name='schema'), name='swagger-ui'),
        path('api/docs/redoc/', SpectacularRedocView.as_view(url_name='schema'), name='redoc'),
    ]
