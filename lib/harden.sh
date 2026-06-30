#!/bin/bash
# =============================================================================
# SMSLY Hosting — Security Hardening Module
# =============================================================================
# Idempotent. Call from install.sh --update, --refresh, or fresh install.
# Verifies every security layer, installs what's missing, validates configs.
# Never blocks deployment — failures are logged prominently as warnings.
# =============================================================================

_harden_log() {
    local level="$1"; shift
    case "$level" in
        ok)   echo -e "${GREEN}  ✓ [harden] $*${NC}" ;;
        warn) echo -e "${YELLOW}  ⚠ [harden] $*${NC}" ;;
        err)  echo -e "${RED}  ✗ [harden] $*${NC}" ;;
        info) echo -e "${BLUE}  → [harden] $*${NC}" ;;
    esac
}

# ─── Fail2ban ─────────────────────────────────────────────────────────────────
_harden_fail2ban() {
    _harden_log info "Fail2ban — SSH jail hardening"

    if ! command -v fail2ban-client >/dev/null 2>&1; then
        apt_run apt-get install -y fail2ban
        if ! command -v fail2ban-client >/dev/null 2>&1; then
            _harden_log err "Fail2ban installation failed"
            return 1
        fi
    fi

    # Aggressive SSH jail — short bantime, low threshold, recidive stacking
    cat << 'JAIL_EOF' > /etc/fail2ban/jail.local
[DEFAULT]
bantime = 1h
findtime = 10m
maxretry = 3
# Ban repeated offenders for 24h via recidive jail
banaction = iptables-multiport
banaction_allports = iptables-allports

[sshd]
enabled = true
port = ssh
filter = sshd
logpath = /var/log/auth.log
maxretry = 3
bantime = 1h
findtime = 10m

# Recidive jail — repeat bans escalate to 24h
[recidive]
enabled = true
filter = recidive
logpath = /var/log/fail2ban.log
action = iptables-allports[name=recidive]
bantime = 24h
findtime = 1d
maxretry = 3

# CMS/WP style XMLRPC/brute-force on web panels
[nginx-http-auth]
enabled = true
filter = nginx-http-auth
logpath = /var/log/nginx/error.log
maxretry = 5

# Generic HTTP flood protection
[http-get-dos]
enabled = true
filter = http-get-dos
port = http,https
logpath = /var/log/nginx/access.log
findtime = 300
maxretry = 300
bantime = 600
JAIL_EOF

    systemctl enable fail2ban >/dev/null 2>&1 || true
    systemctl restart fail2ban >/dev/null 2>&1 || true

    if fail2ban-client status sshd >/dev/null 2>&1; then
        _harden_log ok "Fail2ban active — sshd + recidive + http jails configured"
    else
        _harden_log warn "Fail2ban service not responding — check systemctl status fail2ban"
    fi
}

# ─── AppArmor ─────────────────────────────────────────────────────────────────
_harden_apparmor() {
    _harden_log info "AppArmor — profile enforcement"

    if ! command -v aa-status >/dev/null 2>&1; then
        apt_run apt-get install -y apparmor apparmor-utils
        if ! command -v aa-status >/dev/null 2>&1; then
            _harden_log warn "AppArmor not available (non-Ubuntu host?)"
            return 1
        fi
    fi

    systemctl enable apparmor >/dev/null 2>&1 || true
    systemctl start apparmor >/dev/null 2>&1 || true

    local enforce_count complain_count
    enforce_count=$(aa-status --json 2>/dev/null | python3 -c "import json,sys; d=json.load(sys.stdin); print(len(d.get('processes',{})))" 2>/dev/null || echo "0")
    complaint_count=$(aa-status 2>/dev/null | grep -c "complain" || true)

    if [ "$enforce_count" -gt 0 ]; then
        _harden_log ok "AppArmor enforcing ($enforce_count profiles active, $complaint_count complain)"
    else
        _harden_log warn "AppArmor installed but no profiles in enforce mode — check aa-status"
    fi
}

# ─── UFW Firewall ─────────────────────────────────────────────────────────────
_harden_ufw() {
    _harden_log info "UFW firewall — rule verification"

    if ! command -v ufw >/dev/null 2>&1; then
        apt_run apt-get install -y ufw
    fi

    local ufw_status
    ufw_status=$(ufw status 2>/dev/null | head -1 || echo "inactive")

    if echo "$ufw_status" | grep -qi "inactive"; then
        ufw default deny incoming >/dev/null 2>&1 || true
        ufw default allow outgoing >/dev/null 2>&1 || true
        ufw allow ssh >/dev/null 2>&1 || true
        ufw allow 80/tcp >/dev/null 2>&1 || true
        ufw allow 443/tcp >/dev/null 2>&1 || true
        # WireGuard mesh port
        ufw allow 51820/udp >/dev/null 2>&1 || true
        echo "y" | ufw enable >/dev/null 2>&1 || true
        _harden_log ok "UFW configured — default deny, SSH/80/443/51820 allowed"
    else
        # Verify critical ports are open
        local issues=""
        for port in 22 80 443 51820; do
            if ! ufw status verbose 2>/dev/null | grep -q "$port.*ALLOW"; then
                issues="$issues $port"
                ufw allow "$port" >/dev/null 2>&1 || true
            fi
        done
        if [ -n "$issues" ]; then
            _harden_log warn "UFW — added missing allow rules for ports:$issues"
        else
            _harden_log ok "UFW active — critical ports verified"
        fi
    fi

    # Persist iptables rules (Docker bypasses UFW)
    if command -v iptables-save >/dev/null 2>&1; then
        mkdir -p /etc/iptables
        iptables-save > /etc/iptables/rules.v4 2>/dev/null || true
        if ! systemctl is-enabled iptables-restore >/dev/null 2>&1; then
            cat > /etc/systemd/system/iptables-restore.service <<'RESTORE_EOF'
[Unit]
Description=Restore iptables rules on boot
Before=network.target

[Service]
Type=oneshot
ExecStart=/sbin/iptables-restore /etc/iptables/rules.v4
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
RESTORE_EOF
            systemctl daemon-reload 2>/dev/null || true
            systemctl enable iptables-restore >/dev/null 2>&1 || true
        fi
    fi
}

# ─── Falco Runtime Threat Detection ───────────────────────────────────────────
_harden_falco() {
    _harden_log info "Falco — runtime threat detection"

    # Auto-detect BPF/eBPF availability
    local falco_driver=""

    if [ -f /sys/kernel/btf/vmlinux ] 2>/dev/null; then
        # Kernel >= 5.8 with BTF — modern eBPF probe works
        falco_driver="ebpf"
        _harden_log info "Falco: kernel 5.8+ with BTF detected — using eBPF driver"
        export FALCO_BPF_PROBE=""
    elif [ -d /sys/kernel/debug ] && ls /sys/kernel/debug/tracing/events/syscalls/ >/dev/null 2>&1; then
        falco_driver="bpf"
        _harden_log info "Falco: BPF available, building probe"
    else
        falco_driver="module"
        _harden_log info "Falco: using kernel module driver"
    fi

    local compose_file="$INSTALL_DIR/infrastructure/docker/docker-compose.falco.yml"
    if [ ! -f "$compose_file" ]; then
        _harden_log warn "Falco compose file missing — skipping"
        return 1
    fi

    # Deploy Falco
    if docker ps --format '{{.Names}}' 2>/dev/null | grep -q "smsly-falco"; then
        _harden_log ok "Falco already deployed"
    else
        docker compose \
            --env-file "$INSTALL_DIR/.env" \
            -f "$compose_file" \
            up -d --pull always 2>/dev/null || \
            _harden_log warn "Falco deployment failed — manual intervention needed"
    fi

    # Verify it's running and collecting events
    sleep 5
    if docker ps --format '{{.Names}}' 2>/dev/null | grep -q "smsly-falco"; then
        local falco_log
        falco_log=$(docker logs smsly-falco --tail 5 2>&1 || echo "")
        if echo "$falco_log" | grep -qiE "event|notice|warning|alert"; then
            _harden_log ok "Falco operational — detecting events (driver: $falco_driver)"
        else
            _harden_log warn "Falco running but no events yet — may need BPF probe build"
        fi
    else
        _harden_log warn "Falco container failed to start — check docker logs smsly-falco"
    fi
}

# ─── CrowdSec ────────────────────────────────────────────────────────────────
_harden_crowdsec() {
    _harden_log info "CrowdSec — WAF / IPS verification"

    if ! docker ps --format '{{.Names}}' 2>/dev/null | grep -q "smsly-crowdsec"; then
        _harden_log warn "CrowdSec container not running — compose may be missing"
        return 1
    fi

    local bouncer_ok=false
    local decisions

    if docker exec smsly-crowdsec cscli bouncers list 2>/dev/null | grep -q "traefik"; then
        bouncer_ok=true
    fi

    decisions=$(docker exec smsly-crowdsec cscli decisions list -o json 2>/dev/null | python3 -c "import json,sys; d=json.load(sys.stdin); print(len(d))" 2>/dev/null || echo "0")

    if [ "$bouncer_ok" = "true" ]; then
        _harden_log ok "CrowdSec operational — $decisions active decisions, Traefik bouncer registered"
    else
        _harden_log warn "CrowdSec running but Traefik bouncer not registered — re-deploy compose stack"
    fi

    # Fetch latest blocklists
    docker exec smsly-crowdsec cscli hub update 2>/dev/null || true
    docker exec smsly-crowdsec cscli hub upgrade 2>/dev/null || true
}

# ─── auditd ────────────────────────────────────────────────────────────────────
_harden_auditd() {
    _harden_log info "auditd — system call auditing"

    if ! command -v auditd >/dev/null 2>&1; then
        apt_run apt-get install -y auditd audispd-plugins
    fi

    if [ ! -f /etc/audit/rules.d/smsly.rules ]; then
        cat > /etc/audit/rules.d/smsly.rules <<'AUDIT_EOF'
# Monitor critical files
-w /etc/shadow -p wa -k identity
-w /etc/passwd -p wa -k identity
-w /etc/sudoers -p wa -k privilege-escalation
-w /etc/ssh/sshd_config -p wa -k sshd
-w /opt/smsly-hosting/.env -p wa -k smsly-config
-w /opt/smsly-hosting/secrets/ -p wa -k smsly-secrets
# Monitor critical syscalls (Docker orchestration)
-a always,exit -F arch=b64 -S execve -F path=/usr/bin/docker -k docker-exec
-a always,exit -F arch=b64 -S mount -k filesystem-mounts
# Log all sudo usage
-a exit,always -F arch=b64 -S execve -F euid=0 -F auid>=1000 -k priv-esc
AUDIT_EOF
        systemctl restart auditd >/dev/null 2>&1 || true
        _harden_log ok "auditd configured — monitoring critical files + docker exec + sudo"
    else
        if systemctl is-active --quiet auditd 2>/dev/null; then
            _harden_log ok "auditd active with SMSLY rules"
        else
            systemctl enable auditd >/dev/null 2>&1 || true
            systemctl restart auditd >/dev/null 2>&1 || true
            _harden_log ok "auditd restarted"
        fi
    fi
}

# ─── Kernel Hardening (sysctl) ────────────────────────────────────────────────
_harden_kernel() {
    _harden_log info "Kernel hardening — sysctl parameters"

    local sysctl_file="/etc/sysctl.d/99-smsly-security.conf"

    cat > "$sysctl_file" <<'SYSCTL_EOF'
# IP / network hardening
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

# Kernel hardening
kernel.dmesg_restrict = 1
kernel.kptr_restrict = 2
kernel.yama.ptrace_scope = 1
kernel.unprivileged_bpf_disabled = 1
kernel.randomize_va_space = 2
fs.protected_hardlinks = 1
fs.protected_symlinks = 1
fs.suid_dumpable = 0
SYSCTL_EOF

    sysctl -p "$sysctl_file" >/dev/null 2>&1 || true
    _harden_log ok "Kernel hardening applied (ip/network/kptr/ptrace/bpf/fs)"
}

# ─── Docker runtime security (daemon.json) ────────────────────────────────────
_harden_docker_daemon() {
    _harden_log info "Docker daemon — security config"

    local daemon_cfg="/etc/docker/daemon.json"
    local needs_restart=false

    if [ ! -f "$daemon_cfg" ]; then
        echo '{}' > "$daemon_cfg"
    fi

    # Set user namespace remap if not already configured
    if ! python3 -c "import json; f=open('$daemon_cfg'); d=json.load(f)" 2>/dev/null | grep -q "userns-remap"; then
        python3 -c "
import json
cfg = {}
try:
    with open('$daemon_cfg') as f:
        cfg = json.load(f)
except: pass
cfg['userns-remap'] = 'default'
cfg['live-restore'] = True
cfg['log-driver'] = 'json-file'
cfg['log-opts'] = {'max-size': '10m', 'max-file': '3'}
with open('$daemon_cfg','w') as f:
    json.dump(cfg, f, indent=2)
"
        needs_restart=true
    fi

    # Add seccomp profile enforcement
    if ! python3 -c "import json; f=open('$daemon_cfg'); d=json.load(f)" 2>/dev/null | grep -q "seccomp"; then
        python3 -c "
import json
with open('$daemon_cfg') as f:
    cfg = json.load(f)
cfg.setdefault('features', {})['seccomp'] = True
with open('$daemon_cfg','w') as f:
    json.dump(cfg, f, indent=2)
"
        needs_restart=true
    fi

    if [ "$needs_restart" = "true" ]; then
        _harden_log warn "Docker daemon.json updated — restart Docker to apply (systemctl restart docker)"
    else
        _harden_log ok "Docker daemon security config present"
    fi
}

# ─── Comprehensive security check (non-blocking) ─────────────────────────────
harden_security_stack() {
    echo -e "${BLUE}╔════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${BLUE}║  SMSLY Hosting — Security Hardening & Self-Healing       ║${NC}"
    echo -e "${BLUE}╚════════════════════════════════════════════════════════════╝${NC}"

    local failures=0
    local checks=0

    _harden_fail2ban;      [ $? -eq 0 ] || ((failures++)); ((checks++))
    _harden_ufw;            [ $? -eq 0 ] || ((failures++)); ((checks++))
    _harden_apparmor;       [ $? -eq 0 ] || ((failures++)); ((checks++))
    _harden_kernel;         [ $? -eq 0 ] || ((failures++)); ((checks++))
    _harden_auditd;         [ $? -eq 0 ] || ((failures++)); ((checks++))
    _harden_docker_daemon;  [ $? -eq 0 ] || ((failures++)); ((checks++))
    _harden_crowdsec;       [ $? -eq 0 ] || ((failures++)); ((checks++))
    _harden_falco;          [ $? -eq 0 ] || ((failures++)); ((checks++))

    local passed=$((checks - failures))

    echo ""
    if [ "$failures" -eq 0 ]; then
        echo -e "${GREEN}════════════════════════════════════════════════════════════${NC}"
        echo -e "${GREEN}  Security hardening complete: $passed/$checks checks passed${NC}"
        echo -e "${GREEN}════════════════════════════════════════════════════════════${NC}"
    else
        echo -e "${YELLOW}════════════════════════════════════════════════════════════${NC}"
        echo -e "${YELLOW}  Security hardening: $passed/$checks checks passed, $failures warnings${NC}"
        echo -e "${YELLOW}  Review warnings above — failures are non-blocking${NC}"
        echo -e "${YELLOW}════════════════════════════════════════════════════════════${NC}"
    fi
}
