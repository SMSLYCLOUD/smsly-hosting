#!/bin/bash

_harden_crowdsec_bootstrap() {
    command -v docker >/dev/null 2>&1 || return 0
    # Traefik plugin gate first: decides enforcement before anything
    # attaches the middleware to user routers.
    _harden_crowdsec_traefik_plugin_gate
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

# Traefik downloads the crowdsec-bouncer plugin from plugins.traefik.io
# at boot. On networks where that hub is unreachable the plugin never
# loads, the middleware doesn't exist, and EVERY user router that names
# it 503s (2026-09-17: full user-traffic outage on a fresh box whose
# provider blackholes the hub). The backend attaches the middleware only
# when TRAEFIK_CROWDSEC_ENFORCE is true — when the hub is unreachable
# and the operator hasn't chosen explicitly, default enforcement OFF so
# routes stay up (LAPI + Cloudflare bouncer still protect the edge).
# Sticky: once written, updates never flip it back — delete the line
# from .env to re-enable after the network path is fixed (workers pick
# it up on their next recreate).
_harden_crowdsec_traefik_plugin_gate() {
    [ -n "${INSTALL_DIR:-}" ] || return 0
    [ -f "$INSTALL_DIR/.env" ] || return 0
    grep -q '^TRAEFIK_CROWDSEC_ENFORCE=' "$INSTALL_DIR/.env" 2>/dev/null && return 0
    if timeout 20 curl -s -o /dev/null https://plugins.traefik.io/ 2>/dev/null; then
        return 0
    fi
    echo "TRAEFIK_CROWDSEC_ENFORCE=false" >> "$INSTALL_DIR/.env"
    echo -e "${YELLOW}    ⚠ plugins.traefik.io unreachable — Traefik WAF plugin can't load here; user routers would 503. Set TRAEFIK_CROWDSEC_ENFORCE=false (delete the line to re-enable)${NC}"
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
        # Idempotent: cscli errors when re-adding an existing bouncer.
        # Pre-check keeps update logs clean; the "already exists" fallback
        # covers the race where two paths register concurrently.
        if timeout -k 5 30 docker exec smsly-crowdsec cscli bouncers list 2>/dev/null | grep -qw "traefik-bouncer"; then
            echo -e "${GREEN}  ✓ CrowdSec bouncer already registered${NC}"
        else
            local _add_out=""
            if _add_out="$(timeout -k 5 30 docker exec smsly-crowdsec cscli bouncers add traefik-bouncer -k "$bouncer_key" 2>&1)"; then
                echo -e "${GREEN}  ✓ CrowdSec bouncer registered${NC}"
            elif echo "$_add_out" | grep -q "already exists"; then
                echo -e "${GREEN}  ✓ CrowdSec bouncer already registered${NC}"
            else
                echo "$_add_out"
                echo -e "${YELLOW}    ⚠ CrowdSec bouncer registration failed (non-fatal)${NC}"
            fi
        fi
    fi
}

# ── Cloudflare bouncer (edge enforcement) ─────────────────────────────
# Effective config: PlatformConfig DB first, .env fallback (mirrors
# PlatformConfig.get_config_value). Secret VALUES are never echoed.

# Render the bouncer config. Args: token account action lapi_key outfile.
# Kept side-effect-free (no docker) so the shell test harness can cover it.
_cf_render_cloudflare_bouncer_config() {
    local token="$1"
    local account="$2"
    local action="$3"
    local lapi_key="$4"
    local outfile="$5"
    cat > "$outfile" <<CFEOF
# Managed by lib/harden_crowdsec.sh — DO NOT EDIT (regenerated every run).
crowdsec_lapi_url: http://smsly-crowdsec:8080/
crowdsec_lapi_key: ${lapi_key}
crowdsec_update_frequency: 10s
include_scenarios_containing: []
exclude_scenarios_containing: []
only_include_decisions_from: []
cloudflare_config:
  accounts:
  - id: ${account}
    token: ${token}
    ip_list_prefix: crowdsec
    default_action: ${action}
  update_frequency: 60s
daemon: false
log_mode: stdout
log_level: info
prometheus:
  enabled: false
CFEOF
    chmod 600 "$outfile"
}

# Read one effective CrowdSec-CF value: "CFVAL:<value>" from the backend,
# else .env, else default. Prints the value (may be empty).
_harden_crowdsec_cf_value() {
    local field="$1"
    local env_key="$2"
    local default="${3:-}"
    local val=""
    if docker ps --format '{{.Names}}' 2>/dev/null | grep -q "^smsly-hosting-backend-1$"; then
        val="$(timeout -k 5 60 docker exec smsly-hosting-backend-1 python manage.py shell -c "from apps.deployments.models import PlatformConfig; print('CFVAL:' + str(PlatformConfig.get_config_value('$field', '')))" 2>/dev/null | grep '^CFVAL:' | cut -c7- | tail -n 1)"
    fi
    if [ -z "$val" ] && [ -f "$INSTALL_DIR/.env" ]; then
        val="$(grep -E "^${env_key}=" "$INSTALL_DIR/.env" 2>/dev/null | cut -d= -f2- || true)"
    fi
    if [ -n "$val" ]; then
        echo "$val"
    else
        echo "$default"
    fi
}

_harden_crowdsec_ensure_bouncer_file() {
    # AGENTS.md #24: crowdsec/cloudflare-bouncer.yaml is bind-mounted
    # as a FILE. A missing source makes the daemon auto-create a
    # DIRECTORY, and every later start fails identically until a human
    # removes it. Guarantee a real file on every path through here:
    # drop poison dirs (rmdir only — never delete real content) and
    # touch an empty placeholder (the entrypoint idles on empty).
    local _f="${INSTALL_DIR:-/opt/smsly-hosting}/crowdsec/cloudflare-bouncer.yaml"
    if [ -d "$_f" ] && [ ! -L "$_f" ]; then
        rmdir "$_f" 2>/dev/null || true
    fi
    if [ ! -e "$_f" ]; then
        mkdir -p "$(dirname "$_f")" 2>/dev/null || true
        : > "$_f" 2>/dev/null || true
    fi
}

_harden_crowdsec_cloudflare_bouncer() {
    command -v docker >/dev/null 2>&1 || return 0
    _harden_crowdsec_ensure_bouncer_file
    local enabled_raw=""
    local token=""
    local account=""
    local action=""
    local lapi_key=""
    enabled_raw="$(_harden_crowdsec_cf_value crowdsec_cf_enabled CROWDSEC_CF_ENABLED false)"
    token="$(_harden_crowdsec_cf_value crowdsec_cf_api_token CROWDSEC_CF_API_TOKEN '')"
    account="$(_harden_crowdsec_cf_value crowdsec_cf_account_id CROWDSEC_CF_ACCOUNT_ID '')"
    action="$(_harden_crowdsec_cf_value crowdsec_cf_action CROWDSEC_CF_ACTION block)"
    lapi_key="$(_harden_crowdsec_cf_value crowdsec_cf_bouncer_key CROWDSEC_CF_BOUNCER_KEY '')"
    local enabled="0"
    if [ "$enabled_raw" = "True" ] || [ "$enabled_raw" = "1" ]; then
        enabled="1"
    fi
    if [ "$action" != "block" ] && [ "$action" != "managed_challenge" ]; then
        _harden_log warn "cloudflare bouncer: unknown action '$action', using 'block'"
        action="block"
    fi
    if [ "$enabled" != "1" ] || [ -z "$token" ] || [ -z "$account" ]; then
        # Not configured — keep the container stopped so it never
        # crash-loops on missing config (compose defines it unconditionally).
        if docker ps --format '{{.Names}}' 2>/dev/null | grep -q "^smsly-cloudflare-bouncer$"; then
            docker stop smsly-cloudflare-bouncer >/dev/null 2>&1 || true
            _harden_log info "cloudflare bouncer stopped (not configured)"
        fi
        return 0
    fi
    if [ -z "$lapi_key" ]; then
        lapi_key="$(openssl rand -hex 32 2>/dev/null || python3 -c "import secrets; print(secrets.token_hex(32))" 2>/dev/null || true)"
        if [ -n "$lapi_key" ]; then
            echo "CROWDSEC_CF_BOUNCER_KEY=$lapi_key" >> "$INSTALL_DIR/.env"
            export CROWDSEC_CF_BOUNCER_KEY="$lapi_key"
            _harden_log ok "Auto-generated CROWDSEC_CF_BOUNCER_KEY"
        fi
    fi
    if [ -z "$lapi_key" ]; then
        _harden_log warn "cloudflare bouncer: no LAPI key available, skipping"
        return 0
    fi
    if docker ps --format '{{.Names}}' 2>/dev/null | grep -q "^smsly-crowdsec$"; then
        if timeout -k 5 30 docker exec smsly-crowdsec cscli bouncers list 2>/dev/null | grep -qw "cloudflare-bouncer"; then
            _harden_log info "Cloudflare bouncer already registered"
        else
            local _add_out=""
            if _add_out="$(timeout -k 5 30 docker exec smsly-crowdsec cscli bouncers add cloudflare-bouncer -k "$lapi_key" 2>&1)"; then
                _harden_log ok "Cloudflare bouncer registered"
            elif echo "$_add_out" | grep -q "already exists"; then
                _harden_log info "Cloudflare bouncer already registered"
            else
                _harden_log warn "Cloudflare bouncer registration failed (non-fatal)"
            fi
        fi
    fi
    mkdir -p "$INSTALL_DIR/crowdsec"
    _cf_render_cloudflare_bouncer_config "$token" "$account" "$action" "$lapi_key" \
        "$INSTALL_DIR/crowdsec/cloudflare-bouncer.yaml"
    local env_args=()
    [ -f "$INSTALL_DIR/.env" ] && env_args=(--env-file "$INSTALL_DIR/.env")
    if docker compose "${env_args[@]}" -f "$COMPOSE_FILE" up -d crowdsec-cloudflare-bouncer >/dev/null 2>&1; then
        _harden_log ok "cloudflare bouncer running (edge enforcement)"
    else
        _harden_log warn "cloudflare bouncer compose up failed (non-fatal)"
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
    _harden_crowdsec_cloudflare_bouncer
    _harden_log ok "crowdsec deployed"
    return 0
}
