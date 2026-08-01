#!/bin/bash

_harden_fail2ban_bootstrap() {
    if ! command -v fail2ban-client ; then
        apt_run apt-get install -y fail2ban  || true
    fi
    command -v fail2ban-client  || return 1

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
    if [ -d /var/log/caddy ] || docker volume ls --format '{{.Name}}'  | grep -q caddy_logs; then
        # Never duplicate the sections: fail2ban aborts on a repeated
        # [caddy-auth], and every install/update run would otherwise append.
        if ! grep -q '^\[caddy-auth\]' /etc/fail2ban/jail.local 2>/dev/null; then
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

    systemctl enable fail2ban || _harden_log warn "fail2ban enable failed"
    # Blocking start — wait for the service to actually be ACTIVE (not just for
    # `systemctl restart` to return). If it never comes up we surface the real
    # failure via journalctl instead of spamming socket errors.
    systemctl restart fail2ban || _harden_log warn "fail2ban restart returned non-zero"
    local _up=0
    for _i in $(seq 1 30); do
        if systemctl is-active --quiet fail2ban; then
            _up=1
            break
        fi
        sleep 1
    done
    if [ "$_up" -ne 1 ]; then
        _harden_log err "fail2ban failed to become active — last journalctl output:"
        journalctl -u fail2ban -n 40 --no-pager 2>&1 | sed 's/^/      /' || true
        return 1
    fi
    # Service is active — confirm the client can reach the server socket.
    if ! fail2ban-client ping; then
        _harden_log warn "fail2ban active but client cannot reach socket"
    fi
}

_harden_fail2ban_verify() {
    command -v fail2ban-client  || { _harden_log warn "fail2ban — not installed"; return 1; }
    if ! systemctl is-active --quiet fail2ban; then
        _harden_log warn "fail2ban not running — last journalctl output:"
        journalctl -u fail2ban -n 30 --no-pager 2>&1 | sed 's/^/      /' || true
        return 1
    fi
    if fail2ban-client ping && fail2ban-client status sshd ; then
        _harden_log ok "fail2ban active (sshd + recidive + http)"
        return 0
    fi
    _harden_log warn "fail2ban running but not responding to client"
    return 1
}
