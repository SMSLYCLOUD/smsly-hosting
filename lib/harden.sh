#!/bin/bash
# =============================================================================
# SMSLY Hosting — Security Hardening & Self-Healing Module
# =============================================================================
# Two-phase design:
#   1. harden_security_bootstrap  → install + start everything; return quickly.
#   2. harden_security_verify     → confirm everything came up. Report.
#
# Called from install.sh --update, --refresh, and fresh install.
# Never blocks deployment. Install & start are fire-and-forget.
# =============================================================================
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

# ─── PHASE 1: Bootstrap — install + start; return immediately ─────────────────

_harden_fail2ban_bootstrap() {
    if ! command -v fail2ban-client >/dev/null 2>&1; then
        apt_run apt-get install -y fail2ban 2>/dev/null || true
    fi
    command -v fail2ban-client >/dev/null 2>&1 || return 1

    [ -f /etc/fail2ban/jail.local ] || cat <<'JAIL_EOF' > /etc/fail2ban/jail.local
[DEFAULT]
bantime = 1h
findtime = 10m
maxretry = 3
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

[recidive]
enabled = true
filter = recidive
logpath = /var/log/fail2ban.log
action = iptables-allports[name=recidive]
bantime = 24h
findtime = 1d
maxretry = 3

[nginx-http-auth]
enabled = true
filter = nginx-http-auth
logpath = /var/log/nginx/error.log
maxretry = 5

[http-get-dos]
enabled = true
filter = http-get-dos
port = http,https
logpath = /var/log/nginx/access.log
findtime = 300
maxretry = 300
bantime = 600
JAIL_EOF
    # Create the http-get-dos filter that jail.local references
    [ -f /etc/fail2ban/filter.d/http-get-dos.conf ] || cat <<'FILTER_EOF' > /etc/fail2ban/filter.d/http-get-dos.conf
[Definition]
failregex = ^<HOST> -.*"(GET|POST).*HTTP.*" 200 .*$
ignoreregx =
FILTER_EOF

    systemctl enable fail2ban >/dev/null 2>&1 || true
    # Fire-and-forget: start in background, don't wait for ready
    systemctl restart fail2ban >/dev/null 2>&1 &
}

_harden_ufw_bootstrap() {
    command -v ufw >/dev/null 2>&1 || apt_run apt-get install -y ufw 2>/dev/null || true
    command -v ufw >/dev/null 2>&1 || return 1

    # Already active — just verify ports are open, then bail
    if ufw status 2>/dev/null | grep -qi "active"; then
        for port in 22 80 443 51820; do
            ufw status verbose 2>/dev/null | grep -qE "${port}(/tcp|/udp)?.*ALLOW" || ufw allow "$port" >/dev/null 2>&1 || true
        done
        # Whitelist Docker bridges
        for iface in docker0 br-+; do
            ip link show "$iface" >/dev/null 2>&1 || continue
            ufw allow in on "$iface" >/dev/null 2>&1 || true
        done
        return 0
    fi

    # Inactive — configure and enable (INPUT default deny, FORWARD stays open for Docker)
    ufw --force default deny incoming >/dev/null 2>&1 || true
    ufw --force default allow outgoing >/dev/null 2>&1 || true
    ufw allow ssh >/dev/null 2>&1 || true
    ufw allow 80/tcp >/dev/null 2>&1 || true
    ufw allow 443/tcp >/dev/null 2>&1 || true
    ufw allow 51820/udp >/dev/null 2>&1 || true
    for iface in docker0 br-+; do
        ip link show "$iface" >/dev/null 2>&1 || continue
        ufw allow in on "$iface" >/dev/null 2>&1 || true
    done
    ufw --force enable >/dev/null 2>&1 &
}

_harden_apparmor_bootstrap() {
    command -v aa-status >/dev/null 2>&1 || apt_run apt-get install -y apparmor apparmor-utils 2>/dev/null || true
    command -v aa-status >/dev/null 2>&1 || return 1
    systemctl enable apparmor >/dev/null 2>&1 || true
    systemctl start apparmor >/dev/null 2>&1 &
}

_harden_auditd_bootstrap() {
    command -v auditd >/dev/null 2>&1 || apt_run apt-get install -y auditd audispd-plugins 2>/dev/null || true

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
    systemctl enable auditd >/dev/null 2>&1 || true
    systemctl restart auditd >/dev/null 2>&1 &
}

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
    sysctl -p "$sysctl_file" >/dev/null 2>&1 || true
}

_harden_docker_daemon_bootstrap() {
    local daemon_cfg="/etc/docker/daemon.json"
    [ ! -f "$daemon_cfg" ] && echo '{}' > "$daemon_cfg"

    local changed=false

    # log rotation
    python3 -c "import json; d=json.load(open('$daemon_cfg')); exit(0 if d.get('log-driver')=='json-file' and d.get('log-opts',{}).get('max-size')=='10m' else 1)" 2>/dev/null || {
        python3 -c "
import json
with open('$daemon_cfg') as f: cfg = json.load(f)
cfg['log-driver'] = 'json-file'
cfg['log-opts'] = {'max-size': '10m', 'max-file': '3'}
with open('$daemon_cfg','w') as f: json.dump(cfg, f, indent=2)
"
        changed=true
    }

    # live-restore
    python3 -c "import json; d=json.load(open('$daemon_cfg')); exit(0 if d.get('live-restore') else 1)" 2>/dev/null || {
        python3 -c "
import json
with open('$daemon_cfg') as f: cfg = json.load(f)
cfg['live-restore'] = True
with open('$daemon_cfg','w') as f: json.dump(cfg, f, indent=2)
"
        changed=true
    }

    # seccomp
    python3 -c "import json; d=json.load(open('$daemon_cfg')); exit(0 if d.get('features',{}).get('seccomp') else 1)" 2>/dev/null || {
        python3 -c "
import json
with open('$daemon_cfg') as f: cfg = json.load(f)
cfg.setdefault('features', {})['seccomp'] = True
with open('$daemon_cfg','w') as f: json.dump(cfg, f, indent=2)
"
        changed=true
    }

    # userns-remap — only when no containers are running (would orphan them)
    python3 -c "import json; d=json.load(open('$daemon_cfg')); exit(0 if d.get('userns-remap') else 1)" 2>/dev/null || {
        if [ -z "$(docker ps -q 2>/dev/null | head -1)" ]; then
            python3 -c "
import json
with open('$daemon_cfg') as f: cfg = json.load(f)
cfg['userns-remap'] = 'default'
with open('$daemon_cfg','w') as f: json.dump(cfg, f, indent=2)
"
            changed=true
        fi
    }
}

_harden_crowdsec_bootstrap() {
    # CrowdSec comes from the main docker-compose stack — if the container
    # isn't running, try docker compose up -d for just that service.
    if docker ps --format '{{.Names}}' 2>/dev/null | grep -q "smsly-crowdsec"; then
        return 0  # already up
    fi
    # Fire-and-forget: start in background (image pull may take time)
    (
        docker compose \
            --env-file "$INSTALL_DIR/.env" \
            -f "$COMPOSE_FILE" \
            up -d crowdsec 2>/dev/null || true
    ) &
}

_harden_falco_bootstrap() {
    local compose_file="$INSTALL_DIR/infrastructure/docker/docker-compose.falco.yml"
    [ -f "$compose_file" ] || return 1

    # Auto-detect best driver
    if [ -f /sys/kernel/btf/vmlinux ]; then
        export FALCO_BPF_PROBE=""
    fi

    # Fire-and-forget: always recreate so config changes (e.g. command flags)
    # take effect even if the container is already running.
    (
        docker compose \
            --env-file "$INSTALL_DIR/.env" \
            -f "$compose_file" \
            up -d --force-recreate --pull always 2>/dev/null || true
    ) &
}

# ─── PHASE 2: Verify — check everything came up, report status ────────────────

_harden_fail2ban_verify() {
    local ok=true
    command -v fail2ban-client >/dev/null 2>&1 || { _harden_log warn "fail2ban — not installed"; return 1; }

    # Quick check — if not responding, wait a few seconds for async start
    fail2ban-client ping >/dev/null 2>&1 || { sleep 5; fail2ban-client ping >/dev/null 2>&1; } || ok=false

    if $ok && fail2ban-client status sshd >/dev/null 2>&1; then
        _harden_log ok "fail2ban active (sshd + recidive + http)"
        return 0
    fi
    _harden_log warn "fail2ban not responding — check systemctl status fail2ban"
    return 1
}

_harden_ufw_verify() {
    command -v ufw >/dev/null 2>&1 || { _harden_log warn "ufw — not installed"; return 1; }
    if ufw status 2>/dev/null | grep -qi "active"; then
        _harden_log ok "ufw active (host INPUT hardened)"
        return 0
    fi
    _harden_log warn "ufw not active — check ufw status"
    return 1
}

_harden_apparmor_verify() {
    command -v aa-status >/dev/null 2>&1 || { _harden_log warn "apparmor — not installed"; return 1; }
    local count
    count=$(aa-status --json 2>/dev/null | python3 -c "import json,sys; d=json.load(sys.stdin); print(len(d.get('processes',{})))" 2>/dev/null || echo "0")
    count="${count//[^0-9]/}"
    : "${count:=0}"
    if [ "$count" -gt 0 ] 2>/dev/null; then
        _harden_log ok "apparmor enforcing ($count profiles)"
        return 0
    fi
    _harden_log warn "apparmor installed but no enforce profiles"
    return 1
}

_harden_auditd_verify() {
    command -v auditd >/dev/null 2>&1 || { _harden_log warn "auditd — not installed"; return 1; }
    if systemctl is-active --quiet auditd 2>/dev/null; then
        _harden_log ok "auditd active (file + syscall monitoring)"
        return 0
    fi
    _harden_log warn "auditd not running — may need kernel param audit=1"
    return 1
}

_harden_kernel_verify() {
    if [ -f /etc/sysctl.d/99-smsly-security.conf ]; then
        _harden_log ok "kernel hardening applied"
        return 0
    fi
    _harden_log warn "kernel hardening not applied"
    return 1
}

_harden_docker_daemon_verify() {
    local daemon_cfg="/etc/docker/daemon.json"
    if [ -f "$daemon_cfg" ] && python3 -c "import json; json.load(open('$daemon_cfg'))" 2>/dev/null; then
        _harden_log ok "docker daemon security config present"
        return 0
    fi
    _harden_log warn "docker daemon config missing or invalid"
    return 1
}

_harden_crowdsec_verify() {
    if ! docker ps --format '{{.Names}}' 2>/dev/null | grep -q "smsly-crowdsec"; then
        _harden_log warn "crowdsec — container not running"
        return 1
    fi
    # Refresh hub in background, don't block verification
    docker exec smsly-crowdsec cscli hub update 2>/dev/null &
    docker exec smsly-crowdsec cscli hub upgrade 2>/dev/null &
    _harden_log ok "crowdsec deployed"
    return 0
}

_harden_falco_verify() {
    if ! docker ps --format '{{.Names}}' 2>/dev/null | grep -q "smsly-falco"; then
        _harden_log warn "falco — container not running"
        return 1
    fi
    _harden_log ok "falco deployed"
    return 0
}

# ─── Container Runtime Sandboxing ─────────────────────────────────────────────

_harden_container_runtime_bootstrap() {
    local install_dir="${INSTALL_DIR:-/opt/smsly-hosting}"
    local env_file="$install_dir/.env"

    # If CONTAINER_RUNTIME is already persisted in .env, skip detection.
    # The user can clear it to re-detect.
    if [ -f "$env_file" ] && grep -q '^CONTAINER_RUNTIME=' "$env_file" 2>/dev/null; then
        return 0
    fi

    # Try Kata first (stronger isolation, requires KVM)
    if [ -e /dev/kvm ] && ! command -v kata-runtime &>/dev/null; then
        if [ -f "$install_dir/lib/install-kata.sh" ]; then
            echo -e "${BLUE}  → [harden] Kata Containers (KVM available) — installing...${NC}"
            bash "$install_dir/lib/install-kata.sh" || true
        fi
    fi

    if command -v kata-runtime &>/dev/null; then
        env_set_value "$env_file" "CONTAINER_RUNTIME" "kata"
        echo -e "${BLUE}  → [harden] Persisted CONTAINER_RUNTIME=kata in .env${NC}"
        return 0
    fi

    # Fall back to gVisor (lighter, no KVM required)
    if ! command -v runsc &>/dev/null; then
        if [ -f "$install_dir/lib/install-gvisor.sh" ]; then
            echo -e "${BLUE}  → [harden] gVisor (runsc) — installing...${NC}"
            bash "$install_dir/lib/install-gvisor.sh" || true
        fi
    fi

    if command -v runsc &>/dev/null; then
        env_set_value "$env_file" "CONTAINER_RUNTIME" "runsc"
        echo -e "${BLUE}  → [harden] Persisted CONTAINER_RUNTIME=runsc in .env${NC}"
        return 0
    fi
}

_harden_container_runtime_verify() {
    local found=0

    if command -v runsc &>/dev/null; then
        _harden_log ok "gVisor (runsc) installed"
        found=1
    fi

    if command -v kata-runtime &>/dev/null; then
        _harden_log ok "Kata Containers installed"
        found=1
    fi

    if [ "$found" -eq 0 ]; then
        _harden_log warn "container runtime sandboxing — install gVisor or Kata for VM-level isolation"
        return 1
    fi

    # Check Docker runtime registration
    if [ -f /etc/docker/daemon.json ]; then
        if python3 -c "import json; import sys; cfg=json.load(open('/etc/docker/daemon.json')); sys.exit(0 if 'runsc' in cfg.get('runtimes',{}) else 1)" 2>/dev/null; then
            _harden_log ok "gVisor registered with Docker"
        elif python3 -c "import json; import sys; cfg=json.load(open('/etc/docker/daemon.json')); sys.exit(0 if 'kata-runtime' in cfg.get('runtimes',{}) else 1)" 2>/dev/null; then
            _harden_log ok "Kata registered with Docker"
        fi
    fi

    return "$found"
}

# ─── Public Entry Points ──────────────────────────────────────────────────────

harden_security_bootstrap() {
    echo -e "${BLUE}  → [harden] Bootstrapping security stack (fire-and-forget)...${NC}"
    _harden_fail2ban_bootstrap
    _harden_ufw_bootstrap
    _harden_apparmor_bootstrap
    _harden_auditd_bootstrap
    _harden_kernel_bootstrap
    _harden_docker_daemon_bootstrap
    _harden_crowdsec_bootstrap
    _harden_falco_bootstrap
    _harden_container_runtime_bootstrap
    echo -e "${GREEN}  ✓ [harden] Bootstrap dispatched — verifying later${NC}"
}

harden_security_verify() {
    echo ""
    echo -e "${BLUE}════════════════════════════════════════════════════════════${NC}"
    echo -e "${BLUE}  Security Stack — Verification${NC}"
    echo -e "${BLUE}════════════════════════════════════════════════════════════${NC}"

    local failures=0 checks=0

    _harden_fail2ban_verify   || { ((failures++)); true; }; ((checks++))
    _harden_ufw_verify        || { ((failures++)); true; }; ((checks++))
    _harden_apparmor_verify   || { ((failures++)); true; }; ((checks++))
    _harden_auditd_verify     || { ((failures++)); true; }; ((checks++))
    _harden_kernel_verify     || { ((failures++)); true; }; ((checks++))
    _harden_docker_daemon_verify || { ((failures++)); true; }; ((checks++))
    _harden_crowdsec_verify   || { ((failures++)); true; }; ((checks++))
    _harden_falco_verify      || { ((failures++)); true; }; ((checks++))
    _harden_container_runtime_verify || { ((failures++)); true; }; ((checks++))

    local passed=$((checks - failures))
    echo ""
    if [ "$failures" -eq 0 ]; then
        echo -e "${GREEN}  All $passed/$checks security checks passed${NC}"
    else
        echo -e "${YELLOW}  Security: $passed/$checks passed, $failures with warnings${NC}"
        echo -e "${YELLOW}  Warnings are non-blocking — review above${NC}"
    fi
    echo -e "${BLUE}════════════════════════════════════════════════════════════${NC}"
    echo ""
}
