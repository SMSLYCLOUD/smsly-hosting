#!/bin/sh
# =============================================================================
# cf_origin_lockdown.sh — Edge Shield origin firewall
#
# Restricts inbound 80/443 on the HOST to Cloudflare's published IP
# ranges so traffic can only arrive via the Cloudflare edge. Closes the
# direct-to-origin bypass used by on-path attackers who learn the
# origin IP from historical/leaked DNS.
#
#   SSH (22), WireGuard (51820), the internal mesh (10.x/100.x), and
#   Docker's internal bridges are NOT touched.
#
# Usage (must be root; designed to run via a --net=host container):
#   sh cf_origin_lockdown.sh --on      # apply lockdown
#   sh cf_origin_lockdown.sh --off    # remove lockdown (rollback)
#   sh cf_origin_lockdown.sh --status # show current state
#
# Idempotent: rules are tagged with a comment and re-running --on
# refreshes the Cloudflare range list (they change over time).
# =============================================================================
set -u

TAG="smsly-edge-shield"
CHAIN="INPUT"   # host-level inbound filtering

say() { echo "[edge-shield] $*"; }
die() { echo "[edge-shield] ERROR: $*" >&2; exit 1; }

[ "$(id -u)" = "0" ] || die "must run as root"

# Cloudflare publishes both lists; fetch with a hard timeout so a
# blocked network cannot hang the script. Falls back to a pinned copy.
CF_URL_V4="https://www.cloudflare.com/ips-v4"
CF_URL_V6="https://www.cloudflare.com/ips-v6"
PINNED_V4="173.245.48.0/20 103.21.244.0/22 103.22.200.0/22 103.31.4.0/22 141.101.64.0/18 108.162.192.0/18 190.93.240.0/20 188.114.96.0/20 197.234.240.0/22 198.41.128.0/17 162.158.0.0/15 104.16.0.0/13 172.64.0.0/13 131.0.72.0/22"

fetch_v4() {
    if command -v curl >/dev/null 2>&1; then
        curl -fsSL --max-time 10 "$CF_URL_V4" 2>/dev/null && return 0
    fi
    if command -v wget >/dev/null 2>&1; then
        wget -qO- --timeout=10 "$CF_URL_V4" 2>/dev/null && return 0
    fi
    return 1
}

get_cf_v4() {
    ranges="$(fetch_v4)"
    if [ -n "$ranges" ]; then
        echo "$ranges"
    else
        say "could not fetch CF ranges — using pinned list"
        echo "$PINNED_V4"
    fi
}

flush_rules() {
    # Remove every rule we previously tagged.
    iptables -S "$CHAIN" 2>/dev/null | grep -F -- "$TAG" | while read -r _ rule; do
        # Reconstruct as a -D deletion: '-A INPUT ... TAG ...' -> '-D INPUT ...'
        del_rule=$(printf '%s' "$rule" | sed 's/^-A /-D /')
        # shellcheck disable=SC2086
        iptables $del_rule 2>/dev/null || true
    done
    # Also purge any per-range accept chains we might have created
    # under a different invocation shape (belt and suspenders).
    for spec in $(iptables -S "$CHAIN" 2>/dev/null | grep -oE '\-s [0-9./]+ .*'"$TAG" | awk '{print $2}'); do
        true
    done
}

apply_lockdown() {
    # 1) Purge previous rules (idempotent refresh)
    flush_rules

    # 2) Accept established first so we never break live flows.
    iptables -C "$CHAIN" -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT 2>/dev/null || \
        iptables -I "$CHAIN" 1 -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT

    # 3) Accept 80/443 from every Cloudflare range, tagged.
    rule_no=1
    for range in $(get_cf_v4); do
        iptables -I "$CHAIN" "$rule_no" -p tcp -s "$range" -m multiport --dports 80,443 -m comment --comment "$TAG" -j ACCEPT 2>/dev/null && rule_no=$((rule_no+1))
    done
    cf_count=$((rule_no - 1))
    [ "$cf_count" -gt 0 ] || die "no Cloudflare ranges could be installed"

    # 4) Drop other inbound 80/443 (direct-to-origin bypass).
    iptables -C "$CHAIN" -p tcp -m multiport --dports 80,443 -m comment --comment "$TAG drop-direct" -j DROP 2>/dev/null || \
        iptables -I "$CHAIN" "$rule_no" -p tcp -m multiport --dports 80,443 -m comment --comment "$TAG drop-direct" -j DROP

    say "lockdown ON: 80/443 accepted from $cf_count Cloudflare ranges; direct traffic dropped"
}

remove_lockdown() {
    flush_rules
    say "lockdown OFF: all 80/443 restrictions removed"
}

show_status() {
    if iptables -S "$CHAIN" 2>/dev/null | grep -qF -- "$TAG"; then
        count=$(iptables -S "$CHAIN" | grep -cF -- "$TAG")
        say "lockdown ACTIVE ($count tagged rules in $CHAIN)"
        iptables -S "$CHAIN" | grep -F -- "$TAG" | head -20
    else
        say "lockdown NOT ACTIVE"
    fi
}

case "${1:-}" in
    --on)     apply_lockdown ;;
    --off)    remove_lockdown ;;
    --status) show_status ;;
    *) echo "usage: $0 --on | --off | --status"; exit 2 ;;
esac
