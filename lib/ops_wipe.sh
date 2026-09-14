wipe_existing_install() {
    echo -e "${YELLOW}[WIPE] Removing existing SMSLY Hosting installation artifacts...${NC}"

    if [ "$EUID" -ne 0 ]; then
        echo -e "${RED}x Please run as root (sudo bash install.sh --wipe)${NC}"
        exit 1
    fi

    if [ "${FORCE_WIPE:-0}" != "1" ]; then
        if [ -e /dev/tty ]; then
            echo -e "${RED}  WARNING: This permanently deletes containers, volumes, networks, and $INSTALL_DIR${NC}"
            read -r -p "  Type WIPE to continue: " WIPE_CONFIRM < /dev/tty
            if [ "$WIPE_CONFIRM" != "WIPE" ]; then
                echo -e "${YELLOW}  Wipe cancelled by user.${NC}"
                exit 1
            fi
        else
            echo -e "${RED}x Non-interactive wipe requires FORCE_WIPE=1${NC}"
            exit 1
        fi
    fi

    if [ -f "$COMPOSE_FILE" ]; then
        cd "$INSTALL_DIR"
        docker compose -f "$COMPOSE_FILE" down -v --remove-orphans || echo -e "${YELLOW}    ⚠ docker compose down failed${NC}"
    fi

    SMSLY_CONTAINERS=$(docker ps -a --filter "name=smsly-hosting" -q  || true)
    if [ -n "$SMSLY_CONTAINERS" ]; then
        docker rm -f $SMSLY_CONTAINERS || echo -e "${YELLOW}    ⚠ docker rm containers failed${NC}"
    fi

    SMSLY_VOLUMES=$(docker volume ls --filter "name=smsly-hosting" -q  || true)
    if [ -n "$SMSLY_VOLUMES" ]; then
        for vol in $SMSLY_VOLUMES; do
            docker volume rm "$vol"  || true
        done
    fi

    SMSLY_NETWORKS=$(docker network ls --filter "name=smsly-hosting" -q  || true)
    if [ -n "$SMSLY_NETWORKS" ]; then
        for net in $SMSLY_NETWORKS; do
            docker network rm "$net"  || true
        done
    fi

    # Clean up Caddy watcher service (prevents stale config on reinstall)
    systemctl stop caddy-watcher || echo -e "${YELLOW}    ⚠ systemctl stop caddy-watcher failed${NC}"
    systemctl disable caddy-watcher || echo -e "${YELLOW}    ⚠ systemctl disable caddy-watcher failed${NC}"
    rm -f /etc/systemd/system/caddy-watcher.service

    # Reset Caddyfile to default (prevents stale routing)
    if [ -f "$INSTALL_DIR"/caddy-config/Caddyfile ]; then
        echo ':80 { respond "Caddy is running" 200 }' > "$INSTALL_DIR"/caddy-config/Caddyfile
    fi

    # Remove Cloudflare token override
    rm -rf /etc/systemd/system/caddy.service.d
    systemctl daemon-reload || echo -e "${YELLOW}    ⚠ systemctl daemon-reload failed${NC}"

    rm -rf "$INSTALL_DIR"
    rm -f "$LOG_FILE"

    trap - EXIT
    release_install_lock
    echo -e "${GREEN}OK Wipe complete. The server is ready for a fresh install.${NC}"
    echo -e "${YELLOW}  Run: curl -fsSL https://raw.githubusercontent.com/SMSLYCLOUD/smsly-hosting/master/install.sh -o install.sh${NC}"
    echo -e "${YELLOW}       gpg --verify install.sh  # if you have a signed copy${NC}"
    echo -e "${YELLOW}       sudo bash install.sh${NC}"
    exit 0
}
fix_env_permissions() {
    local env_file="${1:-$INSTALL_DIR/.env}"
    echo -e "${BLUE}  → Fixing .env permissions...${NC}"

    if [ ! -f "$env_file" ]; then
        echo -e "${YELLOW}  ⚠ .env not found at $env_file${NC}"
        return 1
    fi

    # The backend container runs as UID 1000 (smsly user).
    # .env must be group-writable by GID 1000 so the domain-config
    # signal can persist DOMAIN/USE_SSL changes back to .env.
    chown root:1000 "$env_file"  || true
    chmod 664 "$env_file"  || true

    local owner="" mode=""
    owner="$(stat -c '%u:%g' "$env_file"  || echo "?")"
    mode="$(stat -c '%a' "$env_file"  || echo "?")"
    echo -e "${GREEN}  ✓ .env permissions: $mode owner=$owner${NC}"

    # Also fix caddy-config directory for good measure.
    # Owner root (backend writes config as cap-dropped root), group 1000
    # (caddy container uid 1000 reads it).
    if [ -d "$INSTALL_DIR/caddy-config" ]; then
        chown -R 0:1000 "$INSTALL_DIR/caddy-config"  || true
        chmod -R u+rwX,g+rwX "$INSTALL_DIR/caddy-config"  || true
        echo -e "${GREEN}  ✓ caddy-config permissions fixed${NC}"
    fi

    # Fix staticfiles/media directories
    for dir in staticfiles media backups; do
        if [ -d "$INSTALL_DIR/$dir" ]; then
            chown -R 1000:1000 "$INSTALL_DIR/$dir"  || true
        fi
    done

    # Caddy runs as UID 1000 and writes access.log into the caddy_logs
    # Docker volume. If the volume root is root-owned (fresh volume),
    # every config reload fails validation with "permission denied" and
    # Caddy keeps serving a STALE config — new domains get no cert and
    # Cloudflare reports SSL handshake failure (2026-09-10 incident).
    local caddy_log_dir
    caddy_log_dir="$(docker volume inspect smsly-hosting_caddy_logs --format '{{.Mountpoint}}' 2>/dev/null || true)"
    if [ -n "$caddy_log_dir" ] && [ -d "$caddy_log_dir" ]; then
        chown 1000:1000 "$caddy_log_dir"  || true
        chmod u+rwx "$caddy_log_dir"  || true
        echo -e "${GREEN}  ✓ caddy_logs permissions fixed${NC}"
    fi

    # Fix builds and prometheus-targets directories
    for dir in builds prometheus-targets; do
        if [ -d "$INSTALL_DIR/$dir" ]; then
            chown -R 1000:1000 "$INSTALL_DIR/$dir"  || true
            chmod 2777 "$INSTALL_DIR/$dir"  || true
        fi
    done
}
