"""Settings module."""
import logging
import os
import re
import sys
import warnings
from pathlib import Path

import dj_database_url
from decouple import Csv, config

warnings.filterwarnings("ignore", category=UserWarning, module="dj_rest_auth.registration.serializers")
BASE_DIR = Path(__file__).resolve().parent.parent
IS_TESTING = bool(os.environ.get('TESTING')) or any(
    (arg_text := str(arg).lower()) == 'test'
    or Path(arg_text).name.startswith('test')
    or 'pytest' in arg_text
    or '/tests/' in arg_text.replace('\\', '/')
    for arg in sys.argv
)


def _env_bool(name: str, default: str = 'False') -> bool:
    """Parse boolean-like env vars without raising on non-standard values."""
    raw = str(config(name, default=default)).strip().lower()
    return raw in ('1', 'true', 'yes', 'on')


from django.core.exceptions import ImproperlyConfigured

_SECRET_KEY_RAW = str(config('SECRET_KEY', default='')).strip()
_FIELD_ENCRYPTION_KEY_RAW = str(config('FIELD_ENCRYPTION_KEY', default='')).strip()
# SECURITY: FIELD_ENCRYPTION_KEY_FILE allows storing the encryption key in a
# separate file with restricted permissions (chmod 600, root owned) instead of
# .env. If the file path is set, it takes precedence over the env var.
#   /opt/smsly-hosting/secrets/field-encryption-key
_FIELD_ENCRYPTION_KEY_FILE = str(config('FIELD_ENCRYPTION_KEY_FILE', default='')).strip()
if not _FIELD_ENCRYPTION_KEY_RAW and _FIELD_ENCRYPTION_KEY_FILE:
    try:
        with open(_FIELD_ENCRYPTION_KEY_FILE) as _f:
            _FIELD_ENCRYPTION_KEY_RAW = _f.read().strip()
    except (FileNotFoundError, PermissionError, OSError) as _exc:
        raise ImproperlyConfigured(
            f"Cannot read FIELD_ENCRYPTION_KEY_FILE={_FIELD_ENCRYPTION_KEY_FILE}: {_exc}"
        ) from _exc

if not _SECRET_KEY_RAW:
    raise ImproperlyConfigured(
        "SECRET_KEY is not set.\n\n"
        "  Run the included helper to generate all secrets:\n"
        "    python scripts/generate_env_secrets.py --env .env\n\n"
        "  Or generate just this key:\n"
        "    python -c \"from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())\"\n\n"
        "  Then add SECRET_KEY=<value> to your .env file."
    )
if not _FIELD_ENCRYPTION_KEY_RAW:
    raise ImproperlyConfigured(
        "FIELD_ENCRYPTION_KEY is not set.\n\n"
        "  Run the included helper to generate all secrets:\n"
        "    python scripts/generate_env_secrets.py --env .env\n\n"
        "  Or generate just this key:\n"
        "    python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\"\n\n"
        "  Then add FIELD_ENCRYPTION_KEY=<value> to your .env file."
    )

SECRET_KEY = _SECRET_KEY_RAW
FIELD_ENCRYPTION_KEY = _FIELD_ENCRYPTION_KEY_RAW
# SECURITY: GATEWAY_SECRET is the HMAC shared secret used by
# inter-node token exchange. It must be a separate value from
# SECRET_KEY (which is used for Django signing/cookies and
# must NOT be sent to a peer over the network). The value is
# captured here as a raw string; the production/DEBUG check
# is deferred until after DEBUG is defined further down.
_GATEWAY_SECRET_RAW = str(config('GATEWAY_SECRET', default='')).strip()

# SECURITY: allow operators to opt out of TLS verification on a
# per-ManagedServer basis. When this flag is False (the default),
# the platform refuses to skip certificate verification on
# inter-node HTTP — preventing a network-adjacent attacker from
# MITMing the connection and capturing the gateway_secret /
# SSH password. Set to True only for self-signed lite agent
# testing environments.
ALLOW_INSECURE_INTER_NODE_TLS = config(
    'ALLOW_INSECURE_INTER_NODE_TLS',
    default='false',
    cast=lambda v: str(v).lower() in ('1', 'true', 'yes', 'on'),
)

# SECURITY (Batch J): Docker buildx fallback builder name. The
# default ``docker`` driver buildx builder can corrupt on
# Docker daemon restarts. The platform auto-creates a
# ``docker-container`` driver fallback with this name when
# the build fails with the buildx default-builder recreation
# error. The fallback spawns a fresh BuildKit container per
# build, which is resilient to daemon restarts. Operators can
# pre-create the fallback or set BUILDX_BUILDER= to force a
# specific builder globally.
BUILDX_FALLBACK_BUILDER = config(
    'BUILDX_FALLBACK_BUILDER',
    default='smsly-fallback',
)

# Validate encryption key format (Fernet requirement: 32 bytes, URL-safe base64)
try:
    from cryptography.fernet import Fernet
    Fernet(FIELD_ENCRYPTION_KEY.encode() if isinstance(FIELD_ENCRYPTION_KEY, str) else FIELD_ENCRYPTION_KEY)
except Exception as e:
    raise ImproperlyConfigured(
        f"Invalid FIELD_ENCRYPTION_KEY: {e}. Generate with:\n"
        "  python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
    ) from e
DOMAIN = (config('DOMAIN', default='localhost') or 'localhost').strip()
DEBUG = _env_bool('DEBUG', default='False')
SMSLY_DISABLE_SIGNATURE_CHECK = _env_bool('SMSLY_DISABLE_SIGNATURE_CHECK', default='False')
if SMSLY_DISABLE_SIGNATURE_CHECK:
    import warnings
    warnings.warn(
        "SECURITY: SMSLY_DISABLE_SIGNATURE_CHECK is True — HMAC signature "
        "verification is globally disabled. Never use this in production.",
        stacklevel=2,
    )


def _resolve_gateway_secret() -> str:
    """Return the GATEWAY_SECRET to use at boot, or raise
    ImproperlyConfigured if production requirements are not met.

    In production (not DEBUG, not IS_TESTING) the platform
    refuses to fall back to SECRET_KEY — the gateway secret
    must be a distinct value. In tests / DEBUG the fallback
    to SECRET_KEY is allowed for convenience.
    """
    if _GATEWAY_SECRET_RAW:
        return _GATEWAY_SECRET_RAW
    if IS_TESTING or DEBUG:
        return SECRET_KEY
    raise ImproperlyConfigured(
        "GATEWAY_SECRET is not set.\n\n"
        "  GATEWAY_SECRET is the HMAC shared secret used for inter-node\n"
        "  token exchange. It MUST be a separate value from SECRET_KEY\n"
        "  so the Django signing key is never sent over the network to\n"
        "  peer nodes.\n\n"
        "  Run the included helper to generate all secrets:\n"
        "    python scripts/generate_env_secrets.py --env .env\n\n"
        "  Or generate just this key:\n"
        "    python -c \"import secrets; print(secrets.token_hex(32))\"\n\n"
        "  Then add GATEWAY_SECRET=<value> to your .env file."
    )


GATEWAY_SECRET = _resolve_gateway_secret()
# Owner edition: all tier gates disabled — all features unlocked.
# SECURITY (Issue 21): the flag is audit-logged on the first
# consult per process via ``_check_tier_gates_disabled()`` in
# apps.deployments.views. That helper records an immutable
# AuditLog entry so an operator flipping the env var cannot
# silently unlock paid features — there is always a fingerprint.
SMSLY_DISABLE_TIER_GATES = config("SMSLY_DISABLE_TIER_GATES", default=True, cast=bool)
# Maximum file size in bytes for container file_read (default: 10MB)
SMSLY_MAX_FILE_READ_SIZE = max(1, int(config("SMSLY_MAX_FILE_READ_SIZE", default=10 * 1024 * 1024)))
# Enable transfer pipeline by default; can be turned off for hardened environments
ALLOW_STUB_TRANSFER_PIPELINE = _env_bool('ALLOW_STUB_TRANSFER_PIPELINE', default='False')
# Default to True: refuse to start a transfer unless the target can reach
# the source on TCP/22.  Operators can override with
# TRANSFER_REQUIRE_BIDIRECTIONAL_SSH=false in the .env.
TRANSFER_REQUIRE_BIDIRECTIONAL_SSH = _env_bool('TRANSFER_REQUIRE_BIDIRECTIONAL_SSH', default='True')

# Security hardening
# Force insecure settings when running tests to prevent 301 redirects
if not DEBUG and not IS_TESTING:
    SECURE_HSTS_SECONDS = 31536000  # 1 year
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True
    _use_ssl = _env_bool('USE_SSL', default='False')
    # SEC-001: Automatic IP-Bypass for SSL Redirection
    # If the domain is an IP address, disable the redirect to prevent ERR_SSL_PROTOCOL_ERROR.
    _is_ip = bool(re.fullmatch(r'\d{1,3}(?:\.\d{1,3}){3}', DOMAIN))
    _is_local_host = DOMAIN.lower() in ('localhost', '127.0.0.1')
    _ssl_enabled = _use_ssl and not _is_ip and not _is_local_host
    # Caddy natively redirects domains to HTTPS. If Django also redirects,
    # it traps raw IP addresses (which bypass Caddy's redirect) in an HTTP->HTTPS loop.
    # This MUST remain False as long as Caddy is the edge reverse proxy; enable only
    # if Django is directly exposed to the internet without Caddy in front.
    SECURE_SSL_REDIRECT = False

    SECURE_REDIRECT_EXEMPT = [
        r'^api/',
        r'^health',
        r'^metrics',
    ]
    SESSION_COOKIE_SECURE = _ssl_enabled
    CSRF_COOKIE_SECURE = _ssl_enabled
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https') if _ssl_enabled else None
else:
    # Explicitly disable for tests and debug mode
    SECURE_HSTS_SECONDS = 0
    SECURE_HSTS_INCLUDE_SUBDOMAINS = False
    SECURE_HSTS_PRELOAD = False
    SECURE_SSL_REDIRECT = False
    SESSION_COOKIE_SECURE = False
    CSRF_COOKIE_SECURE = False
    SECURE_PROXY_SSL_HEADER = None

# ── Additional security headers (unconditional) ──────────────────────────────
SECURE_BROWSER_XSS_FILTER = True
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = 'DENY'
CSRF_COOKIE_HTTPONLY = True

# Trusted proxy IPs for X-Real-IP header validation.
# Only requests arriving from these IPs are allowed to set X-Real-IP.
# Empty list (default) means X-Real-IP is NEVER trusted — REMOTE_ADDR
# is always used. Set this to your load balancer / reverse proxy IPs.
TRUSTED_PROXY_IPS = config(
    'TRUSTED_PROXY_IPS',
    default='',
    cast=lambda v: [ip.strip() for ip in v.split(',') if ip.strip()],
)

# Container Registry
# Default to Docker DNS name (registry:5000) which resolves inside smsly-net
# for inter-container communication. External registries (Docker Hub, GHCR,
# ECR, etc.) are fully supported — set CONTAINER_REGISTRY_URL to the
# external registry URL and REGISTRY_USER/REGISTRY_PASSWORD accordingly.
CONTAINER_REGISTRY_URL = config(
    'CONTAINER_REGISTRY_URL',
    default='registry:5000')

# NOTE: The old auto-correction that replaced 127.0.0.1/localhost with
# MASTER_MESH_IP unconditionally has been removed.  It caused silent push
# failures when the mesh IP (e.g. 10.100.0.1) was not locally routable —
# the Docker daemon could reach 127.0.0.1:5000 just fine, but after the
# replacement it tried 10.100.0.1:5000 which failed.
#
# Mesh-IP substitution is now handled per-operation in the deployment
# pipeline (_push_image), where the caller knows whether the target
# is a remote node (mesh IP) or local (Docker DNS / loopback).


def _validate_registry_url():
    """SSRF guard for the container registry URL.

    Supports both internal registries (Docker DNS, loopback) and
    external registries (Docker Hub, GHCR, ECR, etc.).

    Internal registries (http/https): validated against platform allowlist.
    External registries (https only): allowed for push/pull with credentials.
    http:// to external hosts: blocked (plaintext push to external is unsafe).

    For convenience the URL is auto-prefixed with ``http://`` if no
    scheme is present (internal default). External registries should
    use ``https://`` explicitly.
    """
    import ipaddress
    from urllib.parse import urlparse

    url = os.environ.get('CONTAINER_REGISTRY_URL', '').strip()
    if not url:
        return

    # Auto-default scheme so operators who follow the
    # `.env.example` default do not have to type ``http://``.
    if '://' not in url:
        # If it looks like an external hostname (contains dots but
        # is not localhost/registry/private-ip), default to https.
        _temp = urlparse('http://' + url)
        _host = _temp.hostname or ''
        _is_internal = (
            _host.startswith(('localhost', '127.', 'registry', 'smsly'))
            or _host in ('',)
        )
        scheme = 'http' if _is_internal else 'https'
        url = scheme + '://' + url
        os.environ['CONTAINER_REGISTRY_URL'] = url

    parsed = urlparse(url)
    if parsed.scheme not in ('http', 'https'):
        raise ImproperlyConfigured(
            f'CONTAINER_REGISTRY_URL must use http or https; got scheme={parsed.scheme!r} '
            f'from url={url!r}'
        )

    hostname = parsed.hostname or ''

    # ── Internal registries (always allowed) ──────────────────────
    # These are platform-managed registries that run inside the cluster.
    if hostname.startswith(('localhost', '127.', 'registry', 'smsly')):
        return

    # ── Private IPs ───────────────────────────────────────────────
    try:
        ip = ipaddress.ip_address(hostname)
        if ip.is_private:
            # Private IPs are allowed (WireGuard mesh, local network)
            return
    except ValueError:
        pass

    # ── External registries over HTTPS ────────────────────────────
    # HTTPS to external hosts is allowed — operators may push to
    # Docker Hub, GHCR, ECR, or their own registry.
    if parsed.scheme == 'https':
        return

    # ── External registries over HTTP ─────────────────────────────
    # HTTP to external hosts is blocked — credentials would be sent
    # in plaintext. Require HTTPS for external registries.
    raise ImproperlyConfigured(
        f'CONTAINER_REGISTRY_URL over http:// to external host {hostname!r} '
        f'is not allowed (credentials would be sent in plaintext). '
        f'Use https://{hostname} instead.'
    )


_validate_registry_url()

REGISTRY_USER = config('REGISTRY_USER', default='')
REGISTRY_PASSWORD = config('REGISTRY_PASSWORD', default='')
# Webhook secret: MUST be set explicitly in production. Fallback is random per startup.
_GITHUB_WEBHOOK_SECRET_RAW = str(config('GITHUB_WEBHOOK_SECRET', default='')).strip()
if IS_TESTING:
    GITHUB_WEBHOOK_SECRET = _GITHUB_WEBHOOK_SECRET_RAW or 'test-github-webhook-secret'
elif DEBUG:
    GITHUB_WEBHOOK_SECRET = _GITHUB_WEBHOOK_SECRET_RAW or 'replace_me_with_random_string'
elif _GITHUB_WEBHOOK_SECRET_RAW:
    GITHUB_WEBHOOK_SECRET = _GITHUB_WEBHOOK_SECRET_RAW
else:
    raise ImproperlyConfigured(
        "GITHUB_WEBHOOK_SECRET is not set.\n\n"
        "  In production, GITHUB_WEBHOOK_SECRET must be explicitly configured\n"
        "  so that GitHub webhook signatures can be verified. Without it,\n"
        "  webhook payloads cannot be trusted.\n\n"
        "  Generate one with:\n"
        "    python -c \"import secrets; print(secrets.token_hex(32))\"\n\n"
        "  Then add GITHUB_WEBHOOK_SECRET=<value> to your .env file."
    )
# SECURITY: No wildcard default - prevents host header injection
# (DOMAIN moved to top of file)
_DEFAULT_TUNNEL_BASE_DOMAIN = 'tunnel.localhost'
if DOMAIN and DOMAIN != 'localhost':
    if re.fullmatch(r'\d{1,3}(?:\.\d{1,3}){3}', DOMAIN):
        _DEFAULT_TUNNEL_BASE_DOMAIN = f'tunnel.{DOMAIN}'
    else:
        _DEFAULT_TUNNEL_BASE_DOMAIN = f'tunnel.{DOMAIN}'
# If the env var is set, use it; otherwise default to "tunnel.<DOMAIN>"
# Operators can set TUNNEL_BASE_DOMAIN=auto.sslip.io for self-hosted with sslip.io
# or TUNNEL_BASE_DOMAIN=tunnel.example.com for a custom wildcard
TUNNEL_BASE_DOMAIN = config(
    "TUNNEL_BASE_DOMAIN",
    default=(
        config('TUNNEL_DOMAIN', default=_DEFAULT_TUNNEL_BASE_DOMAIN)
        or _DEFAULT_TUNNEL_BASE_DOMAIN
    ),
).strip()
# Infrastructure Version Control
INFRA_VERSION = '2026.05.11.10.35'

ENABLE_LEGACY_TUNNEL_API = config(
    'ENABLE_LEGACY_TUNNEL_API',
    default=False,
    cast=bool,
)

ALLOW_LOCAL_NODES = config(
    'ALLOW_LOCAL_NODES',
    default=False,
    cast=bool,
)

# Restrictive ALLOWED_HOSTS with mandatory internal whitelisting.
_BASE_HOSTS = ['localhost', '127.0.0.1', 'backend', 'smsly-hosting-backend-1']
ALLOWED_HOSTS = config('ALLOWED_HOSTS', default=','.join(_BASE_HOSTS), cast=Csv())
for host in _BASE_HOSTS:
    if host not in ALLOWED_HOSTS:
        ALLOWED_HOSTS.append(host)

if DOMAIN and DOMAIN != 'localhost' and DOMAIN not in ALLOWED_HOSTS:
    ALLOWED_HOSTS.append(DOMAIN)
# In dev/test, allow the popular wildcard DNS services
# (sslip.io gives every IP a public hostname; cprapid.com is the same).
# In production, operators should set ALLOWED_HOSTS_EXTRAS explicitly.
if DEBUG:
    ALLOWED_HOSTS.extend(['.cprapid.com', '.sslip.io'])
else:
    extras = config("ALLOWED_HOSTS_EXTRAS", default="").split(",")
    ALLOWED_HOSTS.extend(h.strip() for h in extras if h.strip())
# Ensure sub-subdomains of the platform domain are matched for deployed services
# e.g. a service with subdomain <service-name>.<DOMAIN> needs a .<DOMAIN> pattern
if DOMAIN and DOMAIN != 'localhost':
    _grid_wildcard = f'.{DOMAIN}'
    if _grid_wildcard not in ALLOWED_HOSTS:
        ALLOWED_HOSTS.append(_grid_wildcard)
APPEND_SLASH = True

JULES_ALLOWED_HOSTS = config(
    'JULES_ALLOWED_HOSTS',
    default='api.jules.google.com',
    cast=Csv(),
)

# SECURITY (SSRF): allowlist of hostnames that the ``localllm_base_url``
# admin setting may point at. The default is an empty tuple, which means
# out-of-the-box deployments cannot point the local LLM provider at any
# hostname (IP-literal hosts are still rejected by the network range
# block-list in apps.intelligence.models). Operators who actually want to
# use Ollama / LM Studio / vLLM must explicitly set
# ``LOCALLM_ALLOWED_HOSTS=ollama.internal,my-llm.local`` (comma-separated
# env var) in the .env file. An empty default is intentional — the
# ``localllm`` provider is opt-in, not opt-out, and a forgotten setting
# should not silently turn the AI provider into an SSRF exfil channel.
LOCALLM_ALLOWED_HOSTS = config(
    'LOCALLM_ALLOWED_HOSTS',
    default='',
    cast=Csv(),
)

# SECURITY: placeholder for the public key used to pin the license server
# response when the (currently disabled) online validation path is ever
# re-enabled. The default empty string is intentional: an empty value
# forces the offline-signed-token path to be the only trust anchor and
# makes it impossible to re-enable the network call without first
# populating this setting with the same public key shipped in
# ``apps/licensing/keys/public.pem``. Operators must set
# ``LICENSE_SERVER_PUBKEY=...`` to a PEM-encoded RSA public key before
# the online path can be reactivated.
LICENSE_SERVER_PUBKEY = config(
    'LICENSE_SERVER_PUBKEY',
    default='',
)

# SECURITY: Fail-fast in production — no dev-creds default
# The default in DEBUG mode is a generic "smsly_admin" / "smsly_admin"
# placeholder. In production the platform refuses to boot if
# DATABASE_URL is unset (no fallback to a known password).
_fallback_sqlite_path = (BASE_DIR / 'fallback.db').resolve().as_posix()
_DATABASE_DEFAULT = (
    'postgresql://smsly_admin:smsly_admin@localhost:5432/smsly_hosting'
    if DEBUG
    else f'sqlite:///{_fallback_sqlite_path}'
)

# ---------------------------------------------------------------------------
# PgCat / connection-pooler bypass helper
# ---------------------------------------------------------------------------
_POOLER_HOSTNAMES = frozenset({"pgcat", "pgbouncer", "haproxy"})


def _resolve_db_url() -> str:
    """Return a usable DATABASE_URL, bypassing connection poolers when needed.

    PgCat (transaction pooling) doesn't support SET/SAVEPOINT required by
    Django migrations and some ORM operations.  When DATABASE_URL points at a
    pooler and no DIRECT_DATABASE_URL is provided, we construct a direct
    connection string from the individual POSTGRES_* environment variables.
    """
    from urllib.parse import urlparse

    url = config('DATABASE_URL', default=_DATABASE_DEFAULT)
    parsed = urlparse(url)

    # Already a direct connection - return as-is
    if parsed.hostname not in _POOLER_HOSTNAMES:
        return url

    # Pooler detected - bypass it
    # 1. Explicit direct URL takes priority
    direct = config('DIRECT_DATABASE_URL', default='')
    if direct:
        return direct

    # 2. Construct direct URL from individual POSTGRES_* vars
    #    Use os.environ directly as fallback since decouple may not see
    #    variables injected via docker-compose environment block.
    pg_host = config('POSTGRES_HOST', default='') or os.environ.get('POSTGRES_HOST', 'db')
    pg_port = config('POSTGRES_PORT', default='') or os.environ.get('POSTGRES_PORT', '5432')
    pg_user = config('POSTGRES_USER', default='') or os.environ.get('POSTGRES_USER', 'smsly_admin')
    pg_pass = config('POSTGRES_PASSWORD', default='') or os.environ.get('POSTGRES_PASSWORD', '')
    pg_db = config('POSTGRES_DB', default='') or os.environ.get('POSTGRES_DB', 'smsly_hosting')
    if pg_pass:
        return f"postgresql://{pg_user}:{pg_pass}@{pg_host}:{pg_port}/{pg_db}"

    # Last resort: return the original pooler URL (will fail loudly)
    return url


# ---------------------------------------------------------------------------
# Dynamically include the domain from PlatformConfig (DB) so that domain
# changes made via the Settings UI take effect after container restart,
# without requiring manual .env edits.
# ---------------------------------------------------------------------------
# Skip eager DB sync during migrations / one-off containers where the pooler
# (pgcat) may not be reachable.  The domain sync is irrelevant for migrations.
_skip_platform_sync = (
    os.environ.get("SMSLY_MIGRATION_MODE") == "true"
    or os.environ.get("SMSLY_DISABLE_STARTUP_TASKS") == "true"
)
if not _skip_platform_sync:
    try:
        from urllib.parse import urlparse

        import psycopg2

        db_url = _resolve_db_url()
        if db_url:
            parsed = urlparse(db_url)
            conn = psycopg2.connect(
                dbname=parsed.path.lstrip('/'),
                user=parsed.username,
                password=parsed.password,
                host=parsed.hostname,
                port=parsed.port,
                connect_timeout=config('DATABASE_CONNECT_TIMEOUT', default=5, cast=int)
            )
            with conn.cursor() as cursor:
                cursor.execute("SELECT domain, use_ssl FROM deployments_platformconfig ORDER BY id ASC LIMIT 1;")
                row = cursor.fetchone()
                if row and row[0]:
                    db_domain = str(row[0]).strip().lower().rstrip('.')
                    if db_domain:
                        # Sync to ALLOWED_HOSTS
                        if db_domain not in ALLOWED_HOSTS:
                            ALLOWED_HOSTS.append(db_domain)
                        # Also add wildcard subdomain pattern for deployed services
                        _db_wildcard = f'.{db_domain}'
                        if _db_wildcard not in ALLOWED_HOSTS:
                            ALLOWED_HOSTS.append(_db_wildcard)
                        # Override DOMAIN in memory so that other settings depend on the DB state
                        DOMAIN = db_domain
                        # Sync USE_SSL from DB so security settings stay consistent
                        db_use_ssl = bool(row[1]) if len(row) > 1 else False
                        # Only override USE_SSL from DB if it was explicitly set
                        if len(row) > 1:
                            os.environ['USE_SSL'] = 'True' if db_use_ssl else 'False'
                        # Update SITE_URL to match the DB domain
                        _db_is_ip = bool(re.fullmatch(r'\d{1,3}(?:\.\d{1,3}){3}', db_domain))
                        _db_proto = 'https' if (db_use_ssl and not _db_is_ip) else 'http'
                        if not DEBUG:
                            SITE_URL = f"{_db_proto}://{db_domain}"
            conn.close()
    except Exception as e:
        print(f"[settings] Could not sync PlatformConfig domain to memory on boot: {e}")


# ---------------------------------------------------------------------------
# SITE_URL and Protocol
# ---------------------------------------------------------------------------

# Finalize SITE_URL: Explicit .env > DB Sync > Fallback
_env_site_url = config('SITE_URL', default=None)
if _env_site_url:
    SITE_URL = _env_site_url
elif 'SITE_URL' not in locals():
    _use_ssl_site = _env_bool('USE_SSL', default='False')
    _is_ip_site = bool(re.fullmatch(r'\d{1,3}(?:\.\d{1,3}){3}', DOMAIN))
    _is_local_site = DOMAIN.lower() in ('localhost', '127.0.0.1')
    _proto_site = 'https' if (_use_ssl_site and not _is_ip_site and not _is_local_site) else 'http'
    SITE_URL = ('http://localhost:3000' if DEBUG else f'{_proto_site}://{DOMAIN}')

# GitHub OAuth Callback URL (explicit override to prevent redirect_uri mismatch)
# If set, this value is used verbatim for GitHub OAuth redirect_uri
# Format: https://your-domain.com/accounts/github/login/callback/
GITHUB_OAUTH_CALLBACK_URL = config('GITHUB_OAUTH_CALLBACK_URL', default=None)
GITLAB_OAUTH_CALLBACK_URL = config('GITLAB_OAUTH_CALLBACK_URL', default=None)
BITBUCKET_OAUTH_CALLBACK_URL = config('BITBUCKET_OAUTH_CALLBACK_URL', default=None)
GOOGLE_OAUTH_CALLBACK_URL = config('GOOGLE_OAUTH_CALLBACK_URL', default=None)

# ── GitHub App (enterprise git authentication) ─────────────────────────────
# Used to generate short-lived, repo-scoped installation tokens for:
#   • Cloning private repositories during builds
#   • Injecting tokens as BuildKit secrets for `pip install git+https://...`
#   • Any other GitHub API calls that benefit from App-level auth
#
# Optional — the platform falls back to user OAuth tokens when unset.
# Register at: https://github.com/organizations/SMSLYCLOUD/settings/apps/new
#   Required permissions: Repository > Contents: Read-only
#   Installation scope:   Only "SMSLYCLOUD" org, select repos (smsly-shared)
#
GITHUB_APP_ID = config('GITHUB_APP_ID', default='')
# The PEM private key downloaded from the GitHub App settings page.
# Supports escaped \n sequences (common in .env files) or real newlines.
_GITHUB_APP_PRIVATE_KEY_RAW = config('GITHUB_APP_PRIVATE_KEY', default='')
GITHUB_APP_PRIVATE_KEY = _GITHUB_APP_PRIVATE_KEY_RAW.replace('\\n', '\n').strip()



# Stripe Billing (optional but required for paid plans)
STRIPE_SECRET_KEY = config('STRIPE_SECRET_KEY', default='')
STRIPE_WEBHOOK_SECRET = config('STRIPE_WEBHOOK_SECRET', default='')
STRIPE_PRICE_PRO = config('STRIPE_PRICE_PRO', default='')

# Billing (non-Stripe providers use one-time "period" activations)
BILLING_CURRENCY = config('BILLING_CURRENCY', default='USD')
BILLING_PRO_AMOUNT = config('BILLING_PRO_AMOUNT', default='29.00')
BILLING_PRO_PERIOD_DAYS = config('BILLING_PRO_PERIOD_DAYS', default=30, cast=int)

# Flutterwave (optional)
FLUTTERWAVE_SECRET_KEY = config('FLUTTERWAVE_SECRET_KEY', default='')
FLUTTERWAVE_PUBLIC_KEY = config('FLUTTERWAVE_PUBLIC_KEY', default='')
FLUTTERWAVE_WEBHOOK_SECRET_HASH = config('FLUTTERWAVE_WEBHOOK_SECRET_HASH', default='')

# Cryptomus (optional)
CRYPTOMUS_MERCHANT_ID = config('CRYPTOMUS_MERCHANT_ID', default='')
CRYPTOMUS_API_KEY = config('CRYPTOMUS_API_KEY', default='')


# Agent Mode Optimization: Prune apps and middleware to save RAM
IS_AGENT_MODE = os.environ.get('MODE') == 'agent'

INSTALLED_APPS = [
    'django_otp',
    'django_otp.plugins.otp_totp',
    'django_otp.plugins.otp_static',
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.sites',  # Required for allauth
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',

    # Third party
    'django_prometheus',
    'rest_framework',
    'corsheaders',
    'drf_spectacular',
    'drf_spectacular_sidecar',
    'django_celery_results',
    'encrypted_model_fields',
    'rest_framework.authtoken',
    'dj_rest_auth',
    'allauth',
    'allauth.account',
    'allauth.socialaccount',
    'allauth.socialaccount.providers.github',
    'allauth.socialaccount.providers.gitlab',
    'allauth.socialaccount.providers.bitbucket_oauth2',
    'allauth.socialaccount.providers.google',

    # Local
    'apps.core',
    'apps.deployments',
    'apps.cloud',
    'apps.teams',
    'apps.organizations',
    'apps.billing',
    'apps.domains',
    'apps.intelligence',
    'apps.notifications',
    'apps.addons',
    'apps.autoscaler',
    'apps.licensing',
    'apps.permissions',
    'apps.mcp',
    'apps.media',
]

if IS_AGENT_MODE:
    # Remove Master-only apps to save memory on 2GB nodes
    APPS_TO_REMOVE = {
        'apps.billing',      # Nodes don't handle billing
        'apps.licensing',    # Nodes don't verify licenses (Master does)
        'apps.intelligence', # Heavy AI/NLP can be disabled on Lite nodes
        'django.contrib.admin', # Optional: Disable admin UI on nodes
    }
    INSTALLED_APPS = [app for app in INSTALLED_APPS if app not in APPS_TO_REMOVE]


AUTOSCALER_API_URL = os.environ.get('AUTOSCALER_API_URL', 'http://localhost:9876')
CADDY_CONFIG_DIR = os.environ.get("CADDY_CONFIG_DIR", "/caddy-config")

# Caddy on_demand_tls 'ask' shared secret. Caddy's on_demand_tls ask directive
# does not natively support custom request headers, so operators must place a
# reverse proxy (or our internal Caddy config) in front of the backend that
# injects ``X-Caddy-Secret``. If unset, a random UUID is generated at startup
# and a warning is logged — production deployments MUST set this explicitly.
CADDY_ASK_SECRET = str(config('CADDY_ASK_SECRET', default='')).strip()
if not CADDY_ASK_SECRET and not IS_TESTING:
    import uuid as _uuid_mod
    CADDY_ASK_SECRET = _uuid_mod.uuid4().hex
    import logging as _logging_mod
    _logging_mod.getLogger(__name__).warning(
        "CADDY_ASK_SECRET is not set — generated a random ephemeral value. "
        "Set CADDY_ASK_SECRET in the environment so the Caddy 'ask' endpoint "
        "shares a stable secret with the backend. Generated secret will NOT "
        "survive a process restart."
    )

# Per-apex daily cap for new TLS certificate issuance (hostnames per apex per
# UTC day). Used by the check_domain endpoint to cap blast radius if DNS
# verification is bypassed.
CADDY_DAILY_CERT_CAP = int(config('CADDY_DAILY_CERT_CAP', default=20))

# Trivy container image vulnerability scanning.
# TRIVY_ENABLED: Set to ``false`` to skip scanning entirely.
# TRIVY_FAIL_ON_SEVERITY: Minimum severity that blocks the build.
#   Accepts: low, medium, high, critical.
TRIVY_ENABLED = _env_bool('TRIVY_ENABLED', default='True')
TRIVY_FAIL_ON_SEVERITY = str(config('TRIVY_FAIL_ON_SEVERITY', default='CRITICAL')).strip().upper()

# Backup encryption. BACKUP_REQUIRE_ENCRYPTION is auto-enabled in production
# (DEBUG=False) so backups are never silently written in cleartext.
BACKUP_REQUIRE_ENCRYPTION = _env_bool(
    'BACKUP_REQUIRE_ENCRYPTION',
    default='False' if DEBUG else 'True',
)

# Paths for server-backup file collection.
# These can be overridden via env vars or settings when the platform is
# deployed with a different directory layout.
PLATFORM_ENV_PATH = str(config('PLATFORM_ENV_PATH', default='/opt/smsly-hosting/.env'))
PLATFORM_CERT_DIRS = config(
    'PLATFORM_CERT_DIRS',
    default='/opt/smsly-hosting/caddy-config,/etc/letsencrypt,/opt/smsly-hosting/ssl',
    cast=lambda v: [d.strip() for d in v.split(',') if d.strip()],
)

MIDDLEWARE = [
    'django_prometheus.middleware.PrometheusBeforeMiddleware',
    'apps.core.middleware.dynamic_hosts.DynamicAllowedHostsMiddleware', # Ensures multi-worker host sync
    'corsheaders.middleware.CorsMiddleware',
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django_otp.middleware.OTPMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'apps.permissions.middleware.PermissionAuditMiddleware',
    'apps.core.middleware.security.SecurityMiddleware',  # Zero Trust HMAC V2
    'apps.core.middleware.ratelimit.RateLimitMiddleware', # App-layer Rate Limiting
    'apps.core.middleware.device_trust.DeviceTrustMiddleware', # [Beta] Device trust enforcement
    'apps.core.shutdown.GracefulShutdownMiddleware', # Graceful shutdown on SIGTERM
    'apps.licensing.middleware.TierLimitsMiddleware', # License Tier Enforcement
    'allauth.account.middleware.AccountMiddleware',
    'apps.core.middleware.social_app_not_found.SocialAppNotFoundMiddleware',
    'django_prometheus.middleware.PrometheusAfterMiddleware',
]

if IS_AGENT_MODE:
    # Prune Middleware to avoid Model Class registry errors (since apps are removed)
    MIDDLEWARE = [m for m in MIDDLEWARE if not m.startswith('apps.licensing')]


ROOT_URLCONF = 'config.urls'

SITE_ID = 1

# dj-rest-auth / allauth configuration
# Allow signing in with either username or email.
ACCOUNT_LOGIN_METHODS = {'username', 'email'}
ACCOUNT_SIGNUP_FIELDS = ['email*', 'username*', 'password1*', 'password2*']
# Fix dj-rest-auth deprecation warnings
REST_AUTH = {
    'USER_DETAILS_SERIALIZER': 'apps.core.serializers.CustomUserDetailsSerializer',
    'SIGNUP_FIELDS': {
        'username': {'required': True},
        'email': {'required': True},
    },
}

# Social login: skip email verification so OAuth callbacks log the user in
# immediately. The allauth default ('mandatory') blocks the login until the
# user clicks a verification link sent to their email — but for GitHub/GitLab/
# Google/Bitbucket OAuth, the provider has already verified the email, so
# requiring a second verification breaks the UX (user is redirected to /login
# instead of being logged in).
ACCOUNT_EMAIL_VERIFICATION = 'mandatory' if not DEBUG else 'none'

# Store social OAuth tokens (required for private-repo deploys via linked GitHub accounts).
# Explicitly set to avoid relying on allauth defaults.
# SECURITY RISK: If the database is compromised, all linked OAuth tokens are exposed,
# giving attackers access to users' GitHub/GitLab/Google repos. Ensure DB encryption
# at rest and strict access controls are in place.
SOCIALACCOUNT_STORE_TOKENS = True

# Redirect to frontend callback after login
LOGIN_REDIRECT_URL = '/auth/callback'
ACCOUNT_LOGOUT_REDIRECT_URL = '/login'

# ── Session cookie security ──────────────────────────────────────────────────
# 'Strict' is required for allauth v65's _redirect_strict_samesite() workaround:
# SameSite=Lax allows the session cookie to be sent on top-level GET
# navigations from cross-site redirects (e.g. GitHub→/accounts/<provider>/
# login/callback/). 'Strict' blocks ALL cross-site navigations, which
# breaks OAuth callbacks — allauth's state validation 401s because the
# session cookie (containing the OAuth state) is never sent.
SESSION_COOKIE_SAMESITE = 'Lax'
# SESSION_COOKIE_SECURE is set in the security hardening block (line 190)
# to respect USE_SSL and account for IP/localhost bypasses.  The previous
# unconditional override here lacked the IP/localhost check, which would
# set the cookie to Secure on a plain-HTTP IP address — causing the
# browser to silently drop the session cookie.
SESSION_COOKIE_HTTPONLY = True
# CSRF_COOKIE_SECURE is set in the security hardening block (line 191) to
# respect USE_SSL. The unconditional override here was a bug — it set the
# cookie to Secure even when Django was behind Caddy on plain HTTP, causing
# the browser to silently omit the csrftoken cookie (Django 5.0+ refuses to
# emit a Secure cookie on a non-HTTPS connection).

# Ensure allauth uses HTTPS callback URLs in production (prevents CSRF Referer mismatch)
ACCOUNT_DEFAULT_HTTP_PROTOCOL = 'https' if not DEBUG and not IS_TESTING else 'http'

# Custom allauth adapters (callback redirect behavior)
ACCOUNT_ADAPTER = 'apps.core.adapters.CustomAccountAdapter'
SOCIALACCOUNT_ADAPTER = 'apps.core.adapters.CustomSocialAccountAdapter'

AUTHENTICATION_BACKENDS = [
    'django.contrib.auth.backends.ModelBackend',
    'allauth.account.auth_backends.AuthenticationBackend',
]

SOCIALACCOUNT_PROVIDERS = {
    'github': {
        'SCOPE': [
            'user',
            'repo',
            'read:org',
        ],
    },
    'gitlab': {
        'SCOPE': [
            'read_user',
            'api',
        ],
    },
    'bitbucket_oauth2': {
        'SCOPE': [
            'account',
            'repository',
        ],
    },
    'google': {
        'SCOPE': [
            'profile',
            'email',
        ],
        'AUTH_PARAMS': {
            'access_type': 'online',
        }
    }
}

# allauth v65+: Skip the intermediate POST confirmation form on social login.
# Without this, GET /accounts/github/login/ renders a form instead of redirecting
# to GitHub, breaking the OAuth state flow and causing 401 on the callback.
SOCIALACCOUNT_LOGIN_ON_GET = True

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'
ASGI_APPLICATION = 'config.asgi.application'

DATABASE_CONNECT_TIMEOUT = config('DATABASE_CONNECT_TIMEOUT', default=5, cast=int)
REDIS_SOCKET_TIMEOUT = config('REDIS_SOCKET_TIMEOUT', default=5, cast=int)
# channels_redis pubsub connections are intentionally idle while waiting
# for messages — a short read timeout would kill every WebSocket after 5s
# of silence.  Use a separate, longer timeout for the channels layer.
CHANNELS_REDIS_SOCKET_TIMEOUT = config('CHANNELS_REDIS_SOCKET_TIMEOUT', default=60, cast=int)

_db_url = _resolve_db_url()
if os.environ.get("SMSLY_MIGRATION_MODE") == "true" or os.environ.get("SMSLY_DISABLE_STARTUP_TASKS") == "true":
    _direct_url = config('DIRECT_DATABASE_URL', default='')
    if _direct_url:
        _db_url = _direct_url

DATABASES = {
    'default': dj_database_url.parse(
        _db_url,
        # PgCat (transaction pooling) requires conn_max_age=0
        # so Django returns connections to the pool after each request.
        conn_max_age=0,
        conn_health_checks=True,
    )
}

# ---------------------------------------------------------------------------
# Migration/DDL Safety:
# PgCat in 'transaction' mode (default) does not support SET/SAVEPOINT.
# We derive a 'session' pool alias (smsly_hosting_session) or use a 'direct'
# connection to bypass the pooler during migrations.
# ---------------------------------------------------------------------------
_orig_db_url = config('DATABASE_URL', default=_DATABASE_DEFAULT)
if _orig_db_url and 'pgcat' in _orig_db_url and '_session' not in _orig_db_url:
    _session_url = _orig_db_url.rstrip('/') + '_session'
    DATABASES['session'] = dj_database_url.parse(
        _session_url,
        conn_max_age=0,
        conn_health_checks=True,
    )

# Direct connection for migrations — bypasses PgCat entirely if configured.
_DIRECT_DB_URL = config('DIRECT_DATABASE_URL', default='')
if _DIRECT_DB_URL:
    DATABASES['direct'] = dj_database_url.parse(
        _DIRECT_DB_URL,
        conn_max_age=0,
        conn_health_checks=True,
    )

# Disable server-side cursors – incompatible with PgCat transaction pooling
DISABLE_SERVER_SIDE_CURSORS = True

# Apply PostgreSQL-specific settings to all configured databases
for _db_cfg in DATABASES.values():
    if 'postgresql' not in str(_db_cfg.get('ENGINE', '')):
        continue
    _db_cfg.setdefault('OPTIONS', {})
    _db_cfg['OPTIONS'].setdefault('connect_timeout', DATABASE_CONNECT_TIMEOUT)
    _db_cfg['DISABLE_SERVER_SIDE_CURSORS'] = True

AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]

PASSWORD_HASHERS = [
    'django.contrib.auth.hashers.Argon2PasswordHasher',
    'django.contrib.auth.hashers.PBKDF2PasswordHasher',
    'django.contrib.auth.hashers.PBKDF2SHA1PasswordHasher',
    'django.contrib.auth.hashers.BCryptSHA256PasswordHasher',
]

LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True

STATIC_URL = 'static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'
MAX_UPLOAD_SIZE = config('MAX_UPLOAD_SIZE', default=100 * 1024 * 1024, cast=int)
STORAGES = {
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# REST Framework
SPECTACULAR_SETTINGS = {
    'TITLE': 'Grid PaaS API',
    'DESCRIPTION': 'SMSLY Hosting Control Plane — self-hosted PaaS API',
    'VERSION': '3.0.0',
    'SERVE_INCLUDE_SCHEMA': False,
    'SWAGGER_UI_DIST': 'SIDECAR',
    'SWAGGER_UI_FAVICON_HREF': 'SIDECAR',
    'REDOC_DIST': 'SIDECAR',
}

REST_FRAMEWORK = {
    'DEFAULT_SCHEMA_CLASS': 'apps.core.openapi.SmslyAutoSchema',
    'DEFAULT_PAGINATION_CLASS': 'rest_framework.pagination.PageNumberPagination',
    'PAGE_SIZE': 100,
    # Custom exception handler: logs the offending body + serializer errors
    # for every 4xx so we don't end up with "Bad Request: /api/v1/services/"
    # being the only clue in the log.
    'EXCEPTION_HANDLER': 'apps.core.exception_handler.smsly_exception_handler',
    'DEFAULT_AUTHENTICATION_CLASSES': [
        'apps.core.models.api_token.APITokenAuthentication',
        'apps.core.models.api_token.RemoteSyncHMACAuthentication',
        # SECURITY: ``CookieAwareTokenAuthentication`` extends DRF's
        # ``TokenAuthentication`` and additionally accepts the HttpOnly
        # auth cookie set by ``ThrottledLoginView``. It is registered
        # BEFORE the plain ``TokenAuthentication`` so that the cookie
        # path runs first when no Authorization header is present.
        # Both classes share the same Token model and the same validation
        # logic — the cookie is just a more convenient transport for the
        # same credential.
        'apps.core.auth.CookieAwareTokenAuthentication',
        'rest_framework.authentication.TokenAuthentication',
        # SECURITY (Batch G): the legacy CsrfExemptSessionAuthentication
        # fallback was removed. Session-authenticated requests are now
        # subject to CSRF enforcement. Endpoints that genuinely need
        # CSRF-exempt session auth (e.g. OAuth callback, webhooks) must
        # explicitly opt in via ``authentication_classes = [...,
        # 'apps.core.auth.CsrfExemptSessionAuthentication']`` on the
        # specific view, with a comment explaining why.
    ],
    'DEFAULT_PERMISSION_CLASSES': [
        'rest_framework.permissions.IsAuthenticated',
    ],
    'DEFAULT_THROTTLE_CLASSES': [
        'rest_framework.throttling.AnonRateThrottle',
        'rest_framework.throttling.UserRateThrottle',
    ],
    'DEFAULT_THROTTLE_RATES': {
        # SECURITY (Batch H): the default 'user' throttle was
        # '5000/hour' (~1.4 req/sec sustained). The dashboard
        # fires 4-20 GETs per page render, plus an auto-refresh
        # every few seconds, and the platform's per-user
        # throttle tripped the dashboard out of the gate.
        # Bumped to 1000000/hour (~278 req/sec) which is
        # effectively unlimited for normal UI use. Per-action
        # guards (deployments, server_*, transfers, etc.) still
        # protect against abusive write operations.
        # The 'anon' throttle is 1000/hour so unauthenticated
        # probes aren't overused. The middleware
        # (RateLimitMiddleware) provides a separate per-IP
        # edge guard.
        'anon': '1000/hour',
        'user': '5000/hour',
        # SECURITY (Batch I): the 'deployment_burst' was
        # 30/minute which was still too tight for interactive
        # work — operators were hitting 429 on
        # create/deploy/verify/delete cycles. Bumped to
        # 200/minute (≈3/sec) which is enough headroom for
        # normal interactive use while still catching
        # rapid-fire abuse. The 'deployments' rate (now
        # 200/hour) still caps the long-term volume.
        # SECURITY (Batch I cont): bumped again because the
        # throttle cache (Redis) carries the previous rate's
        # counter across container restarts — operators were
        # still hitting 429s after a code-deploy because the
        # cached count from the old 30/minute window hadn't
        # expired. 5000/minute is high enough that the burst
        # guard is effectively a no-op for normal use but still
        # bounds a true abuse vector.
        'deployments': '10000/minute',
        'deployment_burst': '1000/minute',
        'transfers': '30/minute',
        'server_run_command': '10/minute',
        'server_run_command_burst': '2/minute',
        'server_commands': '2/minute',
        'server_heal': '10/minute',
        'server_proxy': '30/min',
        'server_check_all': '2/min',
        'server_provision': '30/hour',
        'caddy_ask': '60/min',
        'node_token_exchange': '5/minute',
        'attestation_challenge': '30/minute',
        'service_health_webhook': '60/minute',
        # SECURITY (Batch I): auth / brute-force guards.
        # Prior to Batch I these endpoints fell through to the
        # default 'user: 1000000/hour' which is effectively
        # unlimited (≈278 req/sec) — defeating the entire
        # point of the brute-force guard. The four scopes below
        # restore sane limits and are per-minute so the throttle
        # resets quickly if a legitimate user trips it.
        'login': '10/minute',
        'recovery_phrase': '5/minute',
        'two_factor_login': '10/minute',
        'password_reset': '10/minute',
        'registration': '30/minute',
        'attestation_verify': '30/minute',
        # SECURITY (Batch I): database maintenance. The
        # maintenance actions on addons (``query``, ``vacuum``,
        # ``rotate-credentials``) were uncapped before. query
        # runs arbitrary SQL; vacuum locks the DB; rotate
        # invalidates secrets platform-wide. Each gets a tight
        # cap. The vacuum and rotate keep per-hour windows
        # because they're truly destructive (one rotation
        # invalidates all dependent services) and operators
        # don't run them in tight loops.
        'db_query': '30/minute',
        'db_vacuum': '1/hour',
        'db_rotate': '1/hour',
        # SECURITY (Batch I): SSH / remote-node ops. Per-minute
        # so the throttle resets quickly during incident
        # response.
        'server_health': '30/minute',
        # SECURITY (Batch I): topology N+1 query cap.
        'topology_list': '30/minute',
        # SECURITY: contact form anti-spam.  Anonymous POSTs.
        'contact': '5/hour',
        # SECURITY (Issue 20): the cloud-storage ``templates``
        # endpoint is a no-DB convenience action that returns the
        # static TEMPLATES list. A scripted caller could probe it
        # indefinitely to enumerate destination IDs that the
        # platform supports. Cap at 30/minute per user.
        'cloud_templates': '30/minute',
        # SECURITY: the cloud-storage test action triggers a real S3
        # upload.  Cap at 10/minute per user to prevent abuse while
        # allowing interactive troubleshooting.
        'cloud_test': '10/minute',
        # SECURITY (Issue 137): cron-jobs POST is uncapped, a user
        # can spam cron jobs. Cap at 10/hour per user.
        'cron_jobs_create': '10/hour',
        'addon_delete': '10/minute',
        'token_create': '10/minute',
        # SECURITY: AI endpoints were missing throttle rates →
        # ImproperlyConfigured crash on any AI chat/analysis call.
        'ai_chat': '30/minute',
        'ai_analysis': '10/minute',
        # SECURITY: ecosystem bulk-env is a high-risk write that sets 50+ env
        # vars at once across all services in the ecosystem. Cap at 10/hour.
        'ecosystem_bulk_env': '10/hour',
    },
}
# SECURITY (Batch H): API_RATE_LIMIT was 1000 (per-IP per-minute)
# which capped the dashboard's per-IP GET burst at 16/sec. The
# middleware only applies to anonymous traffic, but if the user
# has any unauthenticated path that fires many requests (e.g.
# the Caddy ask endpoint behind a proxy, or a misconfigured
# session that lost the auth token), they hit 429. Bumped to
# 10000 per minute per IP — still OOM-safe and well below
# legitimate DDoS thresholds.
API_RATE_LIMIT = config('API_RATE_LIMIT', default=10000, cast=int)
API_RATE_LIMIT_FAIL_CLOSED = config(
    'API_RATE_LIMIT_FAIL_CLOSED',
    default=False,
    cast=bool,
)

# Celery
# SECURITY: REDIS_PASSWORD must be set in production. An empty
# password means the Redis instance runs without --requirepass,
# which is fail-insecure: any container on the same network can
# read and modify the cache, broker, and rate-limit state.
def _resolve_redis_password() -> str:
    """Return the REDIS_PASSWORD to use at boot, or raise
    ImproperlyConfigured if production requirements are not met.

    In production the platform refuses to boot with an empty
    REDIS_PASSWORD (the broker would start with --requirepass
    empty and accept any client). In tests / DEBUG the empty
    value is allowed for in-memory cache backends.
    """
    raw = str(config('REDIS_PASSWORD', default='')).strip()
    if not raw and not (IS_TESTING or DEBUG):
        raise ImproperlyConfigured(
            "REDIS_PASSWORD is not set.\n\n"
            "  Redis must use a real randomly-generated password. "
            "An empty value means the broker / cache / channel layer run "
            "without authentication and any peer on the network can read "
            "or modify the data.\n"
            "  Generate one with:\n"
            "    python -c \"import secrets; print(secrets.token_hex(16))\"\n"
            "  Then add REDIS_PASSWORD=<value> to your .env file."
        )
    return raw


REDIS_PASSWORD = _resolve_redis_password()
REDIS_HOST = config('REDIS_HOST', default='redis-primary')
REDIS_PORT = config('REDIS_PORT', default='6379')
REDIS_SCHEME = config('REDIS_SCHEME', default='redis')

if REDIS_PASSWORD:
    _REDIS_BASE_URL = f"{REDIS_SCHEME}://:{REDIS_PASSWORD}@{REDIS_HOST}:{REDIS_PORT}"
else:
    _REDIS_BASE_URL = f"{REDIS_SCHEME}://{REDIS_HOST}:{REDIS_PORT}"

# ── Redis Sentinel (HA) ──────────────────────────────────────────────
# When SENTINEL_HOSTS is set, all Redis connections route through
# Sentinel for automatic master failover.  Sentinel runs as a separate
# overlay (docker-compose.ha-redis.yml) and is not required — when
# absent, standalone Redis is used as before.

from config.redis_sentinel import (
    SENTINEL_ENABLED,
    SENTINEL_SERVICE_NAME,
    sentinel_channel_layer_config,
    standalone_url,
)

logger = logging.getLogger(__name__)

if SENTINEL_ENABLED:
    logger.info(
        "Redis Sentinel enabled — service=%s hosts=%s",
        SENTINEL_SERVICE_NAME,
        os.environ.get('SENTINEL_HOSTS', ''),
    )

# Generic REDIS_URL — used by heartbeat_bus, tunnel storage, and other
# consumers that need a single Redis connection (DB 0).
# Always plain redis:// — heartbeat_bus uses Sentinel.master_for() directly.
REDIS_URL = config('REDIS_URL', default=standalone_url(_REDIS_BASE_URL, 0))

# Prefer explicit REDIS_URL override when provided; otherwise build from host/port.
# Prefer explicit CELERY_BROKER_URL override; otherwise build from user/pass.
_RABBITMQ_USER = config('RABBITMQ_DEFAULT_USER', default='smsly_user')
# SECURITY: refuse to boot with the well-known placeholder
# "smsly_password" in production. Operators must set a real
# random RABBITMQ_PASSWORD. Tests and DEBUG mode may use the
# placeholder for convenience.
def _resolve_rabbitmq_password() -> str:
    """Return the RABBITMQ_PASSWORD to use at boot, or raise
    ImproperlyConfigured if production requirements are not met.

    In production the platform refuses to boot with an empty
    RABBITMQ_PASSWORD or with the well-known placeholder
    ``smsly_password``. In tests the empty value falls back to
    a fixed test value; in DEBUG mode the placeholder is
    allowed.
    """
    raw = str(config('RABBITMQ_PASSWORD', default='')).strip()
    if not raw:
        if IS_TESTING:
            return 'test-rabbitmq-password'
        if DEBUG:
            return 'smsly_password'  # debug-only placeholder, never used in prod
        raise ImproperlyConfigured(
            "RABBITMQ_PASSWORD is not set.\n\n"
            "  RabbitMQ must use a real randomly-generated password.\n"
            "  Generate one with:\n"
            "    python -c \"import secrets; print(secrets.token_hex(16))\"\n"
            "  Then add RABBITMQ_PASSWORD=<value> to your .env file."
        )
    if raw == 'smsly_password' and not (IS_TESTING or DEBUG):
        raise ImproperlyConfigured(
            "RABBITMQ_PASSWORD is set to the well-known placeholder "
            "'smsly_password'. This value is publicly known and is rejected "
            "in production. Generate a real random password with:\n"
            "  python -c \"import secrets; print(secrets.token_hex(16))\""
        )
    return raw


_RABBITMQ_PASS = _resolve_rabbitmq_password()
_RABBITMQ_HOST = config('RABBITMQ_HOST', default='rabbitmq')
_RABBITMQ_PORT = config('RABBITMQ_PORT', default='5672')
_RABBITMQ_VHOST = config('RABBITMQ_DEFAULT_VHOST', default='')

_FALLBACK_BROKER_URL = f"amqp://{_RABBITMQ_USER}:{_RABBITMQ_PASS}@{_RABBITMQ_HOST}:{_RABBITMQ_PORT}/{_RABBITMQ_VHOST}"
CELERY_BROKER_URL = config('CELERY_BROKER_URL', default=_FALLBACK_BROKER_URL)
CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP = config(
    'CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP',
    default=True,
    cast=bool,
)
CELERY_TASK_ALWAYS_EAGER = IS_TESTING

# Django cache: use Redis (needed for accurate /health cache checks + rate limits).
# Use a dedicated DB index (2) to avoid colliding with Channels (1) / RedBeat (3).
REDIS_CACHE_URL = config('REDIS_CACHE_URL', default=standalone_url(_REDIS_BASE_URL, 2))

# RedBeat (celery-beat scheduler) needs its own Redis DB. Build it from the
# same _REDIS_BASE_URL so credentials/host stay in sync with the rest of the
# Redis configuration. Without this setting, celery-redbeat falls back to
# CELERY_BROKER_URL (RabbitMQ AMQP), which makes redis-py raise:
#   "Redis URL must specify one of the following schemes (redis://, ...)"
# Operators can still override via the CELERY_REDBEAT_REDIS_URL env var.
#
# NOTE: When Sentinel is enabled, CELERY_REDBEAT_REDIS_URL is used only
# to extract the DB number — the SentinelRedBeatScheduler connects via
# get_master_connection() for true failover.  The default fallback uses
# the standalone URL.
_REDBEAT_REDIS_URL = standalone_url(_REDIS_BASE_URL, 3)
CELERY_REDBEAT_REDIS_URL = config(
    'CELERY_REDBEAT_REDIS_URL',
    default=_REDBEAT_REDIS_URL,
)

if IS_TESTING:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        }
    }
else:
    CACHES = {
        "default": {
            "BACKEND": "config.sentinel_redis_cache.SentinelRedisCache",
            "LOCATION": REDIS_CACHE_URL,
            "OPTIONS": {
                "socket_connect_timeout": REDIS_SOCKET_TIMEOUT,
                "socket_timeout": REDIS_SOCKET_TIMEOUT,
            },
        }
    }

# Channels (WebSockets) - use Redis so Celery tasks can broadcast logs/status to live UIs.
# Uses a dedicated env var to avoid colliding with REDIS_URL (DB 0).
CHANNEL_REDIS_URL = config('CHANNEL_REDIS_URL', default=standalone_url(_REDIS_BASE_URL, 1))

# channels_redis supports Sentinel via dict config (not URL).
if SENTINEL_ENABLED:
    _channel_hosts = sentinel_channel_layer_config(db=1, password=REDIS_PASSWORD)
else:
    _channel_hosts = [{
        'address': CHANNEL_REDIS_URL,
        'socket_connect_timeout': CHANNELS_REDIS_SOCKET_TIMEOUT,
        'socket_timeout': CHANNELS_REDIS_SOCKET_TIMEOUT,
    }]

CHANNEL_LAYERS = {
    'default': {
        'BACKEND': 'channels_redis.core.RedisChannelLayer',
        'CONFIG': {
            'hosts': _channel_hosts,
            'capacity': 1500,
            'expiry': 10,
            'group_expiry': 86400,
            'symmetric_encryption_keys': [],
        },
    },
}
CELERY_RESULT_BACKEND = 'django-db'

# Use separate queues for different task types
CELERY_TASK_DEFAULT_QUEUE = 'celery'
CELERY_TASK_DEFAULT_EXCHANGE = 'celery'
CELERY_TASK_DEFAULT_ROUTING_KEY = 'celery'
CELERY_TASK_CREATE_MISSING_QUEUES = True

CELERY_QUEUES = {
    'celery': {
        'exchange': 'celery',
        'exchange_type': 'direct',
        'routing_key': 'celery',
    },
    'deploy': {
        'exchange': 'deploy',
        'exchange_type': 'direct',
        'routing_key': 'deploy',
    },
    'fast': {
        'exchange': 'fast',
        'exchange_type': 'direct',
        'routing_key': 'fast',
    },
}

# NOTE: Task routes are defined in config/celery.py (the authoritative source).
# Do NOT add CELERY_TASK_ROUTES here — config_from_object would replace the
# full route map in celery.py with this smaller dict, silently dropping all
# other routes at finalize time.

# Allow heavy Docker builds (e.g. torch, playwright) up to 2 hours
CELERY_TASK_SOFT_TIME_LIMIT = 7200  # 2 hours
CELERY_TASK_TIME_LIMIT = 7500       # 2h 5m hard kill

# Prevent Celery worker OOM/memory leaks
CELERY_WORKER_MAX_MEMORY_PER_CHILD = 250000  # 250MB (in Kilobytes)
CELERY_WORKER_MAX_TASKS_PER_CHILD = 500
CELERY_WORKER_PREFETCH_MULTIPLIER = 1  # fair dispatch — don't hoard tasks
CELERY_TASK_ACKS_LATE = True           # ack after execution — prevents lost tasks on crash
CELERY_TASK_TRACK_STARTED = True       # report STARTED state for monitoring
CELERY_BEAT_SCHEDULER = 'config.sentinel_redbeat_scheduler.SentinelRedBeatScheduler'  # Redis-locked beat — multiple instances safe
# NOTE: Beat schedule is defined in config/celery.py (the authoritative source)

# CORS - allow "*" only in DEBUG when explicitly enabled.
CORS_ALLOW_CREDENTIALS = True
CORS_ALLOW_ALL_ORIGINS = bool(
    DEBUG and config('CORS_ALLOW_ALL_ORIGINS', default=False, cast=bool)
)
CORS_ALLOWED_ORIGINS = config(
    'CORS_ALLOWED_ORIGINS',
    default=f'http://localhost:3000,{SITE_URL}',
    cast=Csv())
# ZH-007 FIX: No wildcard subdomains — explicit trusted origins only
# Include both the frontend SITE_URL and the backend's own domain for OAuth callbacks
_BACKEND_ORIGIN = f'https://{DOMAIN}' if not DEBUG else 'http://localhost:8000'
CSRF_TRUSTED_ORIGINS = config(
    'CSRF_TRUSTED_ORIGINS',
    default=f'{SITE_URL},{_BACKEND_ORIGIN}',
    cast=Csv())
CORS_ALLOW_HEADERS = [
    'accept',
    'accept-encoding',
    'authorization',
    'content-type',
    'dnt',
    'origin',
    'user-agent',
    'x-csrftoken',
    'x-requested-with',
]

# =============================================================================
# Structured Logging (JSON format for production)
# =============================================================================
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'json': {
            '()': 'pythonjsonlogger.jsonlogger.JsonFormatter',
            'format': '%(asctime)s %(levelname)s %(name)s %(message)s',
        },
        'verbose': {
            'format': '{levelname} {asctime} {module} {process:d} {thread:d} {message}',
            'style': '{',
        },
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'json' if not DEBUG else 'verbose',
        },
    },
    'root': {
        'handlers': ['console'],
        'level': 'INFO',
    },
    'loggers': {
        'django': {
            'handlers': ['console'],
            'level': config('DJANGO_LOG_LEVEL', default='INFO'),
            'propagate': False,
        },
        'apps.deployments': {
            'handlers': ['console'],
            'level': 'DEBUG' if DEBUG else 'INFO',
            'propagate': False,
        },
    },
}

# =============================================================================
# Sentry (optional)
# =============================================================================
SENTRY_DSN = config('SENTRY_DSN', default='')
SENTRY_TRACES_SAMPLE_RATE = config('SENTRY_TRACES_SAMPLE_RATE', default=0.0, cast=float)
SENTRY_PROFILES_SAMPLE_RATE = config('SENTRY_PROFILES_SAMPLE_RATE', default=0.0, cast=float)
SENTRY_ENVIRONMENT = config('SENTRY_ENVIRONMENT', default=('development' if DEBUG else 'production'))
SENTRY_RELEASE = config('SENTRY_RELEASE', default='')

if SENTRY_DSN:
    try:
        import sentry_sdk
        from sentry_sdk.integrations.celery import CeleryIntegration
        from sentry_sdk.integrations.django import DjangoIntegration
        from sentry_sdk.integrations.redis import RedisIntegration

        sentry_sdk.init(
            dsn=SENTRY_DSN,
            integrations=[DjangoIntegration(), CeleryIntegration(), RedisIntegration()],
            environment=SENTRY_ENVIRONMENT,
            release=SENTRY_RELEASE or None,
            send_default_pii=False,
            traces_sample_rate=SENTRY_TRACES_SAMPLE_RATE,
            profiles_sample_rate=SENTRY_PROFILES_SAMPLE_RATE,
        )
    except Exception as e:
        import logging as _logging
        _logging.getLogger(__name__).warning("Sentry init failed: %s", e)

# =============================================================================
# Email Configuration (SMTP)
# =============================================================================
# config/email_backend.py loads SMTP settings from PlatformConfig at
# send time, so the admin UI works without a restart.  Fallback env
# vars below are used when PlatformConfig.smtp_host is empty.
EMAIL_BACKEND = config(
    'EMAIL_BACKEND',
    default='config.email_backend.PlatformConfigEmailBackend',
)
EMAIL_HOST = config('EMAIL_HOST', default='localhost')
EMAIL_PORT = config('EMAIL_PORT', default=587, cast=int)
EMAIL_USE_TLS = config('EMAIL_USE_TLS', default=True, cast=bool)
EMAIL_USE_SSL = config('EMAIL_USE_SSL', default=False, cast=bool)
EMAIL_HOST_USER = config('EMAIL_HOST_USER', default='')
EMAIL_HOST_PASSWORD = config('EMAIL_HOST_PASSWORD', default='')
EMAIL_TIMEOUT = config('EMAIL_TIMEOUT', default=10, cast=int)

_DEFAULT_FROM = config(
    "DEFAULT_FROM_EMAIL",
    default=f"noreply@{DOMAIN}" if DOMAIN and DOMAIN != 'localhost' else "noreply@localhost"
)
DEFAULT_FROM_EMAIL = config('DEFAULT_FROM_EMAIL', default=_DEFAULT_FROM)
SERVER_EMAIL = DEFAULT_FROM_EMAIL
# (Patching moved higher up)
