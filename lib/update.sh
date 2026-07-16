#!/bin/bash
# Grid by SMSLY - Update Mode Module
# Sourced by install.sh for --update

if [ -n "$UPDATE_MODE" ]; then
    echo -e "${YELLOW}[UPDATE] Running in update mode: $UPDATE_MODE${NC}"
    echo -e "${BLUE}  -> Safe update: preserves database/redis volumes and addon data.${NC}"

    # Ensure repo cache directory exists for user service builds
    mkdir -p /opt/smsly-cache/repos
    chmod 775 /opt/smsly-cache
    chown -R 1000:1000 /opt/smsly-cache 2>/dev/null || true
    mkdir -p /opt/smsly-hosting/builds
    chmod 775 /opt/smsly-hosting/builds
    chown -R 1000:1000 /opt/smsly-hosting/builds 2>/dev/null || true

    # ─── Fix .env permissions BEFORE any containers start ────────────────────
    # The docker-compose.prod.yml mounts .env into the backend container.
    # If .env has 600 permissions (created by old install.sh), the container
    # can't read it and Django crashes with PermissionError.
    # The backend container runs as UID 1000 (smsly user), so the file must be
    # writable by that user to allow the domain-config signal to sync back to .env.
    if [ -f "$INSTALL_DIR/.env" ]; then
        chown root:1000 "$INSTALL_DIR/.env" 2>/dev/null || true
        chmod 640 "$INSTALL_DIR/.env" 2>/dev/null || true
        echo -e "${BLUE}  → Fixed .env permissions to 640 (readable by container UID 1000)${NC}"
    fi

    # ─── Pre-flight ──────────────────────────────────────────────────────────
    if [ "$EUID" -ne 0 ]; then
        echo -e "${RED}✗ Please run as root (sudo bash install.sh --update)${NC}"
        exit 1
    fi

    export PATH="/usr/local/bin:$PATH"
    check_internet
    check_hardware
    check_caddy_conflict
    ensure_system_swap
    ensure_security_tools || true


    # ─── Security: bootstrap (fire-and-forget) ────────────────────────────
    if [ -f "$INSTALL_DIR/lib/harden.sh" ]; then
        source "$INSTALL_DIR/lib/harden.sh"
        harden_security_bootstrap
    fi

    # ─── Registry TLS cert check ─────────────────────────────────────────
    # Regenerate + restart if the cert and key don't match, so the
    # registry container doesn't crash-loop with "private key does not
    # match public key".
    _registry_cert_ok() {
        [ -f "$INSTALL_DIR/certs/registry.key" ] || return 1
        [ -f "$INSTALL_DIR/certs/registry.crt" ] || return 1
        local _cmod _kmod
        _cmod="$(openssl x509 -in "$INSTALL_DIR/certs/registry.crt" -noout -modulus 2>/dev/null | openssl sha256)" || return 1
        _kmod="$(openssl rsa  -in "$INSTALL_DIR/certs/registry.key" -noout -modulus 2>/dev/null | openssl sha256)" || return 1
        [ "$_cmod" = "$_kmod" ]
    }
    if ! _registry_cert_ok; then
        echo -e "${BLUE}  → Registry TLS cert/key missing or mismatch — generating...${NC}"
        mkdir -p "$INSTALL_DIR/certs"
        _tmp="$(mktemp -d)"
        openssl req -x509 -nodes -days 3650 -newkey rsa:2048 \
            -keyout "${_tmp}/registry.key" \
            -out    "${_tmp}/registry.crt" \
            -subj "/CN=registry" \
            -addext "subjectAltName=DNS:registry,DNS:localhost,IP:127.0.0.1,IP:10.100.0.1" 2>/dev/null && {
            mv "${_tmp}/registry.key" "$INSTALL_DIR/certs/registry.key"
            mv "${_tmp}/registry.crt" "$INSTALL_DIR/certs/registry.crt"
            chmod 644 "$INSTALL_DIR/certs/registry.crt"
            chmod 600 "$INSTALL_DIR/certs/registry.key"
            echo -e "${BLUE}  → Restarting registry container...${NC}"
            docker restart smsly-hosting-registry-1 2>/dev/null || true
        } || true
        rm -rf "$_tmp" 2>/dev/null || true
    fi
    mkdir -p "$INSTALL_DIR/auth"
    if [ ! -f "$INSTALL_DIR/auth/htpasswd" ] || [ -z "${REGISTRY_PASSWORD:-}" ] || [ -z "${REGISTRY_USER:-}" ]; then
        echo -e "${BLUE}  → Ensuring registry htpasswd authentication exists...${NC}"
        REGISTRY_PASS="${REGISTRY_PASSWORD:-$(python3 -c "import secrets; print(secrets.token_urlsafe(18))" 2>/dev/null || openssl rand -hex 12 2>/dev/null || { echo "ERROR: Cannot generate registry password" >&2; exit 1; })}"
        if command -v htpasswd >/dev/null 2>&1; then
            htpasswd -Bbn "${REGISTRY_USER:-smsly-registry}" "$REGISTRY_PASS" > "$INSTALL_DIR/auth/htpasswd"
        else
            python3 -c "
import bcrypt, sys
pw = sys.argv[1] if len(sys.argv) > 1 else '$REGISTRY_PASS'
print('${REGISTRY_USER:-smsly-registry}:' + bcrypt.hashpw(pw.encode(), bcrypt.gensalt(10)).decode())
" "$REGISTRY_PASS" > "$INSTALL_DIR/auth/htpasswd" 2>/dev/null || true
        fi
        env_set_value "$INSTALL_DIR/.env" "REGISTRY_USER" "${REGISTRY_USER:-smsly-registry}" 2>/dev/null || true
        env_set_value "$INSTALL_DIR/.env" "REGISTRY_PASSWORD" "$REGISTRY_PASS" 2>/dev/null || true
        chmod 600 "$INSTALL_DIR/auth/htpasswd" 2>/dev/null || true
    fi

    # ─── Self-heal: missing secrets (update paths can miss secret generation) ─
    echo -e "${BLUE}  → Checking for missing secrets and generating if needed...${NC}"
    _ensure_secret() {
        local _name="$1" _bytes="$2"
        if [ -z "${!_name:-}" ]; then
            local _val="$(python3 -c "import secrets; print(secrets.token_hex($_bytes))" 2>/dev/null || openssl rand -hex "$_bytes" 2>/dev/null || true)"
            if [ -n "$_val" ]; then
                printf -v "$_name" '%s' "$_val"
                env_set_value "$INSTALL_DIR/.env" "$_name" "$_val" 2>/dev/null || true
                echo -e "${BLUE}    → Generated $_name${NC}"
            fi
        fi
    }
    _ensure_secret REGISTRY_HTTP_SECRET 32
    _ensure_secret REPLICATION_PASSWORD 32
    _ensure_secret SENTINEL_PASSWORD 32
    _ensure_secret CROWDSEC_BOUNCER_KEY 32
    _ensure_secret COSIGN_PASSWORD 32

    # ─── Self-heal: Cosign keypair ──────────────────────────────────────────
    if command -v cosign >/dev/null 2>&1; then
        mkdir -p "$INSTALL_DIR/cosign-keys"
        COSIGN_PRIVATE_KEY_PATH="$INSTALL_DIR/cosign-keys/cosign.key"
        COSIGN_PUBLIC_KEY_PATH="$INSTALL_DIR/cosign-keys/cosign.pub"
        if [ ! -f "$COSIGN_PRIVATE_KEY_PATH" ] || [ ! -f "$COSIGN_PUBLIC_KEY_PATH" ]; then
            echo -e "${BLUE}  → Cosign keypair missing — generating...${NC}"
            COSIGN_PASSWORD="${COSIGN_PASSWORD:-$(python3 -c "import secrets; print(secrets.token_hex(32))" 2>/dev/null || openssl rand -hex 32 2>/dev/null || true)}"
            COSIGN_PASSWORD="$COSIGN_PASSWORD" cosign generate-key-pair 2>/dev/null || true
            if [ -f cosign.key ]; then
                mv cosign.key "$COSIGN_PRIVATE_KEY_PATH"
                mv cosign.pub "$COSIGN_PUBLIC_KEY_PATH"
                chmod 600 "$COSIGN_PRIVATE_KEY_PATH"
                chmod 644 "$COSIGN_PUBLIC_KEY_PATH"
                env_set_value "$INSTALL_DIR/.env" "COSIGN_PASSWORD" "$COSIGN_PASSWORD" 2>/dev/null || true
                env_set_value "$INSTALL_DIR/.env" "COSIGN_PRIVATE_KEY_PATH" "$COSIGN_PRIVATE_KEY_PATH" 2>/dev/null || true
                echo -e "${GREEN}    ✓ Cosign keypair created${NC}"
            else
                echo -e "${YELLOW}    ⚠ cosign generate-key-pair ran but no output — skipping${NC}"
            fi
        else
            # Key exists but password might be missing
            if [ -z "${COSIGN_PASSWORD:-}" ]; then
                COSIGN_PASSWORD="$(python3 -c "import secrets; print(secrets.token_hex(32))" 2>/dev/null || openssl rand -hex 32 2>/dev/null || true)"
                env_set_value "$INSTALL_DIR/.env" "COSIGN_PASSWORD" "$COSIGN_PASSWORD" 2>/dev/null || true
            fi
        fi
    fi

    # ─── Git Safety ──────────────────────────────────────────────────────────
    # Prevents "dubious ownership" errors on production VPS
    git config --global --add safe.directory "$INSTALL_DIR" 2>/dev/null || true

    ensure_infrastructure_permissions

    if [ ! -d "$INSTALL_DIR/.git" ]; then
        echo -e "${RED}✗ No git repository found at $INSTALL_DIR. Run a fresh install first.${NC}"
        exit 1
    fi

    if [ ! -f "$INSTALL_DIR/.env" ]; then
        echo -e "${RED}✗ No .env file found. Run a fresh install first.${NC}"
        exit 1
    fi

    cd "$INSTALL_DIR"
    if [ "${SMSLY_SKIP_GIT_SYNC:-false}" = "true" ]; then
        set_checkpoint "update_git_synced"
    elif [ "${SMSLY_REEXEC:-}" != "1" ]; then
        # Every new update attempt must hit GitHub. Checkpoints are only for
        # resume/re-exec within the same attempt, not for skipping future pulls.
        clear_checkpoint "update_git_synced"
    fi

    echo -e "${BLUE}  -> Validating existing .env configuration...${NC}"
    ensure_env_runtime_defaults "$INSTALL_DIR/.env"
    if ! validate_env_file "$INSTALL_DIR/.env"; then
        echo -e "${RED}x .env validation failed. Fix the values above and re-run update.${NC}"
        exit 1
    fi
    set_checkpoint "update_preflight_done"

if ! is_checkpoint_done "update_git_synced"; then


    # ─── Git Stash + Pull (CRITICAL BLINDSPOT FIX) ───────────────────────────
    echo -e "${BLUE}  → Checking for local changes...${NC}"
    # Save pre-update HEAD for reliable redeploy detection after git operations.
    # Priority: 1) env var from re-exec (survives exec boundary),
    #           2) stale file from failed previous update (survives process death),
    #           3) current HEAD (normal first run).
    PRE_UPDATE_HEAD=""
    if [ -n "${SMSLY_PRE_UPDATE_HEAD:-}" ]; then
        PRE_UPDATE_HEAD="$SMSLY_PRE_UPDATE_HEAD"
    elif [ -f "$INSTALL_DIR/.pre-update-head" ] && [ -s "$INSTALL_DIR/.pre-update-head" ]; then
        PRE_UPDATE_HEAD="$(cat "$INSTALL_DIR/.pre-update-head" 2>/dev/null || true)"
        echo -e "${YELLOW}  ⚠ Recovering pre-update baseline from prior incomplete run (${PRE_UPDATE_HEAD:0:7})${NC}"
    else
        PRE_UPDATE_HEAD="$(git rev-parse HEAD 2>/dev/null || true)"
    fi
    echo "$PRE_UPDATE_HEAD" > "$INSTALL_DIR/.pre-update-head" 2>/dev/null || true
    ensure_local_ignores
    if [ -n "$(git status --porcelain 2>/dev/null)" ]; then
        echo -e "${YELLOW}  ⚠ Local changes detected — stashing before pull${NC}"
        git stash push --include-untracked -m "install-update-$(date +%s)"
        touch "$INSTALL_DIR/.git-stash-marker"
    fi

    echo -e "${BLUE}  → Force-pulling latest code from GitHub ($SMSLY_BRANCH)...${NC}"

    # Track if git update succeeded
    GIT_UPDATE_OK=true

    if ! git fetch origin "$SMSLY_BRANCH" >/dev/null 2>&1; then
        echo -e "${RED}  ✗ Git fetch failed for $SMSLY_BRANCH. SSL verification is always enforced — check network or CA certificates.${NC}"
        GIT_UPDATE_OK=false
    fi

    if [ "$GIT_UPDATE_OK" = "true" ]; then
        if ! git checkout -B "$SMSLY_BRANCH" "origin/$SMSLY_BRANCH" >/dev/null 2>&1; then
            echo -e "${RED}  ✗ Git checkout failed for $SMSLY_BRANCH.${NC}"
            GIT_UPDATE_OK=false
        else
            git branch --set-upstream-to="origin/$SMSLY_BRANCH" "$SMSLY_BRANCH" >/dev/null 2>&1 || true
        fi
    fi

    # Fallback if git failed but a local bundle was provided
    if [ "$GIT_UPDATE_OK" = "false" ]; then
        if [ -n "${SMSLY_INSTALL_WORKDIR:-}" ] && [ -d "${SMSLY_INSTALL_WORKDIR}" ]; then
            echo -e "${BLUE}  → Fallback: Synchronizing from pre-uploaded source bundle...${NC}"
            # Use rsync if available, otherwise cp. Exclude .git to preserve local repo state if any.
            if command -v rsync >/dev/null 2>&1; then
                rsync -rtv --exclude='.git' "${SMSLY_INSTALL_WORKDIR}/" "$INSTALL_DIR/"
            else
                cp -rv "${SMSLY_INSTALL_WORKDIR}/"* "$INSTALL_DIR/" 2>/dev/null || true
            fi
            echo -e "${GREEN}  ✓ Fallback synchronization complete.${NC}"
        else
            echo -e "${RED}✗ Git update failed and no local fallback bundle available. Update may be incomplete.${NC}"
        fi
    fi
    set_checkpoint "update_git_synced"
fi

    # ─── Self-Update Check ──────────────────────────────────────────────────
    # If the installer itself was updated, we MUST re-execute it to pick up
    # new service names (e.g., celery-deploy) and self-healing logic.
    if [[ "${SMSLY_REEXEC:-}" != "1" ]]; then
        echo -e "${GREEN}  → Installer updated. Re-executing for safe synchronization...${NC}"
        export SMSLY_REEXEC=1
        export NO_SCREEN=true
        export SKIP_SCREEN=1
        # Preserve pre-update HEAD across re-exec so the SHA comparison
        # uses the TRUE baseline commit (before git pull), not the
        # already-updated HEAD (which would prevent redeploy detection).
        export SMSLY_PRE_UPDATE_HEAD="$PRE_UPDATE_HEAD"
        # Release the lock before re-exec so the new process can acquire it.
        # Closing FD 9 releases the flock.
        exec 9>&- 2>/dev/null || true
        exec env SMSLY_REEXEC=1 NO_SCREEN=true SKIP_SCREEN=1 SMSLY_PRE_UPDATE_HEAD="$PRE_UPDATE_HEAD" PATH="/usr/local/bin:$PATH" bash "$SCRIPT_PATH" --no-screen "$@"
    fi

    echo -e "${BLUE}  → Applying platform/domain overrides...${NC}"
    apply_env_platform_overrides "$INSTALL_DIR/.env"
    ensure_env_runtime_defaults "$INSTALL_DIR/.env"
    if ! validate_env_file "$INSTALL_DIR/.env"; then
        echo -e "${RED}x .env validation failed after applying overrides. Fix the values and retry.${NC}"
        exit 1
    fi

    # Clean up stash marker (pull succeeded, we commit to the new code)
    rm -f "$INSTALL_DIR/.git-stash-marker"

    # ─── Validate required files exist ───────────────────────────────────────
    echo -e "${BLUE}  → Validating deployment files...${NC}"

    if [ ! -f "$COMPOSE_FILE" ]; then
        echo -e "${RED}✗ Missing $COMPOSE_FILE — cannot deploy.${NC}"
        exit 1
    fi

    if [ ! -f "backend/Dockerfile" ]; then
        echo -e "${RED}✗ Missing backend/Dockerfile${NC}"
        exit 1
    fi

    if [ "$MODE_NODE" = "true" ] && [ ! -f "backend/requirements.txt" ]; then
        echo -e "${RED}✗ Missing backend/requirements.txt${NC}"
        exit 1
    fi

    if [ "$MODE_NODE" != "true" ] && [ ! -f "frontend/Dockerfile" ]; then
        echo -e "${RED}✗ Missing frontend/Dockerfile${NC}"
        exit 1
    fi

    echo -e "${GREEN}  ✓ All required files present${NC}"

    # ─── Disk space check (prevents mid-build failure) ───────────────────────
    DISK_AVAIL_MB=$(df -BM "$INSTALL_DIR" | tail -1 | awk '{print $4}' | tr -d 'M')
    if [ "$DISK_AVAIL_MB" -lt 5000 ]; then
        echo -e "${YELLOW}  ⚠ Disk space low (${DISK_AVAIL_MB}MB). Running Docker prune...${NC}"
        docker container prune -f || true
        docker image prune -f || true # Only dangling images by default

        if [ "$DISK_AVAIL_MB" -lt 2000 ]; then
            echo -e "${RED}  ⚠ Disk space CRITICAL. Running aggressive prune...${NC}"
            docker image prune -af || true
            bust_core_build_cache
        fi

        DISK_AVAIL_MB=$(df -BM "$INSTALL_DIR" | tail -1 | awk '{print $4}' | tr -d 'M')
        echo -e "${BLUE}  → Disk space after cleanup: ${DISK_AVAIL_MB}MB${NC}"
        if [ "$DISK_AVAIL_MB" -lt 1000 ]; then
            echo -e "${RED}  ✗ Still insufficient disk space (${DISK_AVAIL_MB}MB). Need at least 1GB.${NC}"
            exit 1
        fi
    fi

    # ─── Targeted Rebuild (CRITICAL BLINDSPOT FIX: --no-deps) ────────────────
    # Using --no-deps prevents cascade restart of unrelated services
    if ! is_checkpoint_done "update_containers_rebuilt"; then

    # ─── Safe Update Protocol ─────────────────────────────────────────────
    if [ -f "$INSTALL_DIR/scripts/safe-update.sh" ]; then
        source "$INSTALL_DIR/scripts/safe-update.sh"
        safe_update_snapshot
        safe_update_preflight || { echo -e "${RED}  ✗ Pre-flight checks failed — aborting update${NC}"; exit 1; }
        trap 'safe_update_rollback' ERR
    fi

    # ─── Fix script permissions (Git on Windows strips execute bits) ──────────
    echo -e "${BLUE}  → Fixing script permissions...${NC}"
    find "$INSTALL_DIR" -name "*.sh" -exec chmod +x {} \;
    echo -e "${GREEN}  ✓ Script permissions fixed${NC}"

    # SECURITY: SSH strict host-key checking is ALWAYS enforced. The previous
    # installer rewrote apps/deployments/services/provisioner.py and
    # ssh_client.py to force paramiko.AutoAddPolicy() on every connection, which
    # accepts any host fingerprint on first contact and is an SSH-MITM backdoor
    # (CVE-class: TOFU on every deploy target). Strict checking is now the
    # default; the in-app provisioner/ssh_client code controls policy via the
    # SMSLY_STRICT_SSH_HOST_KEY_CHECK env var. Do not reintroduce a patch that
    # silently overwrites that logic from the installer.
    echo -e "${BLUE}  → SSH strict host-key check enforced (no installer patching of provisioner/ssh_client).${NC}"

     # Ensure shared networks exist (prod stack uses external networks)
     ensure_update_networks

     # Ensure all critical envs are set. The install.sh auto-
     # generates these at first install; on UPDATE, the env
     # file may be missing newer secrets that were added
     # after the original install (e.g. BACKUP_ENCRYPTION_KEY
     # was added in a later release). This block auto-fills
     # any missing secret so the platform doesn't fail-closed
     # in production because of an env added in a newer
     # version. Each secret is only added if it doesn't
     # already exist (preserves any operator-set value).
     # NOTE: This block runs in the top-level update flow (not
     # inside a function), so we use $INSTALL_DIR/.env directly
     # and avoid the `local` keyword.
     _env_file="$INSTALL_DIR/.env"
     if [ -f "$_env_file" ] && [ "$MODE_NODE" != "true" ]; then
         echo -e "${BLUE}[UPDATE] Verifying critical envs in $_env_file...${NC}"
         _missing_count=0
         # Each line: <VAR_NAME>=<generator>
           _env_generators=(
               "REDIS_PASSWORD|$(python3 -c "import secrets; print(secrets.token_hex(32))" 2>/dev/null || true)"
               "RABBITMQ_PASSWORD|$(python3 -c "import secrets; print(secrets.token_hex(32))" 2>/dev/null || true)"
               "GATEWAY_SECRET|$(python3 -c "import secrets; print(secrets.token_hex(64))" 2>/dev/null || true)"
               "GITHUB_WEBHOOK_SECRET|$(python3 -c "import secrets; print(secrets.token_hex(64))" 2>/dev/null || true)"
               "AUTOSCALER_API_TOKEN|$(python3 -c "import secrets; print(secrets.token_hex(64))" 2>/dev/null || true)"
               "FRP_AUTH_TOKEN|$(python3 -c "import secrets; print(secrets.token_hex(64))" 2>/dev/null || true)"
               "PGCAT_ADMIN_PASSWORD|$(python3 -c "import secrets; print(secrets.token_hex(48))" 2>/dev/null || true)"
               "REGISTRY_HTTP_SECRET|$(python3 -c "import secrets; print(secrets.token_hex(32))" 2>/dev/null || true)"
               "BACKUP_ENCRYPTION_KEY|$(python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())" 2>/dev/null || openssl rand -base64 32)"
               "REPLICATION_PASSWORD|$(python3 -c "import secrets; print(secrets.token_hex(32))" 2>/dev/null || true)"
               "SENTINEL_PASSWORD|$(python3 -c "import secrets; print(secrets.token_hex(32))" 2>/dev/null || true)"
               "CROWDSEC_BOUNCER_KEY|$(python3 -c "import secrets; print(secrets.token_hex(32))" 2>/dev/null || true)"
           )
         for _entry in "${_env_generators[@]}"; do
             _key="${_entry%%|*}"
             _generator="${_entry#*|}"
             if ! grep -q "^${_key}=" "$_env_file" 2>/dev/null; then
                 if [ -n "$_generator" ]; then
                     echo -e "${YELLOW}  → Auto-generating missing $_key${NC}"
                     env_set_value "$_env_file" "$_key" "$_generator"
                     _missing_count=$((_missing_count + 1))
                 fi
             fi
         done
         if [ "$_missing_count" -gt 0 ]; then
             echo -e "${GREEN}  ✓ Auto-generated $_missing_count missing secret(s)${NC}"
             # Re-source the env so the new values take effect
             # in the current shell session.
             set -a
             # shellcheck disable=SC1090
             source "$_env_file" 2>/dev/null || true
             set +a
         fi
     fi
     # Unset the helper var to avoid leaking into the rest of the script.
     unset _env_file _env_generators _entry _key _generator _missing_count

     # ── Auto-correct stale .env values from pre-HA upgrades ───────────
     # After the PostgreSQL HA + Redis HA rename, old .env files may
     # still reference single-node hostnames.  Fix them silently so the
     # platform doesn't break after an update.

     # Switch from dev compose (docker-compose.yml) to prod (HA) if
     # the operator hasn't explicitly picked a different one.
     _current_compose="$(env_get_value "$INSTALL_DIR/.env" "COMPOSE_FILE" 2>/dev/null || true)"
     if [ "$_current_compose" = "docker-compose.yml" ] && [ -f "$INSTALL_DIR/docker-compose.prod.yml" ]; then
         # Check if postgres-primary already has migrated data (e.g. from a
         # previous manual migration run).  If so, skip re-migration and
         # switch COMPOSE_FILE immediately so the update pipeline can
         # reach the correct DB hostname.
         _already_migrated=false
         if docker ps --format '{{.Names}}' 2>/dev/null | grep -qx 'smsly-postgres-primary'; then
             _tables=$(timeout 30 docker exec smsly-postgres-primary psql -U smsly_admin -d smsly_hosting -t -A \
                 -c "SELECT count(*) FROM information_schema.tables WHERE table_schema='public';" 2>/dev/null || echo 0)
             if [ "${_tables:-0}" -gt 50 ]; then
                 _already_migrated=true
                 echo -e "${GREEN}  → postgres-primary already has $_tables tables — migration already done${NC}"
             fi
         fi

         if $_already_migrated; then
             # Data is already on postgres-primary — switch to prod compose
             # immediately but ensure the HA stack is up first.
             echo -e "${BLUE}  → HA stack already has data — ensuring services are up...${NC}"
             docker compose -f "$INSTALL_DIR/docker-compose.prod.yml" \
                 up -d --wait --wait-timeout 120 \
                 postgres-primary postgres-replica pgcat redis-primary redis-replica \
                 2>/dev/null || echo -e "${YELLOW}  ⚠ Some HA services may not be healthy yet${NC}"
             echo -e "${YELLOW}  → Switching COMPOSE_FILE: docker-compose.yml → docker-compose.prod.yml${NC}"
             env_set_value "$INSTALL_DIR/.env" "COMPOSE_FILE" "docker-compose.prod.yml"
         else
             # If the old db container still has data, migrate it FIRST before
             # switching COMPOSE_FILE.  If migration fails, we keep the old
             # compose so the platform continues working with the old db.
             _has_old_db=false
             if [ "$(docker ps -a --format '{{.Names}}' 2>/dev/null | grep -cx 'smsly-hosting-db-1' || echo 0)" -gt 0 ]; then
                 _has_old_db=true
             fi

             if $_has_old_db; then
                 _mig_script="$INSTALL_DIR/scripts/migrate-db-to-ha.sh"
                 if [ -f "$_mig_script" ] && [ -x "$_mig_script" ]; then
                     # Bring up the HA stack FIRST so postgres-primary/pgcat exist
                     # before the migration script tries to dump into them.
                     echo -e "${BLUE}  → Starting HA stack (postgres-primary, pgcat, redis-primary)...${NC}"
                     docker compose -f "$INSTALL_DIR/docker-compose.prod.yml" \
                         up -d --wait --wait-timeout 120 \
                         postgres-primary postgres-replica pgcat redis-primary redis-replica \
                         2>/dev/null || echo -e "${YELLOW}  ⚠ Some HA services may not be healthy yet${NC}"

                     echo -e "${BLUE}  → Running data migration from old @db to postgres-primary...${NC}"
                     if bash "$_mig_script"; then
                         echo -e "${GREEN}  ✓ Data migration successful. Switching COMPOSE_FILE to prod (HA).${NC}"
                         env_set_value "$INSTALL_DIR/.env" "COMPOSE_FILE" "docker-compose.prod.yml"
                     else
                         echo -e "${RED}  ✗ Data migration failed. Keeping COMPOSE_FILE=docker-compose.yml.${NC}"
                         echo -e "${YELLOW}     Fix the migration issue and re-run update, or run:${NC}"
                         echo -e "${YELLOW}     sudo bash scripts/migrate-db-to-ha.sh${NC}"
                     fi
                 else
                     echo -e "${YELLOW}  ⚠ migrate-db-to-ha.sh not found or not executable — skipping migration${NC}"
                     echo -e "${YELLOW}  → Switching COMPOSE_FILE anyway (no old db data to lose)${NC}"
                     env_set_value "$INSTALL_DIR/.env" "COMPOSE_FILE" "docker-compose.prod.yml"
                 fi
             else
                 # No old db container — but we still need to ensure the
                 # HA stack is running so pgcat/postgres-primary resolve.
                 # Otherwise manage.py migrate will fail with DNS errors.
                 echo -e "${BLUE}  → Starting HA stack (fresh install)...${NC}"
                 docker compose -f "$INSTALL_DIR/docker-compose.prod.yml" \
                     up -d --wait --wait-timeout 120 \
                     postgres-primary postgres-replica pgcat redis-primary redis-replica \
                     2>/dev/null || echo -e "${YELLOW}  ⚠ Some HA services may not be healthy yet${NC}"
                 echo -e "${YELLOW}  → Switching COMPOSE_FILE: docker-compose.yml → docker-compose.prod.yml${NC}"
                 env_set_value "$INSTALL_DIR/.env" "COMPOSE_FILE" "docker-compose.prod.yml"
             fi
         fi
     fi
     unset _current_compose

     if [ -f "$INSTALL_DIR/.env" ] && [ "$MODE_NODE" != "true" ]; then
         _env_fix_file="$INSTALL_DIR/.env"
         # Read the current COMPOSE_FILE once — used by multiple blocks below
         # to decide whether to apply HA-specific migrations.
         _current_compose_final="$(env_get_value "$_env_fix_file" "COMPOSE_FILE" 2>/dev/null || true)"

         # REDIS_HOST: pre-HA used "redis", now "redis-primary"
         _current_redis_host="$(env_get_value "$_env_fix_file" "REDIS_HOST" 2>/dev/null || true)"
         if [ "$_current_redis_host" = "redis" ] || [ -z "$_current_redis_host" ]; then
             echo -e "${YELLOW}  → Updating REDIS_HOST: ${_current_redis_host:-<unset>} → redis-primary${NC}"
             env_set_value "$_env_fix_file" "REDIS_HOST" "redis-primary"
         fi

         # REDIS_URL: replace stale @redis: with @redis-primary:
         _redis_url="$(env_get_value "$_env_fix_file" "REDIS_URL" 2>/dev/null || true)"
         if echo "$_redis_url" | grep -q '@redis:'; then
             _fixed_redis_url="$(echo "$_redis_url" | sed 's|@redis:|@redis-primary:|g')"
             echo -e "${YELLOW}  → Fixing REDIS_URL hostname: redis → redis-primary${NC}"
             env_set_value "$_env_fix_file" "REDIS_URL" "$_fixed_redis_url"
         fi

         # CONTAINER_REGISTRY_URL: 127.0.0.1:5000 → registry:5000
         _registry="$(env_get_value "$_env_fix_file" "CONTAINER_REGISTRY_URL" 2>/dev/null || true)"
         if [ "$_registry" = "127.0.0.1:5000" ] || [ "$_registry" = "localhost:5000" ]; then
             echo -e "${YELLOW}  → Fixing CONTAINER_REGISTRY_URL: $_registry → registry:5000${NC}"
             env_set_value "$_env_fix_file" "CONTAINER_REGISTRY_URL" "registry:5000"
         fi

         # DATABASE_URL: auto-migrate from pre-HA single-node @db to pgcat
         _db_url="$(env_get_value "$_env_fix_file" "DATABASE_URL" 2>/dev/null || true)"
         if echo "$_db_url" | grep -q '@db:'; then
             if [ -n "$(get_pgcat_if_exists)" ]; then
                 _migrated_url="$(echo "$_db_url" | sed 's|@db:5432|@pgcat:5432|;s|@db/|@pgcat/|')"
                 echo -e "${YELLOW}  → Migrating DATABASE_URL: @db → @pgcat (PostgreSQL HA)${NC}"
                 env_set_value "$_env_fix_file" "DATABASE_URL" "$_migrated_url"
             else
                 echo -e "${YELLOW}  ⚠ DATABASE_URL points to single-node @db, but pgcat service not found.${NC}"
                 echo -e "${YELLOW}     Migrate DATABASE_URL to @postgres-primary or enable HA with pgcat.${NC}"
             fi
         fi

         # DIRECT_DATABASE_URL: only migrate if the compose file already
         # points to prod (HA).  If data migration failed and we kept the
         # dev compose, leaving DIRECT_DATABASE_URL pointed at postgres-primary
         # would crash Django management commands against an empty DB.
         _direct_url="$(env_get_value "$_env_fix_file" "DIRECT_DATABASE_URL" 2>/dev/null || true)"
         if echo "$_direct_url" | grep -q '@db:' && echo "$_current_compose_final" | grep -q 'prod'; then
             _migrated_direct="$(echo "$_direct_url" | sed 's|@db:5432|@postgres-primary:5432|')"
             echo -e "${YELLOW}  → Migrating DIRECT_DATABASE_URL: @db → @postgres-primary${NC}"
             env_set_value "$_env_fix_file" "DIRECT_DATABASE_URL" "$_migrated_direct"
         fi

         # Ensure REDIS_MIN_REPLICAS_TO_WRITE is present only when the
         # prod compose is active (has a replica).  Setting it on the
         # dev compose will cause NOREPLICAS errors on every write.
         if echo "$_current_compose_final" | grep -q 'prod'; then
             if ! grep -q '^REDIS_MIN_REPLICAS_TO_WRITE=' "$_env_fix_file" 2>/dev/null; then
                 echo -e "${YELLOW}  → Adding REDIS_MIN_REPLICAS_TO_WRITE=1 (Redis HA durability)${NC}"
                 echo 'REDIS_MIN_REPLICAS_TO_WRITE=1' >> "$_env_fix_file"
             fi
         else
             # Dev/single-node compose — ensure replica requirement is off
             # so writes don't get rejected.
             _min_rep="$(grep '^REDIS_MIN_REPLICAS_TO_WRITE=' "$_env_fix_file" 2>/dev/null | cut -d= -f2 || true)"
             if [ "$_min_rep" != "0" ]; then
                 echo -e "${YELLOW}  → Setting REDIS_MIN_REPLICAS_TO_WRITE=0 (single-node, no replica)${NC}"
                 env_set_value "$_env_fix_file" "REDIS_MIN_REPLICAS_TO_WRITE" "0"
             fi
         fi

         # Ensure PG_SYNCHRONOUS_COMMIT is present (default: on)
         if ! grep -q '^PG_SYNCHRONOUS_COMMIT=' "$_env_fix_file" 2>/dev/null; then
             echo -e "${YELLOW}  → Adding PG_SYNCHRONOUS_COMMIT=on (PostgreSQL durability)${NC}"
             echo 'PG_SYNCHRONOUS_COMMIT=on' >> "$_env_fix_file"
         else
             _pg_commit="$(env_get_value "$_env_fix_file" "PG_SYNCHRONOUS_COMMIT" 2>/dev/null || true)"
             if [ "$_pg_commit" = "off" ]; then
                 echo -e "${YELLOW}  ⚠ PG_SYNCHRONOUS_COMMIT=off — recent commits may be lost on crash.${NC}"
                 echo -e "${YELLOW}     Consider setting PG_SYNCHRONOUS_COMMIT=on for durability.${NC}"
             fi
         fi

         unset _env_fix_file _current_redis_host _redis_url _fixed_redis_url _registry _db_url _pg_commit _current_compose_final _min_rep
     fi

     # Cache bust only if disk is low (already runs in the disk check above when needed).
      # Moved into case blocks below to avoid redundant double bust.

      docker_login

       case "$UPDATE_MODE" in
         frontend)
              if [ "$MODE_NODE" = "true" ]; then
                  echo -e "${YELLOW}  → Node mode: no frontend to update. Skipping.${NC}"
              else
                  echo -e "${BLUE}  → Rebuilding frontend container (cached)...${NC}"
                docker compose -f "$COMPOSE_FILE" stop --timeout 15 frontend >/dev/null 2>&1 || true
                  docker compose -f "$COMPOSE_FILE" rm -f frontend >/dev/null 2>&1 || true
                  timeout -k 5 600 docker compose -f "$COMPOSE_FILE" build frontend
                  docker compose -f "$COMPOSE_FILE" up -d --force-recreate --no-deps frontend

                 # Custom Domain SSL Setup for Frontend Update
                 if should_manage_caddy; then  # Only for master mode
                     echo -e "\n${YELLOW}[UPDATE] Setting up Custom Domain SSL Services...${NC}"
                     SSL_SCRIPT="install-custom-domain-ssl.sh"
                     [ -f "$SSL_SCRIPT" ] || SSL_SCRIPT="$INSTALL_DIR/scripts/legacy/install-custom-domain-ssl.sh"
                 if [ -f "$SSL_SCRIPT" ]; then
                     echo -e "${BLUE}  → Installing custom domain SSL services...${NC}"
                     timeout -k 5 120 bash "$SSL_SCRIPT" install || true
                     
                     # Start the services
                     echo -e "${BLUE}  → Starting custom domain SSL services...${NC}"
                     timeout -k 5 30 /opt/smsly-hosting/smsly-domain-ssl-manager.sh start || true
                     
                     # Enable auto-start on boot (if not already enabled)
                     echo -e "${BLUE}  → Ensuring auto-start on boot...${NC}"
                     timeout -k 5 30 /opt/smsly-hosting/smsly-domain-ssl-manager.sh enable || true
                     
                     echo -e "${GREEN}  ✓ Custom domain SSL services configured${NC}"
                     else
                         echo -e "${YELLOW}  ⚠ Custom domain SSL manager not found, skipping setup${NC}"
                     fi
                 fi
             fi
             ;;
         backend)
            echo -e "${BLUE}  → Rebuilding backend containers (cached)...${NC}"
            build_svcs="backend celery"
            if [ "$MODE_AGENT_LITE" = "true" ]; then
                build_svcs="backend"
            elif [ "$MODE_NODE" = "true" ]; then
                build_svcs="backend celery celery-deploy celery-fast celery-beat"
            fi
            timeout -k 5 600 docker compose -f "$COMPOSE_FILE" build $build_svcs

            echo -e "${BLUE}  → Ensuring backend dependencies are running...${NC}"
            if [ "$MODE_AGENT_LITE" = "true" ]; then
                verify_agent_lite_connectivity
                docker compose -f "$COMPOSE_FILE" up -d --remove-orphans $(get_redis_service) rabbitmq socket-proxy
                sync_agent_lite_rabbitmq_password
            elif [ "$MODE_NODE" = "true" ]; then
                stop_node_excluded_services
                docker compose -f "$COMPOSE_FILE" up -d --remove-orphans $(get_db_service) $(get_pgcat_if_exists) $(get_redis_service) rabbitmq socket-proxy registry route-fallback traefik
            else
                docker compose -f "$COMPOSE_FILE" up -d --remove-orphans $(get_db_service) $(get_pgcat_if_exists) $(get_redis_service) socket-proxy
            fi
            # Stop backend, celery & pgcat so their DB connections don't block
            # migrations (ALTER TABLE requires exclusive locks).
            echo -e "${BLUE}  → Stopping backend, celery & pgcat for migrations...${NC}"
            docker compose -f "$COMPOSE_FILE" stop --timeout 15 backend celery celery-deploy celery-fast celery-beat $(get_pgcat_if_exists) 2>/dev/null || true

            echo -e "${BLUE}  → Running migrations...${NC}"
            run_backend_migrations --root || {
                echo -e "${YELLOW}  ⚠ Migration failed — retrying in 15s...${NC}"
                sleep 15
                run_backend_migrations --root
            }

            echo -e "${BLUE}  → Starting backend & pgcat...${NC}"
            if [ -n "$(get_pgcat_if_exists)" ]; then docker compose -f "$COMPOSE_FILE" up -d --no-deps pgcat 2>/dev/null || true; fi
            if [ "$MODE_AGENT_LITE" = "true" ]; then
                docker compose -f "$COMPOSE_FILE" up -d --force-recreate backend
            else
                docker compose -f "$COMPOSE_FILE" up -d --force-recreate --no-deps backend
            fi
            wait_for_container_ready "smsly-hosting-backend-1" 120 || true

            echo -e "${BLUE}  • Running post-migration tasks...${NC}"
            timeout 120 docker compose -f "$COMPOSE_FILE" exec -T --user root backend python manage.py fix_sequences 2>/dev/null || true
            timeout 120 docker compose -f "$COMPOSE_FILE" exec -T --user root backend python manage.py collectstatic --noinput 2>/dev/null || true

            set_checkpoint "update_db_migrated"

            # Clean stale celerybeat-schedule (prevents Permission denied crash loop)
            echo -e "${BLUE}  → Cleaning celerybeat-schedule...${NC}"
            timeout 30 docker compose -f "$COMPOSE_FILE" exec -T --user root backend rm -f /app/celerybeat-schedule 2>/dev/null || true

            echo -e "${BLUE}  → Restarting celery workers...${NC}"
            celery_svcs="celery celery-deploy celery-fast celery-beat"
            if [ "$MODE_AGENT_LITE" = "true" ]; then
                celery_svcs="celery-worker"
            fi
            if [ "$MODE_AGENT_LITE" = "true" ]; then
                docker compose -f "$COMPOSE_FILE" up -d --force-recreate $celery_svcs
            else
                 docker compose -f "$COMPOSE_FILE" up -d --force-recreate --no-deps $celery_svcs
             fi
             
             # Custom Domain SSL Setup for Backend Update
             if should_manage_caddy; then  # Only for master mode
                 echo -e "\n${YELLOW}[UPDATE] Setting up Custom Domain SSL Services...${NC}"
                 SSL_SCRIPT="install-custom-domain-ssl.sh"
                 [ -f "$SSL_SCRIPT" ] || SSL_SCRIPT="$INSTALL_DIR/scripts/legacy/install-custom-domain-ssl.sh"
                 if [ -f "$SSL_SCRIPT" ]; then
                     echo -e "${BLUE}  → Installing custom domain SSL services...${NC}"
                     timeout -k 5 120 bash "$SSL_SCRIPT" install || true
                     
                     # Start the services
                     echo -e "${BLUE}  → Starting custom domain SSL services...${NC}"
                     timeout -k 5 30 /opt/smsly-hosting/smsly-domain-ssl-manager.sh start || true
                     
                     # Enable auto-start on boot (if not already enabled)
                     echo -e "${BLUE}  → Ensuring auto-start on boot...${NC}"
                     timeout -k 5 30 /opt/smsly-hosting/smsly-domain-ssl-manager.sh enable || true
                     
                     echo -e "${GREEN}  ✓ Custom domain SSL services configured${NC}"
                 else
                     echo -e "${YELLOW}  ⚠ Custom domain SSL manager not found, skipping setup${NC}"
                 fi
             fi
             ;;
          half)
            echo -e "${BLUE}  → [HALF UPDATE] Rebuilding changed services from cache (no image pulls)${NC}"

            # 1. Rebuild frontend from cached layers (no --pull, no new base images)
            if [ "$MODE_NODE" != "true" ]; then
                echo -e "${BLUE}  → Rebuilding frontend (cached)...${NC}"
                timeout -k 5 600 docker compose -f "$COMPOSE_FILE" build frontend 2>/dev/null || {
                    echo -e "${YELLOW}  ⚠ Frontend build failed (cached layers missing). Skipping frontend.${NC}"
                    echo -e "${YELLOW}    Run --update when Docker Hub is reachable for a full rebuild.${NC}"
                }
                docker compose -f "$COMPOSE_FILE" stop --timeout 15 frontend >/dev/null 2>&1 || true
                docker compose -f "$COMPOSE_FILE" rm -f frontend >/dev/null 2>&1 || true
                docker compose -f "$COMPOSE_FILE" up -d --force-recreate --no-deps frontend 2>/dev/null || true
            fi

            # 2. Stop backend, celery & pgcat so their DB connections don't block
            #    migrations (ALTER TABLE requires exclusive locks).
            echo -e "${BLUE}  → Stopping backend, celery & pgcat for migrations...${NC}"
            docker compose -f "$COMPOSE_FILE" stop --timeout 15 backend celery celery-deploy celery-fast celery-beat $(get_pgcat_if_exists) 2>/dev/null || true

            # 3. Run migrations
            echo -e "${BLUE}  → Running migrations...${NC}"
            run_backend_migrations --root || {
                echo -e "${YELLOW}  ⚠ Migration failed — retrying in 15s...${NC}"
                sleep 15
                run_backend_migrations --root
            }

            # 4. Start pgcat & backend (picks up Python code changes from mounted volume)
            echo -e "${BLUE}  → Starting pgcat & backend...${NC}"
            if [ -n "$(get_pgcat_if_exists)" ]; then docker compose -f "$COMPOSE_FILE" up -d --no-deps pgcat 2>/dev/null || true; fi
            if [ "$MODE_AGENT_LITE" = "true" ]; then
                docker compose -f "$COMPOSE_FILE" up -d --remove-orphans $(get_redis_service) rabbitmq socket-proxy
                sync_agent_lite_rabbitmq_password
                docker compose -f "$COMPOSE_FILE" up -d --force-recreate backend
            else
                docker compose -f "$COMPOSE_FILE" up -d --force-recreate --no-deps backend
            fi
            wait_for_container_ready "smsly-hosting-backend-1" 120 || true

            timeout 120 docker compose -f "$COMPOSE_FILE" exec -T --user root backend python manage.py fix_sequences 2>/dev/null || true
            timeout 120 docker compose -f "$COMPOSE_FILE" exec -T --user root backend python manage.py collectstatic --noinput 2>/dev/null || true

            # 5. Clean celerybeat-schedule and restart celery workers
            echo -e "${BLUE}  → Cleaning celerybeat-schedule...${NC}"
            timeout 30 docker compose -f "$COMPOSE_FILE" exec -T --user root backend rm -f /app/celerybeat-schedule 2>/dev/null || true

            restart_svcs="celery celery-deploy celery-fast celery-beat"
            if [ "$MODE_AGENT_LITE" = "true" ]; then
                restart_svcs="celery-worker"
            fi
            if [ "$MODE_AGENT_LITE" = "true" ]; then
                docker compose -f "$COMPOSE_FILE" up -d --force-recreate $restart_svcs 2>/dev/null || true
            else
                docker compose -f "$COMPOSE_FILE" restart $restart_svcs 2>/dev/null || true
             fi
             set_checkpoint "update_db_migrated"
             
             # Custom Domain SSL Setup for Half Update
             if should_manage_caddy; then  # Only for master mode
                 echo -e "\n${YELLOW}[UPDATE] Setting up Custom Domain SSL Services...${NC}"
                 SSL_SCRIPT="install-custom-domain-ssl.sh"
                 [ -f "$SSL_SCRIPT" ] || SSL_SCRIPT="$INSTALL_DIR/scripts/legacy/install-custom-domain-ssl.sh"
                 if [ -f "$SSL_SCRIPT" ]; then
                     echo -e "${BLUE}  → Installing custom domain SSL services...${NC}"
                     timeout -k 5 120 bash "$SSL_SCRIPT" install || true
                     
                     # Start the services
                     echo -e "${BLUE}  → Starting custom domain SSL services...${NC}"
                     timeout -k 5 30 /opt/smsly-hosting/smsly-domain-ssl-manager.sh start || true
                     
                     # Enable auto-start on boot (if not already enabled)
                     echo -e "${BLUE}  → Ensuring auto-start on boot...${NC}"
                     timeout -k 5 30 /opt/smsly-hosting/smsly-domain-ssl-manager.sh enable || true
                     
                     echo -e "${GREEN}  ✓ Custom domain SSL services configured${NC}"
                 else
                     echo -e "${YELLOW}  ⚠ Custom domain SSL manager not found, skipping setup${NC}"
                 fi
             fi
             ;;
         full)
            echo -e "${BLUE}  → [FULL REBUILD] Rebuilding PaaS core (preserving addon databases)...${NC}"

            # 1. Only stop PaaS core services — NEVER touch addon containers
            CORE_SERVICES="frontend backend celery celery-deploy celery-fast celery-beat"
            if [ "$MODE_AGENT_LITE" = "true" ]; then
                CORE_SERVICES="backend celery-worker"
            elif [ "$MODE_NODE" = "true" ]; then
                CORE_SERVICES="backend celery celery-deploy celery-fast celery-beat"
            fi

            # 2. Skip untagging old PaaS images to prevent zero-downtime gaps on container restarts.
            # Docker compose build will simply overwrite the tag; old images will become dangling and cleaned later.

            # 3. Prune dangling build cache
            echo -e "${BLUE}    ↳ Pruning build cache...${NC}"
            docker builder prune -af 2>/dev/null || true

            # 4. Ensure shared networks exist (create if missing, don't destroy)
            echo -e "${BLUE}    ↳ Ensuring networks exist...${NC}"
            ensure_update_networks

            # 5. Rebuild core images (CACHED unless --no-cache passed manually)
            echo -e "${BLUE}    ↳ Rebuilding core images...${NC}"
            timeout -k 5 600 docker compose -f "$COMPOSE_FILE" build $CORE_SERVICES

            # 6. Start everything (addons stay running, core gets fresh containers)
            # This does a graceful zero-downtime replacement instead of an explicit hard stop
            echo -e "${BLUE}    ↳ Starting all services...${NC}"
            if [ "$MODE_AGENT_LITE" = "true" ]; then
                docker compose -f "$COMPOSE_FILE" up -d --remove-orphans $(get_redis_service) rabbitmq socket-proxy
                sync_agent_lite_rabbitmq_password
                docker compose -f "$COMPOSE_FILE" up -d --force-recreate --remove-orphans $CORE_SERVICES
            elif [ "$MODE_NODE" = "true" ]; then
                stop_node_excluded_services
                docker compose -f "$COMPOSE_FILE" up -d --remove-orphans $(get_db_service) $(get_pgcat_if_exists) $(get_redis_service) rabbitmq socket-proxy registry route-fallback traefik
                docker compose -f "$COMPOSE_FILE" up -d --force-recreate --no-deps --remove-orphans $CORE_SERVICES
            else
                docker compose -f "$COMPOSE_FILE" up -d --force-recreate --no-deps --remove-orphans $CORE_SERVICES
            fi

            if [ "$MODE_AGENT_LITE" != "true" ]; then
                # 7. Reconnect Traefik + socket-proxy to smsly-proxy network
                #    (recreation drops Docker DNS links — causes 502 gateway errors)
                #    NOTE: ensure_container_on_network uses `docker network connect`
                #    which works on running containers. No restart needed.
                echo -e "${BLUE}    ↳ Reconnecting proxy network...${NC}"
                for ctr in smsly-hosting-traefik-1 smsly-hosting-socket-proxy-1; do
                    ensure_container_on_network "smsly-proxy" "$ctr"
                done
            fi

            # 8. Stop backend, celery & pgcat so their DB connections don't block
            #    migrations (ALTER TABLE requires exclusive locks).
            echo -e "${BLUE}  → Stopping backend, celery & pgcat for migrations...${NC}"
            docker compose -f "$COMPOSE_FILE" stop --timeout 15 backend celery celery-deploy celery-fast celery-beat $(get_pgcat_if_exists) 2>/dev/null || true

            # 9. Run migrations
            echo -e "${BLUE}  → Running migrations...${NC}"
            echo -e "${BLUE}  → Ensuring backend dependencies are running...${NC}"
            if [ "$MODE_AGENT_LITE" = "true" ]; then
                verify_agent_lite_connectivity
                docker compose -f "$COMPOSE_FILE" up -d --remove-orphans $(get_redis_service) rabbitmq socket-proxy
                sync_agent_lite_rabbitmq_password
            elif [ "$MODE_NODE" = "true" ]; then
                stop_node_excluded_services
                docker compose -f "$COMPOSE_FILE" up -d --remove-orphans $(get_db_service) $(get_pgcat_if_exists) $(get_redis_service) rabbitmq socket-proxy registry route-fallback traefik
            else
                docker compose -f "$COMPOSE_FILE" up -d --remove-orphans $(get_db_service) $(get_pgcat_if_exists) $(get_redis_service) socket-proxy
            fi
            run_backend_migrations --root || {
                echo -e "${YELLOW}  ⚠ Migration failed — retrying in 15s...${NC}"
                sleep 15
                run_backend_migrations --root
            }

            # 10. Start pgcat & backend
            echo -e "${BLUE}  → Starting pgcat & backend...${NC}"
            if [ -n "$(get_pgcat_if_exists)" ]; then docker compose -f "$COMPOSE_FILE" up -d --no-deps pgcat 2>/dev/null || true; fi
            if [ "$MODE_AGENT_LITE" = "true" ]; then
                docker compose -f "$COMPOSE_FILE" up -d --force-recreate backend
            else
                docker compose -f "$COMPOSE_FILE" up -d --force-recreate --no-deps backend
            fi
            wait_for_container_ready "smsly-hosting-backend-1" 120 || true

            echo -e "${BLUE}  • Running post-migration tasks...${NC}"
            timeout 120 docker compose -f "$COMPOSE_FILE" exec -T --user root backend python manage.py fix_sequences 2>/dev/null || true
            timeout 120 docker compose -f "$COMPOSE_FILE" exec -T --user root backend python manage.py collectstatic --noinput 2>/dev/null || true

            # 11. Clean celerybeat-schedule and restart beat
            echo -e "${BLUE}  → Cleaning celerybeat-schedule...${NC}"
            timeout 30 docker compose -f "$COMPOSE_FILE" exec -T --user root backend rm -f /app/celerybeat-schedule 2>/dev/null || true
            
            restart_svcs="celery celery-beat celery-deploy celery-fast"
            if [ "$MODE_AGENT_LITE" = "true" ]; then
                restart_svcs="celery-worker"
            fi
            if [ "$MODE_AGENT_LITE" = "true" ]; then
                docker compose -f "$COMPOSE_FILE" up -d --force-recreate $restart_svcs 2>/dev/null || true
            else
                docker compose -f "$COMPOSE_FILE" restart $restart_svcs 2>/dev/null || true
            fi
            set_checkpoint "update_db_migrated"

            # Custom Domain SSL Setup for Full Update
            if should_manage_caddy; then
                echo -e "\n${YELLOW}[UPDATE] Setting up Custom Domain SSL Services...${NC}"
                SSL_SCRIPT="install-custom-domain-ssl.sh"
                [ -f "$SSL_SCRIPT" ] || SSL_SCRIPT="$INSTALL_DIR/scripts/legacy/install-custom-domain-ssl.sh"
                if [ -f "$SSL_SCRIPT" ]; then
                    timeout -k 5 120 bash "$SSL_SCRIPT" install || true
                    timeout -k 5 30 /opt/smsly-hosting/smsly-domain-ssl-manager.sh start || true
                    timeout -k 5 30 /opt/smsly-hosting/smsly-domain-ssl-manager.sh enable || true
                    echo -e "${GREEN}  ✓ Custom domain SSL services configured${NC}"
                else
                    echo -e "${YELLOW}  ⚠ Custom domain SSL manager not found, skipping setup${NC}"
                fi
            fi
            ;;
    esac

    # ─── Infisical auto-provision + secret sync ───────────────────────────
    _INFISICAL_COMPOSE="$INSTALL_DIR/infrastructure/docker/docker-compose.infisical.yml"
    if [ -f "$_INFISICAL_COMPOSE" ]; then
        _infisical_running=$(docker ps --filter "name=infisical" --format '{{.Names}}' 2>/dev/null | head -1)
        if [ -n "$_infisical_running" ]; then
            echo -e "${GREEN}  ✓ Infisical already running (${_infisical_running})${NC}"
        else
            # Ensure the infisical data volume exists
            docker volume create infisical_data 2>/dev/null || true

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
                    "SELECT 1 FROM pg_database WHERE datname='infisical'" 2>/dev/null | tr -d '[:space:]' || true)
                if [ "$_db_exists" != "1" ]; then
                    timeout 30 docker exec "$_db_container" psql -U "${_db_user}" -d "${POSTGRES_DB:-smsly_hosting}" -c \
                        "CREATE DATABASE infisical;" 2>/dev/null && \
                        echo -e "${GREEN}  ✓ Created infisical database${NC}" || \
                        echo -e "${YELLOW}  ⚠ Could not create infisical database (may already exist)${NC}"
                fi
            else
                echo -e "${YELLOW}  ⚠ No Postgres container found — skipping infisical database creation${NC}"
            fi

            # Generate env file on the volume (if not already present)
            _gen_script="$INSTALL_DIR/infrastructure/docker/infisical-gen-env.sh"
            if [ -f "$_gen_script" ]; then
                docker run --rm \
                    -v infisical_data:/data \
                    -v "$_gen_script":/tmp/infisical-gen-env.sh:ro \
                    alpine:3.19 \
                    sh /tmp/infisical-gen-env.sh /data/infisical.env 2>/dev/null || \
                    echo -e "${YELLOW}  ⚠ Could not generate Infisical env (may already exist)${NC}"
            fi

            # Bring up Infisical (env_file loaded from volume)
            echo -e "${BLUE}  → Provisioning Infisical secret manager...${NC}"
            docker compose --env-file "$INSTALL_DIR/.env" \
                -f "$_INFISICAL_COMPOSE" up -d --remove-orphans 2>/dev/null && \
                echo -e "${GREEN}  ✓ Infisical is running${NC}" || \
                echo -e "${YELLOW}  ⚠ Infisical startup failed (non-fatal — secrets remain in .env)${NC}"
        fi

        # Sync platform secrets to Infisical if it's running
        _infisical_running=$(docker ps --filter "name=infisical" --format '{{.Names}}' 2>/dev/null | head -1)
        if [ -n "$_infisical_running" ]; then
            echo -e "${BLUE}  → Syncing platform secrets to Infisical...${NC}"
            backend_container="$(resolve_container_target "smsly-hosting-backend-1")"
            if [ -n "$backend_container" ]; then
                timeout 60 docker exec "$backend_container" python manage.py sync_infisical_secrets --push 2>/dev/null || \
                    echo -e "${YELLOW}  ⚠ Infisical sync failed (non-fatal — secrets remain in .env)${NC}"
            fi
        fi
    fi

    # ─── Observability Stack Update (master mode only) ──────────────────────
    if [ "$MODE_AGENT_LITE" != "true" ] && [ "$MODE_NODE" != "true" ]; then
        echo -e "${BLUE}  → Updating observability stack...${NC}"
        # Ensure scripts mounted into containers are executable (git may not preserve +x)
        chmod +x "$INSTALL_DIR"/scripts/alertmanager-entrypoint.sh 2>/dev/null || true
        chmod +x "$INSTALL_DIR"/infrastructure/docker/infisical-gen-env.sh 2>/dev/null || true
        mkdir -p /opt/smsly-hosting/prometheus-targets
        if ! chown -R 1000:1000 /opt/smsly-hosting/prometheus-targets 2>/dev/null; then
            echo -e "${YELLOW}  ⚠ Could not chown prometheus-targets to uid 1000${NC}"
        fi
        chmod 2777 /opt/smsly-hosting/prometheus-targets 2>/dev/null || true
        docker compose \
            --env-file /opt/smsly-hosting/.env \
            -f infrastructure/docker/docker-compose.observability.yml \
            up -d --pull always || \
            echo -e "${YELLOW}  ⚠ Observability stack had issues (non-fatal)${NC}"
        # Restart containers whose bind-mounted config or environment may have
        # changed.  docker compose up -d only recreates on IMAGE changes, so
        # config-file updates require an explicit restart.
        docker restart smsly-grafana 2>/dev/null || true
        docker restart smsly-alertmanager 2>/dev/null || true
        docker restart smsly-prometheus 2>/dev/null || true
        docker restart smsly-docker-labels 2>/dev/null || true
        docker restart smsly-promtail 2>/dev/null || true
        # Deploy/update docker-labels exporter to all remote nodes and
        # regenerate Prometheus file_sd target files (docker-labels,
        # cAdvisor, Node Exporter).
        backend_container=$(docker ps --format '{{.Names}}' | grep -E '^smsly-hosting-backend(-1)?$' | head -1)
        if [ -n "$backend_container" ]; then
            timeout 60 docker exec "$backend_container" python manage.py deploy_docker_labels_exporters --force 2>/dev/null || true
        fi
        echo -e "${GREEN}  ✓ Observability stack updated${NC}"
    fi
    if [ -n "${CROWDSEC_BOUNCER_KEY:-}" ]; then
        echo -e "${BLUE}  → Registering CrowdSec Bouncer...${NC}"
        timeout 30 docker exec smsly-crowdsec cscli bouncers add traefik-bouncer -k "${CROWDSEC_BOUNCER_KEY:-}" >/dev/null 2>&1 || true
    fi

    set_checkpoint "update_containers_rebuilt"
fi

# ─── Vulnerability scan of freshly built images ────────────────────────
if command -v trivy >/dev/null 2>&1; then
    echo -e "${BLUE}  → Scanning rebuilt images for vulnerabilities...${NC}"
    for _trivy_img in backend frontend; do
        _trivy_tag="smsly/${_trivy_img}:latest"
        if docker image inspect "$_trivy_tag" >/dev/null 2>&1; then
            echo -e "${BLUE}    ↳ Scanning $_trivy_tag...${NC}"
            trivy image --scanners vuln --severity CRITICAL,HIGH --exit-code 0 --no-progress "$_trivy_tag" 2>/dev/null || \
                echo -e "${YELLOW}    ⚠ $_trivy_tag scan reported warnings — review output above${NC}"
        fi
    done
    unset _trivy_img _trivy_tag
fi

# ─── Safe Update: Post-Deploy Verification ─────────────────────────────
if command -v safe_update_post_verify >/dev/null 2>&1; then
    echo -e "${BLUE}  → Running post-deploy health checks...${NC}"
    sleep 30  # wait for containers to warm up
    if safe_update_post_verify; then
        echo -e "${GREEN}  ✓ All health checks passed — update successful${NC}"
        trap - ERR  # clear rollback trap on success
        if command -v safe_update_cleanup >/dev/null 2>&1; then
            safe_update_cleanup
        fi
        rm -f "$SNAPSHOT_FILE" 2>/dev/null || true
    else
        echo -e "${RED}  ✗ Post-deploy health checks failed — initiating rollback${NC}"
        safe_update_rollback
        exit 1
    fi
fi

    # ─── Ensure Local Docker cloud provider exists ──────────────────────────
    echo -e "${BLUE}  → Ensuring Local Docker cloud provider exists...${NC}"
    echo "
from apps.cloud.models import CloudProvider
cp, created = CloudProvider.objects.get_or_create(
    provider_type='LOCAL',
    defaults={'name': 'Local Docker', 'is_active': True}
)
if not created and not cp.is_active:
    cp.is_active = True
    cp.save()
" | timeout -k 5 30 docker compose -f "$COMPOSE_FILE" exec -T backend python manage.py shell 2>/dev/null || true
    # ─── Self-Healing: Docker Socket Permissions ──────────────────────────────
    echo -e "${BLUE}  → Hardening Docker socket permissions...${NC}"
    # NOTE: Removed chmod 666 — world-writable docker.sock is a security risk.
    # Group membership (docker group) is the correct access control mechanism.
    if ! groups smsly 2>/dev/null | grep -q "docker"; then
        usermod -aG docker smsly 2>/dev/null || true
    fi

    # ─── Self-Healing: Cleanup Stale Resources ──────────────────────────────
    echo -e "${BLUE}  → Pruning stale deployment containers and BuildKit caches...${NC}"
    # Prune orphaned containers created by the deployment system (labeled)
    docker container prune -f --filter "label=com.smsly.managed=true" --filter "status=created" 2>/dev/null || true
    docker container prune -f --filter "label=com.docker.compose.project" --filter "status=exited" 2>/dev/null || true
    # Prune BuildKit build cache (saves significant disk space)
    docker builder prune -f --filter "until=24h" 2>/dev/null || true
    # Prune stale rollback backup containers left from failed blue-green promotions
    docker container prune -f --filter "status=exited" 2>/dev/null || true
    # Prune dangling images left over after the new images were tagged
    docker image prune -f 2>/dev/null || true
    for ctr in $(docker ps -a --filter "status=exited" --filter "name=-rollback-" --format '{{.Names}}' 2>/dev/null || true); do
        docker rm -f "$ctr" 2>/dev/null || true
    done
    for ctr in $(docker ps -a --filter "status=created" --filter "name=-rollback-" --format '{{.Names}}' 2>/dev/null || true); do
        docker rm -f "$ctr" 2>/dev/null || true
    done

    # ─── Self-Healing: Automatic Queue Restoration ──────────────────────────
    echo -e "${BLUE}  → Checking for stalled deployments/addons in QUEUED state...${NC}"
    backend_container="$(resolve_container_target "smsly-hosting-backend-1")"
    timeout -k 5 120 docker exec -i "$backend_container" python manage.py shell -c "
from apps.deployments.models import Deployment, Service
from apps.deployments.models_addons import Addon
from apps.deployments.tasks import provision_addon_task, recover_stalled_queued_deployments
from django.db.models import Count

# Re-queue deployments
q_count = Deployment.objects.filter(status='QUEUED').count()
if q_count > 0:
    print(f'  [Jump-Start] Re-queueing {q_count} stalled deployments...')
    result = recover_stalled_queued_deployments(limit=q_count)
    print(
        '  [Jump-Start] Deployments restored: queued={queued} '
        'skipped={skipped} failed={failed}'.format(**result)
    )

# Re-queue addons
a_count = Addon.objects.filter(status='QUEUED').count()
if a_count > 0:
    print(f'  [Jump-Start] Re-queueing {a_count} stalled addons...')
    for a in Addon.objects.filter(status='QUEUED'):
        provision_addon_task.delay(str(a.id))

# Re-queue stalled service deletions (lost during worker restart)
d_count = Service.objects.filter(status='DELETION_PENDING').count()
if d_count > 0:
    print(f'  [Jump-Start] Re-queueing {d_count} stalled deletion tasks...')
    from apps.deployments.tasks import delete_service_task
    for s in Service.objects.filter(status='DELETION_PENDING'):
        delete_service_task.delay(str(s.id))
" 2>/dev/null || true

    # ─── Verification: Celery Worker Health ─────────────────────────────────
    echo -e "${BLUE}  → Verifying worker connectivity and queue bindings...${NC}"
    # Give workers a moment to connect to Redis and report active queues
    sleep 15
    raw_worker="smsly-hosting-celery-deploy-1"
    if [ "$MODE_AGENT_LITE" = "true" ]; then
        raw_worker="smsly-hosting-celery-worker-1"
    fi
    worker_container="$(resolve_container_target "$raw_worker")"
    DEPLOY_WORKER_HEALTH="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$worker_container" 2>/dev/null || echo "")"
    if timeout 20 docker exec -i "$worker_container" celery -A config inspect active_queues --timeout=10 2>/dev/null | grep -q "deploy"; then
        echo -e "${GREEN}  ✓ Deployment worker successfully bound to 'deploy' queue${NC}"
    elif [ "$DEPLOY_WORKER_HEALTH" = "healthy" ] || [ "$DEPLOY_WORKER_HEALTH" = "running" ]; then
        echo -e "${GREEN}  ✓ Deployment worker container is healthy/running (queue inspect timed out)${NC}"
    else
        echo -e "${YELLOW}  ⚠ WARNING: Deployment worker not detected on 'deploy' queue. Check logs.${NC}"
    fi

    echo -e "\n${GREEN}  ✨ Update complete. Self-healing applied.${NC}"

    timeout -k 5 120 bash -c "
export COMPOSE_FILE='$COMPOSE_FILE'
source '$INSTALL_DIR/lib/env.sh'
source '$INSTALL_DIR/lib/common.sh'
source '$INSTALL_DIR/lib/platform.sh'
sync_platform_domain_state '$INSTALL_DIR/.env'
" || echo -e "${YELLOW}  ⚠ Domain state sync timed out (non-fatal)${NC}"

    # Refresh proxy/runtime edge stack so routing and TLS state is always clean.
    # NOTE: restart_edge_stack now handles Caddy validation internally (H1+H2 fix).
    restart_edge_stack
    wait_for_traefik_api 30 || true

    sleep 2

    # ─── Fix .env permissions (must be writable by Docker container UID 1000) ──
    if [ -f "$INSTALL_DIR/.env" ]; then
        chown root:1000 "$INSTALL_DIR/.env" 2>/dev/null || true
        chmod 640 "$INSTALL_DIR/.env" 2>/dev/null || true
    fi

    # ─── Caddy: Generate self-signed cert + regenerate Caddyfile ──
    if should_manage_caddy; then
    ensure_selfsigned_cert
    if command -v caddy &> /dev/null; then
        echo -e "${BLUE}  → Regenerating Caddyfile with current service domains...${NC}"

        # ── Step 1: Find the Cloudflare token FIRST (before generating Caddyfile) ──
        CF_TOKEN=""

        # Priority: .env file > PlatformConfig DB
        if [ -z "$CF_TOKEN" ] && [ -f "$INSTALL_DIR/.env" ]; then
            CF_TOKEN="$(grep -m1 '^CLOUDFLARE_API_TOKEN=' "$INSTALL_DIR/.env" 2>/dev/null | cut -d= -f2- || true)"
        fi
        # Fallback: read from PlatformConfig in the database (set via Settings UI)
        if [ -z "$CF_TOKEN" ] || [ "$CF_TOKEN" = "fake" ]; then
            DB_TOKEN="$(timeout -k 5 30 docker compose -f "$COMPOSE_FILE" exec -T backend python manage.py shell -c "
from apps.deployments.models import PlatformConfig
config = PlatformConfig.load()
token = (getattr(config, 'cloudflare_api_token', '') or '').strip()
if token and token.lower() not in ('fake', 'changeme', 'test', ''):
    print(token)
" 2>/dev/null || true)"
            DB_TOKEN="$(echo "$DB_TOKEN" | tr -d '[:space:]')"
            if [ -n "$DB_TOKEN" ]; then
                CF_TOKEN="$DB_TOKEN"
                echo -e "${GREEN}  ✓ Cloudflare token found in Settings DB${NC}"
                # Sync back to .env so it persists
                if grep -q 'CLOUDFLARE_API_TOKEN' "$INSTALL_DIR/.env" 2>/dev/null; then
                    sed -i "s/CLOUDFLARE_API_TOKEN=.*/CLOUDFLARE_API_TOKEN=$CF_TOKEN/" "$INSTALL_DIR/.env"
                else
                    echo "CLOUDFLARE_API_TOKEN=$CF_TOKEN" >> "$INSTALL_DIR/.env"
                fi
            fi
        fi

        # ── Step 2: Generate Caddyfile WITH dns cloudflare if token exists ──
        if [ -n "$CF_TOKEN" ] && [ "$CF_TOKEN" != "fake" ]; then
            echo -e "${GREEN}  ✓ Cloudflare token available — generating Caddyfile with wildcard SSL${NC}"


            # Discover domain
            cf_domain=""
            cf_domain="$(timeout -k 5 30 docker compose -f "$COMPOSE_FILE" exec -T backend python manage.py shell -c "
from apps.deployments.models import PlatformConfig
c = PlatformConfig.load()
d = (c.domain or '').strip()
if d and d != 'localhost':
    print(d)
" 2>/dev/null | tr -d '[:space:]' || true)"
            if [ -z "$cf_domain" ]; then
                cf_domain="$(grep -m1 '^DOMAIN=' "$INSTALL_DIR/.env" 2>/dev/null | cut -d= -f2- || true)"
            fi

            cf_server_ip="$(detect_public_ip)"

            # Discover wildcard-covered hosts and non-wildcard service blocks.
            # - Wildcard-covered hosts route through Traefik via matcher.
            # - Unknown wildcard hosts route to /notice on frontend.
            # - External custom domains keep explicit direct on-demand TLS blocks with Host rewrite.
            cf_wildcard_known_hosts=""
            cf_wildcard_known_hosts="$(timeout -k 5 30 docker compose -f "$COMPOSE_FILE" exec -T backend python manage.py shell -c "
from apps.deployments.models import Service
from apps.domains.models import Domain, DomainStatus
from django.db.models import Q
suffix = '.${cf_domain}'.lower().strip()
hosts = set()
for svc in Service.objects.all():
    d = (svc.public_domain or '').strip().lower()
    if d and suffix and d.endswith(suffix):
        hosts.add(d)
for domain in Domain.objects.filter(
    status__in=[DomainStatus.ACTIVE, DomainStatus.DNS_VERIFIED, DomainStatus.SSL_PROVISIONING],
).filter(Q(verified=True) | Q(status=DomainStatus.ACTIVE)):
    cd = (domain.domain_name or '').strip().lower()
    if cd and suffix and cd.endswith(suffix):
        hosts.add(cd)
print(' '.join(sorted(hosts)))
" 2>/dev/null | tr -d '\r' | tr -d '\n' || true)"

            cf_svc_blocks=""
            cf_svc_blocks="$(timeout -k 5 30 docker compose -f "$COMPOSE_FILE" exec -T backend python manage.py shell -c "
import os
upstream = os.environ.get('SMSLY_SERVICE_PROXY_UPSTREAM', 'traefik:80')
from apps.deployments.models import Service
from apps.domains.models import Domain, DomainStatus
from django.db.models import Q
suffix = '.${cf_domain}'.lower().strip()
seen = set()
for svc in Service.objects.all():
    public_domain = (svc.public_domain or '').strip().lower()
    if public_domain and (not suffix or not public_domain.endswith(suffix)) and public_domain not in seen:
        seen.add(public_domain)
        print(f'{public_domain} {{\n    reverse_proxy {upstream}\n    encode gzip\n}}\n')

for domain in Domain.objects.select_related('service').filter(
    status__in=[DomainStatus.ACTIVE, DomainStatus.DNS_VERIFIED, DomainStatus.SSL_PROVISIONING],
).filter(Q(verified=True) | Q(status=DomainStatus.ACTIVE)):
    custom_domain = (domain.domain_name or '').strip().lower()
    svc = domain.service
    public_domain = (svc.public_domain or '').strip().lower() if svc else ''
    if not custom_domain:
        continue
    if suffix and custom_domain.endswith(suffix):
        continue
    if custom_domain in seen:
        continue
    seen.add(custom_domain)

    if public_domain and public_domain != custom_domain:
        print(f'{custom_domain} {{\n    tls {{\n        on_demand\n    }}\n    reverse_proxy {upstream} {{\n        header_up Host {public_domain}\n    }}\n    encode gzip\n}}\n')
    else:
        print(f'{custom_domain} {{\n    tls {{\n        on_demand\n    }}\n    reverse_proxy {upstream}\n    encode gzip\n}}\n')
" 2>/dev/null | tr -d '\r' || true)"

            # Only generate wildcard Caddyfile for real domains
            cf_is_real_domain=false
            if [ -n "$cf_domain" ] && [ "$cf_domain" != "localhost" ]; then
                if ! echo "$cf_domain" | grep -qE '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$'; then
                    cf_is_real_domain=true
                fi
            fi

            if [ "$cf_is_real_domain" = "true" ]; then
                cf_known_stanza=""
                if [ -n "$cf_wildcard_known_hosts" ]; then
                    cf_known_stanza="    @known_hosts host ${cf_wildcard_known_hosts}
    handle @known_hosts {
        reverse_proxy ${SMSLY_SERVICE_PROXY_UPSTREAM:-traefik:80}
    }"
                fi

                cat > /tmp/Caddyfile.tmp <<CFCADDY
# Auto-generated with Cloudflare DNS challenge (wildcard SSL)
{
    on_demand_tls {
        ask http://backend:8000/api/v1/services/check-domain/
    }
}

${cf_domain} {
    reverse_proxy ${SMSLY_SERVICE_PROXY_UPSTREAM:-traefik:80}
    encode gzip
    log {
        output file /var/log/caddy/access.log
    }
}

*.${cf_domain} {
    tls {
        dns cloudflare {env.CLOUDFLARE_API_TOKEN}
    }
${cf_known_stanza}
    handle {
        reverse_proxy ${SMSLY_SERVICE_PROXY_UPSTREAM:-traefik:80}
    }
}

${cf_server_ip} {
    tls internal
    redir http://${cf_server_ip}{uri} 308
}

${cf_svc_blocks}
CFCADDY
                if install_caddyfile_atomically /tmp/Caddyfile.tmp "wildcard Caddyfile"; then
                    echo -e "${GREEN}  ✓ Caddyfile generated with wildcard SSL for *.${cf_domain}${NC}"
                else
                    echo -e "${YELLOW}  ⚠ Wildcard Caddyfile could not be applied. Falling back to standard HTTPS for ${cf_domain}.${NC}"
                    generate_safe_caddyfile "wildcard Caddyfile apply failed"
                fi
                rm -f /tmp/Caddyfile.tmp
            else
                # IP mode or no domain — fall back to safe Caddyfile
                generate_safe_caddyfile "update flow (IP mode)"
            fi
        else
            # No valid token — generate safe Caddyfile (no dns cloudflare)
            generate_safe_caddyfile "update flow caddy regen"

            # NOTE: Cloudflare dns-challenge stripping is now handled by
            # generate_safe_caddyfile itself, which never emits 'dns cloudflare'
            # blocks when no token is present. (Removed dead 'if false' block.)
        fi

        # Final validation — if still broken, regenerate safe fallback
        if caddy_needs_fix; then
            generate_safe_caddyfile "post-update validation"
        fi

        reload_container_caddy 2>/dev/null || true

        # ─── Python-based Caddyfile overlay (preview-aware routing) ─────────────
        # The bash heredoc above generates a static template without preview
        # environment routing. Django's generate_caddyfile() includes direct
        # container routing for local preview environments, so we overlay it.
        echo -e "${BLUE}  → Overlaying preview-aware Caddyfile from Django...${NC}"
        timeout -k 5 30 docker compose -f "$COMPOSE_FILE" exec -T backend python manage.py shell -c "
from apps.deployments.models import PlatformConfig
from services.caddy_manager import generate_caddyfile, apply_caddyfile
config = PlatformConfig.load()
content = generate_caddyfile(config)
token = (getattr(config, 'cloudflare_api_token', '') or '').strip()
result = apply_caddyfile(content, cloudflare_token=token, preserve_existing_token=True)
print(result.get('message', 'ok'))
" 2>/dev/null && echo -e "${GREEN}  ✓ Preview-aware Caddyfile applied${NC}" || \
            echo -e "${YELLOW}  ⚠ Python Caddyfile overlay failed (non-fatal, static template still active)${NC}"

        reload_container_caddy 2>/dev/null || true

        # Verify Caddy is running
        sleep 2
        if docker compose -f "$COMPOSE_FILE" ps -q caddy 2>/dev/null | grep -q .; then
            echo -e "${GREEN}  ✓ Caddy config regenerated and running${NC}"
        else
            echo -e "${YELLOW}  ⚠ Caddy failed to start. Run: journalctl -u caddy --no-pager -n 20${NC}"
        fi

        POST_CADDY_DOMAIN="$(timeout -k 5 30 docker compose -f "$COMPOSE_FILE" exec -T backend python manage.py shell -c "
from apps.deployments.models import PlatformConfig
c = PlatformConfig.load()
d = (c.domain or '').strip()
if d and d != 'localhost':
    print(d)
" 2>/dev/null | tr -d '[:space:]' || true)"
        if [ -z "$POST_CADDY_DOMAIN" ]; then
            POST_CADDY_DOMAIN="$(grep -m1 '^DOMAIN=' "$INSTALL_DIR/.env" 2>/dev/null | cut -d= -f2- || true)"
        fi

        install_caddy_health_guard "$POST_CADDY_DOMAIN"
    fi
    fi

    timeout -k 5 600 bash -c "
export COMPOSE_FILE='$COMPOSE_FILE'
source '$INSTALL_DIR/lib/common.sh' 2>/dev/null
safe_refresh_runtime_services
" || true
    timeout -k 5 300 bash -c "
export COMPOSE_FILE='$COMPOSE_FILE'
source '$INSTALL_DIR/lib/common.sh' 2>/dev/null
ensure_celery_workers_running
" || true

    # ─── Auto-redeploy active services when platform code or domain state changes ──
    PRE_HEAD="$(cat "$INSTALL_DIR/.pre-update-head" 2>/dev/null || true)"
    CURRENT_HEAD="$(cd "$INSTALL_DIR" && git rev-parse HEAD 2>/dev/null || true)"
    CODE_CHANGED=false
    if [ -n "$PRE_HEAD" ] && [ "$PRE_HEAD" != "$CURRENT_HEAD" ]; then
        CODE_CHANGED=true
        echo -e "${BLUE}  → Platform code changed (${PRE_HEAD:0:7} → ${CURRENT_HEAD:0:7})${NC}"
    fi
    if [ "$CODE_CHANGED" = "true" ] || [ "$FORCE_REDEPLOY" = "true" ]; then
        echo -e "${BLUE}  → Auto-redeploying active services (platform code changed)...${NC}"
        if ! queue_active_service_redeploys "Platform update auto-redeploy" ""; then
            echo -e "${YELLOW}  ⚠ Auto-redeploy encountered issues (check logs above)${NC}"
        fi
    elif [ "${DOMAIN_SYNC_REDEPLOY_REQUIRED:-0}" = "1" ]; then
        echo -e "${BLUE}  → Auto-redeploying rewritten services (platform domain changed)...${NC}"
        if ! queue_active_service_redeploys "Platform domain change auto-redeploy" "${DOMAIN_SYNC_SERVICE_IDS}"; then
            echo -e "${YELLOW}  ⚠ Domain-change redeploy encountered issues (check logs above)${NC}"
        fi
    else
        echo -e "${GREEN}  ✓ No platform code or domain-driven redeploys required${NC}"
    fi
    # Clean up marker
    rm -f "$INSTALL_DIR/.pre-update-head" 2>/dev/null || true

    # ─── Endpoint Verification (3 checks) ──────────────────────────────────
    echo -e "\n${BLUE}  → Running endpoint verification (3 checks)...${NC}"
    sleep 5
    PASS_COUNT=0
    FAIL_COUNT=0

    # ── Check 1: Backend API health (docker exec into backend container) ──
    EP1_FALLBACK_URL="http://127.0.0.1:8000/health"
    _LITE_HOST_HEADER=""
    if [ "$MODE_AGENT_LITE" = "true" ]; then
        _ep1_domain="$(grep -m1 '^DOMAIN=' "$INSTALL_DIR/.env" 2>/dev/null | cut -d= -f2- | tr -d '[:space:]' || true)"
        if [ -n "$_ep1_domain" ] && [ "$_ep1_domain" != "localhost" ]; then
            _LITE_HOST_HEADER="$_ep1_domain"
        fi
    fi
    echo -e "${BLUE}  [1/3] Backend API health...${NC}"
    echo -e "${BLUE}        Endpoint: backend:8000/health (via docker exec)${NC}"
    BACKEND_OK=false
    EP1_CODE="000"
    for attempt in 1 2 3 4 5; do
        if [ "$MODE_AGENT_LITE" = "true" ]; then
            if [ -n "${_LITE_HOST_HEADER:-}" ]; then
                # Route through Traefik with the correct Host header
                EP1_CODE=$(curl -so /dev/null -w '%{http_code}' --max-time 5 -H "Host: ${_LITE_HOST_HEADER}" "http://127.0.0.1/health" 2>/dev/null) || EP1_CODE="000"
            else
                # No domain — route through Traefik on port 80
                EP1_CODE=$(curl -so /dev/null -w '%{http_code}' --max-time 5 "http://127.0.0.1/health" 2>/dev/null) || EP1_CODE="000"
            fi
        else
            if timeout 15 docker compose -f "$COMPOSE_FILE" exec -T backend curl -fsS --max-time 5 http://127.0.0.1:8000/health >/dev/null 2>&1; then
                EP1_CODE="200"
            elif curl -fsS --max-time 5 "$EP1_FALLBACK_URL" >/dev/null 2>&1; then
                EP1_CODE="200"
            else
                EP1_CODE=$(curl -so /dev/null -w '%{http_code}' --max-time 5 "$EP1_FALLBACK_URL" 2>/dev/null) || EP1_CODE="000"
            fi
        fi
        case "$EP1_CODE" in
            2*|3*)
            BACKEND_OK=true
            break
            ;;
        esac
        sleep 3
    done
    if [ "$BACKEND_OK" = "true" ]; then
        EP1_RESULT="${GREEN}PASS${NC}"
        echo -e "${GREEN}  ✓ [1/3] PASS — HTTP $EP1_CODE${NC}"
        PASS_COUNT=$((PASS_COUNT + 1))
    else
        EP1_RESULT="${RED}FAIL${NC}"
        echo -e "${RED}  ✗ [1/3] FAIL — HTTP $EP1_CODE${NC}"
        echo -e "${YELLOW}        Fix: docker compose -f $COMPOSE_FILE logs --tail=30 backend${NC}"
        FAIL_COUNT=$((FAIL_COUNT + 1))
    fi

    # ── Check 2: HTTPS platform domain (auto-discovered from DB → through Caddy) ──
    echo -e "${BLUE}  [2/3] HTTPS platform domain...${NC}"
    # Auto-discover domain from PlatformConfig in DB — zero config needed
    EP_DOMAIN="$(timeout -k 5 30 docker compose -f "$COMPOSE_FILE" exec -T backend python manage.py shell -c "
from apps.deployments.models import PlatformConfig
config = PlatformConfig.load()
d = (config.domain or '').strip()
if d and d != 'localhost':
    print(d)
" 2>/dev/null | tr -d '[:space:]' || true)"
    # Fallback to .env if DB query failed
    if [ -z "$EP_DOMAIN" ]; then
        EP_DOMAIN="$(grep -m1 '^DOMAIN=' "$INSTALL_DIR/.env" 2>/dev/null | cut -d= -f2- || true)"
    fi
    HTTPS_OK=false
    EP2_CODE="---"
    EP2_URL="(skipped)"
    if ! should_manage_caddy; then
        EP2_RESULT="${YELLOW}SKIP${NC}"
        echo -e "${YELLOW}  [2/3] SKIPPED (Caddy/HTTPS is master-only in this mode)${NC}"
    elif [ -n "$EP_DOMAIN" ] && [ "$EP_DOMAIN" != "localhost" ] && ! echo "$EP_DOMAIN" | grep -qE '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$'; then
        EP2_URL="https://${EP_DOMAIN}/health"
        echo -e "${BLUE}        Endpoint: $EP2_URL${NC}"
        for attempt in 1 2 3; do
            EP2_CODE=$(curl -so /dev/null -w '%{http_code}' --max-time 8 "$EP2_URL" 2>/dev/null) || EP2_CODE="000"
            case "$EP2_CODE" in
                2*|3*)
                    HTTPS_OK=true
                    break
                    ;;
            esac
            sleep 3
        done
        if [ "$HTTPS_OK" = "true" ]; then
            EP2_RESULT="${GREEN}PASS${NC}"
            echo -e "${GREEN}  ✓ [2/3] PASS — HTTP $EP2_CODE${NC}"
            PASS_COUNT=$((PASS_COUNT + 1))
        else
            EP2_RESULT="${RED}FAIL${NC}"
            echo -e "${RED}  ✗ [2/3] FAIL — HTTP $EP2_CODE${NC}"
            echo -e "${YELLOW}        Fix: docker compose -f $COMPOSE_FILE logs --tail=15 caddy${NC}"
            FAIL_COUNT=$((FAIL_COUNT + 1))
        fi
    elif [ -n "$EP_DOMAIN" ] && echo "$EP_DOMAIN" | grep -qE '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$'; then
        EP2_URL="(skipped: IP mode)"
        EP2_RESULT="${YELLOW}SKIP${NC}"
        echo -e "${YELLOW}  [2/3] SKIPPED (HTTPS requires a domain name, not raw IP $EP_DOMAIN)${NC}"
    else
        EP2_RESULT="${YELLOW}SKIP${NC}"
        echo -e "${YELLOW}  ⊘ [2/3] SKIPPED (no domain configured)${NC}"
    fi

    # ── Check 3+: ALL deployed services (auto-discovered from DB) ──
    echo -e "${BLUE}  [3/N] Deployed services routing...${NC}"

    # Query ALL active service domains from the DB (public + custom)
    ALL_SVC_DOMAINS="$(timeout -k 5 30 docker compose -f "$COMPOSE_FILE" exec -T backend python manage.py shell -c "
from apps.deployments.models import Service
for svc in Service.objects.exclude(public_domain__isnull=True).exclude(public_domain='').order_by('name'):
    print(f'{svc.name}|{svc.public_domain.strip()}')
    for cd in (svc.custom_domains or []):
        cd = cd.strip()
        if cd:
            print(f'{svc.name} (custom)|{cd}')
" 2>/dev/null | tr -d '\r' || true)"

    # Also check Traefik port directly
    if [ "$MODE_AGENT_LITE" = "true" ]; then
        EP3_URL="http://127.0.0.1/"
    else
        EP3_URL="http://127.0.0.1:8081/"
    fi
    EP3_CODE=$(curl -so /dev/null -w '%{http_code}' --max-time 5 "$EP3_URL" 2>/dev/null) || EP3_CODE="000"
    if [ "$EP3_CODE" != "000" ] && [ "$EP3_CODE" != "502" ]; then
        EP3_RESULT="${GREEN}PASS${NC}"
        echo -e "${GREEN}  ✓ Traefik proxy ($EP3_URL) — HTTP $EP3_CODE${NC}"
        PASS_COUNT=$((PASS_COUNT + 1))
    else
        EP3_RESULT="${RED}FAIL${NC}"
        echo -e "${RED}  ✗ Traefik proxy ($EP3_URL) — HTTP $EP3_CODE${NC}"
        echo -e "${YELLOW}        Fix: docker compose -f $COMPOSE_FILE logs --tail=20 traefik${NC}"
        FAIL_COUNT=$((FAIL_COUNT + 1))
    fi

    # Collect service results for the table
    SVC_RESULTS=""
    SVC_COUNT=0
    if [ -n "$ALL_SVC_DOMAINS" ]; then
        while IFS='|' read -r svc_name svc_domain; do
            [ -z "$svc_domain" ] && continue
            SVC_COUNT=$((SVC_COUNT + 1))
            if should_manage_caddy; then
                svc_url="https://${svc_domain}/"
            else
                svc_url="http://${svc_domain}/"
            fi
            echo -e "${BLUE}        Testing: $svc_name → $svc_url${NC}"
            svc_code="000"
            svc_ok=false
            for attempt in 1 2 3; do
                svc_code=$(curl -so /dev/null -w '%{http_code}' --max-time 8 "$svc_url" 2>/dev/null) || svc_code="000"
                if [ "$svc_code" != "000" ] && [ "$svc_code" != "502" ] && [ "$svc_code" != "503" ]; then
                    svc_ok=true
                    break
                fi
                sleep 2
            done
            if [ "$svc_ok" = "true" ]; then
                svc_result="${GREEN}PASS${NC}"
                echo -e "${GREEN}  ✓ $svc_name: HTTP $svc_code${NC}"
                PASS_COUNT=$((PASS_COUNT + 1))
            else
                svc_result="${RED}FAIL${NC}"
                echo -e "${RED}  ✗ $svc_name: HTTP $svc_code${NC}"
                FAIL_COUNT=$((FAIL_COUNT + 1))
            fi
            SVC_RESULTS="${SVC_RESULTS}${svc_name}|${svc_url}|${svc_code}|${svc_result}\n"
        done <<< "$ALL_SVC_DOMAINS"
    fi
    if [ "$SVC_COUNT" -eq 0 ]; then
        echo -e "${YELLOW}        No active services deployed${NC}"
    fi

    # ── Results Table ──
    TOTAL_CHECKS=$((PASS_COUNT + FAIL_COUNT))
    echo ""
    echo -e "${BLUE}  ╔══════════════════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${BLUE}  ║                        ENDPOINT VERIFICATION REPORT                     ║${NC}"
    echo -e "${BLUE}  ╠════════════════════════════════════════════════════════╦══════╦══════════╣${NC}"
    echo -e "${BLUE}  ║  Endpoint                                            ║ HTTP ║  Result  ║${NC}"
    echo -e "${BLUE}  ╠════════════════════════════════════════════════════════╬══════╬══════════╣${NC}"
    printf "  ║  %-52.52s ║ %-4s ║ " "Backend (docker exec):8000/health" "$EP1_CODE"
    echo -e " $EP1_RESULT  ║"
    printf "  ║  %-52.52s ║ %-4s ║ " "HTTPS: $EP2_URL" "$EP2_CODE"
    echo -e " $EP2_RESULT  ║"
    printf "  ║  %-52.52s ║ %-4s ║ " "Traefik: $EP3_URL" "$EP3_CODE"
    echo -e " $EP3_RESULT  ║"
    # Print each deployed service row
    if [ -n "$SVC_RESULTS" ]; then
        echo -e "${BLUE}  ╠════════════════════════════════════════════════════════╬══════╬══════════╣${NC}"
        while IFS='|' read -r s_name s_url s_code s_result; do
            [ -z "$s_name" ] && continue
            printf "  ║  %-52.52s ║ %-4s ║ " "$s_name" "$s_code"
            echo -e " $s_result  ║"
        done <<< "$(echo -e "$SVC_RESULTS")"
    fi
    echo -e "${BLUE}  ╚════════════════════════════════════════════════════════╩══════╩══════════╝${NC}"

    # ── Summary ──
    if [ "$FAIL_COUNT" -eq 0 ]; then
        echo -e "\n${GREEN}  ✓ All $PASS_COUNT/$TOTAL_CHECKS endpoint checks passed${NC}"
    else
        echo -e "\n${YELLOW}  ⚠ $PASS_COUNT passed, $FAIL_COUNT failed out of $TOTAL_CHECKS checks${NC}"
    fi

    # Show container status
    echo -e "\n${BLUE}Container Status:${NC}"
    docker compose -f "$COMPOSE_FILE" ps --format "table {{.Name}}\t{{.Status}}" 2>/dev/null || \
        docker compose -f "$COMPOSE_FILE" ps 2>/dev/null || true

    # ─── Update autoscaler service (picks up code changes + new token) ────────
    if [ -f "$INSTALL_DIR/scripts/smsly-autoscaler.py" ]; then
        echo -e "${BLUE}  → Updating smsly-autoscaler service...${NC}"
        mkdir -p /opt/smsly
        cp "$INSTALL_DIR/scripts/smsly-autoscaler.py" /opt/smsly/autoscaler.py
        chmod +x /opt/smsly/autoscaler.py

        AUTOSCALER_API_TOKEN="$(env_get_value "$INSTALL_DIR/.env" "AUTOSCALER_API_TOKEN")"
        if [ -n "$AUTOSCALER_API_TOKEN" ] && [ -f /etc/systemd/system/smsly-autoscaler.service ]; then
            # Update token in existing service file
            sed -i "s|^Environment=AUTOSCALER_API_TOKEN=.*|Environment=AUTOSCALER_API_TOKEN=${AUTOSCALER_API_TOKEN}|" \
                /etc/systemd/system/smsly-autoscaler.service
            systemctl daemon-reload
        fi
        systemctl restart smsly-autoscaler 2>/dev/null || true
        echo -e "${GREEN}  ✓ Autoscaler updated${NC}"
    fi

    # ─── Re-apply OOM protection (scores reset when containers restart) ──────
    echo -e "${BLUE}  → Re-applying OOM protection for critical containers...${NC}"
    oom_containers="smsly-hosting-backend-1 $(get_db_service | sed 's|^|smsly-hosting-|' || echo smsly-hosting-postgres-primary) smsly-hosting-pgcat-1 smsly-hosting-celery-1 smsly-hosting-celery-deploy-1 smsly-hosting-celery-fast-1 smsly-hosting-celery-beat-1 smsly-hosting-socket-proxy-1"
    if [ "$MODE_AGENT_LITE" = "true" ]; then
        oom_containers="smsly-hosting-backend-1 smsly-hosting-celery-worker-1 smsly-hosting-socket-proxy-1"
    fi
    for CONTAINER in $oom_containers; do
        resolved_container="$(resolve_container_target "$CONTAINER")"
        CPID=$(docker inspect --format '{{.State.Pid}}' "$resolved_container" 2>/dev/null || echo "")
        if [ -n "$CPID" ] && [ "$CPID" != "0" ] && [ -f "/proc/$CPID/oom_score_adj" ]; then
            echo -500 > "/proc/$CPID/oom_score_adj" 2>/dev/null || true
        fi
    done
    echo -e "${GREEN}  ✓ OOM protection set (core, database, celery, proxy)${NC}"

    # ─── Ensure iptables-restore systemd service exists ─────────────────────
    if command -v iptables-save >/dev/null 2>&1; then
        mkdir -p /etc/iptables
        iptables-save > /etc/iptables/rules.v4 2>/dev/null || true
        if [ ! -f /etc/systemd/system/iptables-restore.service ]; then
            echo -e "${BLUE}  → Installing iptables-restore systemd service...${NC}"
            cat > /etc/systemd/system/iptables-restore.service <<'RESTORE_EOF'
[Unit]
Description=Restore iptables rules
Before=docker.service
After=network-pre.target

[Service]
Type=oneshot
ExecStart=/sbin/iptables-restore /etc/iptables/rules.v4
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
RESTORE_EOF
            systemctl daemon-reload 2>/dev/null || true
            systemctl enable iptables-restore 2>/dev/null || true
            echo -e "${GREEN}  ✓ iptables-restore service installed and enabled${NC}"
        fi
    fi

    # ─── Ensure platform update watcher and caddy watcher services exist ───
    if [ -f "$INSTALL_DIR/scripts/smsly-update-watcher.service" ]; then
        echo -e "${BLUE}  → Ensuring platform update and Caddy config watcher services...${NC}"
        chmod +x "$INSTALL_DIR/scripts/platform-update.sh" "$INSTALL_DIR/scripts/caddy-reload.sh" 2>/dev/null || true
        cp "$INSTALL_DIR/scripts/smsly-update-watcher.service" /etc/systemd/system/smsly-update-watcher.service 2>/dev/null || true
        cp "$INSTALL_DIR/scripts/caddy-watcher.service" /etc/systemd/system/caddy-watcher.service 2>/dev/null || true
        systemctl daemon-reload 2>/dev/null || true
        systemctl enable smsly-update-watcher caddy-watcher 2>/dev/null || true
        systemctl restart smsly-update-watcher caddy-watcher 2>/dev/null || true
        echo -e "${GREEN}  ✓ smsly-update-watcher and caddy-watcher services updated and started${NC}"
    fi

    # ─── Ensure WireGuard mesh service is enabled ───────────────────────────
    if [ -d /etc/wireguard ]; then
        for wg_conf in /etc/wireguard/*.conf; do
            [ -f "$wg_conf" ] || continue
            wg_iface=$(basename "$wg_conf" .conf)
            if ! systemctl is-enabled "wg-quick@${wg_iface}" >/dev/null 2>&1; then
                echo -e "${BLUE}  → Re-enabling WireGuard mesh ($wg_iface)...${NC}"
                systemctl enable --now "wg-quick@${wg_iface}" 2>/dev/null || true
                echo -e "${GREEN}  ✓ WireGuard $wg_iface re-enabled${NC}"
            fi
            if ! systemctl is-active "wg-quick@${wg_iface}" >/dev/null 2>&1; then
                echo -e "${YELLOW}  ⚠ WireGuard $wg_iface is not running, attempting restart...${NC}"
                systemctl start "wg-quick@${wg_iface}" 2>/dev/null || true
            fi
        done
    fi

    trap - EXIT
    release_install_lock
    echo -e "\n${GREEN}════════════════════════════════════════════════════════════${NC}"
    # Infrastructure Diagnostic & Auto-Fix
    # Infrastructure Handshake & Health Stabilization
    echo -e "\n${BLUE}  🔄 Running infrastructure handshake and stabilization...${NC}"
    chmod +x scripts/grid-handshake.sh 2>/dev/null || true
    SMSLY_MIGRATIONS_DONE=1 bash scripts/grid-handshake.sh || \
        echo -e "${YELLOW}  ⚠️ Handshake stabilization failed (non-fatal). You can run it manually later.${NC}"

    # ─── Fix .env permissions (ensures domain signal can write back) ─────
    fix_env_permissions "$INSTALL_DIR/.env" || true

    # ─── Install/update infrastructure monitor timer ─────────────────────
    if [ -f "$INSTALL_DIR/scripts/monitor_infra.sh" ]; then
        echo -e "${BLUE}  → Installing critical infrastructure monitoring timer...${NC}"
        chmod +x "$INSTALL_DIR/scripts/monitor_infra.sh"
        cp "$INSTALL_DIR/scripts/smsly-infra-monitor.service" /etc/systemd/system/smsly-infra-monitor.service 2>/dev/null || true
        cp "$INSTALL_DIR/scripts/smsly-infra-monitor.timer" /etc/systemd/system/smsly-infra-monitor.timer 2>/dev/null || true
        systemctl daemon-reload
        systemctl enable smsly-infra-monitor.timer 2>/dev/null || true
        systemctl restart smsly-infra-monitor.timer 2>/dev/null || true
        echo -e "${GREEN}  ✓ smsly-infra-monitor timer installed and started${NC}"
    fi

    echo -e "${GREEN}   ✓ UPDATE SUCCESSFUL ($UPDATE_MODE)${NC}"

    # ─── Security verify ──────────────────────────────────────────────────
    if [ -f "$INSTALL_DIR/lib/harden.sh" ]; then
        harden_security_verify
    fi

    # ─── Image signature verification ────────────────────────────────────
    if command -v cosign >/dev/null 2>&1 && [ -f "$INSTALL_DIR/scripts/cosign-verify.sh" ]; then
        echo -e "${BLUE}  → Verifying production image signatures...${NC}"
        source "$INSTALL_DIR/scripts/cosign-verify.sh"
        cosign_verify_image "smsly/backend:latest" || \
            echo -e "${YELLOW}  ⚠ Backend image signature verification failed (non-fatal on existing installs)${NC}"
    fi

    echo -e "${GREEN}════════════════════════════════════════════════════════════${NC}"
    echo -e "${YELLOW}  Debug snapshot:    sudo bash install.sh --debug${NC}"
    echo -e "${YELLOW}  Runtime recovery:  sudo bash install.sh --recover${NC}"
    echo -e "${YELLOW}  Fix permissions:   sudo bash install.sh --fix-permissions${NC}"
    exit 0
fi