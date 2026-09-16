#!/bin/bash
# Infisical is provisioned by the deploy flows (lib/fresh_deploy.sh on
# fresh installs, lib/update_rebuild.sh on updates), which own the
# database creation, env-file extraction, and compose up. There is no
# lib/infisical.sh — this layer only verifies the result here so the
# security-stack report reflects reality.

_harden_infisical_bootstrap() {
    _harden_log info "infisical managed by deploy flows (fresh_deploy/update_rebuild) — nothing to bootstrap here"
    return 0
}

_harden_infisical_verify() {
    command -v docker >/dev/null 2>&1 || return 0
    local env_file="${INSTALL_DIR:-/opt/smsly-hosting}/.infisical.env"
    # Not provisioned (fresh hosts where DB setup was skipped, or
    # external-DB mode): absence is a valid state, not a failure.
    if [ ! -f "$env_file" ]; then
        _harden_log info "infisical not provisioned — skipping"
        return 0
    fi
    if docker ps --format '{{.Names}}'  | grep -q "infisical"; then
        _harden_log ok "infisical running"
        return 0
    fi
    _harden_log warn "infisical provisioned ($env_file exists) but container not running — re-run install.sh --update"
    return 1
}
