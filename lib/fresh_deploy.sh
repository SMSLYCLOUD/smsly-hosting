# -----------------------------------------------------------------------------
# 4. Deployment
# -----------------------------------------------------------------------------
STACK_DEPLOYED_FROM_CHECKPOINT=false
if is_checkpoint_done "stack_deployed"; then
    STACK_DEPLOYED_FROM_CHECKPOINT=true
else
    echo -e "\n${YELLOW}[4/9] Deploying Container Stack...${NC}"

# Ensure networks exist
docker network create smsly-net  || true
docker network create smsly-proxy  || true

# Ensure external volumes exist.
# docker-compose.yml marks `caddy_data` as `external: true` with fixed name
# `smsly-hosting_caddy_data`. Compose refuses to create external volumes
# and aborts `up` with `external volume "..." not found` if they are
# missing. Pre-create here (idempotent — Compose / Docker return a benign
# "already exists" error which we swallow).
#
# Note: caddy_config is no longer a separate named volume. The caddy
# container now reads /config from the same ./caddy-config bind mount
# the backend writes the IP self-signed cert to, fixing the
# "open /config/certs/ip.crt: no such file or directory" crash loop.
if command -v docker ; then
    docker volume create --name smsly-hosting_caddy_data  || true

    # Caddy container runs as uid 1000 (nextjs user); chown the volume
    # root so the container can read/write its ACME state. Same pattern
    # already used for backups_data in ensure_infrastructure_permissions.
    if docker volume inspect smsly-hosting_caddy_data ; then
        docker run --rm -v smsly-hosting_caddy_data:/data alpine chown -R 1000:1000 /data  || true
    fi
fi

# ─── BLINDSPOT FIX: Ensure entrypoint.sh has execute permissions ────────────
# Windows git can strip +x bits. Fix before building.
#
# NOTE: backend/Dockerfile already runs `chmod +x entrypoint.sh` inside the image.
# Avoid mutating the git working tree on the host (file mode flips can block `git pull`).
#

# Both IP and SSL modes use the same compose stack.
# Master exposes public HTTP/HTTPS through Caddy; node/agent modes expose HTTP through Traefik.
# Generate registry TLS cert + htpasswd if missing (required for auth-enabled registry)
echo -e "${BLUE}  → Configuring Docker registry auth and TLS...${NC}"
mkdir -p "$INSTALL_DIR/auth" "$INSTALL_DIR/certs"

# Regenerate registry TLS if EITHER file is missing OR if the existing
# key/cert don't match (e.g. one was rotated independently). The earlier
# `||` check only caught missing files; mismatched pairs caused
# `registry:2.8.3` to crash-loop with "tls: private key does not match
# public key" forever. Regenerating as a matched pair is the only safe
# option — we cannot repair an existing cert without the issuing key.
_regen_registry_tls() {
    echo -e "${BLUE}    Generating self-signed TLS cert for registry...${NC}"
    # openssl req writes key then cert; if key write fails halfway the
    # cert from the prior generation would be orphaned. The atomic
    # rename pattern below ensures consumers (the registry container)
    # never see a half-written pair.
    _tmp_dir="$(mktemp -d)"
    openssl req -x509 -nodes -days 3650 -newkey rsa:2048 \
        -keyout "${_tmp_dir}/registry.key" \
        -out    "${_tmp_dir}/registry.crt" \
        -subj "/CN=registry" \
        -addext "subjectAltName=DNS:registry,DNS:localhost,IP:127.0.0.1,IP:10.100.0.1" 
    local _rc=$?
    if [ "$_rc" -ne 0 ]; then
        rm -rf "$_tmp_dir"
        echo -e "${YELLOW}    ⚠ Failed to generate registry cert (openssl missing?)${NC}"
        return $_rc
    fi
    mv "${_tmp_dir}/registry.key" "$INSTALL_DIR/certs/registry.key"
    mv "${_tmp_dir}/registry.crt" "$INSTALL_DIR/certs/registry.crt"
    rm -rf "$_tmp_dir"
    chmod 644 "$INSTALL_DIR/certs/registry.crt" "$INSTALL_DIR/certs/registry.key"
}

_registry_tls_ok() {
    [ -f "$INSTALL_DIR/certs/registry.key" ] || return 1
    [ -f "$INSTALL_DIR/certs/registry.crt" ] || return 1
    # openssl x509 -noout -modulus matches the cert's modulus;
    # openssl rsa  -noout -modulus matches the key's modulus. They must
    # be equal for the TLS handshake to succeed.
    local _cmod _kmod
    _cmod="$(openssl x509 -in "$INSTALL_DIR/certs/registry.crt" -noout -modulus  | openssl sha256)" || return 1
    _kmod="$(openssl rsa  -in "$INSTALL_DIR/certs/registry.key" -noout -modulus  | openssl sha256)" || return 1
    [ "$_cmod" = "$_kmod" ]
}

if ! _registry_tls_ok; then
    _regen_registry_tls
    if ! _registry_tls_ok; then
        echo -e "${RED}    ✗ Registry TLS cert/key still mismatched or missing after regen attempt${NC}"
        echo -e "${YELLOW}      Manual fix on host: openssl req -x509 -nodes -days 3650 -newkey rsa:2048 \\${NC}"
        echo -e "${YELLOW}        -keyout /opt/smsly-hosting/certs/registry.key \\${NC}"
        echo -e "${YELLOW}        -out    /opt/smsly-hosting/certs/registry.crt \\${NC}"
        echo -e "${YELLOW}        -subj '/CN=registry'${NC}"
    else
        echo -e "${BLUE}    Restarting registry container to pick up new TLS certs...${NC}"
        docker restart smsly-hosting-registry-1 || echo -e "${YELLOW}    ⚠ Registry restart failed${NC}"
    fi
fi
if [ ! -f "$INSTALL_DIR/auth/htpasswd" ] || [ -z "${REGISTRY_PASSWORD:-}" ] || [ -z "${REGISTRY_USER:-}" ]; then
    REGISTRY_PASS="${REGISTRY_PASSWORD:-$(python3 -c "import secrets; print(secrets.token_urlsafe(18))"  || openssl rand -hex 12  || echo 'auto-generated-change-me')}"
    if command -v htpasswd ; then
        htpasswd -Bbn "${REGISTRY_USER:-smsly-registry}" "$REGISTRY_PASS" > "$INSTALL_DIR/auth/htpasswd"
    else
        # Python-based bcrypt fallback
        python3 -c "
import bcrypt, sys
pw = sys.argv[1] if len(sys.argv) > 1 else '$REGISTRY_PASS'
print(f'${REGISTRY_USER:-smsly-registry}:' + bcrypt.hashpw(pw.encode(), bcrypt.gensalt(10)).decode())
" "$REGISTRY_PASS" > "$INSTALL_DIR/auth/htpasswd"  || \
        echo -e "${YELLOW}    ⚠ Failed to generate htpasswd (neither htpasswd nor python bcrypt available)${NC}"
    fi
    env_set_value "$INSTALL_DIR/.env" "REGISTRY_USER" "${REGISTRY_USER:-smsly-registry}"
    env_set_value "$INSTALL_DIR/.env" "REGISTRY_PASSWORD" "$REGISTRY_PASS"
fi
echo -e "${GREEN}  ✓ Registry auth + TLS configured${NC}"

# Install registry cert into Docker's cert trust store so the daemon
# connects via HTTPS (not HTTP fallback) to the registry.
install_registry_docker_certs

# Authenticate Docker CLI with the private registry so the daemon can
# pull base images during builds without 403 errors.
docker_login

# Ensure bind-mounted config paths exist before `docker compose up`.
ensure_infrastructure_permissions
# Pre-create caddy bind-mount directories (needed by compose volume driver)
mkdir -p "$INSTALL_DIR/caddy-config" "$INSTALL_DIR/caddy-logs"
if [ "$MODE_AGENT_LITE" = "true" ]; then
    echo -e "${BLUE}  → Lite Agent mode: disabling master-only Caddy services before Traefik bind.${NC}"
    true
elif [ "$MODE_NODE" = "true" ]; then
    echo -e "${BLUE}  → Node mode: deploying prod stack without frontend/Caddy; Traefik binds public HTTP.${NC}"
fi
echo -e "${BLUE}  → Disabling backend entrypoint bootstrap for installer-controlled migrations...${NC}"
env_set_value "$INSTALL_DIR/.env" "SMSLY_RUN_ENTRYPOINT_TASKS" "false"
    echo -e "${BLUE}  → Starting App Stack (Build + Deploy)...${NC}"
    cleanup_stale_containers
    ( while true; do sleep 30; echo -e "${BLUE}      ↳ Progress: Deployment in progress... $(date +%H:%M:%S)${NC}"; done ) &
    HEARTBEAT_PID=$!
    # TODO(install): replace set -e toggle with explicit conditional. The
    # conditional rebuild + retry makes a flat `if ! cmd` rewrite risky; the
    # rc-capture pattern is intentionally retained.
    set +e
    compose_stack_build --no-cache
    DEPLOY_RC=$?
    if [ "$DEPLOY_RC" -eq 0 ]; then
        # Scan freshly built images for vulnerabilities
        if command -v trivy ; then
            echo -e "${BLUE}  → Scanning built images for vulnerabilities...${NC}"
            for _trivy_img in backend frontend; do
                _trivy_tag="smsly/${_trivy_img}:latest"
                if docker image inspect "$_trivy_tag" ; then
                    echo -e "${BLUE}    ↳ Scanning $_trivy_tag...${NC}"
                    trivy image --insecure --scanners vuln --severity CRITICAL,HIGH --exit-code 0 --no-progress "$_trivy_tag"  || echo -e "${YELLOW}    ⚠ $_trivy_tag scan reported warnings — review output above${NC}"
                fi
            done
            unset _trivy_img _trivy_tag
        fi
        compose_stack_up --remove-orphans
        DEPLOY_RC=$?
    fi
    set -e
    kill $HEARTBEAT_PID  || true
    wait $HEARTBEAT_PID  || true
    if [ "$DEPLOY_RC" -ne 0 ]; then
        echo -e "${RED}  ✗ Docker Compose failed during stack deployment (exit $DEPLOY_RC).${NC}"
        echo -e "${YELLOW}  ↳ Re-run with --resume to skip completed steps: sudo bash install.sh --resume${NC}"
        docker compose -f "$COMPOSE_FILE" ps  || true
        docker compose -f "$COMPOSE_FILE" logs --tail=120  || true
        exit "$DEPLOY_RC"
    fi
    if [ "$MODE_AGENT_LITE" = "true" ]; then
        sync_agent_lite_rabbitmq_password
    else
        echo -e "${BLUE}  → Deploying Observability Stack...${NC}"
        # Ensure scripts mounted into containers are executable (git may not preserve +x)
        chmod +x "$INSTALL_DIR"/scripts/alertmanager-entrypoint.sh  || true
        chmod +x "$INSTALL_DIR"/infrastructure/docker/infisical-gen-env.sh  || true
        if [ -f "infrastructure/docker/docker-compose.observability.yml" ]; then
            docker compose -f infrastructure/docker/docker-compose.observability.yml pull --ignore-pull-failures || \
                echo -e "${YELLOW}  ⚠ Observability stack pull failed (non-fatal)${NC}"
            docker compose -f infrastructure/docker/docker-compose.observability.yml up -d --pull always || \
                echo -e "${YELLOW}  ⚠ Observability stack start failed (non-fatal)${NC}"
        fi
    fi
    # Deploy docker-labels exporter to all remote nodes and regenerate target files
    if [ "$MODE_AGENT_LITE" != "true" ]; then
        backend_container=$(docker ps --format '{{.Names}}' | grep -E '^smsly-hosting-backend(-1)?$' | head -1)
        if [ -n "$backend_container" ]; then
            timeout 60 docker exec "$backend_container" python manage.py deploy_docker_labels_exporters || echo -e "${YELLOW}    ⚠ deploy_docker_labels_exporters failed${NC}"
        fi
    fi

    # ─── Infisical auto-provision (master mode only) ─────────────────────
    _INFISICAL_COMPOSE="$INSTALL_DIR/infrastructure/docker/docker-compose.infisical.yml"
    if [ "$MODE_AGENT_LITE" != "true" ] && [ -f "$_INFISICAL_COMPOSE" ]; then
        _infisical_running=$(docker ps --filter "name=infisical" --format '{{.Names}}'  | head -1)
        if [ -n "$_infisical_running" ]; then
            echo -e "${GREEN}  ✓ Infisical already running (${_infisical_running})${NC}"
        else
            echo -e "${BLUE}  → Provisioning Infisical secret manager...${NC}"
            docker volume create infisical_data  || true

            # Create the infisical database in Postgres if it doesn't exist
            _db_container=""
            # HA mode: smsly-postgres-primary
            if docker ps --format '{{.Names}}' | grep -q '^smsly-postgres-primary$'; then
                _db_container="smsly-postgres-primary"
                _db_user="${POSTGRES_USER:-smsly_admin}"
            # Standard mode: smsly-hosting-db-1
            elif docker ps --format '{{.Names}}' | grep -q '^smsly-hosting-db-1$'; then
                _db_container="smsly-hosting-db-1"
                _db_user="${POSTGRES_USER:-postgres}"
            fi
            if [ -n "$_db_container" ]; then
                _db_exists=$(timeout 30 docker exec "$_db_container" psql -U "${_db_user}" -d "${POSTGRES_DB:-smsly_hosting}" -tc \
                    "SELECT 1 FROM pg_database WHERE datname='infisical'"  | tr -d '[:space:]' || true)
                if [ "$_db_exists" != "1" ]; then
                    timeout 30 docker exec "$_db_container" psql -U "${_db_user}" -d "${POSTGRES_DB:-smsly_hosting}" -c \
                        "CREATE DATABASE infisical;"  && \
                        echo -e "${GREEN}  ✓ Created infisical database${NC}" || \
                        echo -e "${YELLOW}  ⚠ Could not create infisical database (may already exist)${NC}"
                fi
            else
                echo -e "${YELLOW}  ⚠ No Postgres container found — skipping infisical database creation${NC}"
            fi

            # Generate env file on the volume
            _gen_script="$INSTALL_DIR/infrastructure/docker/infisical-gen-env.sh"
            if [ -f "$_gen_script" ]; then
                docker run --rm \
                    -v infisical_data:/data \
                    -v "$_gen_script":/tmp/infisical-gen-env.sh:ro \
                    alpine:3.19 \
                    sh /tmp/infisical-gen-env.sh /data/infisical.env  || \
                    echo -e "${YELLOW}  ⚠ Could not generate Infisical env${NC}"
            fi

            docker compose --env-file "$INSTALL_DIR/.env" \
                -f "$_INFISICAL_COMPOSE" up -d --remove-orphans  && \
                echo -e "${GREEN}  ✓ Infisical is running${NC}" || \
                echo -e "${YELLOW}  ⚠ Infisical startup failed (non-fatal — secrets remain in .env)${NC}"
        fi
    fi

    set_checkpoint "stack_deployed"

    # Docker login now that the registry is actually running
    docker_login
fi
if [ "$STACK_DEPLOYED_FROM_CHECKPOINT" = "true" ]; then
    reconcile_compose_stack_after_resume
fi
