    fi
    return 1  # Config is fine
}

bust_core_build_cache() {
    echo -e "${BLUE}  Busting frontend/backend build cache...${NC}"

    # Remove old app image layers for deterministic rebuilds (no DB/data touched).
    for svc in frontend backend celery celery-beat; do
        local image_ids=""
        image_ids="$(docker compose -f "$COMPOSE_FILE" images -q "$svc" 2>/dev/null | awk 'NF' | sort -u || true)"
        if [ -n "$image_ids" ]; then
            while read -r image_id; do
                [ -n "$image_id" ] && docker rmi -f "$image_id" >/dev/null 2>&1 || true
            done <<< "$image_ids"
        fi
    done

    # Build cache only (no global container/image prune).
    docker builder prune -af >/dev/null 2>&1 || true
    
    # NEW: Prune old unused images older than 7 days to prevent disk space exhaustion.
    echo -e "${BLUE}  -> Pruning deeply stale images (>7 days old)...${NC}"
    docker image prune -a -f --filter "until=168h" >/dev/null 2>&1 || true
    
    echo -e "${GREEN}  OK Cache bust complete (targeted images + build cache + deep prune)${NC}"
}

restart_edge_stack() {
    local edge_services="socket-proxy traefik route-fallback nginx"

    echo -e "${BLUE}  -> Refreshing edge proxy stack (nginx/traefik/socket-proxy/route-fallback)...${NC}"
    docker compose -f "$COMPOSE_FILE" up -d --force-recreate --no-deps $edge_services >/dev/null 2>&1 || \
        docker compose -f "$COMPOSE_FILE" up -d --force-recreate $edge_services >/dev/null 2>&1 || true

