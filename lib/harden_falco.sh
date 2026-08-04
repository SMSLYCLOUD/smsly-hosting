#!/bin/bash

_harden_falco_bootstrap() {
    command -v docker >/dev/null 2>&1 || return 0
    local compose_file="$INSTALL_DIR/infrastructure/docker/docker-compose.falco.yml"
    [ -f "$compose_file" ] || return 1

    # Blocking start — always recreate so config changes take effect.
    # The harden bootstrap may run before fresh_config has generated .env,
    # so only pass --env-file when the file exists (compose file needs no vars).
    local env_args=()
    [ -f "$INSTALL_DIR/.env" ] && env_args=(--env-file "$INSTALL_DIR/.env")
    # smsly-net is declared external in the falco compose file but is only
    # created during stack deploy (fresh_deploy.sh) — the harden bootstrap
    # runs earlier, so create it here if missing.
    docker network inspect smsly-net >/dev/null 2>&1 || docker network create smsly-net >/dev/null 2>&1 || true
    docker compose \
        "${env_args[@]}" \
        -f "$compose_file" \
        up -d --force-recreate --pull always || echo -e "${YELLOW}    ⚠ falco docker compose up failed${NC}"
    for _i in $(seq 1 15); do
        docker ps --format '{{.Names}}'  | grep -q "smsly-falco" && break
        sleep 2
    done
}

_harden_falco_verify() {
    command -v docker >/dev/null 2>&1 || return 0
    if ! docker ps --format '{{.Names}}'  | grep -q "smsly-falco"; then
        _harden_log warn "falco — container not running"
        return 1
    fi
    _harden_log ok "falco deployed"
    return 0
}
