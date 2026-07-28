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
        timeout -k 5 300 docker compose -f "$COMPOSE_FILE" exec -T \
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

incoming_domain = normalize_platform_domain(os.environ.get("SMSLY_SYNC_DOMAIN", ""))
db_has_real_domain = bool(original_domain) and original_domain not in ("", "localhost")
incoming_is_ip_or_empty = not incoming_domain
if db_has_real_domain and incoming_is_ip_or_empty:
    print(f"[sync] Preserving existing DB domain '{original_domain}' (incoming was empty/IP)")
else:
    cfg.domain = incoming_domain

_incoming_use_ssl = parse_bool(os.environ.get("SMSLY_SYNC_USE_SSL", "false"))
_db_already_has_ssl = bool(cfg.use_ssl)
if _incoming_use_ssl:
    cfg.use_ssl = True
elif not _db_already_has_ssl:
    cfg.use_ssl = False

_incoming_wildcard = parse_bool(os.environ.get("SMSLY_SYNC_WILDCARD", "false"))
_db_already_has_wildcard = bool(cfg.wildcard_subdomains)
if _incoming_wildcard:
    cfg.wildcard_subdomains = True
elif not _db_already_has_wildcard:
    cfg.wildcard_subdomains = False
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

    DOMAIN_SYNC_UPDATED_COUNT="$(printf '%s' "$sync_json" | python3 -c "import json,sys; print(json.load(sys.stdin).get('updated_service_domains', 0))"  || echo 0)"
    DOMAIN_SYNC_REDEPLOY_REQUIRED="$(printf '%s' "$sync_json" | python3 -c "import json,sys; print(1 if json.load(sys.stdin).get('redeploy_required') else 0)"  || echo 0)"
    DOMAIN_SYNC_SERVICE_IDS="$(printf '%s' "$sync_json" | python3 -c "import json,sys; print(','.join(json.load(sys.stdin).get('service_ids', [])))"  || true)"

    echo -e "${GREEN}  ✓ PlatformConfig synced: domain=$(printf '%s' "$sync_json" | python3 -c "import json,sys; print(json.load(sys.stdin).get('domain', ''))" )${NC}"
    if [ "${DOMAIN_SYNC_UPDATED_COUNT:-0}" -gt 0 ]; then
        echo -e "${GREEN}  ✓ Rewrote ${DOMAIN_SYNC_UPDATED_COUNT} existing service public domain(s)${NC}"
    fi

    _effective_domain="$(printf '%s' "$sync_json" | python3 -c "
import json,sys
d = json.load(sys.stdin)
print(d.get('domain', '') or '')
"  || true)"
    _env_domain="$(env_get_value "$env_file" "DOMAIN")"
    _env_use_ssl="$(env_get_value "$env_file" "USE_SSL")"
    _env_wildcard="$(env_get_value "$env_file" "WILDCARD_SUBDOMAINS")"
    _db_use_ssl="$(printf '%s' "$sync_json" | python3 -c "import json,sys; print('true' if json.load(sys.stdin).get('use_ssl') else 'false')" )"
    _db_wildcard="$(printf '%s' "$sync_json" | python3 -c "import json,sys; print('true' if json.load(sys.stdin).get('wildcard_subdomains') else 'false')" )"
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

    local backend_container
    backend_container="$(resolve_container_target "smsly-hosting-backend-1")"
    local backend_state
    backend_state="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$backend_container"  || echo 'missing')"
    if [ "$backend_state" != "healthy" ] && [ "$backend_state" != "running" ]; then
        echo -e "${YELLOW}  ⚠ Backend container ($backend_container) not ready (state=$backend_state). Waiting 15s...${NC}" >&2
        sleep 15
        backend_state="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$backend_container"  || echo 'missing')"
        if [ "$backend_state" != "healthy" ] && [ "$backend_state" != "running" ]; then
            echo -e "${RED}  ✗ Backend container still not ready after wait. Skipping redeploy.${NC}" >&2
            return 1
        fi
    fi

    timeout -k 5 300 docker compose -f "$COMPOSE_FILE" exec -T \
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
except Exception as exc:
    print(f"WARN: {exc}")
    traceback.print_exc()
PY
}
