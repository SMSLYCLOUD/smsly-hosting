#!/bin/bash

_harden_infisical_bootstrap() {
    local infisical_script="$INSTALL_DIR/lib/infisical.sh"
    if [ ! -f "$infisical_script" ]; then
        _harden_log info "Infisical script not found — skipping"
        return 0
    fi
    # Source Infisical functions and bootstrap
    # shellcheck disable=SC1090
    source "$infisical_script"  || {
        _harden_log warn "Failed to source infisical.sh"
        return 1
    }
    if ! command -v infisical_bootstrap ; then
        _harden_log warn "infisical_bootstrap function not found"
        return 1
    fi
    infisical_bootstrap  || {
        _harden_log warn "Infisical bootstrap had issues"
        return 1
    }
    return 0
}

_harden_infisical_verify() {
    command -v docker >/dev/null 2>&1 || return 0
    if docker ps --format '{{.Names}}'  | grep -q "smsly-infisical"; then
        _harden_log ok "Infisical running"
        return 0
    fi
    _harden_log warn "Infisical — container not running"
    return 1
}
