#!/bin/bash

_harden_kernel_bootstrap() {
    local sysctl_file="/etc/sysctl.d/99-smsly-security.conf"
    [ -f "$sysctl_file" ] && return 0  # already applied

    cat > "$sysctl_file" <<'SYSCTL_EOF'
net.ipv4.tcp_syncookies = 1
net.ipv4.conf.all.rp_filter = 1
net.ipv4.conf.default.rp_filter = 1
net.ipv4.conf.all.accept_redirects = 0
net.ipv4.conf.default.accept_redirects = 0
net.ipv4.conf.all.send_redirects = 0
net.ipv4.conf.all.accept_source_route = 0
net.ipv4.conf.default.accept_source_route = 0
net.ipv6.conf.all.accept_redirects = 0
net.ipv6.conf.default.accept_redirects = 0
net.ipv6.conf.all.accept_source_route = 0
net.ipv6.conf.default.accept_source_route = 0
kernel.dmesg_restrict = 1
kernel.kptr_restrict = 2
kernel.yama.ptrace_scope = 1
kernel.unprivileged_bpf_disabled = 1
kernel.randomize_va_space = 2
fs.protected_hardlinks = 1
fs.protected_symlinks = 1
fs.suid_dumpable = 0
SYSCTL_EOF
    sysctl -p "$sysctl_file" || echo -e "${YELLOW}    ⚠ sysctl -p failed${NC}"
}

_harden_kernel_verify() {
    if [ -f /etc/sysctl.d/99-smsly-security.conf ]; then
        _harden_log ok "kernel hardening applied"
        return 0
    fi
    _harden_log warn "kernel hardening not applied"
    return 1
}
