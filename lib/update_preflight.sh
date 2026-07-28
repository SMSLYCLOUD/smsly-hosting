    echo -e "${YELLOW}[UPDATE] Running in update mode: $UPDATE_MODE${NC}"
    echo -e "${BLUE}  -> Safe update: preserves database/redis volumes and addon data.${NC}"

    # Ensure repo cache directory exists for user service builds
    mkdir -p /opt/smsly-cache/repos
    chmod 775 /opt/smsly-cache
    chown -R 1000:1000 /opt/smsly-cache  || true
    mkdir -p /opt/smsly-hosting/builds
    chmod 775 /opt/smsly-hosting/builds
    chown -R 1000:1000 /opt/smsly-hosting/builds  || true

    # ─── Fix .env permissions BEFORE any containers start ────────────────────
    # The docker-compose.prod.yml mounts .env into the backend container.
    # If .env has 600 permissions (created by old install.sh), the container
    # can't read it and Django crashes with PermissionError.
    # The backend container runs as UID 1000 (smsly user), so the file must be
    # writable by that user to allow the domain-config signal to sync back to .env.
    if [ -f "$INSTALL_DIR/.env" ]; then
        chown root:1000 "$INSTALL_DIR/.env"  || true
        chmod 640 "$INSTALL_DIR/.env"  || true
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
        _cmod="$(openssl x509 -in "$INSTALL_DIR/certs/registry.crt" -noout -modulus  | openssl sha256)" || return 1
        _kmod="$(openssl rsa  -in "$INSTALL_DIR/certs/registry.key" -noout -modulus  | openssl sha256)" || return 1
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
            -addext "subjectAltName=DNS:registry,DNS:localhost,IP:127.0.0.1,IP:10.100.0.1"  && {
            mv "${_tmp}/registry.key" "$INSTALL_DIR/certs/registry.key"
            mv "${_tmp}/registry.crt" "$INSTALL_DIR/certs/registry.crt"
            chmod 644 "$INSTALL_DIR/certs/registry.crt"
            chmod 600 "$INSTALL_DIR/certs/registry.key"
            echo -e "${BLUE}  → Restarting registry container...${NC}"
            docker restart smsly-hosting-registry-1 || echo -e "${YELLOW}    ⚠ Registry restart failed${NC}"
        } || true
        rm -rf "$_tmp"  || true
    fi
    mkdir -p "$INSTALL_DIR/auth"
    if [ ! -f "$INSTALL_DIR/auth/htpasswd" ] || [ -z "${REGISTRY_PASSWORD:-}" ] || [ -z "${REGISTRY_USER:-}" ]; then
        echo -e "${BLUE}  → Ensuring registry htpasswd authentication exists...${NC}"
        REGISTRY_PASS="${REGISTRY_PASSWORD:-$(python3 -c "import secrets; print(secrets.token_urlsafe(18))"  || openssl rand -hex 12  || { echo "ERROR: Cannot generate registry password" >&2; exit 1; })}"
        if command -v htpasswd ; then
            htpasswd -Bbn "${REGISTRY_USER:-smsly-registry}" "$REGISTRY_PASS" > "$INSTALL_DIR/auth/htpasswd"
        else
            python3 -c "
import bcrypt, sys
pw = sys.argv[1] if len(sys.argv) > 1 else '$REGISTRY_PASS'
print('${REGISTRY_USER:-smsly-registry}:' + bcrypt.hashpw(pw.encode(), bcrypt.gensalt(10)).decode())
" "$REGISTRY_PASS" > "$INSTALL_DIR/auth/htpasswd"  || true
        fi
        env_set_value "$INSTALL_DIR/.env" "REGISTRY_USER" "${REGISTRY_USER:-smsly-registry}"  || true
        env_set_value "$INSTALL_DIR/.env" "REGISTRY_PASSWORD" "$REGISTRY_PASS"  || true
        chmod 600 "$INSTALL_DIR/auth/htpasswd"  || true
    fi

    # Install registry cert into Docker's cert trust store so the daemon
    # connects via HTTPS (not HTTP fallback) to the registry.
    install_registry_docker_certs

    # ─── Self-heal: missing secrets (update paths can miss secret generation) ─
    echo -e "${BLUE}  → Checking for missing secrets and generating if needed...${NC}"
    _ensure_secret() {
        local _name="$1" _bytes="$2"
        if [ -z "${!_name:-}" ]; then
            local _val="$(python3 -c "import secrets; print(secrets.token_hex($_bytes))"  || openssl rand -hex "$_bytes"  || true)"
            if [ -n "$_val" ]; then
                printf -v "$_name" '%s' "$_val"
                env_set_value "$INSTALL_DIR/.env" "$_name" "$_val"  || true
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
    if command -v cosign ; then
        mkdir -p "$INSTALL_DIR/cosign-keys"
        COSIGN_PRIVATE_KEY_PATH="$INSTALL_DIR/cosign-keys/cosign.key"
        COSIGN_PUBLIC_KEY_PATH="$INSTALL_DIR/cosign-keys/cosign.pub"
        if [ ! -f "$COSIGN_PRIVATE_KEY_PATH" ] || [ ! -f "$COSIGN_PUBLIC_KEY_PATH" ]; then
            echo -e "${BLUE}  → Cosign keypair missing — generating...${NC}"
            COSIGN_PASSWORD="${COSIGN_PASSWORD:-$(python3 -c "import secrets; print(secrets.token_hex(32))"  || openssl rand -hex 32  || true)}"
            COSIGN_PASSWORD="$COSIGN_PASSWORD" cosign generate-key-pair  || true
            if [ -f cosign.key ]; then
                mv cosign.key "$COSIGN_PRIVATE_KEY_PATH"
                mv cosign.pub "$COSIGN_PUBLIC_KEY_PATH"
                chmod 600 "$COSIGN_PRIVATE_KEY_PATH"
                chmod 644 "$COSIGN_PUBLIC_KEY_PATH"
                env_set_value "$INSTALL_DIR/.env" "COSIGN_PASSWORD" "$COSIGN_PASSWORD"  || true
                env_set_value "$INSTALL_DIR/.env" "COSIGN_PRIVATE_KEY_PATH" "$COSIGN_PRIVATE_KEY_PATH"  || true
                echo -e "${GREEN}    ✓ Cosign keypair created${NC}"
            else
                echo -e "${YELLOW}    ⚠ cosign generate-key-pair ran but no output — skipping${NC}"
            fi
        else
            # Key exists but password might be missing
            if [ -z "${COSIGN_PASSWORD:-}" ]; then
                COSIGN_PASSWORD="$(python3 -c "import secrets; print(secrets.token_hex(32))"  || openssl rand -hex 32  || true)"
                env_set_value "$INSTALL_DIR/.env" "COSIGN_PASSWORD" "$COSIGN_PASSWORD"  || true
            fi
        fi
    fi

    # ─── Git Safety ──────────────────────────────────────────────────────────
    # Prevents "dubious ownership" errors on production VPS
    git config --global --add safe.directory "$INSTALL_DIR"  || true

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
