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
