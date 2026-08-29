#!/bin/bash

_harden_crowdsec_bootstrap() {
    command -v docker >/dev/null 2>&1 || return 0
    # CrowdSec comes from the main docker-compose stack — if the container
    # isn't running, try docker compose up -d for just that service.
    if docker ps --format '{{.Names}}'  | grep -q "smsly-crowdsec"; then
        # Container already up — just register the bouncer if needed.
        _harden_crowdsec_register_bouncer
        return 0
    fi
    # Blocking start — wait for container to be healthy
    # The harden bootstrap may run before fresh_config has generated .env,
    # so only pass --env-file when the file exists.
    local env_args=()
    [ -f "$INSTALL_DIR/.env" ] && env_args=(--env-file "$INSTALL_DIR/.env")
    docker compose \
        "${env_args[@]}" \
        -f "$COMPOSE_FILE" \
        up -d crowdsec || echo -e "${YELLOW}    ⚠ crowdsec docker compose up failed${NC}"
    for _i in $(seq 1 15); do
        docker ps --format '{{.Names}}'  | grep -q "smsly-crowdsec" && break
        sleep 2
    done
    _harden_crowdsec_register_bouncer
}

_harden_crowdsec_register_bouncer() {
    command -v docker >/dev/null 2>&1 || return 0
    # Ensure the Traefik bouncer is registered with CrowdSec LAPI.
    # Uses CROWDSEC_BOUNCER_KEY from .env — auto-generate if missing.
    local bouncer_key="${CROWDSEC_BOUNCER_KEY:-}"
    if [ -z "$bouncer_key" ] && [ -f "$INSTALL_DIR/.env" ]; then
        bouncer_key=$(grep -E '^CROWDSEC_BOUNCER_KEY=' "$INSTALL_DIR/.env" | cut -d= -f2- || true)
    fi
    if [ -z "$bouncer_key" ]; then
        bouncer_key=$(openssl rand -hex 32 2>/dev/null || python3 -c "import secrets; print(secrets.token_hex(32))" 2>/dev/null || true)
        if [ -n "$bouncer_key" ]; then
            echo "CROWDSEC_BOUNCER_KEY=$bouncer_key" >> "$INSTALL_DIR/.env"
            export CROWDSEC_BOUNCER_KEY="$bouncer_key"
            echo -e "${GREEN}  ✓ Auto-generated CROWDSEC_BOUNCER_KEY${NC}"
        fi
    fi
    if [ -n "$bouncer_key" ]; then
        timeout 30 docker exec smsly-crowdsec cscli bouncers add traefik-bouncer -k "$bouncer_key" \
            || echo -e "${YELLOW}    ⚠ CrowdSec bouncer registration failed (already exists, non-fatal)${NC}"
    fi
}

_harden_crowdsec_verify() {
    command -v docker >/dev/null 2>&1 || return 0
    if ! docker ps --format '{{.Names}}'  | grep -q "smsly-crowdsec"; then
        _harden_log warn "crowdsec — container not running"
        return 1
    fi
    # Refresh hub scenarios — only upgrade when explicitly allowed.
    # Auto-upgrading on every harden.sh run can silently break
    # production WAF if CrowdSec ships a breaking parser change.
    timeout -k 5 60 docker exec smsly-crowdsec cscli hub update  || _harden_log warn "crowdsec hub update failed"
    if [ "${CROWDSEC_AUTO_UPGRADE_HUB:-0}" = "1" ]; then
        timeout -k 5 60 docker exec smsly-crowdsec cscli hub upgrade  || _harden_log warn "crowdsec hub upgrade failed"
    else
        _harden_log info "crowdsec hub upgrade skipped (set CROWDSEC_AUTO_UPGRADE_HUB=1 to enable)"
    fi
    _harden_crowdsec_register_bouncer
    _harden_log ok "crowdsec deployed"
    return 0
}
