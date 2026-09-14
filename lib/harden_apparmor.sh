#!/bin/bash

_harden_apparmor_bootstrap() {
    command -v aa-status  || apt_run apt-get install -y apparmor apparmor-utils  || true
    command -v aa-status  || return 1
    systemctl enable apparmor || echo -e "${YELLOW}    ⚠ apparmor enable failed${NC}"
    systemctl start apparmor || echo -e "${YELLOW}    ⚠ apparmor start failed${NC}"
}

_harden_apparmor_verify() {
    command -v aa-status  || { _harden_log warn "apparmor — not installed"; return 1; }
    local count
    count=$(aa-status --json  | python3 -c "import json,sys; d=json.load(sys.stdin); print(len(d.get('processes',{})))"  || echo "0")
    count="${count//[^0-9]/}"
    : "${count:=0}"
    if [ "$count" -gt 0 ] ; then
        _harden_log ok "apparmor enforcing ($count profiles)"
        return 0
    fi
    _harden_log warn "apparmor installed but no enforce profiles"
    return 1
}
