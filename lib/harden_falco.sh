#!/bin/bash

_harden_falco_bootstrap() {
    local compose_file="$INSTALL_DIR/infrastructure/docker/docker-compose.falco.yml"
    [ -f "$compose_file" ] || return 1

    # Blocking start — always recreate so config changes take effect
    docker compose \
        --env-file "$INSTALL_DIR/.env" \
        -f "$compose_file" \
        up -d --force-recreate --pull always || echo -e "${YELLOW}    ⚠ falco docker compose up failed${NC}"
    for _i in $(seq 1 15); do
        docker ps --format '{{.Names}}'  | grep -q "smsly-falco" && break
        sleep 2
    done
}

_harden_falco_verify() {
    if ! docker ps --format '{{.Names}}'  | grep -q "smsly-falco"; then
        _harden_log warn "falco — container not running"
        return 1
    fi
    _harden_log ok "falco deployed"
    return 0
}
