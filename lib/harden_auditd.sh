#!/bin/bash

_harden_auditd_bootstrap() {
    command -v auditd  || apt_run apt-get install -y auditd audispd-plugins  || true

    if [ ! -f /etc/audit/rules.d/smsly.rules ]; then
        mkdir -p /etc/audit/rules.d
        cat > /etc/audit/rules.d/smsly.rules <<'AUDIT_EOF'
-w /etc/shadow -p wa -k identity
-w /etc/passwd -p wa -k identity
-w /etc/sudoers -p wa -k privilege-escalation
-w /etc/ssh/sshd_config -p wa -k sshd
-w /opt/smsly-hosting/.env -p wa -k smsly-config
-w /opt/smsly-hosting/secrets/ -p wa -k smsly-secrets
-a always,exit -F arch=b64 -S execve -F path=/usr/bin/docker -k docker-exec
-a always,exit -F arch=b64 -S mount -k filesystem-mounts
-a exit,always -F arch=b64 -S execve -F euid=0 -F auid>=1000 -k priv-esc
AUDIT_EOF
    fi
    systemctl enable auditd || echo -e "${YELLOW}    ⚠ auditd enable failed${NC}"
    systemctl restart auditd || echo -e "${YELLOW}    ⚠ auditd restart failed${NC}"
}

_harden_auditd_verify() {
    command -v auditd  || { _harden_log warn "auditd — not installed"; return 1; }
    if systemctl is-active --quiet auditd ; then
        _harden_log ok "auditd active (file + syscall monitoring)"
        return 0
    fi
    _harden_log warn "auditd not running — may need kernel param audit=1"
    return 1
}
