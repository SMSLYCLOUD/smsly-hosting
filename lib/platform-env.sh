apply_env_platform_overrides() {
    local env_file="$1"
    local changed=false
    local current_domain="" current_use_ssl="" current_acme_email="" current_wildcard="" current_cf_token="" current_public_ip="" current_registry_bind=""
    local desired_domain="" desired_use_ssl="" desired_acme_email="" desired_wildcard="" desired_cf_token="" desired_public_ip="" desired_registry_bind=""

    [ -f "$env_file" ] || return 0

    current_domain="$(env_get_value "$env_file" "DOMAIN")"
    current_use_ssl="$(env_get_value "$env_file" "USE_SSL")"
    current_acme_email="$(env_get_value "$env_file" "ACME_EMAIL")"
    current_wildcard="$(env_get_value "$env_file" "WILDCARD_SUBDOMAINS")"
    current_cf_token="$(env_get_value "$env_file" "CLOUDFLARE_API_TOKEN")"
    current_public_ip="$(env_get_value "$env_file" "PUBLIC_IP")"
    current_registry_bind="$(env_get_value "$env_file" "REGISTRY_PUBLIC_BIND_IP")"

    if [ "${DOMAIN+x}" = "x" ]; then
        desired_domain="${DOMAIN}"
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

    # Registry public bind: the compose default is a hardcoded IP. When
    # .env has no override — or the override points at ANOTHER host's IP
    # (cloned .env during migration) — the registry port bind fails with
    # "cannot assign requested address" and the whole install dies. Pin it
    # to this host's detected public IP in both cases.
    desired_registry_bind="$current_registry_bind"
    if [ -z "$desired_registry_bind" ] || ! _registry_bind_ip_is_local "$desired_registry_bind"; then
        if [ -n "$desired_public_ip" ] && _registry_bind_ip_is_local "$desired_public_ip"; then
            desired_registry_bind="$desired_public_ip"
        else
            # Detection failed or disagrees with local interfaces — fall
            # back to the first local non-loopback IPv4 so the bind always
            # targets an address this host holds.
            desired_registry_bind="$(hostname -I 2>/dev/null | tr ' ' '\n' | grep -E '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$' | grep -v '^127\.' | head -1 || true)"
        fi
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
    if [ -n "$desired_registry_bind" ] && [ "$desired_registry_bind" != "$current_registry_bind" ]; then
        env_set_value "$env_file" "REGISTRY_PUBLIC_BIND_IP" "$desired_registry_bind"
        changed=true
    fi

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


# True when $1 is an IPv4 address assigned to this host's interfaces.
_registry_bind_ip_is_local() {
    local ip="$1"
    [ -n "$ip" ] || return 1
    hostname -I 2>/dev/null | tr ' ' '\n' | grep -qxF "$ip"
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

    if [ -f "$env_file" ]; then
        local env_node_type
        env_node_type="$(env_get_value "$env_file" "NODE_TYPE"  || true)"
        if [ "$env_node_type" = "agent-lite" ] || [ "$env_node_type" = "agent" ]; then
            MODE_AGENT_LITE="true"
        fi
    fi

    if [ "${MODE_AGENT_LITE:-false}" = "true" ]; then
        if [ -z "${MASTER_IP:-}" ]; then
            if [ -f "$env_file" ]; then
                MASTER_IP="$(env_get_value "$env_file" "MASTER_IP"  || true)"
            fi
            if [ -z "${MASTER_IP:-}" ] && [ -f "/opt/smsly-hosting/.agent_lite_seed" ]; then
                MASTER_IP="$(env_get_value "/opt/smsly-hosting/.agent_lite_seed" "MASTER_IP"  || true)"
            fi
        fi

        if [ -z "${MASTER_MESH_IP:-}" ]; then
            if [ -f "$env_file" ]; then
                MASTER_MESH_IP="$(env_get_value "$env_file" "MASTER_MESH_IP"  || true)"
            fi
            if [ -z "${MASTER_MESH_IP:-}" ] && [ -f "/opt/smsly-hosting/.agent_lite_seed" ]; then
                MASTER_MESH_IP="$(env_get_value "/opt/smsly-hosting/.agent_lite_seed" "MASTER_MESH_IP"  || true)"
            fi
        fi

        if [ -z "${MASTER_DB_USER:-}" ]; then
            if [ -f "$env_file" ]; then
                MASTER_DB_USER="$(env_get_value "$env_file" "MASTER_DB_USER"  || true)"
            fi
            if [ -z "${MASTER_DB_USER:-}" ] && [ -f "/opt/smsly-hosting/.agent_lite_seed" ]; then
                MASTER_DB_USER="$(env_get_value "/opt/smsly-hosting/.agent_lite_seed" "MASTER_DB_USER"  || true)"
            fi
        fi

        if [ -z "${MASTER_DB_PASSWORD:-}" ]; then
            if [ -f "$env_file" ]; then
                MASTER_DB_PASSWORD="$(env_get_value "$env_file" "MASTER_DB_PASSWORD"  || true)"
            fi
            if [ -z "${MASTER_DB_PASSWORD:-}" ] && [ -f "/opt/smsly-hosting/.agent_lite_seed" ]; then
                MASTER_DB_PASSWORD="$(env_get_value "/opt/smsly-hosting/.agent_lite_seed" "MASTER_DB_PASSWORD"  || true)"
            fi
            if [ -z "${MASTER_DB_PASSWORD:-}" ] && [ -f "$env_file" ]; then
                local db_url
                db_url="$(env_get_value "$env_file" "DATABASE_URL"  || true)"
                if [[ "$db_url" =~ ://[^:]+:([^@]+)@ ]]; then
                    MASTER_DB_PASSWORD="${BASH_REMATCH[1]}"
                fi
            fi
        fi

        if [ -z "${MASTER_MQ_PASSWORD:-}" ]; then
            if [ -f "$env_file" ]; then
                MASTER_MQ_PASSWORD="$(env_get_value "$env_file" "MASTER_MQ_PASSWORD"  || true)"
            fi
            if [ -z "${MASTER_MQ_PASSWORD:-}" ] && [ -f "/opt/smsly-hosting/.agent_lite_seed" ]; then
                MASTER_MQ_PASSWORD="$(env_get_value "/opt/smsly-hosting/.agent_lite_seed" "MASTER_MQ_PASSWORD"  || true)"
            fi
        fi
    fi

    env_ensure_var "$env_file" "SECRET_KEY" "$(python3 -c "import secrets,string; print(''.join(secrets.choice(string.ascii_letters+string.digits) for _ in range(50)))"  || openssl rand -hex 32)" "Django SECRET_KEY (minimum 32 chars)"
    env_ensure_var "$env_file" "FIELD_ENCRYPTION_KEY" "$(python3 -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'  || python3 -c 'import secrets,base64; print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())')" "Fernet key for Django field-level encryption"
    env_ensure_var "$env_file" "POSTGRES_PASSWORD" "$(gen_hex_secret 32)" "PostgreSQL admin password"
    env_ensure_var "$env_file" "REDIS_PASSWORD" "$(gen_hex_secret 32)" "Redis authentication password"
    env_ensure_var "$env_file" "RABBITMQ_PASSWORD" "$(gen_hex_secret 32)" "RabbitMQ authentication password"
    env_ensure_var "$env_file" "GATEWAY_SECRET" "$(gen_hex_secret 64)" "Inter-service HMAC authentication secret"
    env_ensure_var "$env_file" "GITHUB_WEBHOOK_SECRET" "$(gen_hex_secret 64)" "GitHub webhook signature verification"
    env_ensure_var "$env_file" "AUTOSCALER_API_TOKEN" "$(gen_hex_secret 64)" "Autoscaler API bearer token (shared between autoscaler service and Django backend)"
    env_ensure_var "$env_file" "FRP_AUTH_TOKEN" "$(gen_hex_secret 64)" "FRP tunnel relay authentication token"
    env_ensure_var "$env_file" "CADDY_ASK_SECRET" "$(gen_hex_secret 64)" "Shared secret for the Caddy on_demand_tls 'ask' endpoint (X-Caddy-Secret header). Without this the backend logs a warning and generates an ephemeral random secret on every restart."
    env_ensure_var "$env_file" "PATRONI_SUPERUSER_PASSWORD" "$(gen_hex_secret 32)" "Patroni superuser password for HA cluster"
    env_ensure_var "$env_file" "NODE_SECURITY" "1" "Enable full hardening stack (auditd, kernel, docker, gVisor/Kata)"
    env_ensure_var "$env_file" "NODE_OBSERVABILITY" "1" "Enable node metrics stack (cadvisor, node-exporter, docker-labels)"
    env_ensure_var "$env_file" "NODE_CROWDSEC" "1" "Enable CrowdSec WAF/IPS"
    env_ensure_var "$env_file" "NODE_FALCO" "1" "Enable Falco runtime security"
    env_ensure_var "$env_file" "NODE_SPIRE" "1" "Enable SPIRE mTLS"
    env_ensure_var "$env_file" "NODE_LOG_SHIPPING" "1" "Ship node access logs to master CrowdSec (security path, independent of observability metrics)"
    # WAF shadow is default-on at >=8GB RAM, off below (the shadow costs
    # ~1-2GB real). Fill-if-absent so an explicit operator value survives
    # updates; mirrors the fresh_config sizing ladder.
    if [ -z "$(env_get_value "$env_file" "OPENAPPSEC_ENABLED")" ]; then
        local _waf_ram_mb=""
        _waf_ram_mb="$(free -m 2>/dev/null | awk '/^Mem:/{print $2}')"
        [ -n "$_waf_ram_mb" ] || _waf_ram_mb=8192
        if [ "$_waf_ram_mb" -ge 8192 ]; then
            env_set_value "$env_file" "OPENAPPSEC_ENABLED" "1"
        else
            env_set_value "$env_file" "OPENAPPSEC_ENABLED" "0"
        fi
    fi
    env_ensure_var "$env_file" "OPENAPPSEC_MODE" "detect-learn" "WAF enforcement mode (detect-learn shadow vs prevent enforce); set from Settings → Security Scanning"
    env_ensure_var "$env_file" "BACKUP_ENCRYPTION_KEY" "$(python3 -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'  || python3 -c 'import secrets,base64; print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())')" "Fernet key used to encrypt on-disk backups (required when BACKUP_REQUIRE_ENCRYPTION=True)"
    env_ensure_var "$env_file" "BACKUP_REQUIRE_ENCRYPTION" "true" "Refuse to write unencrypted backups"
    env_ensure_var "$env_file" "SMSLY_DISABLE_TIER_GATES" "true" "Disable owner-tier paywall gates in this edition"
    env_ensure_var "$env_file" "SMSLY_ENABLE_STARTUP_CADDY_SYNC" "false" "Keep AppConfig.ready side-effect free; installer/watchers sync edge config"
    env_ensure_var "$env_file" "PGCAT_ADMIN_PASSWORD" "$(gen_hex_secret 48)" "PgCat administration password (mandatory for 1.2+)"
    env_ensure_var "$env_file" "GRAFANA_PASSWORD" "$(python3 -c "import secrets,string; print(''.join(secrets.choice(string.ascii_letters+string.digits+'-_') for _ in range(40)))"  || openssl rand -base64 30 | tr -d '+/=')" "Grafana admin password (used by the standalone observability stack)"
    env_ensure_var "$env_file" "REPLICATION_PASSWORD" "$(gen_hex_secret 32)" "PostgreSQL streaming replication password"
    env_ensure_var "$env_file" "SENTINEL_PASSWORD" "$(gen_hex_secret 32)" "Redis Sentinel authentication password"
    env_ensure_var "$env_file" "SENTINEL_SERVICE_NAME" "mymaster" "Redis Sentinel service name"
    # Auto-detect sentinel containers and populate SENTINEL_HOSTS if empty.
    # Sentinel containers are named smsly-redis-sentinel-{1,2,3} and listen
    # on port 26379.  Without this, the backend falls back to direct
    # redis-primary connection which breaks after sentinel failover.
    local current_sentinel_hosts
    current_sentinel_hosts="$(env_get_value "$env_file" "SENTINEL_HOSTS")"
    if [ -z "$current_sentinel_hosts" ]; then
        local detected_sentinels=""
        local _si
        for _si in 1 2 3; do
            if docker ps --format '{{.Names}}' 2>/dev/null | grep -q "smsly-redis-sentinel-${_si}$"; then
                if [ -n "$detected_sentinels" ]; then
                    detected_sentinels="${detected_sentinels},"
                fi
                detected_sentinels="${detected_sentinels}smsly-redis-sentinel-${_si}:26379"
            fi
        done
        if [ -n "$detected_sentinels" ]; then
            echo -e "${BLUE}  -> Auto-detected Redis Sentinels: ${detected_sentinels}${NC}"
            env_set_value "$env_file" "SENTINEL_HOSTS" "$detected_sentinels"
            echo -e "${GREEN}  OK SENTINEL_HOSTS set${NC}"
        fi
    fi
    env_ensure_var "$env_file" "REGISTRY_HTTP_SECRET" "$(gen_hex_secret 32)" "Docker registry HTTP secret"
    env_ensure_var "$env_file" "SMSLY_STRICT_SSH_HOST_KEY_CHECK" "false" "SSH host key verification (True=strict, False=accept-first)"
    # DB HA mode + compose profiles: without COMPOSE_PROFILES the profiled
    # db/postgres services are never created and every backend crashes with
    # "could not translate host name db" (2026-09-10 fresh-install incident).
    # Default is full (run everything): local-ha|patroni|external + medium
    # (observability) + full (Falco, SPIRE servers, apt-cacher, verdaccio).
    local _db_ha_mode=""
    _db_ha_mode="$(env_get_value "$env_file" "DB_HA_ENABLED")"
    [ -n "$_db_ha_mode" ] || _db_ha_mode="local-ha"
    env_ensure_var "$env_file" "DB_HA_ENABLED" "$_db_ha_mode" "Database HA mode: local-ha | patroni | external"
    env_ensure_var "$env_file" "COMPOSE_PROFILES" "${_db_ha_mode},medium,full" "Compose profiles to activate (DB mode + observability + full stack)"
    # Backfill older installs that predate the full default (local-ha or
    # local-ha,medium): ensure the current DB mode + medium + full are
    # present, and drop any STALE db-mode token (local-ha|patroni|external)
    # so two postgres stacks never start side by side (haproxy :7000
    # would clash with frps :7000). Idempotent, case-insensitive.
    local _prof_cur="" _prof_new=""
    _prof_cur="$(env_get_value "$env_file" "COMPOSE_PROFILES")"
    if command -v python3 >/dev/null 2>&1; then
        _prof_new="$(DB_MODE="$_db_ha_mode" CUR_PROF="$_prof_cur" python3 -c '
import os
mode = os.environ.get("DB_MODE", "local-ha").strip() or "local-ha"
cur = os.environ.get("CUR_PROF", "")
db_modes = {"local-ha", "patroni", "external"}
seen = set()
out = []
for tok in [t.strip() for t in cur.split(",")]:
    if not tok:
        continue
    low = tok.lower()
    if low in db_modes and low != mode.lower():
        continue
    if low not in seen:
        seen.add(low)
        out.append(tok)
for want in [mode, "medium", "full"]:
    if want.lower() not in seen:
        seen.add(want.lower())
        out.append(want)
print(",".join(out))
' || true)"
        if [ -n "$_prof_new" ] && [ "$_prof_new" != "$_prof_cur" ]; then
            env_set_value "$env_file" "COMPOSE_PROFILES" "$_prof_new"
        fi
    else
        env_append_csv_values "$env_file" "COMPOSE_PROFILES" "$_db_ha_mode" "medium" "full" > /dev/null
    fi
    # Read-replica routing must name the replica the compose stack actually
    # starts. Empty here + compose-level default used to agree by accident;
    # make it explicit so .env, pgcat, and the dashboard disagree never.
    # External mode keeps operator-managed values (never overwrite).
    local _replica_hosts=""
    _replica_hosts="$(env_get_value "$env_file" "DB_REPLICA_HOSTS")"
    if [ -z "$_replica_hosts" ]; then
        case "$_db_ha_mode" in
            patroni) env_set_value "$env_file" "DB_REPLICA_HOSTS" "haproxy:5001" ;;
            local-ha) env_set_value "$env_file" "DB_REPLICA_HOSTS" "postgres-replica:5432" ;;
        esac
    fi
    # Idle-minimal sizing (mirrors fresh_config; fill-if-absent so
    # operator-tuned values survive updates). DB buffer changes take
    # effect on the next postgres recreate; gunicorn/celery on the next
    # worker restart (update/refresh flows recreate them).
    local _size_ram_mb="" _size_cpus=""
    _size_ram_mb="$(free -m 2>/dev/null | awk '/^Mem:/{print $2}')"
    [ -n "$_size_ram_mb" ] || _size_ram_mb=8192
    _size_cpus="$(nproc 2>/dev/null || echo 4)"
    local _want_workers="" _want_buffers="" _want_cache=""
    if [ "$_size_cpus" -le 2 ]; then _want_workers=2; else _want_workers=4; fi
    if [ "$_size_ram_mb" -le 4096 ]; then _want_buffers=256MB; _want_cache=1GB
    elif [ "$_size_ram_mb" -le 8192 ]; then _want_buffers=512MB; _want_cache=2GB
    else _want_buffers=1GB; _want_cache=4GB; fi
    env_ensure_var "$env_file" "GUNICORN_WORKERS" "$_want_workers" "Gunicorn workers (host-sized; burst via autoscaler)"
    env_ensure_var "$env_file" "DB_SHARED_BUFFERS" "$_want_buffers" "Postgres shared buffers (host-sized; pinned shm)"
    env_ensure_var "$env_file" "DB_EFFECTIVE_CACHE_SIZE" "$_want_cache" "Postgres planner cache hint (no RAM cost)"
    env_ensure_var "$env_file" "CELERY_QUEUES" "celery,fast,deploy" "Main worker drains all queues (burst workers idle-stop safely)"
    env_ensure_var "$env_file" "CELERY_AUTOSCALE_ENABLED" "true" "Idle-stop burst workers on empty queues"
    env_ensure_var "$env_file" "PROMETHEUS_RETENTION" "30d" "Prometheus TSDB retention (main driver of metrics disk+RAM growth; 7d on small hosts)"
    env_ensure_var "$env_file" "LOKI_RETENTION" "30d" "Loki log retention (set together with PROMETHEUS_RETENTION)"
    env_ensure_var "$env_file" "FALCO_MEMORY_LIMIT" "512M" "Falco runtime-security memory cap (node stack defaults to 256M)"
    # Registry public bind: without an explicit override the compose
    # fallback is a hardcoded IP from another host and the registry port
    # bind kills the whole install (2026-09-10 fresh-install incident).
    # This runs on every update path (unlike the overrides step, which
    # resume can skip), so the key is always repaired.
    local _rt_bind=""
    _rt_bind="$(env_get_value "$env_file" "REGISTRY_PUBLIC_BIND_IP")"
    if [ -z "$_rt_bind" ] || ! _registry_bind_ip_is_local "$_rt_bind"; then
        _rt_bind="$(hostname -I 2>/dev/null | tr ' ' '\n' | grep -E '^[0-9][0-9.]*$' | grep -v '^127\.' | head -1 || true)"
        [ -n "$_rt_bind" ] && env_set_value "$env_file" "REGISTRY_PUBLIC_BIND_IP" "$_rt_bind"
    fi
    # Mesh bind fallback: the compose default (10.100.0.1) only exists when
    # the WireGuard mesh is up. If wg0 failed (no kernel module, VPS
    # without wireguard), binding it kills the ENTIRE compose deployment
    # with "cannot assign requested address". Only the untouched default
    # is ever rewritten — an explicitly set mesh IP is the operator's
    # intent and is left alone (fresh_deploy validates it fail-closed).
    # 127.0.0.2 is loopback-range (always bindable) and distinct from the
    # 127.0.0.1 first bind, so the triple-bind stays conflict-free while
    # single-host pulls keep working via 127.0.0.1/registry:5000.
    local _rt_mesh=""
    _rt_mesh="$(env_get_value "$env_file" "REGISTRY_MESH_BIND_IP")"
    if { [ -z "$_rt_mesh" ] || [ "$_rt_mesh" = "10.100.0.1" ]; } && ! _registry_bind_ip_is_local "10.100.0.1"; then
        env_set_value "$env_file" "REGISTRY_MESH_BIND_IP" "127.0.0.2"
        echo -e "${YELLOW}  ⚠ WireGuard mesh (10.100.0.1) not present — registry mesh bind parked on 127.0.0.2 (single-host OK, no mesh pulls)${NC}"
    fi
    # CoreDNS mesh bind: identical guard to the registry mesh bind above.
    # The compose default (10.100.0.1) only exists when the WireGuard mesh
    # is up; parking on 127.0.0.2 keeps the compose deployment alive on
    # single-host installs (nothing else listens on 127.0.0.2:53 —
    # systemd-resolved uses 127.0.0.53 only).
    local _cd_mesh=""
    _cd_mesh="$(env_get_value "$env_file" "COREDNS_MESH_BIND_IP")"
    if { [ -z "$_cd_mesh" ] || [ "$_cd_mesh" = "10.100.0.1" ]; } && ! _registry_bind_ip_is_local "10.100.0.1"; then
        env_set_value "$env_file" "COREDNS_MESH_BIND_IP" "127.0.0.2"
        echo -e "${YELLOW}  ⚠ WireGuard mesh (10.100.0.1) not present — coredns mesh bind parked on 127.0.0.2${NC}"
    fi
    # Backfill core platform identity keys (2026-09-12: resume runs can
    # preserve a stub .env that never went through fresh_config full
    # template - DOMAIN/USE_SSL/PUBLIC_IP/FRONTEND_APP_URL missing breaks
    # Caddy sync, frontend bake, CORS. Idempotent: never overwrites).
    local _bf_public_ip="" _bf_domain="" _bf_use_ssl="" _bf_origins=""
    _bf_public_ip="$(env_get_value "$env_file" "PUBLIC_IP")"
    if [ -z "$_bf_public_ip" ]; then
        _bf_public_ip="$(hostname -I 2>/dev/null | tr ' ' '\n' | grep -E '^[0-9][0-9.]*$' | grep -v '^127\.' | head -1 || true)"
        [ -n "$_bf_public_ip" ] && env_ensure_var "$env_file" "PUBLIC_IP" "$_bf_public_ip" "Server public IP (auto-detected)"
    fi
    _bf_domain="$(env_get_value "$env_file" "DOMAIN")"
    if [ -z "$_bf_domain" ]; then
        if [ -n "$_bf_public_ip" ]; then _bf_domain="$_bf_public_ip"; else _bf_domain="localhost"; fi
        env_ensure_var "$env_file" "DOMAIN" "$_bf_domain" "Platform domain or IP"
    fi
    _bf_use_ssl="$(env_get_value "$env_file" "USE_SSL")"
    if [ -z "$_bf_use_ssl" ]; then
        if echo "$_bf_domain" | grep -qE '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$'; then _bf_use_ssl="false"; else _bf_use_ssl="true"; fi
        env_ensure_var "$env_file" "USE_SSL" "$_bf_use_ssl" "Use SSL (false for raw IP)"
    fi
    if echo "$_bf_domain" | grep -qE '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$' || [ "$_bf_use_ssl" != "true" ]; then _bf_origins="http://$_bf_domain"; else _bf_origins="https://$_bf_domain"; fi
    env_ensure_var "$env_file" "FRONTEND_APP_URL" "$_bf_origins" "Canonical public origin baked into frontend"
    env_ensure_var "$env_file" "CONTAINER_REGISTRY_URL" "registry:5000" "Private Docker registry"
    env_ensure_var "$env_file" "REGISTRY_USER" "smsly-registry" "Registry username"
    env_ensure_var "$env_file" "DOCKER_NETWORK" "smsly-net" "Docker network for services"
    env_ensure_var "$env_file" "WILDCARD_SUBDOMAINS" "false" "Wildcard subdomain SSL"
    env_ensure_var "$env_file" "CADDY_CONFIG_DIR" "/caddy-config" "Caddy config directory"
    env_ensure_var "$env_file" "ACME_EMAIL" "" "ACME email for Lets Encrypt"
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
        local compose_target="${COMPOSE_FILE:-docker-compose.prod.yml}"
        if [ -f "$compose_target" ] && grep -q "^  *pgcat:" "$compose_target" ; then
            expected_database_url="postgresql://smsly_admin:${postgres_password}@pgcat:5432/smsly_hosting"
        else
            expected_database_url="postgresql://smsly_admin:${postgres_password}@db:5432/smsly_hosting"
        fi
        current_database_url="$(env_get_value "$env_file" "DATABASE_URL")"

        if [ "$MODE_AGENT_LITE" = "true" ] && [ -n "${MASTER_IP:-}" ]; then
            echo -e "${BLUE}  -> Configuring for Edge Node (Lite Agent) mode...${NC}"

            if [ -z "${MASTER_MESH_IP:-}" ] && [ -f "$env_file" ]; then
                MASTER_MESH_IP="$(env_get_value "$env_file" "MASTER_MESH_IP")"
            fi
            local db_user="${MASTER_DB_USER:-smsly_admin}"
            local db_pass="${MASTER_DB_PASSWORD:-$postgres_password}"
            local mq_pass="${MASTER_MQ_PASSWORD:-$rabbitmq_password}"

            local db_host="${MASTER_MESH_IP}"
            expected_database_url="postgresql://${db_user}:${db_pass}@${db_host}:5432/smsly_hosting"
            expected_direct_url="postgresql://${db_user}:${db_pass}@${db_host}:5432/smsly_hosting"
            expected_celery_broker_url="amqp://smsly_user:${rabbitmq_password}@rabbitmq:5672//"

            env_set_value "$env_file" "DATABASE_URL" "$expected_database_url"
            env_set_value "$env_file" "DIRECT_DATABASE_URL" "$expected_direct_url"
            env_set_value "$env_file" "CELERY_BROKER_URL" "$expected_celery_broker_url"
            if [ -n "${MASTER_MESH_IP:-}" ]; then
                env_set_value "$env_file" "MASTER_MESH_IP" "$MASTER_MESH_IP"
            fi

            current_database_url="$expected_database_url"
            current_celery_broker_url="$expected_celery_broker_url"
        fi

        if [ "$MODE_NODE" = "true" ] && [ -n "$postgres_password" ]; then
            local node_env_mode="$(mode_env_value)"
            local node_expected_db_url="postgresql://smsly_admin:${postgres_password}@db:5432/smsly_hosting"
            local node_expected_direct_url="postgresql://smsly_admin:${postgres_password}@db:5432/smsly_hosting"
            if [ "$current_database_url" != "$node_expected_db_url" ]; then
                echo -e "${BLUE}  -> Setting DATABASE_URL for node mode (local DB direct)${NC}"
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
            env_set_value "$env_file" "COMPOSE_FILE" "infrastructure/docker/docker-compose.node.yml"

            if [ -z "$(env_get_value "$env_file" "MASTER_URL" 2>/dev/null || true)" ] && [ -n "${MASTER_URL:-}" ]; then
                env_set_value "$env_file" "MASTER_URL" "$MASTER_URL"
                echo -e "${GREEN}  OK MASTER_URL set to ${MASTER_URL}${NC}"
            fi
        fi

        if [[ "$current_database_url" =~ @db:5432 ]] && [ "$MODE_AGENT_LITE" != "true" ] && [ "$MODE_NODE" != "true" ] && [ -f "$compose_target" ] && grep -q "^  *pgcat:" "$compose_target" ; then
            echo -e "${BLUE}  -> Migrating DATABASE_URL from db to pgcat${NC}"
            local migrated_url="${current_database_url/@db:5432/@pgcat:5432}"
            env_set_value "$env_file" "DATABASE_URL" "$migrated_url"
            current_database_url="$migrated_url"
            echo -e "${GREEN}  OK DATABASE_URL migrated to pgcat${NC}"
        fi

        if [[ "$current_database_url" =~ @pgbouncer:5432 ]]; then
            local migrated_url
            if [ -f "$compose_target" ] && grep -q "^  *pgcat:" "$compose_target" ; then
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

        local expected_direct_url=""
        if [ "$MODE_AGENT_LITE" = "true" ]; then
            expected_direct_url="postgresql://${MASTER_DB_USER:-smsly_admin}:${MASTER_DB_PASSWORD:-$postgres_password}@${MASTER_MESH_IP:-db}:5432/smsly_hosting"
        else
            # Direct endpoint follows the DB mode (migrations bypass the
            # pooler): local-ha talks to postgres-primary, patroni goes
            # through HAProxy's write port, external uses the managed
            # host from PGCAT_DB_HOST/PORT. env_ensure_var below only
            # fills when missing, so operator-customized URLs survive.
            local _direct_host="postgres-primary" _direct_port="5432"
            case "$_db_ha_mode" in
                patroni) _direct_host="haproxy"; _direct_port="5000" ;;
                external)
                    _direct_host="$(env_get_value "$env_file" "PGCAT_DB_HOST")"
                    [ -n "$_direct_host" ] || _direct_host="postgres-primary"
                    _direct_port="$(env_get_value "$env_file" "PGCAT_DB_PORT")"
                    [ -n "$_direct_port" ] || _direct_port="5432"
                    ;;
            esac
            expected_direct_url="postgresql://smsly_admin:${postgres_password}@${_direct_host}:${_direct_port}/smsly_hosting"
        fi

        if [ -z "$current_database_url" ]; then
            env_ensure_var "$env_file" "DATABASE_URL" "$expected_database_url" "PostgreSQL connection string (via PgCat)"

            env_ensure_var "$env_file" "DIRECT_DATABASE_URL" "$expected_direct_url" "Direct connection bypass for migrations"
        elif [[ "$current_database_url" =~ ^postgresql://smsly_admin:.*@pgcat:5432/smsly_hosting$ ]] && [ "$current_database_url" != "$expected_database_url" ]; then
            echo -e "${BLUE}  -> Fixing DATABASE_URL to match POSTGRES_PASSWORD${NC}"
            env_set_value "$env_file" "DATABASE_URL" "$expected_database_url"
            echo -e "${GREEN}  OK DATABASE_URL password synced${NC}"
        fi

        env_ensure_var "$env_file" "DIRECT_DATABASE_URL" "$expected_direct_url" "Direct PostgreSQL connection (migrations only)"
    fi

    return 0
}
