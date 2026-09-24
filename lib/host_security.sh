#!/bin/sh
# Host security posture snapshot for the PaaS Security Intel tab.
# UFW/auditd are invisible from inside containers by design; this host
# cron publishes JSON lines that promtail ships to Loki as
# job="host-security", which the backend security-events view reads.
# No secrets, no PII — status booleans and counts only.
#
# Cron: */5 * * * * root /opt/smsly-hosting/lib/host_security.sh >> /var/log/host-security.log 2>/dev/null
# Logrotate: /var/log/host-security.log grows ~1 line/5min — negligible.
set -u

LOG="/var/log/host-security.log"
TS=$(date -u +%FT%TZ)

ufw_active=false
if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -q "Status: active"; then
    ufw_active=true
fi

auditd_active=false
if command -v systemctl >/dev/null 2>&1 && [ "$(systemctl is-active auditd 2>/dev/null)" = "active" ]; then
    auditd_active=true
fi

# fail2ban: per-jail currently-banned counts (best-effort, never fails run)
f2b='{}'
if command -v fail2ban-client >/dev/null 2>&1 && fail2ban-client ping >/dev/null 2>&1; then
    jails=$(fail2ban-client status 2>/dev/null | sed -n 's/.*Jail list:[[:space:]]*//p' | tr ',' ' ')
    parts=""
    for j in $jails; do
        n=$(fail2ban-client status "$j" 2>/dev/null | sed -n 's/.*Currently banned:[[:space:]]*\([0-9]*\).*/\1/p' | head -n 1)
        [ -z "$n" ] && n=0
        parts="$parts\"$j\":$n,"
    done
    f2b="{${parts%,}}"
fi

printf '{"ts":"%s","ufw_active":%s,"auditd_active":%s,"fail2ban_jails":%s}\n' \
    "$TS" "$ufw_active" "$auditd_active" "$f2b" >> "$LOG"
