apply_agent_lite_env_overrides() {
    local env_file="$1"
    local seed_file="/opt/smsly-hosting/.agent_lite_seed"

    [ "$MODE_AGENT_LITE" = "true" ] || return 0

    # --- Self-Healing: Recovery from existing .env if env vars are missing ---
    if [ -z "${MASTER_IP:-}" ] && [ -f "$env_file" ]; then
        MASTER_IP="$(env_get_value "$env_file" "MASTER_IP")"
    fi
    if [ -z "${MASTER_MESH_IP:-}" ] && [ -f "$env_file" ]; then
        MASTER_MESH_IP="$(env_get_value "$env_file" "MASTER_MESH_IP")"
    fi
    if [ -z "${MASTER_FIELD_ENCRYPTION_KEY:-}" ] && [ -f "$env_file" ]; then
        MASTER_FIELD_ENCRYPTION_KEY="$(env_get_value "$env_file" "FIELD_ENCRYPTION_KEY")"
    fi
    # NOTE: FIELD_ENCRYPTION_KEY is NOT read from the seed file — it is
    # stored in .env only to limit exposure in plaintext recovery files.
    if [ -z "${MASTER_DB_PASSWORD:-}" ] && [ -f "$env_file" ]; then
        # If we are updating and MASTER_DB_PASSWORD wasn't passed, try to preserve the existing one
        local db_url
        db_url="$(env_get_value "$env_file" "DATABASE_URL")"
        if [[ "$db_url" =~ ://[^:]+:([^@]+)@ ]]; then
            MASTER_DB_PASSWORD="${BASH_REMATCH[1]}"
        fi
    fi

    # --- Validation ---
    if [ -z "${MASTER_IP:-}" ]; then
        echo -e "${RED}  ✗ ERROR: MASTER_IP is missing. Lite Agent cannot function without a Master node.${NC}"
        echo -e "${YELLOW}    To fix: Run the update from the Master Dashboard or pass MASTER_IP=... to the script.${NC}"
        exit 1
    fi

    # MASTER_MESH_IP is the WireGuard IP used for internal services (DB, MQ, Redis).
    # Must be set — no fallback to MASTER_IP (public IP is firewalled for internal ports).
    if [ -z "${MASTER_MESH_IP:-}" ]; then
        echo -e "${RED}  ✗ ERROR: MASTER_MESH_IP is missing. Lite Agent requires the WireGuard mesh IP.${NC}"
        echo -e "${YELLOW}    Set MASTER_MESH_IP to the WireGuard IP of the master node.${NC}"
        exit 1
    fi

    MASTER_DB_USER="${MASTER_DB_USER:-smsly_admin}"
    # If password is still missing after recovery attempt, we must stop.
    if [ -z "${MASTER_DB_PASSWORD:-}" ]; then
        echo -e "${RED}  ✗ ERROR: MASTER_DB_PASSWORD is missing and could not be recovered.${NC}"
        exit 1
    fi

    MASTER_MQ_PASSWORD="${MASTER_MQ_PASSWORD:-$MASTER_DB_PASSWORD}"
    SMSLY_NODE_HOST="${SMSLY_NODE_HOST:-$(detect_public_ip  || true)}"
    [ -n "$SMSLY_NODE_HOST" ] || SMSLY_NODE_HOST="$(hostname -f  || hostname  || echo agent)"
    SMSLY_NODE_ID="${SMSLY_NODE_ID:-$SMSLY_NODE_HOST}"
    local node_slug
    node_slug="$(sanitize_node_identifier "$SMSLY_NODE_ID")"
    SMSLY_NODE_QUEUE="${SMSLY_NODE_QUEUE:-smsly-node-${node_slug}}"

    # Use MASTER_MESH_IP for database only (shared DB).
    # Redis and RabbitMQ run locally on each node — no cross-node dependency.
    local node_redis_password
    node_redis_password="$(env_get_value "$env_file" "REDIS_PASSWORD"  || true)"
    if [ -z "$node_redis_password" ]; then
        node_redis_password="$(python3 -c "import secrets; print(secrets.token_hex(16))"  || openssl rand -hex 16  || echo "")"
    fi
    local redis_url="redis://redis:6379/0"
    if [ -n "$node_redis_password" ]; then
        redis_url="redis://:${node_redis_password}@redis:6379/0"
    fi

    local node_rabbitmq_password
    node_rabbitmq_password="$(env_get_value "$env_file" "RABBITMQ_PASSWORD"  || true)"
    if [ -z "$node_rabbitmq_password" ]; then
        node_rabbitmq_password="$(python3 -c "import secrets; print(secrets.token_hex(16))"  || openssl rand -hex 16  || echo "")"
    fi
    local celery_broker_url="amqp://smsly_user:${node_rabbitmq_password}@rabbitmq:5672//"

    # --- Persistence: Save a recovery seed for future manual updates ---
    cat > "$seed_file" <<EOF
# SMSLY Lite Agent Recovery Seed
# Generated on $(date)
# NOTE: FIELD_ENCRYPTION_KEY is stored in .env only (not duplicated here
# for security — it is the master's database encryption key).
MASTER_IP="$MASTER_IP"
MASTER_MESH_IP="$MASTER_MESH_IP"
MASTER_DB_USER="$MASTER_DB_USER"
MASTER_DB_PASSWORD="$MASTER_DB_PASSWORD"
MASTER_MQ_PASSWORD="$MASTER_MQ_PASSWORD"
MASTER_REDIS_PASSWORD="${MASTER_REDIS_PASSWORD:-}"
MASTER_GATEWAY_SECRET="${MASTER_GATEWAY_SECRET:-}"
MASTER_BACKUP_ENCRYPTION_KEY="${MASTER_BACKUP_ENCRYPTION_KEY:-}"
MASTER_BACKUP_REQUIRE_ENCRYPTION="${MASTER_BACKUP_REQUIRE_ENCRYPTION:-}"
MASTER_GITHUB_WEBHOOK_SECRET="${MASTER_GITHUB_WEBHOOK_SECRET:-}"
MASTER_AUTOSCALER_API_TOKEN="${MASTER_AUTOSCALER_API_TOKEN:-}"
MASTER_FRP_AUTH_TOKEN="${MASTER_FRP_AUTH_TOKEN:-}"
MASTER_PGCAT_ADMIN_PASSWORD="${MASTER_PGCAT_ADMIN_PASSWORD:-}"
SMSLY_NODE_ID="$SMSLY_NODE_ID"
SMSLY_NODE_QUEUE="$SMSLY_NODE_QUEUE"
EOF
    chmod 600 "$seed_file"

    env_set_value "$env_file" "NODE_TYPE" "agent-lite"
    env_set_value "$env_file" "MODE" "agent"
    env_set_value "$env_file" "COMPOSE_FILE" "infrastructure/docker/docker-compose.agent-lite.yml"
    env_set_value "$env_file" "TRAEFIK_HTTP_BIND" "0.0.0.0:80"
    env_set_value "$env_file" "TRAEFIK_ENABLE_WEBSECURE" "false"
    env_set_value "$env_file" "MASTER_IP" "$MASTER_IP"
    env_set_value "$env_file" "MASTER_MESH_IP" "$MASTER_MESH_IP"
    env_set_value "$env_file" "SMSLY_NODE_HOST" "$SMSLY_NODE_HOST"
    env_set_value "$env_file" "SMSLY_NODE_ID" "$SMSLY_NODE_ID"
    env_set_value "$env_file" "SMSLY_NODE_QUEUE" "$SMSLY_NODE_QUEUE"
    env_set_value "$env_file" "DATABASE_URL" "postgresql://${MASTER_DB_USER}:${MASTER_DB_PASSWORD}@${MASTER_MESH_IP}:5432/smsly_hosting"
    env_set_value "$env_file" "DIRECT_DATABASE_URL" "postgresql://${MASTER_DB_USER}:${MASTER_DB_PASSWORD}@${MASTER_MESH_IP}:5432/smsly_hosting"
    # Local RabbitMQ (runs on the same node via docker-compose.agent-lite.yml)
    env_set_value "$env_file" "RABBITMQ_PASSWORD" "${node_rabbitmq_password:-}"
    env_set_value "$env_file" "RABBITMQ_DEFAULT_USER" "smsly_user"
    env_set_value "$env_file" "RABBITMQ_DEFAULT_PASS" "${node_rabbitmq_password:-}"
    env_set_value "$env_file" "CELERY_BROKER_URL" "$celery_broker_url"
    # Local Redis (runs on the same node via docker-compose.agent-lite.yml)
    env_set_value "$env_file" "REDIS_URL" "$redis_url"
    env_set_value "$env_file" "REDIS_PASSWORD" "${node_redis_password:-}"
    env_set_value "$env_file" "REDIS_HOST" "redis"
    env_set_value "$env_file" "REDIS_PORT" "6379"
    local registry_host="${MASTER_MESH_IP}"
    env_set_value "$env_file" "CONTAINER_REGISTRY_URL" "${registry_host}:5000"
    if [ -n "${MASTER_GATEWAY_SECRET:-}" ]; then
        env_set_value "$env_file" "GATEWAY_SECRET" "$MASTER_GATEWAY_SECRET"
    fi
    if [ -n "${MASTER_FIELD_ENCRYPTION_KEY:-}" ]; then
        env_set_value "$env_file" "FIELD_ENCRYPTION_KEY" "$MASTER_FIELD_ENCRYPTION_KEY"
    fi

    # Batch J: sync the remaining critical master secrets to the
    # node. Without these the node can't decrypt shared backups,
    # verify GitHub webhooks, or authenticate to the autoscaler
    # API. Each is a one-way sync from master to node: the
    # node inherits the value but never overwrites the master's
    # copy. If a var is unset on the master, we skip it (an
    # older master that pre-dates the var is treated as "not
    # required" rather than failed).
    local _master_secrets_to_sync=(
        "BACKUP_ENCRYPTION_KEY:master's Fernet key for at-rest backup encryption"
        "|BACKUP_REQUIRE_ENCRYPTION:master's backup-encryption policy (true/false)"
        "|GITHUB_WEBHOOK_SECRET:master's GitHub webhook signature verification secret"
        "|AUTOSCALER_API_TOKEN:master's autoscaler-service bearer token"
        "|FRP_AUTH_TOKEN:master's FRP tunnel relay authentication token"
        "|PGCAT_ADMIN_PASSWORD:master's PgCat administration password"
    )
    local _entry
    for _entry in "${_master_secrets_to_sync[@]}"; do
        local _key="${_entry%%|*}"
        # Read the master secret from the master's .env file.
        # MASTER_ENV_<KEY> env vars are NOT exported by the provisioner;
        # secrets are written to a temporary file and read via env_get_value.
        local _master_val=""
        if [ -f "$MASTER_ENV_FILE" ]; then
            _master_val="$(env_get_value "$MASTER_ENV_FILE" "$_key"  || true)"
        fi
        if [ -n "$_master_val" ]; then
            env_set_value "$env_file" "$_key" "$_master_val"
        fi
    done

    env_set_value "$env_file" "SMSLY_DISABLE_LOCAL_SERVICES" "false"
    env_set_value "$env_file" "SMSLY_RUN_ENTRYPOINT_TASKS" "false"
    env_set_value "$env_file" "SMSLY_ENABLE_STARTUP_CADDY_SYNC" "false"
}

verify_agent_lite_connectivity() {
    [ "$MODE_AGENT_LITE" = "true" ] || return 0
    echo -e "${BLUE}  → Verifying connectivity to Master node (${MASTER_IP})...${NC}"
    
    # 1. Ping Master (public IP)
    if ! ping -c 1 -W 2 "$MASTER_IP" ; then
        echo -e "${YELLOW}  ⚠ Warning: Master node ${MASTER_IP} is not responding to ICMP. Proceeding anyway...${NC}"
    fi

    # 2. Check Database port via mesh IP (internal services use WireGuard)
    local db_check_ip="${MASTER_MESH_IP}"
    if ! timeout -k 5 2 bash -c "</dev/tcp/${db_check_ip}/5432" ; then
        echo -e "${RED}  ✗ ERROR: Master Database (port 5432) is unreachable on ${db_check_ip}.${NC}"
        echo -e "${YELLOW}    Ensure the Master allows port 5432 from this node's IP via WireGuard mesh.${NC}"
        return 1
    fi

    # 3. Redis and RabbitMQ run locally on agent-lite nodes (no Master dependency)
    echo -e "${BLUE}  → Redis and RabbitMQ will run locally on this node.${NC}"

    # 4. The deploy path pulls master-built images from the master's registry.
    local registry_check_ip="${MASTER_MESH_IP}"
    if ! timeout -k 5 2 bash -c "</dev/tcp/${registry_check_ip}/5000" ; then
        echo -e "${RED}  ✗ ERROR: Master container registry (port 5000) is unreachable on ${registry_check_ip}.${NC}"
        echo -e "${YELLOW}    Ensure the Master registry is running and the mesh/firewall allows port 5000 from this node.${NC}"
        return 1
    fi
    if command -v curl ; then
        local registry_code
        registry_code="$(curl -sk -o /dev/null -w '%{http_code}' --max-time 5 "http://${registry_check_ip}:5000/v2/"  || true)"
        # Retry with HTTPS if HTTP returned 000 (connection refused / TLS redirect)
        if [ "$registry_code" = "000" ] || [ "$registry_code" = "400" ]; then
            registry_code="$(curl -sk -o /dev/null -w '%{http_code}' --max-time 5 "https://${registry_check_ip}:5000/v2/"  || true)"
        fi
        case "$registry_code" in
            2*|401) ;;
            *)
                echo -e "${RED}  ✗ ERROR: Master container registry did not answer correctly on ${registry_check_ip}:5000 (HTTP ${registry_code:-000}).${NC}"
                return 1
                ;;
        esac
    fi

    echo -e "${GREEN}  ✓ Connectivity to Master verified.${NC}"
    return 0
}
