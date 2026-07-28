#!/bin/bash

_harden_crowdsec_bootstrap() {
    # CrowdSec comes from the main docker-compose stack — if the container
    # isn't running, try docker compose up -d for just that service.
    if docker ps --format '{{.Names}}'  | grep -q "smsly-crowdsec"; then
        return 0  # already up
    fi
    # Blocking start — wait for container to be healthy
    docker compose \
        --env-file "$INSTALL_DIR/.env" \
        -f "$COMPOSE_FILE" \
        up -d crowdsec || echo -e "${YELLOW}    ⚠ crowdsec docker compose up failed${NC}"
    for _i in $(seq 1 15); do
        docker ps --format '{{.Names}}'  | grep -q "smsly-crowdsec" && break
        sleep 2
    done
}

_harden_crowdsec_verify() {
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
    _harden_log ok "crowdsec deployed"
    return 0
}
