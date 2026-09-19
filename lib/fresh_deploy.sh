# -----------------------------------------------------------------------------
# 4. Deployment
# -----------------------------------------------------------------------------
# ─── Registry auth pair self-heal (runs on EVERY invocation, resume included)
# .env's REGISTRY_PASSWORD and auth/htpasswd must always agree: the deploy
# step generates them as a pair, but rollback can restore a pre-generation
# .env while htpasswd keeps the new password (or an operator overwrites one
# side by hand). Either divergence breaks every push with "no basic auth
# credentials". Idempotent, touches nothing running — registry:2.8.3 reads
# htpasswd per-request, so no restart is needed after a rewrite.
ensure_registry_auth_pair() {
    [ -f "${INSTALL_DIR:-/opt/smsly-hosting}/.env" ] || return 0
    local _pair_user="" _pair_pass="" _pair_ok=false
    _pair_user="$(grep -m1 '^REGISTRY_USER=' "$INSTALL_DIR/.env" 2>/dev/null | cut -d= -f2- || true)"
    [ -n "$_pair_user" ] || _pair_user="smsly-registry"
    _pair_pass="$(grep -m1 '^REGISTRY_PASSWORD=' "$INSTALL_DIR/.env" 2>/dev/null | cut -d= -f2- || true)"
    if [ -z "$_pair_pass" ]; then
        _pair_pass="$(python3 -c "import secrets; print(secrets.token_urlsafe(24))" 2>/dev/null || openssl rand -hex 18 || echo 'auto-generated-change-me')"
        if grep -q '^REGISTRY_PASSWORD=' "$INSTALL_DIR/.env" 2>/dev/null; then
            sed -i "s/^REGISTRY_PASSWORD=.*/REGISTRY_PASSWORD=${_pair_pass}/" "$INSTALL_DIR/.env"
        else
            echo "REGISTRY_PASSWORD=${_pair_pass}" >> "$INSTALL_DIR/.env"
        fi
        if ! grep -q '^REGISTRY_USER=' "$INSTALL_DIR/.env" 2>/dev/null; then
            echo "REGISTRY_USER=${_pair_user}" >> "$INSTALL_DIR/.env"
        fi
        echo -e "${BLUE}  → Generated missing REGISTRY_PASSWORD${NC}"
    fi
    if [ -f "$INSTALL_DIR/auth/htpasswd" ]; then
        if command -v htpasswd >/dev/null 2>&1; then
            htpasswd -vb "$INSTALL_DIR/auth/htpasswd" "$_pair_user" "$_pair_pass" >/dev/null 2>&1 && _pair_ok=true
        else
            _pair_ok=true  # no verifier available; leave the file alone
        fi
    fi
    if [ "$_pair_ok" != "true" ]; then
        mkdir -p "$INSTALL_DIR/auth"
        if command -v htpasswd >/dev/null 2>&1; then
            htpasswd -Bbn "$_pair_user" "$_pair_pass" > "$INSTALL_DIR/auth/htpasswd" 2>/dev/null \
                && echo -e "${BLUE}  → Rewrote registry htpasswd from .env${NC}" \
                || echo -e "${YELLOW}    ⚠ Could not rewrite registry htpasswd${NC}"
        else
            python3 -c "import bcrypt,sys; print('${_pair_user}:' + bcrypt.hashpw(sys.argv[1].encode(), bcrypt.gensalt(10)).decode())" "$_pair_pass" > "$INSTALL_DIR/auth/htpasswd" 2>/dev/null \
                && echo -e "${BLUE}  → Rewrote registry htpasswd from .env${NC}" \
                || echo -e "${YELLOW}    ⚠ Could not rewrite registry htpasswd${NC}"
        fi
    fi
    unset _pair_user _pair_pass _pair_ok
}
ensure_registry_auth_pair
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
    local _cmod="" _kmod=""
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
        echo -e "${RED}    ✗ Aborting: continuing would leave registry:2.8.3 crash-looping on 'tls: private key does not match public key'. Fix the pair, then re-run with --resume.${NC}"
        exit 1
    else
        echo -e "${BLUE}    Restarting registry container to pick up new TLS certs...${NC}"
        _reg_target="smsly-hosting-registry-1"
        if command -v resolve_container_target >/dev/null 2>&1; then
            _reg_target="$(resolve_container_target "smsly-hosting-registry-1" || echo "smsly-hosting-registry-1")"
        fi
        timeout 60 docker restart "$_reg_target" || echo -e "${YELLOW}    ⚠ Registry restart failed${NC}"
        unset _reg_target
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
    # Export into the shell: docker_login() below (and the post-stack retry)
    # reads REGISTRY_USER/REGISTRY_PASSWORD from the environment. On fresh
    # installs the shell never sourced them (the template has no
    # REGISTRY_PASSWORD yet), so without this the login silently no-ops
    # and every authenticated push/pull 401s.
    export REGISTRY_USER="${REGISTRY_USER:-smsly-registry}" REGISTRY_PASSWORD="$REGISTRY_PASS"
fi
echo -e "${GREEN}  ✓ Registry auth + TLS configured${NC}"

# Install registry cert into Docker's cert trust store so the daemon
# connects via HTTPS (not HTTP fallback) to the registry.
install_registry_docker_certs

# Authenticate Docker CLI with the private registry so the daemon can
# pull base images during builds without 403 errors.
docker_login

# ─── Fail-closed registry bind validation ────────────────────────────
# The registry publishes :5000 on three host IPs (loopback, mesh,
# public). A non-local bind IP aborts the WHOLE `up` with "cannot
# assign requested address", and REGISTRY_BIND_IP=0.0.0.0 overlaps both
# other binds on :5000 ("port is already allocated"). Catch both here
# with the fix attached instead of dumping a compose traceback.
# 127/8 is always bindable (covers the 127.0.0.2 mesh fallback).
if [ "${REGISTRY_BIND_IP:-127.0.0.1}" = "0.0.0.0" ]; then
    echo -e "${RED}  ✗ REGISTRY_BIND_IP=0.0.0.0 overlaps the mesh/public :5000 binds.${NC}"
    echo -e "${YELLOW}    Unset REGISTRY_BIND_IP in $INSTALL_DIR/.env (multi-bind is the supported topology) and re-run.${NC}"
    exit 1
fi
_reg_bind_ok=true
for _reg_entry in "REGISTRY_BIND_IP:${REGISTRY_BIND_IP:-127.0.0.1}" \
    "REGISTRY_MESH_BIND_IP:${REGISTRY_MESH_BIND_IP:-10.100.0.1}" \
    "REGISTRY_PUBLIC_BIND_IP:${REGISTRY_PUBLIC_BIND_IP:-127.0.0.1}"; do
    _reg_var="${_reg_entry%%:*}"
    _reg_ip="${_reg_entry#*:}"
    if echo "$_reg_ip" | grep -qE '^127\.[0-9]+\.[0-9]+\.[0-9]+$'; then
        continue
    fi
    if ! _registry_bind_ip_is_local "$_reg_ip" 2>/dev/null; then
        echo -e "${RED}  ✗ ${_reg_var}=${_reg_ip} is not assigned to this host — registry :5000 bind would fail.${NC}"
        _reg_bind_ok=false
    fi
done
if [ "$_reg_bind_ok" != "true" ]; then
    echo -e "${YELLOW}    Fix: set each to an IP from \`hostname -I\` (or 127.0.0.1), or unset REGISTRY_MESH_BIND_IP handling to platform-env (it parks a missing mesh on 127.0.0.2).${NC}"
    echo -e "${YELLOW}    Then re-run with --resume: sudo bash install.sh --resume${NC}"
    exit 1
fi
unset _reg_bind_ok _reg_entry _reg_var _reg_ip

# Ensure bind-mounted config paths exist before `docker compose up`.
ensure_infrastructure_permissions
# Pre-create caddy bind-mount directories (needed by compose volume driver)
mkdir -p "$INSTALL_DIR/caddy-config" "$INSTALL_DIR/caddy-logs"
# Pre-create the Traefik dynamic-config dir (canary WRR files). The
# traefik_dynamic volume bind-mounts it; a missing dir breaks the mount.
# If the dir was missing when the volume was first created, the volume
# object is stuck broken (mounts keep failing after the dir appears) —
# drop it so compose recreates it. Safe: bind volumes store nothing
# themselves; the files live in this dir (empty on fresh installs).
if [ ! -d "$INSTALL_DIR/traefik-dynamic" ]; then
    mkdir -p "$INSTALL_DIR/traefik-dynamic" 2>/dev/null || true
    docker volume rm smsly-hosting_traefik_dynamic >/dev/null 2>&1 || true
else
    mkdir -p "$INSTALL_DIR/traefik-dynamic" 2>/dev/null || true
fi
# AGENTS.md #24: crowdsec/cloudflare-bouncer.yaml is bind-mounted as a
# FILE by the crowdsec-cloudflare-bouncer service. Guarantee it (empty =
# the bouncer idles) before any compose up; drop daemon-poisoned dirs.
if [ -d "$INSTALL_DIR/crowdsec/cloudflare-bouncer.yaml" ] && [ ! -L "$INSTALL_DIR/crowdsec/cloudflare-bouncer.yaml" ]; then
    rmdir "$INSTALL_DIR/crowdsec/cloudflare-bouncer.yaml" 2>/dev/null || true
fi
mkdir -p "$INSTALL_DIR/crowdsec" 2>/dev/null || true
if [ ! -e "$INSTALL_DIR/crowdsec/cloudflare-bouncer.yaml" ]; then
    : > "$INSTALL_DIR/crowdsec/cloudflare-bouncer.yaml" 2>/dev/null || true
fi
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
        # Ensure entrypoint.sh has execute permissions (git may not preserve +x)
        chmod +x "$INSTALL_DIR"/scripts/alertmanager-entrypoint.sh  || true
        chmod +x "$INSTALL_DIR"/infrastructure/docker/infisical-gen-env.sh  || true
        # Profiles (medium/full) must be active or this `up` silently skips
        # loki/promtail/grafana — and a later `up --remove-orphans` from a
        # narrower profile set would delete them as orphans.
        ensure_compose_profiles
        if [ -f "infrastructure/docker/docker-compose.observability.yml" ]; then
            docker compose -f infrastructure/docker/docker-compose.observability.yml pull --ignore-pull-failures || \
                echo -e "${YELLOW}  ⚠ Observability stack pull failed (non-fatal)${NC}"
            docker compose -f infrastructure/docker/docker-compose.observability.yml up -d --pull always || \
                echo -e "${YELLOW}  ⚠ Observability stack start failed (non-fatal)${NC}"
        fi
    fi
    # ─── Build-cache services (apt-cacher-ng, verdaccio) ─────────────
    # These are profile-gated in compose (full/build-cache) so a plain
    # `up` never starts them — start explicitly by name (profile-proof,
    # no --remove-orphans). They provide package caches on the platform
    # network for builds configured to use a proxy; builds without
    # proxy settings are unaffected. Non-fatal by design.
    if [ "$MODE_AGENT_LITE" != "true" ]; then
        echo -e "${BLUE}  → Starting build-cache services (apt-cacher, verdaccio)...${NC}"
        timeout -k 5 240 docker compose -f "$COMPOSE_FILE" up -d apt-cacher verdaccio 2>&1 | tail -3 || \
            echo -e "${YELLOW}  ⚠ Build-cache services start failed (non-fatal)${NC}"
    fi
    # ─── Egress mirror (Alpine CDN rewrite for blocked networks) ───
    # Nixpacks app builds run `apk add` against dl-cdn.alpinelinux.org
    # with no mirror flag; on networks where that CDN is unreachable
    # every Alpine-based app build fails. Best-effort + boot-persistent
    # (own systemd unit installed below).
    if [ -f "$INSTALL_DIR/lib/egress_mirror.sh" ]; then
        # shellcheck disable=SC1090
        source "$INSTALL_DIR/lib/egress_mirror.sh" || true
        if command -v ensure_egress_mirror >/dev/null 2>&1; then
            ensure_egress_mirror || true
        fi
    fi
    if [ -f "$INSTALL_DIR/scripts/setup-egress-mirror.sh" ]; then
        chmod +x "$INSTALL_DIR/scripts/setup-egress-mirror.sh" || true
        cp "$INSTALL_DIR/scripts/smsly-egress-mirror.service" /etc/systemd/system/smsly-egress-mirror.service 2>/dev/null || true
        systemctl daemon-reload 2>/dev/null || true
        systemctl enable smsly-egress-mirror.service 2>/dev/null || \
            echo -e "${YELLOW}    ⚠ smsly-egress-mirror enable failed (non-fatal)${NC}"
    fi
    # ─── iptables-shim image (backend network scoping) ──────────────
    # Backend network scoping prefers pre-baked smsly/iptables-shim over
    # ad-hoc `apk add` inside plain alpine: on networks where
    # dl-cdn.alpinelinux.org is dead each fallback call hangs ~4 min as
    # a zombie container (observed live). The egress mirror above already
    # unblocks dl-cdn for this build. Idempotent: skipped when present.
    if ! docker image inspect smsly/iptables-shim:latest >/dev/null 2>&1; then
        if [ -d "$INSTALL_DIR/docker/iptables-shim" ]; then
            echo -e "${BLUE}  → Building iptables-shim image (network scoping)...${NC}"
            timeout -k 10 300 docker build -t smsly/iptables-shim:latest "$INSTALL_DIR/docker/iptables-shim" 2>&1 | tail -3 || \
                echo -e "${YELLOW}  ⚠ iptables-shim build failed (non-fatal; backend uses slower apk fallback)${NC}"
        fi
    fi
    # ─── WAF converge (open-appsec is full-gated AND env-gated) ────────
    # A plain `up` with the default full profiles starts the shadow WAF
    # even when OPENAPPSEC_ENABLED=0; converge it down so disabled stays
    # inert (and harden verify stays green). Guarded for old checkouts
    # whose inlined harden copy predates the reconcile helper.
    if command -v _harden_openappsec_reconcile >/dev/null 2>&1; then
        _harden_openappsec_reconcile || true
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

            # Create the infisical database in Postgres if it doesn't exist.
            # Endpoint follows the DB mode: local-ha uses the primary
            # container directly, patroni goes through HAProxy's write
            # port as the superuser (any node may be leader), external
            # has no local database (skip with a clear message).
            _db_container=""
            _db_user=""
            _infisical_db_host="smsly-postgres-primary"
            _infisical_via_haproxy=false
            # HA mode: smsly-postgres-primary
            if docker ps --format '{{.Names}}' | grep -q '^smsly-postgres-primary$'; then
                _db_container="smsly-postgres-primary"
                _db_user="${POSTGRES_USER:-smsly_admin}"
            # Standard mode: smsly-hosting-db-1
            elif docker ps --format '{{.Names}}' | grep -q '^smsly-hosting-db-1$'; then
                _db_container="smsly-hosting-db-1"
                _db_user="${POSTGRES_USER:-postgres}"
            # Patroni HA: any healthy node means the cluster is up; writes
            # go through HAProxy so leadership never matters here.
            elif docker ps --format '{{.Names}}' | grep -qE '^smsly-patroni-[123]$'; then
                _infisical_db_host="haproxy"
                _infisical_via_haproxy=true
            fi
            # The compose file interpolates INFISICAL_DB_HOST (defaults to
            # smsly-postgres-primary); export the mode-correct value.
            export INFISICAL_DB_HOST="$_infisical_db_host"
            if [ "$_infisical_via_haproxy" = "true" ]; then
                if [ -n "${PATRONI_SUPERUSER_PASSWORD:-}" ]; then
                    _db_exists=$(timeout 30 docker run --rm --network smsly-net \
                        -e PGPASSWORD="$PATRONI_SUPERUSER_PASSWORD" postgres:16-alpine \
                        psql -h haproxy -p 5000 -U postgres -d postgres -tc \
                        "SELECT 1 FROM pg_database WHERE datname='infisical'"  | tr -d '[:space:]' || true)
                    if [ "$_db_exists" != "1" ]; then
                        timeout 30 docker run --rm --network smsly-net \
                            -e PGPASSWORD="$PATRONI_SUPERUSER_PASSWORD" postgres:16-alpine \
                            psql -h haproxy -p 5000 -U postgres -d postgres -c \
                            "CREATE DATABASE infisical;"  && \
                            echo -e "${GREEN}  ✓ Created infisical database (via haproxy)${NC}" || \
                            echo -e "${YELLOW}  ⚠ Could not create infisical database (may already exist)${NC}"
                    fi
                else
                    echo -e "${YELLOW}  ⚠ PATRONI_SUPERUSER_PASSWORD unset — skipping infisical database creation${NC}"
                fi
            elif [ -n "$_db_container" ]; then
                _db_exists=$(timeout 30 docker exec "$_db_container" psql -U "${_db_user}" -d "${POSTGRES_DB:-smsly_hosting}" -tc \
                    "SELECT 1 FROM pg_database WHERE datname='infisical'"  | tr -d '[:space:]' || true)
                if [ "$_db_exists" != "1" ]; then
                    timeout 30 docker exec "$_db_container" psql -U "${_db_user}" -d "${POSTGRES_DB:-smsly_hosting}" -c \
                        "CREATE DATABASE infisical;"  && \
                        echo -e "${GREEN}  ✓ Created infisical database${NC}" || \
                        echo -e "${YELLOW}  ⚠ Could not create infisical database (may already exist)${NC}"
                fi
            else
                echo -e "${YELLOW}  ⚠ No Postgres container found (external DB mode?) — skipping infisical database creation${NC}"
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

            # Compose reads env_file from the HOST, not from inside a
            # volume — extract the generated secrets to a host file.
            export INFISICAL_ENV_FILE="$INSTALL_DIR/.infisical.env"
            _infisical_ready=""
            if ! docker run --rm -v infisical_data:/data alpine:3.19 \
                    cat /data/infisical.env > "$INFISICAL_ENV_FILE" 2>/dev/null; then
                echo -e "${YELLOW}  ⚠ Could not read Infisical env from volume — skipping Infisical${NC}"
            elif ! grep -q "^ENCRYPTION_KEY=.\+" "$INFISICAL_ENV_FILE" || ! grep -q "^AUTH_SECRET=.\+" "$INFISICAL_ENV_FILE"; then
                echo -e "${YELLOW}  ⚠ Infisical env incomplete — skipping Infisical${NC}"
            else
                chmod 600 "$INFISICAL_ENV_FILE"
                # Persist the host path so later `up` invocations (update
                # flows, manual compose) resolve the same env_file without
                # relying on this shell's export.
                env_set_value "$INSTALL_DIR/.env" "INFISICAL_ENV_FILE" "$INFISICAL_ENV_FILE"
                # DB credentials: the compose file defaults
                # (postgres/postgres) never match HA hosts — export the real
                # ones for interpolation. Patroni authenticates as the
                # superuser through HAProxy (see above).
                _pg_pass="$(grep '^POSTGRES_PASSWORD=' "$INSTALL_DIR/.env" 2>/dev/null | cut -d= -f2-)"
                if [ "$_infisical_via_haproxy" = "true" ]; then
                    _db_user="postgres"
                    _pg_pass="${PATRONI_SUPERUSER_PASSWORD:-}"
                fi
                if [ -n "${_db_user:-}" ] && [ -n "$_pg_pass" ]; then
                    export POSTGRES_USER="$_db_user" POSTGRES_PASSWORD="$_pg_pass"
                    # INFISICAL_DB_HOST was exported during DB detection
                    # above; re-export defensively (this block may run in
                    # flows where detection was skipped).
                    export INFISICAL_DB_HOST="$_infisical_db_host"
                    _redis_pass="$(grep '^REDIS_PASSWORD=' "$INSTALL_DIR/.env" 2>/dev/null | cut -d= -f2-)"
                    if [ -n "$_redis_pass" ]; then
                        export REDIS_PASSWORD="$_redis_pass"
                        _infisical_ready=1
                    else
                        echo -e "${YELLOW}  ⚠ No Redis password — skipping Infisical${NC}"
                    fi
                else
                    echo -e "${YELLOW}  ⚠ No Postgres credentials — skipping Infisical${NC}"
                fi
            fi
            if [ -n "$_infisical_ready" ]; then
                # SITE_URL must be a valid absolute URL or the app crashes
                # at boot ("Invalid URL"). The compose default assumes a
                # domain (https://secrets.<domain>); in IP mode use the
                # loopback-published port directly. Persisted so later
                # manual `up` invocations resolve it without this export.
                _site_domain="$(grep '^DOMAIN=' "$INSTALL_DIR/.env" 2>/dev/null | cut -d= -f2-)"
                if echo "${_site_domain:-}" | grep -qE '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$'; then
                    export INFISICAL_SITE_URL="http://${_site_domain}:8085"
                else
                    export INFISICAL_SITE_URL="https://secrets.${_site_domain:-localhost}"
                fi
                env_set_value "$INSTALL_DIR/.env" "INFISICAL_SITE_URL" "$INFISICAL_SITE_URL"
                # Explicit project name; never --remove-orphans on a shared
                # directory (AGENTS.md #16).
                docker compose -p smsly-infisical --env-file "$INSTALL_DIR/.env" \
                    -f "$_INFISICAL_COMPOSE" up -d  && \
                    echo -e "${GREEN}  ✓ Infisical is running${NC}" || \
                    echo -e "${YELLOW}  ⚠ Infisical startup failed (non-fatal — secrets remain in .env)${NC}"
                unset POSTGRES_USER POSTGRES_PASSWORD REDIS_PASSWORD INFISICAL_DB_HOST INFISICAL_SITE_URL
            fi
        fi
    fi

    set_checkpoint "stack_deployed"

    # Docker login now that the registry is actually running
    docker_login
    # Retry the Envoy sidecar image build+push now that registry auth and
    # the registry itself exist. The harden-phase attempt runs before
    # fresh_config writes REGISTRY_PASSWORD, so it always 401s on a true
    # fresh host (2026-09-12); without this retry the image stays local-only
    # and the catalog stays empty. Non-fatal: deploy-time self-heal covers it.
    if command -v _harden_envoy_image_bootstrap >/dev/null 2>&1; then
        _harden_envoy_image_bootstrap || true
    fi
fi
if [ "$STACK_DEPLOYED_FROM_CHECKPOINT" = "true" ]; then
    reconcile_compose_stack_after_resume
fi
