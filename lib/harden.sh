#!/bin/bash
set +e

_harden_log() {
    local level="$1"; shift
    case "$level" in
        ok)   echo -e "${GREEN}  ✓ [harden] $*${NC}" ;;
        warn) echo -e "${YELLOW}  ⚠ [harden] $*${NC}" ;;
        err)  echo -e "${RED}  ✗ [harden] $*${NC}" ;;
        info) echo -e "${BLUE}  → [harden] $*${NC}" ;;
    esac
}

source "$(dirname "${BASH_SOURCE[0]}")/harden_fail2ban.sh"
source "$(dirname "${BASH_SOURCE[0]}")/harden_ufw.sh"
source "$(dirname "${BASH_SOURCE[0]}")/harden_apparmor.sh"
source "$(dirname "${BASH_SOURCE[0]}")/harden_auditd.sh"
source "$(dirname "${BASH_SOURCE[0]}")/harden_kernel.sh"
source "$(dirname "${BASH_SOURCE[0]}")/harden_docker_daemon.sh"
source "$(dirname "${BASH_SOURCE[0]}")/harden_crowdsec.sh"
source "$(dirname "${BASH_SOURCE[0]}")/harden_falco.sh"
source "$(dirname "${BASH_SOURCE[0]}")/harden_container_runtime.sh"
source "$(dirname "${BASH_SOURCE[0]}")/harden_trivy.sh"
source "$(dirname "${BASH_SOURCE[0]}")/harden_infisical.sh"

_harden_envoy_image_bootstrap() {
    # Ensure the Envoy sidecar image exists in the platform registry.
    # Fresh hosts never built it, so every sidecar injection died with
    # 404 (2026-09-11). Idempotent: skips when the tag already resolves.
    # Best-effort: if the registry isn't up yet the deploy-time pull
    # covers it and the next update retries.
    command -v docker >/dev/null 2>&1 || return 0
    local envoy_dir="$INSTALL_DIR/infrastructure/envoy"
    [ -f "$envoy_dir/Dockerfile" ] || { _harden_log warn "envoy Dockerfile missing"; return 1; }
    local envoy_tag="registry:5000/smsly/envoy-spire-sidecar:latest"
    if docker image inspect "$envoy_tag" >/dev/null 2>&1; then
        return 0
    fi
    if docker pull "$envoy_tag" >/dev/null 2>&1; then
        _harden_log ok "envoy sidecar image present"
        return 0
    fi
    local loop_tag="127.0.0.1:5000/smsly/envoy-spire-sidecar:latest"
    if ! docker build -t "$envoy_tag" -t "$loop_tag" "$envoy_dir" >/dev/null 2>&1; then
        _harden_log warn "envoy sidecar image build failed"
        return 1
    fi
    if ! docker push "$loop_tag" >/dev/null 2>&1; then
        _harden_log warn "envoy sidecar image push failed (registry may not be up yet)"
        return 1
    fi
    _harden_log ok "envoy sidecar image built and pushed"
    return 0
}

_harden_spire_start_agent() {
    # Start one SPIRE agent with a freshly minted single-use join token
    # (mirrors apps/mtls/views.py::_start_agent_with_token).
    # $1 agent container, $2 server container, $3 agent.conf host path,
    # $4 data volume, $5 socket volume, $6 svids volume.
    local agent="$1" server="$2" conf="$3" data_vol="$4" sock_vol="$5" svids_vol="$6"
    if [ "$(docker inspect -f '{{.State.Running}}' "$agent" 2>/dev/null)" = "true" ]; then
        return 0
    fi
    local token
    token="$(docker exec "$server" /opt/spire/bin/spire-server token generate -socketPath /tmp/spire-server/private/api.sock 2>/dev/null | grep 'Token:' | awk '{print $2}' | head -1)"
    if [ -z "$token" ]; then
        _harden_log warn "$agent — could not mint join token"
        return 1
    fi
    docker rm -f "$agent" >/dev/null 2>&1 || true
    docker run -d --name "$agent" --hostname "$agent" --network smsly-net --pid host --restart unless-stopped \
        -v "$data_vol:/opt/spire/data" -v "$sock_vol:/opt/spire/run" -v "$svids_vol:/opt/spire/svids" \
        -v /var/run/docker.sock:/var/run/docker.sock:ro \
        -v "$conf:/etc/spire/agent.conf:ro,z" \
        -e DOCKER_HOST=unix:///var/run/docker.sock \
        ghcr.io/spiffe/spire-agent:1.9.6 -config /etc/spire/agent.conf -joinToken "$token" >/dev/null 2>&1 || {
        _harden_log warn "$agent — docker run failed"
        return 1
    }
    return 0
}

_harden_spire_bootstrap() {
    [ "${NODE_SPIRE:-1}" = "1" ] || { _harden_log info "spire skipped (NODE_SPIRE=0)"; return 0; }
    # SPIRE servers run on the master only — nodes/agents must never mint
    # their own trust roots.
    if command -v is_master_mode >/dev/null 2>&1 && ! is_master_mode; then
        _harden_log info "spire skipped (not master mode)"
        return 0
    fi
    command -v docker >/dev/null 2>&1 || return 0
    local spire_file="$INSTALL_DIR/docker-compose.spire.yml"
    [ -f "$spire_file" ] || { _harden_log warn "spire compose file missing"; return 1; }
    docker network inspect smsly-net >/dev/null 2>&1 || docker network create smsly-net >/dev/null 2>&1 || true
    # Servers are idempotent under compose (running services are kept).
    docker compose -p smsly-spire -f "$spire_file" up -d spire-server spire-server-ecosystem >/dev/null 2>&1 || {
        _harden_log warn "spire servers failed to start"
        return 1
    }
    local _i
    for _i in $(seq 1 30); do
        if [ "$(docker inspect -f '{{.State.Running}}' smsly-spire-server 2>/dev/null)" = "true" ] && \
           [ "$(docker inspect -f '{{.State.Running}}' smsly-spire-server-ecosystem 2>/dev/null)" = "true" ]; then
            break
        fi
        sleep 2
    done
    sleep 5
    _harden_spire_start_agent "smsly-spire-agent" "smsly-spire-server" "$INSTALL_DIR/infrastructure/spire/agent.conf" \
        "smsly-hosting_spire-agent-data" "smsly-hosting_spire-agent-socket" "smsly-hosting_spire-agent-svids" || return 1
    _harden_spire_start_agent "smsly-spire-agent-ecosystem" "smsly-spire-server-ecosystem" "$INSTALL_DIR/infrastructure/spire/agent-ecosystem.conf" \
        "smsly-spire_spire-ecosystem-agent-data" "smsly-spire_spire-ecosystem-agent-socket" "smsly-spire_spire-ecosystem-agent-svids" || return 1
    # Sidecar image last: non-fatal (the registry may not be up yet on a
    # fresh install; deploy-time pull and the next update retry it).
    _harden_envoy_image_bootstrap || true
    return 0
}

_harden_spire_verify() {
    [ "${NODE_SPIRE:-1}" = "1" ] || return 0
    command -v docker >/dev/null 2>&1 || return 0
    if [ "$(docker inspect -f '{{.State.Running}}' smsly-spire-agent 2>/dev/null)" != "true" ] || \
       [ "$(docker inspect -f '{{.State.Running}}' smsly-spire-agent-ecosystem 2>/dev/null)" != "true" ]; then
        # One self-heal attempt: resume runs can skip the bootstrap step.
        _harden_spire_bootstrap >/dev/null 2>&1 || true
    fi
    local _fail=0
    [ "$(docker inspect -f '{{.State.Running}}' smsly-spire-agent 2>/dev/null)" = "true" ] || { _harden_log warn "spire-agent — container not running"; _fail=1; }
    [ "$(docker inspect -f '{{.State.Running}}' smsly-spire-agent-ecosystem 2>/dev/null)" = "true" ] || { _harden_log warn "spire-agent-ecosystem — container not running"; _fail=1; }
    if [ "$_fail" = "0" ]; then
        _harden_log ok "spire deployed"
        return 0
    fi
    return 1
}

harden_security_bootstrap() {
    echo -e "${BLUE}  → [harden] Bootstrapping security stack (blocking)...${NC}"
    local _harden_failures=0
    _harden_fail2ban_bootstrap   || { _harden_failures=$((_harden_failures + 1)); }
    _harden_ufw_bootstrap        || { _harden_failures=$((_harden_failures + 1)); }
    _harden_apparmor_bootstrap   || { _harden_failures=$((_harden_failures + 1)); }
    _harden_auditd_bootstrap     || { _harden_failures=$((_harden_failures + 1)); }
    _harden_kernel_bootstrap
    _harden_docker_daemon_bootstrap
    _harden_crowdsec_bootstrap   || { _harden_failures=$((_harden_failures + 1)); }
    _harden_falco_bootstrap      || { _harden_failures=$((_harden_failures + 1)); }
    _harden_spire_bootstrap      || { _harden_failures=$((_harden_failures + 1)); }
    _harden_container_runtime_bootstrap
    _harden_trivy_bootstrap      || { _harden_failures=$((_harden_failures + 1)); }
    _harden_infisical_bootstrap  || { _harden_failures=$((_harden_failures + 1)); }
    if [ "$_harden_failures" -gt 0 ]; then
        echo -e "${YELLOW}  ⚠ [harden] $_harden_failures layer(s) had issues — verify will report details${NC}"
    else
        echo -e "${GREEN}  ✓ [harden] Bootstrap complete — all layers started${NC}"
    fi
    return 0
}

harden_security_verify() {
    echo ""
    echo -e "${BLUE}════════════════════════════════════════════════════════════${NC}"
    echo -e "${BLUE}  Security Stack — Verification${NC}"
    echo -e "${BLUE}════════════════════════════════════════════════════════════${NC}"

    local failures=0 checks=0

    # NOTE: never use standalone `((checks++))` here — when the counter is 0
    # the arithmetic expression evaluates to 0 → exit status 1 → under `set -e`
    # (re-enabled by fresh_hardening.sh after harden.sh's `set +e`) the whole
    # install dies silently after the first check.
    if ! _harden_fail2ban_verify; then failures=$((failures + 1)); fi
    checks=$((checks + 1))
    if ! _harden_ufw_verify; then failures=$((failures + 1)); fi
    checks=$((checks + 1))
    if ! _harden_apparmor_verify; then failures=$((failures + 1)); fi
    checks=$((checks + 1))
    if ! _harden_auditd_verify; then failures=$((failures + 1)); fi
    checks=$((checks + 1))
    if ! _harden_kernel_verify; then failures=$((failures + 1)); fi
    checks=$((checks + 1))
    if ! _harden_docker_daemon_verify; then failures=$((failures + 1)); fi
    checks=$((checks + 1))
    if ! _harden_crowdsec_verify; then failures=$((failures + 1)); fi
    checks=$((checks + 1))
    if ! _harden_falco_verify; then failures=$((failures + 1)); fi
    checks=$((checks + 1))
    if ! _harden_spire_verify; then failures=$((failures + 1)); fi
    checks=$((checks + 1))
    if ! _harden_container_runtime_verify; then failures=$((failures + 1)); fi
    checks=$((checks + 1))
    if ! _harden_trivy_verify; then failures=$((failures + 1)); fi
    checks=$((checks + 1))
    if ! _harden_infisical_verify; then failures=$((failures + 1)); fi
    checks=$((checks + 1))

    local passed=$((checks - failures))
    echo ""
    if [ "$failures" -eq 0 ]; then
        echo -e "${GREEN}  All $passed/$checks security checks passed${NC}"
    else
        echo -e "${RED}  Security: $passed/$checks passed, $failures FAILED${NC}"
        echo -e "${YELLOW}  Review failures above — run 'sudo bash install.sh --debug' for details${NC}"
    fi
    echo -e "${BLUE}════════════════════════════════════════════════════════════${NC}"
    echo ""
}
