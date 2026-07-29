recover_runtime_stack() {
    echo -e "${BLUE}  -> Running runtime recovery (network + core services + edge)...${NC}"

    ensure_update_networks
    ensure_infrastructure_permissions

    # Only restart Docker if the daemon was reconfigured (e.g. for registry trust).
    # Unconditional restart during recovery can cascade-fail all running
    # containers — including the proxy (Caddy/Traefik) — causing a total outage.
    if [ -f "/etc/docker/daemon.json" ] && [ -f "/var/run/docker.sock" ]; then
        echo -e "${BLUE}    -> Docker daemon is running; skipping restart to preserve live containers${NC}"
    fi

    echo -e "${BLUE}    -> Starting dependency services...${NC}"

    # Ensure registry TLS cert + htpasswd exist before starting the registry.
    # The registry container will crash-loop without these files. Also
    # regenerate if the existing key/cert don't match — `openssl req`
    # produces a matched pair in one shot, so a mismatch means one
    # file was rotated independently of the other.
    mkdir -p "$INSTALL_DIR/auth" "$INSTALL_DIR/certs"
    _regen_registry_tls() {
        echo -e "${BLUE}      Generating self-signed TLS cert for registry...${NC}"
        _tmp_dir="$(mktemp -d)"
        openssl req -x509 -nodes -days 3650 -newkey rsa:2048 \
            -keyout "${_tmp_dir}/registry.key" \
            -out    "${_tmp_dir}/registry.crt" \
            -subj "/CN=registry" \
            -addext "subjectAltName=DNS:registry,DNS:localhost,IP:127.0.0.1,IP:10.100.0.1" 
        local _rc=$?
        if [ "$_rc" -ne 0 ]; then
            rm -rf "$_tmp_dir"
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
        local _cmod _kmod
        _cmod="$(openssl x509 -in "$INSTALL_DIR/certs/registry.crt" -noout -modulus  | openssl sha256)" || return 1
        _kmod="$(openssl rsa  -in "$INSTALL_DIR/certs/registry.key" -noout -modulus  | openssl sha256)" || return 1
        [ "$_cmod" = "$_kmod" ]
    }
    if ! _registry_tls_ok; then
        _regen_registry_tls
        if ! _registry_tls_ok; then
            echo -e "${RED}    ✗ Registry TLS cert/key still mismatched after regen attempt${NC}"
            echo -e "${YELLOW}      Manual fix: openssl req -x509 -nodes -days 3650 -newkey rsa:2048 \\${NC}"
            echo -e "${YELLOW}        -keyout $INSTALL_DIR/certs/registry.key \\${NC}"
            echo -e "${YELLOW}        -out    $INSTALL_DIR/certs/registry.crt \\${NC}"
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
            python3 -c "
import bcrypt, sys
pw = sys.argv[1] if len(sys.argv) > 1 else '$REGISTRY_PASS'
print('${REGISTRY_USER:-smsly-registry}:' + bcrypt.hashpw(pw.encode(), bcrypt.gensalt(10)).decode())
" "$REGISTRY_PASS" > "$INSTALL_DIR/auth/htpasswd"  || true
        fi
        env_set_value "$INSTALL_DIR/.env" "REGISTRY_USER" "${REGISTRY_USER:-smsly-registry}"
        env_set_value "$INSTALL_DIR/.env" "REGISTRY_PASSWORD" "$REGISTRY_PASS"
    fi

    # Install registry cert into Docker's cert trust store so the daemon
    # connects via HTTPS (not HTTP fallback) to the registry.
    install_registry_docker_certs

    # ─── Self-heal: missing secrets + cosign keypair ───────────────────────
    if [ -f "$INSTALL_DIR/.env" ]; then
        _ensure_secret() {
            local _name="$1" _bytes="$2"
            if ! grep -q "^${_name}=" "$INSTALL_DIR/.env"  || [ -z "$(grep "^${_name}=" "$INSTALL_DIR/.env"  | cut -d= -f2)" ]; then
                local _val="$(python3 -c "import secrets; print(secrets.token_hex($_bytes))"  || openssl rand -hex "$_bytes"  || true)"
                if [ -n "$_val" ]; then
                    printf -v "$_name" '%s' "$_val"
                    env_set_value "$INSTALL_DIR/.env" "$_name" "$_val"  || true
                    echo -e "${BLUE}    → Self-healed $_name${NC}"
                fi
            fi
        }
        _ensure_secret REGISTRY_HTTP_SECRET 32
        _ensure_secret REPLICATION_PASSWORD 32
        _ensure_secret SENTINEL_PASSWORD 32
        _ensure_secret CROWDSEC_BOUNCER_KEY 32
        _ensure_secret COSIGN_PASSWORD 32
    fi
    if command -v cosign ; then
        mkdir -p "$INSTALL_DIR/cosign-keys"
        if [ ! -f "$INSTALL_DIR/cosign-keys/cosign.key" ] || [ ! -f "$INSTALL_DIR/cosign-keys/cosign.pub" ]; then
            echo -e "${BLUE}    → Cosign keypair missing — generating...${NC}"
            COSIGN_PASSWORD="${COSIGN_PASSWORD:-$(python3 -c "import secrets; print(secrets.token_hex(32))"  || openssl rand -hex 32  || true)}"
            COSIGN_PASSWORD="$COSIGN_PASSWORD" cosign generate-key-pair  || true
            if [ -f cosign.key ]; then
                mv cosign.key "$INSTALL_DIR/cosign-keys/cosign.key"
                mv cosign.pub "$INSTALL_DIR/cosign-keys/cosign.pub"
                chmod 600 "$INSTALL_DIR/cosign-keys/cosign.key"
                chmod 644 "$INSTALL_DIR/cosign-keys/cosign.pub"
                env_set_value "$INSTALL_DIR/.env" "COSIGN_PASSWORD" "$COSIGN_PASSWORD"  || true
                env_set_value "$INSTALL_DIR/.env" "COSIGN_PRIVATE_KEY_PATH" "$INSTALL_DIR/cosign-keys/cosign.key"  || true
                echo -e "${GREEN}      ✓ Cosign keypair created${NC}"
            fi
        fi
    fi

    if [ "$MODE_AGENT_LITE" = "true" ]; then
        docker compose -f "$COMPOSE_FILE" up -d redis rabbitmq socket-proxy || true
        wait_for_container_ready "smsly-redis-primary" 120 || true
        sync_agent_lite_rabbitmq_password
    else
        docker compose -f "$COMPOSE_FILE" up -d $(get_db_service) $(get_pgcat_if_exists) redis rabbitmq socket-proxy registry || true
        wait_for_container_ready "smsly-postgres-primary" 120 || true
        if [ -n "$(get_pgcat_if_exists)" ]; then wait_for_container_ready "smsly-hosting-pgcat-1" 120 || true; fi
        wait_for_container_ready "smsly-hosting-redis-1" 120 || true
    fi

    if should_manage_caddy && docker compose -f "$COMPOSE_FILE" ps caddy  | grep -q "Up"; then
        if caddy_needs_fix; then
            generate_safe_caddyfile "recover_runtime_stack"
        fi
    fi

    echo -e "${BLUE}    -> Refreshing runtime services...${NC}"
    if ! refresh_runtime_services; then
        echo -e "${YELLOW}  WARN Runtime recovery could not fully refresh all runtime services${NC}"
        return 1
    fi

    echo -e "${GREEN}  OK Runtime recovery completed${NC}"
}
