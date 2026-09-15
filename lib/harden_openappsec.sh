#!/bin/bash
# open-appsec WAF — edge-first, detect-learn shadow (phase 1).
# Brings up agent + Envoy-with-attachment on LOOPBACK shadow port only
# (no 80/443 touch, zero traffic impact). Phase 2 cutover flips Envoy
# to 80/443 with SNI chains — separate change with its own rollback.
# Gated by OPENAPPSEC_ENABLED=1 in .env (default 0 = fully inert).

# Resolve the kill-switch from the shell env first, then straight from
# .env (callers don't always export it — e.g. direct lib invocation).
# Returns 0 (true) when the WAF stack should be up.
_harden_openappsec_is_enabled() {
    [ "${OPENAPPSEC_ENABLED:-0}" = "1" ] && return 0
    if [ -f "${INSTALL_DIR:-/opt/smsly-hosting}/.env" ]; then
        local _file_flag=""
        _file_flag=$(grep -E '^OPENAPPSEC_ENABLED=' "${INSTALL_DIR:-/opt/smsly-hosting}/.env" 2>/dev/null | cut -d= -f2- | tr -d '[:space:]' || true)
        [ "$_file_flag" = "1" ] && return 0
    fi
    return 1
}

_harden_openappsec_bootstrap() {
    command -v docker >/dev/null 2>&1 || return 0
    if ! _harden_openappsec_is_enabled; then
        echo -e "${BLUE}  → [harden] open-appsec disabled (OPENAPPSEC_ENABLED!=1) — skipping${NC}"
        return 0
    fi
    local conf_dir="$INSTALL_DIR/infrastructure/openappsec/conf"
    local localconfig_dir="$INSTALL_DIR/infrastructure/openappsec/localconfig"
    mkdir -p "$conf_dir" "$localconfig_dir" 2>/dev/null || true
    # Seed the declarative policy from the image default on first run.
    # The image default is detect-learn (observe-only) — we never ship a
    # hand-written policy, so there is no schema to drift. Never
    # overwrite an existing policy (operator/SaaS tuning survives updates).
    if [ ! -f "$conf_dir/local_policy.yaml" ]; then
        local agent_image="ghcr.io/openappsec/agent:${OPENAPPSEC_VERSION:-latest}"
        if timeout 60 docker run --rm -v "$conf_dir:/seed:z" "$agent_image" \
                sh -c 'cp /etc/cp/conf/local_policy.yaml /seed/local_policy.yaml 2>/dev/null || cp /etc/cp/conf/*.yaml /seed/ 2>/dev/null || true' >/dev/null 2>&1; then
            if [ -f "$conf_dir/local_policy.yaml" ]; then
                echo -e "${GREEN}  ✓ open-appsec policy seeded from image default (detect-learn)${NC}"
            else
                echo -e "${YELLOW}  ⚠ open-appsec image carries no default policy — agent first-run will generate it${NC}"
            fi
        else
            echo -e "${YELLOW}  ⚠ open-appsec policy seed failed (non-fatal — agent first-run generates it)${NC}"
        fi
    fi
    local env_args=()
    [ -f "$INSTALL_DIR/.env" ] && env_args=(--env-file "$INSTALL_DIR/.env")
    # Explicit service list — never --remove-orphans on this stack (AGENTS.md #16).
    if ! timeout 570 docker compose "${env_args[@]}" -f "$COMPOSE_FILE" \
            up -d appsec-agent appsec-shared-storage appsec-smartsync appsec-tuning-svc appsec-db appsec-envoy 2>&1 | tail -5; then
        echo -e "${YELLOW}  ⚠ open-appsec docker compose up failed${NC}"
        return 1
    fi
    # Blocking start — wait for the shadow port to answer.
    local shadow_port="${OPENAPPSEC_SHADOW_HTTP_PORT:-18081}"
    local i=""
    for i in $(seq 1 30); do
        if timeout 5 bash -c "echo > /dev/tcp/127.0.0.1/$shadow_port" 2>/dev/null; then
            echo -e "${GREEN}  ✓ open-appsec shadow envoy answering on 127.0.0.1:$shadow_port${NC}"
            break
        fi
        sleep 2
    done
    # Record resolved digests so image updates are deliberate, not silent.
    docker inspect smsly-appsec-agent smsly-appsec-envoy --format '{{.RepoDigests}}' 2>/dev/null > "$conf_dir/.digests" || true
    return 0
}

_harden_openappsec_verify() {
    command -v docker >/dev/null 2>&1 || return 0
    if ! _harden_openappsec_is_enabled; then
        # Inert by design — not a failure. (If containers exist while
        # disabled, flag it: a half-on WAF is worse than off.)
        if docker ps --format '{{.Names}}' 2>/dev/null | grep -q "^smsly-appsec-"; then
            _harden_log warn "open-appsec disabled but containers still running — down them or set OPENAPPSEC_ENABLED=1"
            return 1
        fi
        _harden_log ok "open-appsec disabled (inert)"
        return 0
    fi
    local _fail=0
    [ "$(docker inspect -f '{{.State.Running}}' smsly-appsec-agent 2>/dev/null)" = "true" ] || { _harden_log warn "appsec-agent — container not running"; _fail=1; }
    [ "$(docker inspect -f '{{.State.Running}}' smsly-appsec-envoy 2>/dev/null)" = "true" ] || { _harden_log warn "appsec-envoy — container not running"; _fail=1; }
    if [ "$_fail" = "0" ]; then
        # Attachment signal: the agent logs attachment registration; the
        # envoy must serve the shadow path identically to direct Caddy.
        local shadow_port="${OPENAPPSEC_SHADOW_HTTP_PORT:-18081}"
        if docker logs --since 30m smsly-appsec-agent 2>/dev/null | grep -qiE "attach"; then
            _harden_log ok "open-appsec agent+envoy up (attachment seen in agent log)"
        else
            _harden_log warn "open-appsec up but no attachment mention in agent log yet — check shadow parity"
        fi
        return 0
    fi
    return 1
}
