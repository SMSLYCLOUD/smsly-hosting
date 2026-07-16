#!/bin/bash
# =============================================================================
# SMSLY Hosting — Security Hardening & Self-Healing Module
# =============================================================================
# Two-phase design:
#   1. harden_security_bootstrap  → install + start everything; block until done.
#   2. harden_security_verify     → confirm everything is up. Report.
#
# Called from install.sh --update, --refresh, and fresh install.
# Blocks until each layer is installed and started; failures are logged but
# do not halt deployment (caller receives a non-zero exit, decides what to do).
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
JAIL_EOF
    # Enable Caddy jails when Caddy logs are available
    if [ -d /var/log/caddy ] || docker volume ls --format '{{.Name}}' 2>/dev/null | grep -q caddy_logs; then
        cat <<'CADDY_JAIL_EOF' >> /etc/fail2ban/jail.local

[caddy-auth]
enabled = true
filter = caddy-auth
port = http,https
logpath = /var/log/caddy/access.log
maxretry = 5
bantime = 1h

[caddy-dos]
enabled = true
filter = caddy-dos
port = http,https
logpath = /var/log/caddy/access.log
findtime = 300
maxretry = 300
bantime = 600
CADDY_JAIL_EOF
    fi
    # Caddy auth filter (JSON access log — 401/403 responses)
    [ -f /etc/fail2ban/filter.d/caddy-auth.conf ] || cat <<'FILTER_EOF' > /etc/fail2ban/filter.d/caddy-auth.conf
[Definition]
failregex = ^.*"remote_ip":"<HOST>".*"status":(401|403).*$
ignoreregex =
FILTER_EOF
    # Caddy DoS filter (JSON access log — any request)
    [ -f /etc/fail2ban/filter.d/caddy-dos.conf ] || cat <<'FILTER_EOF' > /etc/fail2ban/filter.d/caddy-dos.conf
[Definition]
failregex = ^.*"remote_ip":"<HOST>".*"method":"(GET|POST|HEAD|PUT|DELETE|PATCH)".*$
ignoreregex =
FILTER_EOF

    systemctl enable fail2ban || echo -e "${YELLOW}    ⚠ fail2ban enable failed${NC}"
    # Blocking start — wait for service to be ready
    systemctl restart fail2ban || echo -e "${YELLOW}    ⚠ fail2ban restart failed${NC}"
    for _i in $(seq 1 10); do
        fail2ban-client ping >/dev/null 2>&1 && break
        sleep 1
    done
}

_harden_ufw_bootstrap() {
    command -v ufw >/dev/null 2>&1 || apt_run apt-get install -y ufw 2>/dev/null || true
    command -v ufw >/dev/null 2>&1 || return 1

    # Already active — just verify ports are open, then bail
    if ufw status 2>/dev/null | grep -qi "active"; then
        for port in 22 80 443 51820; do
            ufw status verbose 2>/dev/null | grep -qE "${port}(/tcp|/udp)?.*ALLOW" || ufw allow "$port" || echo -e "${YELLOW}    ⚠ ufw allow port $port failed${NC}"
        done
        # Whitelist Docker bridges
        for iface in docker0 br-+; do
            ip link show "$iface" >/dev/null 2>&1 || continue
            ufw allow in on "$iface" || echo -e "${YELLOW}    ⚠ ufw allow in on $iface failed${NC}"
        done
        return 0
    fi

    # Inactive — configure and enable (INPUT default deny, FORWARD stays open for Docker)
    ufw --force default deny incoming || echo -e "${YELLOW}    ⚠ ufw default deny incoming failed${NC}"
    ufw --force default allow outgoing || echo -e "${YELLOW}    ⚠ ufw default allow outgoing failed${NC}"
    ufw allow ssh || echo -e "${YELLOW}    ⚠ ufw allow ssh failed${NC}"
    ufw allow 80/tcp || echo -e "${YELLOW}    ⚠ ufw allow 80/tcp failed${NC}"
    ufw allow 443/tcp || echo -e "${YELLOW}    ⚠ ufw allow 443/tcp failed${NC}"
    ufw allow 51820/udp || echo -e "${YELLOW}    ⚠ ufw allow 51820/udp failed${NC}"
    for iface in docker0 br-+; do
        ip link show "$iface" >/dev/null 2>&1 || continue
        ufw allow in on "$iface" || echo -e "${YELLOW}    ⚠ ufw allow in on $iface failed${NC}"
    done
    ufw --force enable || echo -e "${YELLOW}    ⚠ ufw enable failed${NC}"
    # Verify it actually came up
    for _i in $(seq 1 5); do
        ufw status 2>/dev/null | grep -qi "active" && break
        sleep 2
    done
}

_harden_apparmor_bootstrap() {
    command -v aa-status >/dev/null 2>&1 || apt_run apt-get install -y apparmor apparmor-utils 2>/dev/null || true
    command -v aa-status >/dev/null 2>&1 || return 1
    systemctl enable apparmor || echo -e "${YELLOW}    ⚠ apparmor enable failed${NC}"
    systemctl start apparmor || echo -e "${YELLOW}    ⚠ apparmor start failed${NC}"
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
    systemctl enable auditd || echo -e "${YELLOW}    ⚠ auditd enable failed${NC}"
    systemctl restart auditd || echo -e "${YELLOW}    ⚠ auditd restart failed${NC}"
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
    sysctl -p "$sysctl_file" || echo -e "${YELLOW}    ⚠ sysctl -p failed${NC}"
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

    # Restart Docker if config changed AND no SMSLY containers are running
    # (doing so live would kill production).
    if [ "$changed" = "true" ]; then
        local _smsly_ctrs
        _smsly_ctrs="$(docker ps --format '{{.Names}}' 2>/dev/null | grep -c smsly || true)"
        if [ "$_smsly_ctrs" -eq 0 ]; then
            _harden_log info "Docker daemon config changed — restarting Docker..."
            systemctl restart docker || { _harden_log error "Docker restart failed"; }
            for _i in $(seq 1 30); do
                docker info >/dev/null 2>&1 && break
                sleep 2
            done
            _harden_log ok "Docker daemon restarted with security config"
        else
            _harden_log warn "Docker daemon config changed but $_smsly_ctrs SMSLY containers are running — deferring restart (apply on next daemon reload)"
        fi
    fi
}

_harden_crowdsec_bootstrap() {
    # CrowdSec comes from the main docker-compose stack — if the container
    # isn't running, try docker compose up -d for just that service.
    if docker ps --format '{{.Names}}' 2>/dev/null | grep -q "smsly-crowdsec"; then
        return 0  # already up
    fi
    # Blocking start — wait for container to be healthy
    docker compose \
        --env-file "$INSTALL_DIR/.env" \
        -f "$COMPOSE_FILE" \
        up -d crowdsec || echo -e "${YELLOW}    ⚠ crowdsec docker compose up failed${NC}"
    for _i in $(seq 1 15); do
        docker ps --format '{{.Names}}' 2>/dev/null | grep -q "smsly-crowdsec" && break
        sleep 2
    done
}

_harden_falco_bootstrap() {
    local compose_file="$INSTALL_DIR/infrastructure/docker/docker-compose.falco.yml"
    [ -f "$compose_file" ] || return 1

    # Blocking start — always recreate so config changes take effect
    docker compose \
        --env-file "$INSTALL_DIR/.env" \
        -f "$compose_file" \
        up -d --force-recreate --pull always || echo -e "${YELLOW}    ⚠ falco docker compose up failed${NC}"
    for _i in $(seq 1 15); do
        docker ps --format '{{.Names}}' 2>/dev/null | grep -q "smsly-falco" && break
        sleep 2
    done
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
    # Refresh hub scenarios — only upgrade when explicitly allowed.
    # Auto-upgrading on every harden.sh run can silently break
    # production WAF if CrowdSec ships a breaking parser change.
    docker exec smsly-crowdsec cscli hub update 2>/dev/null || _harden_log warn "crowdsec hub update failed"
    if [ "${CROWDSEC_AUTO_UPGRADE_HUB:-0}" = "1" ]; then
        docker exec smsly-crowdsec cscli hub upgrade 2>/dev/null || _harden_log warn "crowdsec hub upgrade failed"
    else
        _harden_log info "crowdsec hub upgrade skipped (set CROWDSEC_AUTO_UPGRADE_HUB=1 to enable)"
    fi
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

# ─── Trivy Vulnerability Scanner ──────────────────────────────────────────────

_harden_trivy_bootstrap() {
    if command -v trivy >/dev/null 2>&1; then
        return 0  # already installed
    fi

    _harden_log info "Installing Trivy vulnerability scanner..."
    local trivy_version="v0.54.1"
    local arch
    arch="$(uname -m)"
    case "$arch" in
        x86_64)  arch="64bit" ;;
        aarch64) arch="ARM64" ;;
        *)       _harden_log warn "Trivy — unsupported architecture: $arch"; return 1 ;;
    esac

    local deb_url="https://github.com/aquasecurity/trivy/releases/download/${trivy_version}/trivy_${trivy_version#v}_Linux-${arch}.deb"
    local tmp_deb
    tmp_deb="$(mktemp /tmp/trivy.XXXXXX.deb)"

    # Attempt 1: Direct DEB download with retries and timeouts
    if curl --retry 3 --retry-delay 2 --connect-timeout 15 -fsSL "$deb_url" -o "$tmp_deb" 2>/dev/null; then
        if ! dpkg -i "$tmp_deb" 2>/dev/null; then
            apt-get install -f -y 2>/dev/null || true
            dpkg -i "$tmp_deb" 2>/dev/null || true
        fi
        rm -f "$tmp_deb"
    else
        rm -f "$tmp_deb"
        _harden_log info "Direct DEB download failed — trying official APT repo and install script..."
    fi

    # Attempt 2: Official APT Repository fallback
    if ! command -v trivy >/dev/null 2>&1; then
        apt-get update -qq 2>/dev/null || true
        if ! apt-get install -y trivy 2>/dev/null; then
            if command -v gpg >/dev/null 2>&1; then
                curl --retry 2 --connect-timeout 10 -fsSL https://aquasecurity.github.io/trivy-repo/deb/public.key 2>/dev/null | gpg --dearmor -o /usr/share/keyrings/trivy.gpg 2>/dev/null || true
                echo "deb [signed-by=/usr/share/keyrings/trivy.gpg] https://aquasecurity.github.io/trivy-repo/deb $(lsb_release -sc 2>/dev/null || echo stable) main" > /etc/apt/sources.list.d/trivy.list 2>/dev/null || true
                apt-get update -qq 2>/dev/null || true
                apt-get install -y trivy 2>/dev/null || true
            fi
        fi
    fi

    # Attempt 3: Official Contrib script fallback
    if ! command -v trivy >/dev/null 2>&1; then
        curl --retry 2 --connect-timeout 10 -sfL https://raw.githubusercontent.com/aquasecurity/trivy/main/contrib/install.sh | sh -s -- -b /usr/local/bin 2>/dev/null || true
    fi

    if command -v trivy >/dev/null 2>&1; then
        _harden_log ok "Trivy installed successfully"
        return 0
    fi
    _harden_log warn "Trivy — download and installation fallbacks failed"
    return 1
}

_harden_trivy_verify() {
    if command -v trivy >/dev/null 2>&1; then
        local ver
        ver="$(trivy --version 2>/dev/null | head -1 || true)"
        _harden_log ok "Trivy available: ${ver}"
        return 0
    fi
    _harden_log warn "Trivy — not installed (image vulnerability scanning unavailable)"
    return 1
}

# ─── Infisical Secret Management ──────────────────────────────────────────────

_harden_infisical_bootstrap() {
    local infisical_script="$INSTALL_DIR/lib/infisical.sh"
    if [ ! -f "$infisical_script" ]; then
        _harden_log info "Infisical script not found — skipping"
        return 0
    fi
    # Source Infisical functions and bootstrap
    # shellcheck disable=SC1090
    source "$infisical_script" 2>/dev/null || {
        _harden_log warn "Failed to source infisical.sh"
        return 1
    }
    if ! command -v infisical_bootstrap >/dev/null 2>&1; then
        _harden_log warn "infisical_bootstrap function not found"
        return 1
    fi
    infisical_bootstrap 2>/dev/null || {
        _harden_log warn "Infisical bootstrap had issues"
        return 1
    }
    return 0
}

_harden_infisical_verify() {
    if docker ps --format '{{.Names}}' 2>/dev/null | grep -q "smsly-infisical"; then
        _harden_log ok "Infisical running"
        return 0
    fi
    _harden_log warn "Infisical — container not running"
    return 1
}

# ─── Public Entry Points ──────────────────────────────────────────────────────

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

    _harden_fail2ban_verify   || { ((failures++)); true; }; ((checks++))
    _harden_ufw_verify        || { ((failures++)); true; }; ((checks++))
    _harden_apparmor_verify   || { ((failures++)); true; }; ((checks++))
    _harden_auditd_verify     || { ((failures++)); true; }; ((checks++))
    _harden_kernel_verify     || { ((failures++)); true; }; ((checks++))
    _harden_docker_daemon_verify || { ((failures++)); true; }; ((checks++))
    _harden_crowdsec_verify   || { ((failures++)); true; }; ((checks++))
    _harden_falco_verify      || { ((failures++)); true; }; ((checks++))
    _harden_container_runtime_verify || { ((failures++)); true; }; ((checks++))
    _harden_trivy_verify      || { ((failures++)); true; }; ((checks++))
    _harden_infisical_verify   || { ((failures++)); true; }; ((checks++))

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
