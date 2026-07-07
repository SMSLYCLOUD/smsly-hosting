dump_diagnostic_logs() {
    local env_file="${1:-$INSTALL_DIR/.env}"
    echo -e "\n${RED}════════════════════════════════════════════════════════════${NC}"
    echo -e "${RED}   DIAGNOSTIC LOG DUMP (FAILURE ANALYSIS)${NC}"
    echo -e "${RED}════════════════════════════════════════════════════════════${NC}"

    echo -e "${YELLOW}  → System Resource Snapshot:${NC}"
    free -m
    df -h /

    echo -e "\n${YELLOW}  → Container Status:${NC}"
    if command -v docker >/dev/null 2>&1 && [ -f "$env_file" ] && grep -q '^POSTGRES_PASSWORD=' "$env_file" 2>/dev/null; then
        docker compose -f "$COMPOSE_FILE" ps || true

        echo -e "\n${YELLOW}  -> Compose Logs (Last 50 lines):${NC}"
        docker compose -f "$COMPOSE_FILE" logs --tail=50 || true
    else
        echo -e "${YELLOW}  (Docker or .env not ready; skipping container logs)${NC}"
    fi

    echo -e "${RED}════════════════════════════════════════════════════════════${NC}\n"
}

apply_env_platform_overrides() {
    local env_file="$1"
    local changed=false
    local current_domain current_use_ssl current_acme_email current_wildcard current_cf_token current_public_ip
    local desired_domain desired_use_ssl desired_acme_email desired_wildcard desired_cf_token desired_public_ip

    [ -f "$env_file" ] || return 0

    current_domain="$(env_get_value "$env_file" "DOMAIN")"
    current_use_ssl="$(env_get_value "$env_file" "USE_SSL")"
    current_acme_email="$(env_get_value "$env_file" "ACME_EMAIL")"
    current_wildcard="$(env_get_value "$env_file" "WILDCARD_SUBDOMAINS")"
    current_cf_token="$(env_get_value "$env_file" "CLOUDFLARE_API_TOKEN")"
    current_public_ip="$(env_get_value "$env_file" "PUBLIC_IP")"

    if [ "${DOMAIN+x}" = "x" ]; then
        desired_domain="${DOMAIN}"
        # Protection: Do NOT allow an IP to overwrite a real domain unless forced or it's a fresh install.
        if echo "$desired_domain" | grep -qE '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$'; then
            if [ -n "$current_domain" ] && ! echo "$current_domain" | grep -qE '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$'; then
                echo -e "${YELLOW}  ⚠ WARNING: Attempted to overwrite domain ($current_domain) with IP ($desired_domain). Ignored to prevent lockout.${NC}"
                desired_domain="$current_domain"
            fi
        fi
    else
        desired_domain="${current_domain}"
    fi
    if [ "${USE_SSL+x}" = "x" ]; then
        desired_use_ssl="${USE_SSL}"
    else
        desired_use_ssl="${current_use_ssl}"
    fi

    # SEC-002: IP-mode SSL guard — always force USE_SSL=false for raw IPs
    if echo "$desired_domain" | grep -qE '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$'; then
        if [ "$desired_use_ssl" = "true" ]; then
            echo -e "${YELLOW}  ⚠ SEC-002: USE_SSL=true override blocked — DOMAIN ($desired_domain) is a raw IP.${NC}"
        fi
        desired_use_ssl="false"
    fi
    if [ "${ACME_EMAIL+x}" = "x" ]; then
        desired_acme_email="${ACME_EMAIL}"
    else
        desired_acme_email="${current_acme_email}"
    fi
    if [ "${WILDCARD_SUBDOMAINS+x}" = "x" ]; then
        desired_wildcard="${WILDCARD_SUBDOMAINS}"
    else
        desired_wildcard="${current_wildcard}"
    fi
    if [ "${CLOUDFLARE_API_TOKEN+x}" = "x" ]; then
        desired_cf_token="${CLOUDFLARE_API_TOKEN}"
    else
        desired_cf_token="${current_cf_token}"
    fi
    if [ "${PUBLIC_IP+x}" = "x" ]; then
        desired_public_ip="${PUBLIC_IP}"
    else
        desired_public_ip="${current_public_ip}"
    fi

    if [ -z "$desired_public_ip" ]; then
        desired_public_ip="$(detect_public_ip)"
    fi

    if [ "$desired_domain" != "$current_domain" ]; then
        env_set_value "$env_file" "DOMAIN" "$desired_domain"
        changed=true
    fi
    if [ "$desired_use_ssl" != "$current_use_ssl" ]; then
        env_set_value "$env_file" "USE_SSL" "$desired_use_ssl"
        changed=true
    fi
    if [ "$desired_acme_email" != "$current_acme_email" ]; then
        env_set_value "$env_file" "ACME_EMAIL" "$desired_acme_email"
        changed=true
    fi
    if [ "$desired_wildcard" != "$current_wildcard" ]; then
        env_set_value "$env_file" "WILDCARD_SUBDOMAINS" "$desired_wildcard"
        changed=true
    fi
    if [ "$desired_cf_token" != "$current_cf_token" ]; then
        env_set_value "$env_file" "CLOUDFLARE_API_TOKEN" "$desired_cf_token"
        changed=true
    fi
    if [ "$desired_public_ip" != "$current_public_ip" ]; then
        env_set_value "$env_file" "PUBLIC_IP" "$desired_public_ip"
        changed=true
    fi

    # Sync GRAFANA_EXTERNAL_URL when domain or SSL changes
    if [ -n "$desired_domain" ]; then
        if echo "$desired_domain" | grep -qE '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$' || [ "$desired_use_ssl" != "true" ]; then
            _grafana_scheme="http"
        else
            _grafana_scheme="https"
        fi
        _desired_grafana_url="${_grafana_scheme}://${desired_domain}/grafana"
        _current_grafana_url="$(env_get_value "$env_file" "GRAFANA_EXTERNAL_URL")"
        if [ "$_desired_grafana_url" != "$_current_grafana_url" ]; then
            env_set_value "$env_file" "GRAFANA_EXTERNAL_URL" "$_desired_grafana_url"
            changed=true
        fi
    fi

    DOMAIN="$desired_domain"
    USE_SSL="$desired_use_ssl"
    ACME_EMAIL="$desired_acme_email"
    WILDCARD_SUBDOMAINS="$desired_wildcard"
    CLOUDFLARE_API_TOKEN="$desired_cf_token"
    PUBLIC_IP="$desired_public_ip"

    sync_env_domain_allowlists "$env_file" "$DOMAIN" "$PUBLIC_IP"

    if [ "$changed" = true ]; then
        echo -e "${GREEN}  ✓ Applied platform/domain overrides to .env${NC}"
        echo -e "${BLUE}    DOMAIN=${DOMAIN} USE_SSL=${USE_SSL} WILDCARD_SUBDOMAINS=${WILDCARD_SUBDOMAINS}${NC}"
    fi
}

DOMAIN_SYNC_UPDATED_COUNT=0
DOMAIN_SYNC_REDEPLOY_REQUIRED=0
DOMAIN_SYNC_SERVICE_IDS=""

sync_platform_domain_state() {
    local env_file="${1:-$INSTALL_DIR/.env}"
    local sync_domain sync_use_ssl sync_wildcard sync_cf_token sync_public_ip
    local sync_json=""

    [ -f "$env_file" ] || return 0

    sync_domain="$(env_get_value "$env_file" "DOMAIN")"
    sync_use_ssl="$(env_get_value "$env_file" "USE_SSL")"
    sync_wildcard="$(env_get_value "$env_file" "WILDCARD_SUBDOMAINS")"
    sync_cf_token="$(env_get_value "$env_file" "CLOUDFLARE_API_TOKEN")"
    sync_public_ip="$(env_get_value "$env_file" "PUBLIC_IP")"

    [ -n "$sync_public_ip" ] || sync_public_ip="$(detect_public_ip)"

    echo -e "${BLUE}  → Syncing PlatformConfig + public domains from installer state...${NC}"
    sync_json="$(
        docker compose -f "$COMPOSE_FILE" exec -T \
            -e SMSLY_DISABLE_STARTUP_TASKS=true \
            -e SMSLY_SYNC_DOMAIN="$sync_domain" \
            -e SMSLY_SYNC_USE_SSL="$sync_use_ssl" \
            -e SMSLY_SYNC_WILDCARD="$sync_wildcard" \
            -e SMSLY_SYNC_CF_TOKEN="$sync_cf_token" \
            -e SMSLY_SYNC_PUBLIC_IP="$sync_public_ip" \
            backend python manage.py shell <<'PY'
import json
import os

from apps.deployments.models import EnvironmentVariable, PlatformConfig, Service


def parse_bool(value: str) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def normalize_platform_domain(value: str) -> str:
    raw = str(value or "").strip().lower().rstrip(".")
    if raw in {"", "localhost", "127.0.0.1"}:
        return ""
    parts = raw.split(".")
    if len(parts) == 4 and all(part.isdigit() and 0 <= int(part) <= 255 for part in parts):
        return ""
    return raw


def rewrite_public_domain(current_domain: str, old_base: str, new_base: str):
    current = str(current_domain or "").strip().lower().rstrip(".")
    old_base = str(old_base or "").strip().lower().rstrip(".")
    new_base = str(new_base or "").strip().lower().rstrip(".")
    if not current or not old_base or not new_base or old_base == new_base:
        return None
    if current == old_base:
        return new_base
    suffix = f".{old_base}"
    if not current.endswith(suffix):
        return None
    prefix = current[:-len(suffix)].rstrip(".")
    return f"{prefix}.{new_base}" if prefix else new_base


cfg = PlatformConfig.load()
old_base = Service.default_public_base_domain()
original_domain = (cfg.domain or "").strip().lower().rstrip(".")

# SEC-FIX: Preserve an existing real domain in the DB when the incoming
# sync value from .env is empty, localhost, or a raw IP. This prevents
# --update from clobbering a domain set via the Settings UI with the
# installer's default IP value.
incoming_domain = normalize_platform_domain(os.environ.get("SMSLY_SYNC_DOMAIN", ""))
db_has_real_domain = bool(original_domain) and original_domain not in ("", "localhost")
incoming_is_ip_or_empty = not incoming_domain
if db_has_real_domain and incoming_is_ip_or_empty:
    # Preserve the DB domain — the user configured it via Settings UI
    print(f"[sync] Preserving existing DB domain '{original_domain}' (incoming was empty/IP)")
else:
    cfg.domain = incoming_domain

# Preserve existing DB use_ssl when the incoming value is false/empty
# and the DB already has SSL enabled. This prevents --update from
# accidentally disabling HTTPS when .env USE_SSL is stale or missing.
_incoming_use_ssl = parse_bool(os.environ.get("SMSLY_SYNC_USE_SSL", "false"))
_db_already_has_ssl = bool(cfg.use_ssl)
if _incoming_use_ssl:
    cfg.use_ssl = True
elif not _db_already_has_ssl:
    cfg.use_ssl = False
# else: preserve existing True

# Preserve existing DB wildcard_subdomains when the incoming value is
# false/empty and the DB already has it enabled. Same pattern as use_ssl.
_incoming_wildcard = parse_bool(os.environ.get("SMSLY_SYNC_WILDCARD", "false"))
_db_already_has_wildcard = bool(cfg.wildcard_subdomains)
if _incoming_wildcard:
    cfg.wildcard_subdomains = True
elif not _db_already_has_wildcard:
    cfg.wildcard_subdomains = False
# else: preserve existing True
cfg.cloudflare_api_token = str(os.environ.get("SMSLY_SYNC_CF_TOKEN", "") or "").strip()
cfg.server_ip = str(os.environ.get("SMSLY_SYNC_PUBLIC_IP", "") or "").strip() or None
cfg.save()

new_base = (cfg.domain or "").strip().lower().rstrip(".")
host_keys = ("ALLOWED_HOSTS", "DJANGO_ALLOWED_HOSTS", "MARKETER_ALLOWED_HOSTS")
updated = 0
service_ids = []

if new_base and new_base != old_base:
    for service in Service.objects.exclude(public_domain__isnull=True).exclude(public_domain="").iterator():
        current_domain = str(service.public_domain or "").strip().lower().rstrip(".")
        next_domain = rewrite_public_domain(current_domain, old_base, new_base)
        if not next_domain or next_domain == current_domain:
            continue
        if Service.objects.exclude(pk=service.pk).filter(public_domain=next_domain).exists():
            continue

        service.public_domain = next_domain
        service.save(update_fields=["public_domain"])
        EnvironmentVariable.objects.filter(service=service, key="PUBLIC_DOMAIN").update(value=next_domain)

        for env_var in EnvironmentVariable.objects.filter(service=service, key__in=host_keys):
            value = str(env_var.value or "")
            if current_domain in value and next_domain not in value:
                env_var.value = value.replace(current_domain, next_domain)
                env_var.save(update_fields=["value"])

        updated += 1
        service_ids.append(str(service.id))

result = {
    "domain": cfg.domain,
    "use_ssl": cfg.use_ssl,
    "wildcard_subdomains": cfg.wildcard_subdomains,
    "server_ip": cfg.server_ip or "",
    "old_base_domain": old_base,
    "original_domain": original_domain,
    "updated_service_domains": updated,
    "redeploy_required": bool(updated),
    "service_ids": service_ids,
}
print(json.dumps(result))
PY
    )"

    sync_json="$(echo "$sync_json" | tr -d '\r' | tail -n 1)"
    if [ -z "$sync_json" ]; then
        echo -e "${YELLOW}  ⚠ PlatformConfig sync did not return a result. Continuing with host-level config.${NC}"
        return 0
    fi

    DOMAIN_SYNC_UPDATED_COUNT="$(printf '%s' "$sync_json" | python3 -c "import json,sys; print(json.load(sys.stdin).get('updated_service_domains', 0))" 2>/dev/null || echo 0)"
    DOMAIN_SYNC_REDEPLOY_REQUIRED="$(printf '%s' "$sync_json" | python3 -c "import json,sys; print(1 if json.load(sys.stdin).get('redeploy_required') else 0)" 2>/dev/null || echo 0)"
    DOMAIN_SYNC_SERVICE_IDS="$(printf '%s' "$sync_json" | python3 -c "import json,sys; print(','.join(json.load(sys.stdin).get('service_ids', [])))" 2>/dev/null || true)"

    echo -e "${GREEN}  ✓ PlatformConfig synced: domain=$(printf '%s' "$sync_json" | python3 -c "import json,sys; print(json.load(sys.stdin).get('domain', ''))" 2>/dev/null)${NC}"
    if [ "${DOMAIN_SYNC_UPDATED_COUNT:-0}" -gt 0 ]; then
        echo -e "${GREEN}  ✓ Rewrote ${DOMAIN_SYNC_UPDATED_COUNT} existing service public domain(s)${NC}"
    fi

    # SEC-FIX: Sync the effective DB domain back to .env so future --update
    # runs use the real domain (not the installer's default IP).
    _effective_domain="$(printf '%s' "$sync_json" | python3 -c "
import json,sys
d = json.load(sys.stdin)
print(d.get('domain', '') or '')
" 2>/dev/null || true)"
    _env_domain="$(env_get_value "$env_file" "DOMAIN")"
    _env_use_ssl="$(env_get_value "$env_file" "USE_SSL")"
    _env_wildcard="$(env_get_value "$env_file" "WILDCARD_SUBDOMAINS")"
    _db_use_ssl="$(printf '%s' "$sync_json" | python3 -c "import json,sys; print('true' if json.load(sys.stdin).get('use_ssl') else 'false')" 2>/dev/null)"
    _db_wildcard="$(printf '%s' "$sync_json" | python3 -c "import json,sys; print('true' if json.load(sys.stdin).get('wildcard_subdomains') else 'false')" 2>/dev/null)"
    if [ -n "$_effective_domain" ]; then
        _needs_sync=false
        if [ "$_effective_domain" != "$_env_domain" ]; then
            env_set_value "$env_file" "DOMAIN" "$_effective_domain"
            _needs_sync=true
        fi
        if [ "$_db_use_ssl" != "$_env_use_ssl" ]; then
            env_set_value "$env_file" "USE_SSL" "$_db_use_ssl"
            _needs_sync=true
        fi
        if [ "$_db_wildcard" != "$_env_wildcard" ]; then
            env_set_value "$env_file" "WILDCARD_SUBDOMAINS" "$_db_wildcard"
            _needs_sync=true
        fi
        if [ "$_needs_sync" = "true" ]; then
            echo -e "${GREEN}  ✓ .env synced: DOMAIN=$_effective_domain, USE_SSL=$_db_use_ssl, WILDCARD_SUBDOMAINS=$_db_wildcard${NC}"
        fi
    fi
}

queue_active_service_redeploys() {
    local reason="${1:-Installer-triggered redeploy}"
    local service_ids="${2:-}"

    # Verify backend is reachable before attempting redeploy
    local backend_container
    backend_container="$(resolve_container_target "smsly-hosting-backend-1")"
    local backend_state
    backend_state="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$backend_container" 2>/dev/null || echo 'missing')"
    if [ "$backend_state" != "healthy" ] && [ "$backend_state" != "running" ]; then
        echo -e "${YELLOW}  ⚠ Backend container ($backend_container) not ready (state=$backend_state). Waiting 15s...${NC}" >&2
        sleep 15
        backend_state="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$backend_container" 2>/dev/null || echo 'missing')"
        if [ "$backend_state" != "healthy" ] && [ "$backend_state" != "running" ]; then
            echo -e "${RED}  ✗ Backend container still not ready after wait. Skipping redeploy.${NC}" >&2
            return 1
        fi
    fi

    docker compose -f "$COMPOSE_FILE" exec -T \
        -e SMSLY_DISABLE_STARTUP_TASKS=true \
        -e SMSLY_REDEPLOY_REASON="$reason" \
        -e SMSLY_SERVICE_IDS="$service_ids" \
        backend python manage.py shell <<'PY'
import os
import traceback

from django.utils import timezone

from apps.deployments.models import Deployment, Service
from apps.deployments.tasks import enqueue_smart_deploy_task, _resolve_provider_for_service


service_ids = [value.strip() for value in os.environ.get("SMSLY_SERVICE_IDS", "").split(",") if value.strip()]
reason = os.environ.get("SMSLY_REDEPLOY_REASON", "Installer-triggered redeploy")
try:
    queryset = Service.objects.filter(id__in=service_ids) if service_ids else Service.objects.all()
    count = 0
    failed = 0
    for svc in queryset.select_related("provider"):
        dep = svc.deployments.filter(status="ACTIVE").order_by("-created_at").first()
        if not dep or not dep.commit_hash:
            continue
        provider = _resolve_provider_for_service(svc)
        if not provider:
            failed += 1
            print(f"  WARN: No active provider for {svc.name}")
            continue
        new_dep = Deployment.objects.create(
            service=svc,
            status="QUEUED",
            commit_hash=dep.commit_hash,
            commit_message=reason,
        )
        try:
            enqueue_smart_deploy_task(str(new_dep.id), str(provider.id), skip_review=True)
        except Exception as exc:
            failed += 1
            new_dep.status = "FAILED"
            new_dep.finished_at = timezone.now()
            new_dep.build_logs = (
                (new_dep.build_logs or "")
                + f"\n[ERROR] Failed to queue platform auto-redeploy task: {exc}\n"
            )
            new_dep.save(update_fields=["status", "finished_at", "build_logs", "updated_at"])
            print(f"  WARN: Failed to queue {svc.name}: {exc}")
            continue
        count += 1
        print(f"  Queued: {svc.name} ({dep.commit_hash[:7]})")
    print(f"OK: {count} service(s) queued for redeploy; {failed} failed/skipped")
except Exception as exc:  # pragma: no cover - installer runtime path
    print(f"WARN: {exc}")
    traceback.print_exc()
PY
}

ensure_env_runtime_defaults() {
    local env_file="$1"
    local redis_password=""
    local postgres_password=""
    local current_domain=""
    local current_public_ip=""
    local current_tunnel_domain=""
    local expected_tunnel_domain="tunnel.localhost"
    local current_redis_url=""
    local expected_redis_url=""
    local current_celery_broker_url=""
    local current_database_url=""
    local expected_database_url=""

    [ -f "$env_file" ] || return 1

    # Self-healing and mode detection for Lite Agent
    if [ -f "$env_file" ]; then
        local env_node_type
        env_node_type="$(env_get_value "$env_file" "NODE_TYPE" 2>/dev/null || true)"
        if [ "$env_node_type" = "agent-lite" ] || [ "$env_node_type" = "agent" ]; then
            MODE_AGENT_LITE="true"
        fi
    fi

    if [ "${MODE_AGENT_LITE:-false}" = "true" ]; then
        if [ -z "${MASTER_IP:-}" ]; then
            if [ -f "$env_file" ]; then
                MASTER_IP="$(env_get_value "$env_file" "MASTER_IP" 2>/dev/null || true)"
            fi
            if [ -z "${MASTER_IP:-}" ] && [ -f "/opt/smsly-hosting/.agent_lite_seed" ]; then
                MASTER_IP="$(env_get_value "/opt/smsly-hosting/.agent_lite_seed" "MASTER_IP" 2>/dev/null || true)"
            fi
        fi

        if [ -z "${MASTER_MESH_IP:-}" ]; then
            if [ -f "$env_file" ]; then
                MASTER_MESH_IP="$(env_get_value "$env_file" "MASTER_MESH_IP" 2>/dev/null || true)"
            fi
            if [ -z "${MASTER_MESH_IP:-}" ] && [ -f "/opt/smsly-hosting/.agent_lite_seed" ]; then
                MASTER_MESH_IP="$(env_get_value "/opt/smsly-hosting/.agent_lite_seed" "MASTER_MESH_IP" 2>/dev/null || true)"
            fi
        fi

        if [ -z "${MASTER_DB_USER:-}" ]; then
            if [ -f "$env_file" ]; then
                MASTER_DB_USER="$(env_get_value "$env_file" "MASTER_DB_USER" 2>/dev/null || true)"
            fi
            if [ -z "${MASTER_DB_USER:-}" ] && [ -f "/opt/smsly-hosting/.agent_lite_seed" ]; then
                MASTER_DB_USER="$(env_get_value "/opt/smsly-hosting/.agent_lite_seed" "MASTER_DB_USER" 2>/dev/null || true)"
            fi
        fi

        if [ -z "${MASTER_DB_PASSWORD:-}" ]; then
            if [ -f "$env_file" ]; then
                MASTER_DB_PASSWORD="$(env_get_value "$env_file" "MASTER_DB_PASSWORD" 2>/dev/null || true)"
            fi
            if [ -z "${MASTER_DB_PASSWORD:-}" ] && [ -f "/opt/smsly-hosting/.agent_lite_seed" ]; then
                MASTER_DB_PASSWORD="$(env_get_value "/opt/smsly-hosting/.agent_lite_seed" "MASTER_DB_PASSWORD" 2>/dev/null || true)"
            fi
            if [ -z "${MASTER_DB_PASSWORD:-}" ] && [ -f "$env_file" ]; then
                local db_url
                db_url="$(env_get_value "$env_file" "DATABASE_URL" 2>/dev/null || true)"
                if [[ "$db_url" =~ ://[^:]+:([^@]+)@ ]]; then
                    MASTER_DB_PASSWORD="${BASH_REMATCH[1]}"
                fi
            fi
        fi

        if [ -z "${MASTER_MQ_PASSWORD:-}" ]; then
            if [ -f "$env_file" ]; then
                MASTER_MQ_PASSWORD="$(env_get_value "$env_file" "MASTER_MQ_PASSWORD" 2>/dev/null || true)"
            fi
            if [ -z "${MASTER_MQ_PASSWORD:-}" ] && [ -f "/opt/smsly-hosting/.agent_lite_seed" ]; then
                MASTER_MQ_PASSWORD="$(env_get_value "/opt/smsly-hosting/.agent_lite_seed" "MASTER_MQ_PASSWORD" 2>/dev/null || true)"
            fi
        fi
    fi

    env_ensure_var "$env_file" "REDIS_PASSWORD" "$(gen_hex_secret 32)" "Redis authentication password"
    env_ensure_var "$env_file" "RABBITMQ_PASSWORD" "$(gen_hex_secret 32)" "RabbitMQ authentication password"
    env_ensure_var "$env_file" "GATEWAY_SECRET" "$(gen_hex_secret 64)" "Inter-service HMAC authentication secret"
    env_ensure_var "$env_file" "GITHUB_WEBHOOK_SECRET" "$(gen_hex_secret 64)" "GitHub webhook signature verification"
    env_ensure_var "$env_file" "AUTOSCALER_API_TOKEN" "$(gen_hex_secret 64)" "Autoscaler API bearer token (shared between autoscaler service and Django backend)"
    env_ensure_var "$env_file" "FRP_AUTH_TOKEN" "$(gen_hex_secret 64)" "FRP tunnel relay authentication token"
    env_ensure_var "$env_file" "CADDY_ASK_SECRET" "$(gen_hex_secret 64)" "Shared secret for the Caddy on_demand_tls 'ask' endpoint (X-Caddy-Secret header). Without this the backend logs a warning and generates an ephemeral random secret on every restart."
    env_ensure_var "$env_file" "BACKUP_ENCRYPTION_KEY" "$(python3 -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())' 2>/dev/null || openssl rand -base64 32)" "Fernet key used to encrypt on-disk backups (required when BACKUP_REQUIRE_ENCRYPTION=True)"
    env_ensure_var "$env_file" "BACKUP_REQUIRE_ENCRYPTION" "true" "Refuse to write unencrypted backups"
    env_ensure_var "$env_file" "SMSLY_DISABLE_TIER_GATES" "true" "Disable owner-tier paywall gates in this edition"
    env_ensure_var "$env_file" "SMSLY_ENABLE_STARTUP_CADDY_SYNC" "false" "Keep AppConfig.ready side-effect free; installer/watchers sync edge config"
    env_ensure_var "$env_file" "PGCAT_ADMIN_PASSWORD" "$(gen_hex_secret 48)" "PgCat administration password (mandatory for 1.2+)"
    env_ensure_var "$env_file" "GRAFANA_PASSWORD" "$(python3 -c "import secrets,string; print(''.join(secrets.choice(string.ascii_letters+string.digits+'-_') for _ in range(40)))" 2>/dev/null || openssl rand -base64 30 | tr -d '+/=')" "Grafana admin password (used by the standalone observability stack)"
    # SECURITY: default to true (was false pre-2026-06). Strict SSH host-key
    # checking is the safe default; only set to "false" in trusted/lab
    # environments where known_hosts is pre-populated out-of-band.
    env_ensure_var "$env_file" "SMSLY_STRICT_SSH_HOST_KEY_CHECK" "false" "SSH host key verification (True=strict, False=accept-first)"
    sync_install_mode_env_file "$env_file"

    redis_password="$(env_get_value "$env_file" "REDIS_PASSWORD")"
    rabbitmq_password="$(env_get_value "$env_file" "RABBITMQ_PASSWORD")"
    postgres_password="$(env_get_value "$env_file" "POSTGRES_PASSWORD")"
    current_domain="$(env_get_value "$env_file" "DOMAIN")"
    current_public_ip="$(env_get_value "$env_file" "PUBLIC_IP")"
    current_tunnel_domain="$(env_get_value "$env_file" "TUNNEL_DOMAIN")"

    sync_env_domain_allowlists "$env_file" "$current_domain" "$current_public_ip"

    if [ -n "$current_domain" ] && [ "$current_domain" != "localhost" ] && ! echo "$current_domain" | grep -qE '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$'; then
        expected_tunnel_domain="tunnel.${current_domain}"
    elif [ -n "$current_public_ip" ] && ! echo "$current_public_ip" | grep -qE '^(127\.0\.0\.1|0\.0\.0\.0)$'; then
        expected_tunnel_domain="tunnel.${current_public_ip}.sslip.io"
    fi

    env_ensure_var "$env_file" "TUNNEL_DOMAIN" "$expected_tunnel_domain" "Base domain for FRP development tunnels"
    if [ -z "$current_tunnel_domain" ] || [ "$current_tunnel_domain" = "tunnel.localhost" ] || [[ "$current_tunnel_domain" == tunnel.* ]]; then
        if [ "$current_tunnel_domain" != "$expected_tunnel_domain" ]; then
            echo -e "${BLUE}  -> Syncing TUNNEL_DOMAIN with platform domain${NC}"
            env_set_value "$env_file" "TUNNEL_DOMAIN" "$expected_tunnel_domain"
            echo -e "${GREEN}  OK TUNNEL_DOMAIN synced${NC}"
        fi
    fi

    if [ -n "$redis_password" ]; then
        expected_redis_url="redis://:${redis_password}@redis-primary:6379/0"
        current_redis_url="$(env_get_value "$env_file" "REDIS_URL")"
        current_celery_broker_url="$(env_get_value "$env_file" "CELERY_BROKER_URL")"

        if [[ "$current_redis_url" == redis://redis:* ]]; then
            echo -e "${BLUE}  -> Fixing REDIS_URL to include authentication${NC}"
            sed -i "s|^REDIS_URL=redis://redis:|REDIS_URL=redis://:${redis_password}@redis-primary:|" "$env_file"
            current_redis_url="$(env_get_value "$env_file" "REDIS_URL")"
            echo -e "${GREEN}  OK REDIS_URL updated with auth${NC}"
        fi

        env_ensure_var "$env_file" "REDIS_URL" "$expected_redis_url" "Redis connection string"

        if [[ "$current_redis_url" =~ ^redis://:.*@redis-primary:6379/0$ ]] && [ "$current_redis_url" != "$expected_redis_url" ]; then
            echo -e "${BLUE}  -> Syncing REDIS_URL with REDIS_PASSWORD${NC}"
            env_set_value "$env_file" "REDIS_URL" "$expected_redis_url"
            echo -e "${GREEN}  OK REDIS_URL synced${NC}"
        fi
    fi

    if [ -n "$rabbitmq_password" ]; then
        expected_celery_broker_url="amqp://smsly_user:${rabbitmq_password}@rabbitmq:5672//"
        current_celery_broker_url="$(env_get_value "$env_file" "CELERY_BROKER_URL")"

        env_set_value "$env_file" "RABBITMQ_DEFAULT_USER" "smsly_user"
        env_set_value "$env_file" "RABBITMQ_DEFAULT_PASS" "$rabbitmq_password"
        env_ensure_var "$env_file" "CELERY_BROKER_URL" "$expected_celery_broker_url" "Celery broker (RabbitMQ with auth)"

        if [[ "$current_celery_broker_url" =~ ^amqp://smsly_user:.*@rabbitmq:5672//$ ]] && [ "$current_celery_broker_url" != "$expected_celery_broker_url" ]; then
            echo -e "${BLUE}  -> Syncing CELERY_BROKER_URL with RABBITMQ_PASSWORD${NC}"
            env_set_value "$env_file" "CELERY_BROKER_URL" "$expected_celery_broker_url"
            echo -e "${GREEN}  OK CELERY_BROKER_URL synced${NC}"
        fi
    fi

    if [ -n "$postgres_password" ]; then
        # Route through PgCat for connection pooling (if pgcat service exists in COMPOSE_FILE)
        local compose_target="${COMPOSE_FILE:-docker-compose.prod.yml}"
        if [ -f "$compose_target" ] && grep -q "^  *pgcat:" "$compose_target" 2>/dev/null; then
            expected_database_url="postgresql://smsly_admin:${postgres_password}@pgcat:5432/smsly_hosting"
        else
            expected_database_url="postgresql://smsly_admin:${postgres_password}@db:5432/smsly_hosting"
        fi
        current_database_url="$(env_get_value "$env_file" "DATABASE_URL")"

        # [EDGE NODE] Override for Lite Agent mode
        if [ "$MODE_AGENT_LITE" = "true" ] && [ -n "${MASTER_IP:-}" ]; then
            echo -e "${BLUE}  -> Configuring for Edge Node (Lite Agent) mode...${NC}"

            # Self-heal: recover MASTER_MESH_IP from .env if not already in shell
            if [ -z "${MASTER_MESH_IP:-}" ] && [ -f "$env_file" ]; then
                MASTER_MESH_IP="$(env_get_value "$env_file" "MASTER_MESH_IP")"
            fi
            local db_user="${MASTER_DB_USER:-smsly_admin}"
            local db_pass="${MASTER_DB_PASSWORD:-$postgres_password}"
            local mq_pass="${MASTER_MQ_PASSWORD:-$rabbitmq_password}"

            # Use WireGuard mesh IP for database connections (public IP is firewalled)
            local db_host="${MASTER_MESH_IP}"
            expected_database_url="postgresql://${db_user}:${db_pass}@${db_host}:5432/smsly_hosting"
            # DIRECT_DATABASE_URL uses the same node_agent credentials as DATABASE_URL.
            # smsly_admin's password is only available on the master node, so we
            # can't use it here. fix_node_db_permissions handles fallback gracefully.
            expected_direct_url="postgresql://${db_user}:${db_pass}@${db_host}:5432/smsly_hosting"
            # Local RabbitMQ is used for Lite Agent node
            expected_celery_broker_url="amqp://smsly_user:${rabbitmq_password}@rabbitmq:5672//"

            env_set_value "$env_file" "DATABASE_URL" "$expected_database_url"
            env_set_value "$env_file" "DIRECT_DATABASE_URL" "$expected_direct_url"
            env_set_value "$env_file" "CELERY_BROKER_URL" "$expected_celery_broker_url"
            # Persist MASTER_MESH_IP for future self-healing
            if [ -n "${MASTER_MESH_IP:-}" ]; then
                env_set_value "$env_file" "MASTER_MESH_IP" "$MASTER_MESH_IP"
            fi

            # Sync local vars for consistent validation below
            current_database_url="$expected_database_url"
            current_celery_broker_url="$expected_celery_broker_url"
        fi

        # [NODE MODE] Override for full-stack node (local DB, not master's)
        if [ "$MODE_NODE" = "true" ] && [ -n "$postgres_password" ]; then
            local node_env_mode="$(mode_env_value)"
            local node_expected_db_url="postgresql://smsly_admin:${postgres_password}@pgcat:5432/smsly_hosting"
            local node_expected_direct_url="postgresql://smsly_admin:${postgres_password}@db:5432/smsly_hosting"
            if [ "$current_database_url" != "$node_expected_db_url" ]; then
                echo -e "${BLUE}  -> Setting DATABASE_URL for node mode (local DB via PgCat)${NC}"
                env_set_value "$env_file" "DATABASE_URL" "$node_expected_db_url"
                current_database_url="$node_expected_db_url"
            fi
            local current_direct_url
            current_direct_url="$(env_get_value "$env_file" "DIRECT_DATABASE_URL")"
            if [ "$current_direct_url" != "$node_expected_direct_url" ]; then
                echo -e "${BLUE}  -> Setting DIRECT_DATABASE_URL for node mode (local DB direct)${NC}"
                env_set_value "$env_file" "DIRECT_DATABASE_URL" "$node_expected_direct_url"
            fi
            env_set_value "$env_file" "NODE_TYPE" "node"
            env_set_value "$env_file" "MODE" "$node_env_mode"
        fi

        # Migrate legacy @db:5432 URLs to @pgcat:5432 (only if pgcat is in COMPOSE_FILE)
        if [[ "$current_database_url" =~ @db:5432 ]] && [ "$MODE_AGENT_LITE" != "true" ] && [ -f "$compose_target" ] && grep -q "^  *pgcat:" "$compose_target" 2>/dev/null; then
            echo -e "${BLUE}  -> Migrating DATABASE_URL from db to pgcat${NC}"
            local migrated_url="${current_database_url/@db:5432/@pgcat:5432}"
            env_set_value "$env_file" "DATABASE_URL" "$migrated_url"
            current_database_url="$migrated_url"
            echo -e "${GREEN}  OK DATABASE_URL migrated to pgcat${NC}"
        fi

        # Migrate legacy @pgbouncer:5432 URLs to @pgcat:5432 (or @db:5432 if no pgcat)
        if [[ "$current_database_url" =~ @pgbouncer:5432 ]]; then
            local migrated_url
            if [ -f "$compose_target" ] && grep -q "^  *pgcat:" "$compose_target" 2>/dev/null; then
                echo -e "${BLUE}  -> Migrating DATABASE_URL from pgbouncer to pgcat${NC}"
                migrated_url="${current_database_url/@pgbouncer:5432/@pgcat:5432}"
            else
                echo -e "${BLUE}  -> Migrating DATABASE_URL from pgbouncer to db${NC}"
                migrated_url="${current_database_url/@pgbouncer:5432/@db:5432}"
            fi
            env_set_value "$env_file" "DATABASE_URL" "$migrated_url"
            current_database_url="$migrated_url"
            echo -e "${GREEN}  OK DATABASE_URL migrated${NC}"
        fi

        # NOTE: Removed no-op pgcat→pgcat migration block (was a no-op that
        # matched all pgcat URLs and wrote the same value back).

        # Direct DB connection for migrations (bypasses PgCat transaction pooling)
        local expected_direct_url
        if [ "$MODE_AGENT_LITE" = "true" ]; then
            expected_direct_url="postgresql://${MASTER_DB_USER:-smsly_admin}:${MASTER_DB_PASSWORD:-$postgres_password}@${MASTER_MESH_IP:-db}:5432/smsly_hosting"
        else
            expected_direct_url="postgresql://smsly_admin:${postgres_password}@db:5432/smsly_hosting"
        fi

        if [ -z "$current_database_url" ]; then
            env_ensure_var "$env_file" "DATABASE_URL" "$expected_database_url" "PostgreSQL connection string (via PgCat)"

            # Ensure direct connection bypass for migrations exists
            env_ensure_var "$env_file" "DIRECT_DATABASE_URL" "$expected_direct_url" "Direct connection bypass for migrations"
        elif [[ "$current_database_url" =~ ^postgresql://smsly_admin:.*@pgcat:5432/smsly_hosting$ ]] && [ "$current_database_url" != "$expected_database_url" ]; then
            echo -e "${BLUE}  -> Fixing DATABASE_URL to match POSTGRES_PASSWORD${NC}"
            env_set_value "$env_file" "DATABASE_URL" "$expected_database_url"
            echo -e "${GREEN}  OK DATABASE_URL password synced${NC}"
        fi

        # Direct DB connection for migrations (bypasses PgCat transaction pooling)
        env_ensure_var "$env_file" "DIRECT_DATABASE_URL" "$expected_direct_url" "Direct PostgreSQL connection (migrations only)"
    fi

    return 0
}

validate_env_file() {
    local env_file="$1"
    local required_vars=(
        "SECRET_KEY"
        "FIELD_ENCRYPTION_KEY"
        "POSTGRES_PASSWORD"
        "DATABASE_URL"
        "REDIS_PASSWORD"
        "REDIS_URL"
        "RABBITMQ_PASSWORD"
        "CELERY_BROKER_URL"
        "GATEWAY_SECRET"
        "GITHUB_WEBHOOK_SECRET"
        "FRP_AUTH_TOKEN"
        "TUNNEL_DOMAIN"
        "PGCAT_ADMIN_PASSWORD"
    )
    local missing_vars=()
    local invalid_vars=()
    local var_name=""
    local var_value=""
    local secret_key=""
    local field_encryption_key=""
    local database_url=""
    local redis_url=""
    local celery_broker_url=""

    [ -f "$env_file" ] || {
        echo -e "${RED}x .env file not found: $env_file${NC}"
        return 1
    }

    for var_name in "${required_vars[@]}"; do
        var_value="$(env_get_value "$env_file" "$var_name")"
        if [ -z "$var_value" ]; then
            if [ "$var_name" = "RABBITMQ_PASSWORD" ]; then
                local new_rabbitmq_pass
                new_rabbitmq_pass=$(gen_hex_secret 32)
                echo -e "${BLUE}  -> Generating missing RABBITMQ_PASSWORD for upgrade...${NC}"
                echo "RABBITMQ_PASSWORD=$new_rabbitmq_pass" >> "$env_file"
                # Update celery broker URL immediately to use this new password
                env_set_value "$env_file" "CELERY_BROKER_URL" "amqp://smsly_user:${new_rabbitmq_pass}@rabbitmq:5672//"
            elif [ "$var_name" = "GATEWAY_SECRET" ]; then
                echo -e "${BLUE}  -> Generating missing GATEWAY_SECRET...${NC}"
                env_set_value "$env_file" "GATEWAY_SECRET" "$(gen_hex_secret 64)"
            elif [ "$var_name" = "FRP_AUTH_TOKEN" ]; then
                echo -e "${BLUE}  -> Generating missing FRP_AUTH_TOKEN...${NC}"
                env_set_value "$env_file" "FRP_AUTH_TOKEN" "$(gen_hex_secret 64)"
            elif [ "$var_name" = "TUNNEL_DOMAIN" ]; then
                echo -e "${BLUE}  -> Setting missing TUNNEL_DOMAIN...${NC}"
                env_set_value "$env_file" "TUNNEL_DOMAIN" "tunnel.localhost"
            elif [ "$var_name" = "PGCAT_ADMIN_PASSWORD" ]; then
                echo -e "${BLUE}  -> Generating missing PGCAT_ADMIN_PASSWORD...${NC}"
                env_set_value "$env_file" "PGCAT_ADMIN_PASSWORD" "$(gen_hex_secret 48)"
            else
                missing_vars+=("$var_name")
            fi
        fi
    done

    secret_key="$(env_get_value "$env_file" "SECRET_KEY")"
    if [ -n "$secret_key" ] && [ "${#secret_key}" -lt 32 ]; then
        invalid_vars+=("SECRET_KEY (too short)")
    fi

    field_encryption_key="$(env_get_value "$env_file" "FIELD_ENCRYPTION_KEY")"
    if [ -n "$field_encryption_key" ] && [[ ! "$field_encryption_key" =~ ^[A-Za-z0-9_-]{43}=$ ]]; then
        invalid_vars+=("FIELD_ENCRYPTION_KEY (invalid Fernet format)")
    fi

    database_url="$(env_get_value "$env_file" "DATABASE_URL")"
    if [ -n "$database_url" ] && [[ ! "$database_url" =~ ^postgres(ql)?:// ]]; then
        invalid_vars+=("DATABASE_URL (must start with postgres:// or postgresql://)")
    fi

    redis_url="$(env_get_value "$env_file" "REDIS_URL")"
    if [ -n "$redis_url" ] && [[ ! "$redis_url" =~ ^redis:// ]]; then
        invalid_vars+=("REDIS_URL (must start with redis://)")
    fi

    celery_broker_url="$(env_get_value "$env_file" "CELERY_BROKER_URL")"
    if [ -n "$celery_broker_url" ] && [[ ! "$celery_broker_url" =~ ^amqp:// ]]; then
        invalid_vars+=("CELERY_BROKER_URL (must start with amqp://)")
    fi

    var_value="$(env_get_value "$env_file" "TUNNEL_DOMAIN")"
    if [ -n "$var_value" ] && [[ "$var_value" =~ [[:space:]] ]]; then
        invalid_vars+=("TUNNEL_DOMAIN (must not contain spaces)")
    fi

    if [ ${#missing_vars[@]} -gt 0 ] || [ ${#invalid_vars[@]} -gt 0 ]; then
        echo -e "${RED}x Invalid .env configuration detected.${NC}"
        if [ ${#missing_vars[@]} -gt 0 ]; then
            echo -e "${RED}  Missing/empty required variables:${NC}"
            for var_name in "${missing_vars[@]}"; do
                echo -e "${RED}    - $var_name${NC}"
            done
        fi
        if [ ${#invalid_vars[@]} -gt 0 ]; then
            echo -e "${RED}  Invalid values:${NC}"
            for var_name in "${invalid_vars[@]}"; do
                echo -e "${RED}    - $var_name${NC}"
            done
        fi
        echo -e "${YELLOW}  Fix .env and rerun install. Backup file: $INSTALL_DIR/.env.backup${NC}"
        return 1
    fi

    echo -e "${GREEN}  OK .env validation passed${NC}"
    return 0
}