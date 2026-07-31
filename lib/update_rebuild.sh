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
               "REDIS_PASSWORD|$(python3 -c "import secrets; print(secrets.token_hex(32))"  || true)"
               "RABBITMQ_PASSWORD|$(python3 -c "import secrets; print(secrets.token_hex(32))"  || true)"
               "GATEWAY_SECRET|$(python3 -c "import secrets; print(secrets.token_hex(64))"  || true)"
               "GITHUB_WEBHOOK_SECRET|$(python3 -c "import secrets; print(secrets.token_hex(64))"  || true)"
               "AUTOSCALER_API_TOKEN|$(python3 -c "import secrets; print(secrets.token_hex(64))"  || true)"
               "FRP_AUTH_TOKEN|$(python3 -c "import secrets; print(secrets.token_hex(64))"  || true)"
               "PGCAT_ADMIN_PASSWORD|$(python3 -c "import secrets; print(secrets.token_hex(48))"  || true)"
               "REGISTRY_HTTP_SECRET|$(python3 -c "import secrets; print(secrets.token_hex(32))"  || true)"
               "BACKUP_ENCRYPTION_KEY|$(python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"  || openssl rand -base64 32)"
               "REPLICATION_PASSWORD|$(python3 -c "import secrets; print(secrets.token_hex(32))"  || true)"
               "SENTINEL_PASSWORD|$(python3 -c "import secrets; print(secrets.token_hex(32))"  || true)"
               "CROWDSEC_BOUNCER_KEY|$(python3 -c "import secrets; print(secrets.token_hex(32))"  || true)"
           )
         for _entry in "${_env_generators[@]}"; do
             _key="${_entry%%|*}"
             _generator="${_entry#*|}"
             if ! grep -q "^${_key}=" "$_env_file" ; then
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
             source "$_env_file"  || true
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
     _current_compose="$(env_get_value "$INSTALL_DIR/.env" "COMPOSE_FILE"  || true)"
     if [ "$_current_compose" = "docker-compose.yml" ] && [ -f "$INSTALL_DIR/docker-compose.prod.yml" ]; then
         # Check if postgres-primary already has migrated data (e.g. from a
         # previous manual migration run).  If so, skip re-migration and
         # switch COMPOSE_FILE immediately so the update pipeline can
         # reach the correct DB hostname.
         _already_migrated=false
         if docker ps --format '{{.Names}}'  | grep -qx 'smsly-postgres-primary'; then
             _tables=$(timeout 30 docker exec smsly-postgres-primary psql -U smsly_admin -d smsly_hosting -t -A \
                 -c "SELECT count(*) FROM information_schema.tables WHERE table_schema='public';"  || echo 0)
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
                 db postgres-replica pgcat redis-primary redis-replica \
                  || echo -e "${YELLOW}  ⚠ Some HA services may not be healthy yet${NC}"
             echo -e "${YELLOW}  → Switching COMPOSE_FILE: docker-compose.yml → docker-compose.prod.yml${NC}"
             env_set_value "$INSTALL_DIR/.env" "COMPOSE_FILE" "docker-compose.prod.yml"
         else
             # If the old db container still has data, migrate it FIRST before
             # switching COMPOSE_FILE.  If migration fails, we keep the old
             # compose so the platform continues working with the old db.
             _has_old_db=false
             if [ "$(docker ps -a --format '{{.Names}}'  | grep -cx 'smsly-hosting-db-1' || echo 0)" -gt 0 ]; then
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
                         db postgres-replica pgcat redis-primary redis-replica \
                          || echo -e "${YELLOW}  ⚠ Some HA services may not be healthy yet${NC}"

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
                     db postgres-replica pgcat redis-primary redis-replica \
                      || echo -e "${YELLOW}  ⚠ Some HA services may not be healthy yet${NC}"
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
         _current_compose_final="$(env_get_value "$_env_fix_file" "COMPOSE_FILE"  || true)"

         # REDIS_HOST: pre-HA used "redis", now "redis-primary"
         _current_redis_host="$(env_get_value "$_env_fix_file" "REDIS_HOST"  || true)"
         if [ "$_current_redis_host" = "redis" ] || [ -z "$_current_redis_host" ]; then
             echo -e "${YELLOW}  → Updating REDIS_HOST: ${_current_redis_host:-<unset>} → redis-primary${NC}"
             env_set_value "$_env_fix_file" "REDIS_HOST" "redis-primary"
         fi

         # REDIS_URL: replace stale @redis: with @redis-primary:
         _redis_url="$(env_get_value "$_env_fix_file" "REDIS_URL"  || true)"
         if echo "$_redis_url" | grep -q '@redis:'; then
             _fixed_redis_url="$(echo "$_redis_url" | sed 's|@redis:|@redis-primary:|g')"
             echo -e "${YELLOW}  → Fixing REDIS_URL hostname: redis → redis-primary${NC}"
             env_set_value "$_env_fix_file" "REDIS_URL" "$_fixed_redis_url"
         fi

         # CONTAINER_REGISTRY_URL: 127.0.0.1:5000 → registry:5000
         _registry="$(env_get_value "$_env_fix_file" "CONTAINER_REGISTRY_URL"  || true)"
         if [ "$_registry" = "127.0.0.1:5000" ] || [ "$_registry" = "localhost:5000" ]; then
             echo -e "${YELLOW}  → Fixing CONTAINER_REGISTRY_URL: $_registry → registry:5000${NC}"
             env_set_value "$_env_fix_file" "CONTAINER_REGISTRY_URL" "registry:5000"
         fi

         # DATABASE_URL: auto-migrate from pre-HA single-node @db to pgcat
         _db_url="$(env_get_value "$_env_fix_file" "DATABASE_URL"  || true)"
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
         _direct_url="$(env_get_value "$_env_fix_file" "DIRECT_DATABASE_URL"  || true)"
         if echo "$_direct_url" | grep -q '@db:' && echo "$_current_compose_final" | grep -q 'prod'; then
             _migrated_direct="$(echo "$_direct_url" | sed 's|@db:5432|@postgres-primary:5432|')"
             echo -e "${YELLOW}  → Migrating DIRECT_DATABASE_URL: @db → @postgres-primary${NC}"
             env_set_value "$_env_fix_file" "DIRECT_DATABASE_URL" "$_migrated_direct"
         fi

         # Ensure REDIS_MIN_REPLICAS_TO_WRITE is present only when the
         # prod compose is active (has a replica).  Setting it on the
         # dev compose will cause NOREPLICAS errors on every write.
         if echo "$_current_compose_final" | grep -q 'prod'; then
             if ! grep -q '^REDIS_MIN_REPLICAS_TO_WRITE=' "$_env_fix_file" ; then
                 echo -e "${YELLOW}  → Adding REDIS_MIN_REPLICAS_TO_WRITE=1 (Redis HA durability)${NC}"
                 echo 'REDIS_MIN_REPLICAS_TO_WRITE=1' >> "$_env_fix_file"
             fi
         else
             # Dev/single-node compose — ensure replica requirement is off
             # so writes don't get rejected.
             _min_rep="$(grep '^REDIS_MIN_REPLICAS_TO_WRITE=' "$_env_fix_file"  | cut -d= -f2 || true)"
             if [ "$_min_rep" != "0" ]; then
                 echo -e "${YELLOW}  → Setting REDIS_MIN_REPLICAS_TO_WRITE=0 (single-node, no replica)${NC}"
                 env_set_value "$_env_fix_file" "REDIS_MIN_REPLICAS_TO_WRITE" "0"
             fi
         fi

         # Ensure PG_SYNCHRONOUS_COMMIT is present (default: on)
         if ! grep -q '^PG_SYNCHRONOUS_COMMIT=' "$_env_fix_file" ; then
             echo -e "${YELLOW}  → Adding PG_SYNCHRONOUS_COMMIT=on (PostgreSQL durability)${NC}"
             echo 'PG_SYNCHRONOUS_COMMIT=on' >> "$_env_fix_file"
         else
             _pg_commit="$(env_get_value "$_env_fix_file" "PG_SYNCHRONOUS_COMMIT"  || true)"
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
                docker compose -f "$COMPOSE_FILE" stop --timeout 15 frontend || echo -e "${YELLOW}    ⚠ docker compose stop frontend failed (non-fatal)${NC}"
                  docker compose -f "$COMPOSE_FILE" rm -f frontend || echo -e "${YELLOW}    ⚠ docker compose rm frontend failed (non-fatal)${NC}"
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
            docker compose -f "$COMPOSE_FILE" stop --timeout 15 backend celery celery-deploy celery-fast celery-beat $(get_pgcat_if_exists) || echo -e "${YELLOW}    ⚠ docker compose stop backend/celery failed (non-fatal)${NC}"

            echo -e "${BLUE}  → Running migrations...${NC}"
            run_backend_migrations --root || {
                echo -e "${YELLOW}  ⚠ Migration failed — retrying in 15s...${NC}"
                sleep 15
                run_backend_migrations --root
            }

            echo -e "${BLUE}  → Starting backend & pgcat...${NC}"
            if [ -n "$(get_pgcat_if_exists)" ]; then docker compose -f "$COMPOSE_FILE" up -d --no-deps pgcat || echo -e "${YELLOW}    ⚠ docker compose up pgcat failed (non-fatal)${NC}"; fi
            if [ "$MODE_AGENT_LITE" = "true" ]; then
                docker compose -f "$COMPOSE_FILE" up -d --force-recreate backend
            else
                docker compose -f "$COMPOSE_FILE" up -d --force-recreate --no-deps backend
            fi
            wait_for_container_ready "smsly-hosting-backend-1" 120 || true

            echo -e "${BLUE}  • Running post-migration tasks...${NC}"
            echo -e "${BLUE}    ↳ Running collectstatic...${NC}"
            timeout 120 docker compose -f "$COMPOSE_FILE" exec -T --user root backend python manage.py collectstatic --noinput || echo -e "${YELLOW}    ⚠ collectstatic failed or timed out${NC}"

            set_checkpoint "update_db_migrated"

            # Clean stale celerybeat-schedule (prevents Permission denied crash loop)
            echo -e "${BLUE}  → Cleaning celerybeat-schedule...${NC}"
            timeout 30 docker compose -f "$COMPOSE_FILE" exec -T --user root backend rm -f /app/celerybeat-schedule || echo -e "${YELLOW}    ⚠ celerybeat-schedule cleanup failed${NC}"

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
                timeout -k 5 600 docker compose -f "$COMPOSE_FILE" build frontend  || {
                    echo -e "${YELLOW}  ⚠ Frontend build failed (cached layers missing). Skipping frontend.${NC}"
                    echo -e "${YELLOW}    Run --update when Docker Hub is reachable for a full rebuild.${NC}"
                }
                docker compose -f "$COMPOSE_FILE" stop --timeout 15 frontend || echo -e "${YELLOW}    ⚠ docker compose stop frontend failed (non-fatal)${NC}"
                docker compose -f "$COMPOSE_FILE" rm -f frontend || echo -e "${YELLOW}    ⚠ docker compose rm frontend failed (non-fatal)${NC}"
                docker compose -f "$COMPOSE_FILE" up -d --force-recreate --no-deps frontend || echo -e "${YELLOW}    ⚠ docker compose up frontend failed (non-fatal)${NC}"
            fi

            # 2. Stop backend, celery & pgcat so their DB connections don't block
            #    migrations (ALTER TABLE requires exclusive locks).
            echo -e "${BLUE}  → Stopping backend, celery & pgcat for migrations...${NC}"
            docker compose -f "$COMPOSE_FILE" stop --timeout 15 backend celery celery-deploy celery-fast celery-beat $(get_pgcat_if_exists) || echo -e "${YELLOW}    ⚠ docker compose stop backend/celery failed (non-fatal)${NC}"

            # 3. Run migrations
            echo -e "${BLUE}  → Running migrations...${NC}"
            run_backend_migrations --root || {
                echo -e "${YELLOW}  ⚠ Migration failed — retrying in 15s...${NC}"
                sleep 15
                run_backend_migrations --root
            }

            # 4. Start pgcat & backend (picks up Python code changes from mounted volume)
            echo -e "${BLUE}  → Starting pgcat & backend...${NC}"
            if [ -n "$(get_pgcat_if_exists)" ]; then docker compose -f "$COMPOSE_FILE" up -d --no-deps pgcat || echo -e "${YELLOW}    ⚠ docker compose up pgcat failed (non-fatal)${NC}"; fi
            if [ "$MODE_AGENT_LITE" = "true" ]; then
                docker compose -f "$COMPOSE_FILE" up -d --remove-orphans $(get_redis_service) rabbitmq socket-proxy
                sync_agent_lite_rabbitmq_password
                docker compose -f "$COMPOSE_FILE" up -d --force-recreate backend
            else
                docker compose -f "$COMPOSE_FILE" up -d --force-recreate --no-deps backend
            fi
            wait_for_container_ready "smsly-hosting-backend-1" 120 || true

            echo -e "${BLUE}  • Running post-migration tasks...${NC}"
            echo -e "${BLUE}    ↳ Running collectstatic...${NC}"
            timeout 120 docker compose -f "$COMPOSE_FILE" exec -T --user root backend python manage.py collectstatic --noinput || echo -e "${YELLOW}    ⚠ collectstatic failed or timed out${NC}"

            # 5. Clean celerybeat-schedule and restart celery workers
            echo -e "${BLUE}  → Cleaning celerybeat-schedule...${NC}"
            timeout 30 docker compose -f "$COMPOSE_FILE" exec -T --user root backend rm -f /app/celerybeat-schedule || echo -e "${YELLOW}    ⚠ celerybeat-schedule cleanup failed (non-fatal)${NC}"

            restart_svcs="celery celery-deploy celery-fast celery-beat"
            if [ "$MODE_AGENT_LITE" = "true" ]; then
                restart_svcs="celery-worker"
            fi
            if [ "$MODE_AGENT_LITE" = "true" ]; then
                docker compose -f "$COMPOSE_FILE" up -d --force-recreate $restart_svcs || echo -e "${YELLOW}    ⚠ docker compose up celery failed (non-fatal)${NC}"
            else
                docker compose -f "$COMPOSE_FILE" restart $restart_svcs || echo -e "${YELLOW}    ⚠ docker compose restart celery failed (non-fatal)${NC}"
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
            docker builder prune -af  || true

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
            docker compose -f "$COMPOSE_FILE" stop --timeout 15 backend celery celery-deploy celery-fast celery-beat $(get_pgcat_if_exists) || echo -e "${YELLOW}    ⚠ docker compose stop backend/celery failed (non-fatal)${NC}"

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
            if [ -n "$(get_pgcat_if_exists)" ]; then docker compose -f "$COMPOSE_FILE" up -d --no-deps pgcat || echo -e "${YELLOW}    ⚠ docker compose up pgcat failed (non-fatal)${NC}"; fi
            if [ "$MODE_AGENT_LITE" = "true" ]; then
                docker compose -f "$COMPOSE_FILE" up -d --force-recreate backend
            else
                docker compose -f "$COMPOSE_FILE" up -d --force-recreate --no-deps backend
            fi
            wait_for_container_ready "smsly-hosting-backend-1" 120 || true

            echo -e "${BLUE}  • Running post-migration tasks...${NC}"
            echo -e "${BLUE}    ↳ Running collectstatic...${NC}"
            timeout 120 docker compose -f "$COMPOSE_FILE" exec -T --user root backend python manage.py collectstatic --noinput || echo -e "${YELLOW}    ⚠ collectstatic failed or timed out${NC}"

            # 11. Clean celerybeat-schedule and restart beat
            echo -e "${BLUE}  → Cleaning celerybeat-schedule...${NC}"
            timeout 30 docker compose -f "$COMPOSE_FILE" exec -T --user root backend rm -f /app/celerybeat-schedule || echo -e "${YELLOW}    ⚠ celerybeat-schedule cleanup failed (non-fatal)${NC}"
            
            restart_svcs="celery celery-beat celery-deploy celery-fast"
            if [ "$MODE_AGENT_LITE" = "true" ]; then
                restart_svcs="celery-worker"
            fi
            if [ "$MODE_AGENT_LITE" = "true" ]; then
                docker compose -f "$COMPOSE_FILE" up -d --force-recreate $restart_svcs || echo -e "${YELLOW}    ⚠ docker compose up celery failed (non-fatal)${NC}"
            else
                docker compose -f "$COMPOSE_FILE" restart $restart_svcs || echo -e "${YELLOW}    ⚠ docker compose restart celery failed (non-fatal)${NC}"
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
        _infisical_running=$(docker ps --filter "name=infisical" --format '{{.Names}}'  | head -1)
        if [ -n "$_infisical_running" ]; then
            echo -e "${GREEN}  ✓ Infisical already running (${_infisical_running})${NC}"
        else
            # Ensure the infisical data volume exists
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

            # Generate env file on the volume (if not already present)
            _gen_script="$INSTALL_DIR/infrastructure/docker/infisical-gen-env.sh"
            if [ -f "$_gen_script" ]; then
                docker run --rm \
                    -v infisical_data:/data \
                    -v "$_gen_script":/tmp/infisical-gen-env.sh:ro \
                    alpine:3.19 \
                    sh /tmp/infisical-gen-env.sh /data/infisical.env  || \
                    echo -e "${YELLOW}  ⚠ Could not generate Infisical env (may already exist)${NC}"
            fi

            # Bring up Infisical (env_file loaded from volume)
            echo -e "${BLUE}  → Provisioning Infisical secret manager...${NC}"
            docker compose --env-file "$INSTALL_DIR/.env" \
                -f "$_INFISICAL_COMPOSE" up -d --remove-orphans  && \
                echo -e "${GREEN}  ✓ Infisical is running${NC}" || \
                echo -e "${YELLOW}  ⚠ Infisical startup failed (non-fatal — secrets remain in .env)${NC}"
        fi

        # Sync platform secrets to Infisical if it's running
        _infisical_running=$(docker ps --filter "name=infisical" --format '{{.Names}}'  | head -1)
        if [ -n "$_infisical_running" ]; then
            echo -e "${BLUE}  → Syncing platform secrets to Infisical...${NC}"
            backend_container="$(resolve_container_target "smsly-hosting-backend-1")"
            if [ -n "$backend_container" ]; then
                timeout 60 docker exec "$backend_container" python manage.py sync_infisical_secrets --push  || \
                    echo -e "${YELLOW}  ⚠ Infisical sync failed (non-fatal — secrets remain in .env)${NC}"
            fi
        fi
    fi

    # ─── Observability Stack Update (master mode only) ──────────────────────
    if [ "$MODE_AGENT_LITE" != "true" ] && [ "$MODE_NODE" != "true" ]; then
        echo -e "${BLUE}  → Updating observability stack...${NC}"
        # Ensure scripts mounted into containers are executable (git may not preserve +x)
        chmod +x "$INSTALL_DIR"/scripts/alertmanager-entrypoint.sh  || true
        chmod +x "$INSTALL_DIR"/infrastructure/docker/infisical-gen-env.sh  || true
        mkdir -p /opt/smsly-hosting/prometheus-targets
        if ! chown -R 1000:1000 /opt/smsly-hosting/prometheus-targets ; then
            echo -e "${YELLOW}  ⚠ Could not chown prometheus-targets to uid 1000${NC}"
        fi
        chmod 2777 /opt/smsly-hosting/prometheus-targets  || true
        docker compose \
            --env-file /opt/smsly-hosting/.env \
            -f infrastructure/docker/docker-compose.observability.yml \
            up -d --pull always || \
            echo -e "${YELLOW}  ⚠ Observability stack had issues (non-fatal)${NC}"
        # Restart containers whose bind-mounted config or environment may have
        # changed.  docker compose up -d only recreates on IMAGE changes, so
        # config-file updates require an explicit restart.
        docker restart smsly-grafana || echo -e "${YELLOW}    ⚠ docker restart smsly-grafana failed (non-fatal)${NC}"
        docker restart smsly-alertmanager || echo -e "${YELLOW}    ⚠ docker restart smsly-alertmanager failed (non-fatal)${NC}"
        docker restart smsly-prometheus || echo -e "${YELLOW}    ⚠ docker restart smsly-prometheus failed (non-fatal)${NC}"
        docker restart smsly-docker-labels || echo -e "${YELLOW}    ⚠ docker restart smsly-docker-labels failed (non-fatal)${NC}"
        docker restart smsly-promtail || echo -e "${YELLOW}    ⚠ docker restart smsly-promtail failed (non-fatal)${NC}"
        # Deploy/update docker-labels exporter to all remote nodes and
        # regenerate Prometheus file_sd target files (docker-labels,
        # cAdvisor, Node Exporter).
        backend_container=$(docker ps --format '{{.Names}}' | grep -E '^smsly-hosting-backend(-1)?$' | head -1)
        if [ -n "$backend_container" ]; then
            timeout 60 docker exec "$backend_container" python manage.py deploy_docker_labels_exporters --force || echo -e "${YELLOW}    ⚠ deploy_docker_labels_exporters failed${NC}"
        fi
        echo -e "${GREEN}  ✓ Observability stack updated${NC}"
    fi
    if [ -n "${CROWDSEC_BOUNCER_KEY:-}" ]; then
        echo -e "${BLUE}  → Registering CrowdSec Bouncer...${NC}"
        timeout 30 docker exec smsly-crowdsec cscli bouncers add traefik-bouncer -k "${CROWDSEC_BOUNCER_KEY:-}" || echo -e "${YELLOW}    ⚠ CrowdSec bouncer registration failed (already exists, non-fatal)${NC}"
    fi

    set_checkpoint "update_containers_rebuilt"
fi
