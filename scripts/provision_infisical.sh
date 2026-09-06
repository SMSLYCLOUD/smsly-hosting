#!/bin/bash
# Provision + activate Infisical secret manager end to end (idempotent).
#
# Automates everything automatable:
#   1. infisical_data volume, infisical Postgres database, generated env
#   2. host env file ($INSTALL_DIR/.infisical.env, 0600) extracted from volume
#   3. compose up (NO --remove-orphans — see AGENTS.md #16), wait healthy
#   4. first-user admin signup via API (saved to .infisical-admin, 0600)
#   5. optional wiring: if INFISCAL_TOKEN / INFISICAL_SERVICE_TOKEN is
#      supplied in the environment, writes it to .env, restarts the
#      backend, and pushes platform secrets (sync_infisical_secrets).
#
# Service-token minting stays a 2-click UI step (Organization Settings →
# Service Tokens): token APIs differ across Infisical versions and blind
# guessing risks junk state. Re-run this script with the token exported
# to finish wiring non-interactively.
set -u

INSTALL_DIR="${INSTALL_DIR:-/opt/smsly-hosting}"
COMPOSE="$INSTALL_DIR/infrastructure/docker/docker-compose.infisical.yml"
ENV_FILE="$INSTALL_DIR/.infisical.env"
ADMIN_FILE="$INSTALL_DIR/.infisical-admin"
DOTENV="$INSTALL_DIR/.env"
BASE="http://127.0.0.1:8085"

log()  { echo -e "  $1"; }
ok()   { echo -e "  \033[0;32m✓ $1\033[0m"; }
warn() { echo -e "  \033[1;33m⚠ $1\033[0m"; }
fail() { echo -e "  \033[0;31m✗ $1\033[0m"; exit 1; }

command -v docker >/dev/null || fail "docker not found"
[ -f "$COMPOSE" ] || fail "compose file missing: $COMPOSE"
[ -f "$DOTENV" ] || fail ".env missing: $DOTENV"

# ── 1. already running AND healthy? ─────────────────────────────────
_existing="$(docker ps --filter "name=infisical" --format '{{.Names}}' | head -1)"
if [ -n "$_existing" ] && curl -sf -m 10 "$BASE/api/status" > /dev/null 2>&1; then
    ok "Infisical already running and healthy ($_existing)"
else
    [ -n "$_existing" ] && docker rm -f "$_existing" > /dev/null 2>&1 || true
    log "Provisioning Infisical..."
    docker volume create infisical_data > /dev/null || true

    # ── 2. database ──────────────────────────────────────────────────
    _db_container=""
    if docker ps --format '{{.Names}}' | grep -q '^smsly-postgres-primary$'; then
        _db_container="smsly-postgres-primary"; _db_user="${POSTGRES_USER:-smsly_admin}"
    elif docker ps --format '{{.Names}}' | grep -q '^smsly-hosting-db-1$'; then
        _db_container="smsly-hosting-db-1"; _db_user="${POSTGRES_USER:-postgres}"
    fi
    if [ -n "$_db_container" ]; then
        # shellcheck disable=SC2154
        _db_exists=$(timeout 30 docker exec "$_db_container" psql -U "${_db_user}" -d "${POSTGRES_DB:-smsly_hosting}" -tc \
            "SELECT 1 FROM pg_database WHERE datname='infisical'" | tr -d '[:space:]' || true)
        if [ "$_db_exists" != "1" ]; then
            timeout 30 docker exec "$_db_container" psql -U "${_db_user}" -d "${POSTGRES_DB:-smsly_hosting}" -c \
                "CREATE DATABASE infisical;" > /dev/null && ok "Created infisical database" \
                || warn "Could not create infisical database (may already exist)"
        else
            ok "Infisical database exists"
        fi
    else
        warn "No Postgres container found — Infisical needs its database"
    fi

    # ── 3. generate + extract env ────────────────────────────────────
    _gen_script="$INSTALL_DIR/infrastructure/docker/infisical-gen-env.sh"
    if [ -f "$_gen_script" ]; then
        docker run --rm -v infisical_data:/data -v "$_gen_script":/tmp/infisical-gen-env.sh:ro \
            alpine:3.19 sh /tmp/infisical-gen-env.sh /data/infisical.env > /dev/null \
            || warn "Env generation had issues (may already exist)"
    fi
    docker run --rm -v infisical_data:/data alpine:3.19 cat /data/infisical.env > "$ENV_FILE" 2>/dev/null \
        || fail "Could not read generated env from infisical_data volume"
    chmod 600 "$ENV_FILE"
    grep -q "^ENCRYPTION_KEY=.\+" "$ENV_FILE" || fail "ENCRYPTION_KEY missing in $ENV_FILE"
    grep -q "^AUTH_SECRET=.\+" "$ENV_FILE" || fail "AUTH_SECRET missing in $ENV_FILE"
    ok "Secrets extracted to $ENV_FILE"

    # ── 4. compose up (never --remove-orphans on a shared directory) ─
    export INFISICAL_ENV_FILE="$ENV_FILE"
    # DB credentials: the compose file defaults (postgres/postgres) never
    # match HA hosts — export the real ones for interpolation.
    _pg_pass="$(grep '^POSTGRES_PASSWORD=' "$DOTENV" 2>/dev/null | cut -d= -f2-)"
    [ -n "${_db_user:-}" ] && [ -n "$_pg_pass" ] \
        || fail "No Postgres credentials (need POSTGRES_PASSWORD in .env)"
    export POSTGRES_USER="$_db_user" POSTGRES_PASSWORD="$_pg_pass"
    _redis_pass="$(grep '^REDIS_PASSWORD=' "$DOTENV" 2>/dev/null | cut -d= -f2-)"
    [ -n "$_redis_pass" ] || fail "No Redis credentials (need REDIS_PASSWORD in .env)"
    export REDIS_PASSWORD="$_redis_pass"
    docker rm -f docker-infisical-1 > /dev/null 2>&1 || true
    # shellcheck disable=SC2154
    if docker compose -p smsly-infisical --env-file "$DOTENV" -f "$COMPOSE" up -d; then
        ok "Infisical starting"
    else
        fail "docker compose up failed"
    fi
    unset POSTGRES_USER POSTGRES_PASSWORD REDIS_PASSWORD
fi

# ── 5. wait healthy ──────────────────────────────────────────────────
log "Waiting for Infisical health (up to 5 min)..."
_healthy=""
for _i in $(seq 1 30); do
    if curl -sf -m 10 "$BASE/api/status" > /dev/null 2>&1; then _healthy=1; break; fi
    sleep 10
done
[ -n "$_healthy" ] || fail "Infisical did not become healthy at $BASE"
ok "Infisical healthy at $BASE"

# ── 6. admin signup (first user = admin) ─────────────────────────────
if [ -f "$ADMIN_FILE" ] && grep -q "^ADMIN_EMAIL=.\+" "$ADMIN_FILE" 2>/dev/null; then
    ok "Admin account already bootstrapped ($ADMIN_FILE)"
else
    log "Creating initial admin (first signup becomes admin)..."
    _admin_email="admin@smsly.local"
    _admin_pass="$(openssl rand -base64 24 2>/dev/null || head -c 24 /dev/urandom | base64 | head -c 32)"
    _signup_code=$(curl -s -m 30 -o /tmp/infisical_signup.json -w "%{http_code}" \
        -X POST "$BASE/api/v1/auth/signup" \
        -H "Content-Type: application/json" \
        -d "{\"email\":\"$_admin_email\",\"password\":\"$_admin_pass\",\"firstName\":\"SMSLY\",\"lastName\":\"Admin\"}" || echo "000")
    if [ "$_signup_code" = "200" ] || [ "$_signup_code" = "201" ]; then
        {
            echo "ADMIN_EMAIL=$_admin_email"
            echo "ADMIN_PASSWORD=$_admin_pass"
            echo "SIGNUP_AT=$(date -u +%FT%TZ)"
        } > "$ADMIN_FILE"
        chmod 600 "$ADMIN_FILE"
        ok "Admin created — credentials in $ADMIN_FILE"
    else
        warn "Signup API returned HTTP $_signup_code — an admin may already exist."
        warn "Sign up manually if needed, then mint a service token in the UI."
        cat /tmp/infisical_signup.json 2>/dev/null | head -c 300; echo
    fi
fi

# ── 7. optional wiring with a supplied token ─────────────────────────
_SUPPLIED_TOKEN="${INFISCAL_TOKEN:-${INFISICAL_SERVICE_TOKEN:-}}"
if [ -n "$_SUPPLIED_TOKEN" ]; then
    log "Wiring service token into .env..."
    if grep -q "^INFISICAL_SERVICE_TOKEN=" "$DOTENV"; then
        # shellcheck disable=SC2154
        sed -i "s|^INFISICAL_SERVICE_TOKEN=.*|INFISICAL_SERVICE_TOKEN=$_SUPPLIED_TOKEN|" "$DOTENV"
    else
        echo "INFISICAL_SERVICE_TOKEN=$_SUPPLIED_TOKEN" >> "$DOTENV"
    fi
    chmod 600 "$DOTENV"
    _backend="$(docker ps --format '{{.Names}}' | grep -E '^smsly-hosting-backend(-1)?$' | head -1)"
    if [ -n "$_backend" ]; then
        docker restart "$_backend" > /dev/null && ok "Backend restarted" || warn "Backend restart failed"
        sleep 15
        timeout 90 docker exec "$_backend" python manage.py sync_infisical_secrets --push \
            && ok "Platform secrets pushed to Infisical" \
            || warn "Secret push failed — retry: docker exec $_backend python manage.py sync_infisical_secrets --push"
    fi
else
    echo
    echo "Next (2 minutes, one time):"
    echo "  1. Open the Infisical UI (Traefik: https://secrets.<your-domain>, or SSH tunnel to 127.0.0.1:8085)"
    echo "     admin email: $(grep -h ^ADMIN_EMAIL= "$ADMIN_FILE" 2>/dev/null | cut -d= -f2 || echo admin@smsly.local)"
    echo "     admin password: in $ADMIN_FILE"
    echo "  2. Organization Settings → Service Tokens → create → copy the token, then re-run:"
    echo "     INFISICAL_SERVICE_TOKEN=<token> bash $0"
fi

echo
ok "Infisical provisioning complete"
