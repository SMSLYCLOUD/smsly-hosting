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
        return HttpResponse("Token expired", status=403)

    host = payload_data["host"]
    name = payload_data["name"]
    server_id = payload_data.get("server_id", "")
    is_lite_agent = payload_data.get("is_lite_agent", False)
    is_media_node = payload_data.get("is_media_node", False)
    is_primary = payload_data.get("is_primary", False)
    allow_user_workloads = payload_data.get("allow_user_workloads", True)
    node_components = payload_data.get("node_components", {})
    node_number = payload_data.get("node_number", "")
    node_domain = payload_data.get("node_domain", "")
    wg_address = payload_data.get("wg_address", "")
    wg_private_key = payload_data.get("wg_private_key", "")
    wg_public_key = payload_data.get("wg_public_key", "")
    gateway_secret = payload_data.get("gateway_secret", "")
    master_wg_pubkey = payload_data.get("master_wg_pubkey", "")
    master_wg_endpoint = payload_data.get("master_wg_endpoint", "")

    master_url = os.environ.get("PUBLIC_URL", "https://grid.smsly.cloud")
    master_ip = os.environ.get("PUBLIC_IP", "")

    if not gateway_secret:
        gateway_secret = os.environ.get("GATEWAY_SECRET", "")

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
    if wg_private_key:
        env_lines.append(_env_line("WG_PRIVATE_KEY", wg_private_key))
    if master_wg_pubkey:
        env_lines.append(_env_line("MASTER_WG_PUBKEY", master_wg_pubkey))
    if master_wg_endpoint:
        env_lines.append(_env_line("MASTER_WG_ENDPOINT", master_wg_endpoint))
    if install_mode == "node" and compose_file:
        env_lines.append(_env_line("COMPOSE_FILE", compose_file))
    if is_media_node:
        try:
            from apps.deployments.models.platform import PlatformConfig
            pc = PlatformConfig.objects.first()
            if pc:
                if pc.media_repo_url:
                    env_lines.append(_env_line("MEDIA_REPO_URL", pc.media_repo_url))
                if pc.media_repo_token:
                    env_lines.append(_env_line("MEDIA_REPO_TOKEN", str(pc.media_repo_token)))
        except Exception:
            pass
    if is_lite_agent:
        master_mesh_ip = os.environ.get("MASTER_MESH_IP", "")
        if not master_mesh_ip:
            try:
                from apps.deployments.services.provisioner.helpers.server_config import _get_master_mesh_ip
                master_mesh_ip = _get_master_mesh_ip()
            except Exception:
                master_mesh_ip = master_ip
        if master_mesh_ip:
            env_lines.append(_env_line("MASTER_MESH_IP", master_mesh_ip))

    env_block = "\n".join(env_lines)

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
    path('', include('django_prometheus.urls')),

    # ─── Frontend compatibility aliases (MUST come before the broad
    # ``path('api/v1/', include('apps.deployments.urls'))`` so the
    # /api/v1/<anything>/ matchers in deployments.urls do not eat
    # these paths first and 404 the frontend.
    path('api/v1/dashboard/overview/',
         include('apps.core.urls.dashboard_alias')),
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
    path('api/v1/', include('apps.deployments.urls')),
    path('api/v1/cloud/', include('apps.cloud.urls')),
    path('api/v1/teams/', include('apps.teams.urls')),
    path('api/v1/autoscaler/', include('apps.autoscaler.urls')),
    path('api/v1/notifications/', include('apps.notifications.urls')),
    path('api/v1/core/', include('apps.core.urls')),
    path('api/v1/domains/', include('apps.domains.urls')),
    path('api/v1/organizations/', include('apps.organizations.urls')),

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
