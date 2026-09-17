#!/usr/bin/env bash
# Egress mirror for CDN-blocked networks (dl-cdn.alpinelinux.org).
#
# Installs a host-local nginx that rewrites dl-cdn requests to working
# vendor mirrors, and steers port-80 TCP to it via REDIRECT.
#
# Why REDIRECT-all instead of matching dl-cdn only: netfilter NAT
# decisions happen on the SYN (no HTTP payload yet), so Host-based
# matching cannot steer — only the proxy itself can route by Host.
# Consequently ALL plain-HTTP egress takes a local hop; the default
# server passes non-dl-cdn traffic through byte-identical, HTTPS is
# untouched, and nginx is monitored like any platform service. This is
# the standard transparent-proxy pattern, not a hack.
#
# Idempotent: safe to run on every install/update/resume and at boot.
# Best-effort: never aborts the caller (returns 0); build failures still
# surface at the real `apk` step with the vendor error intact.

SMSLY_EGRESS_MIRROR_PORT="${SMSLY_EGRESS_MIRROR_PORT:-8888}"

_egress_log() {
    echo -e "${BLUE:-}  → [egress-mirror] $*${NC:-}" 2>/dev/null || echo "  → [egress-mirror] $*"
}

_egress_warn() {
    echo -e "${YELLOW:-}  ⚠ [egress-mirror] $*${NC:-}" 2>/dev/null || echo "  ⚠ [egress-mirror] $*"
}

_docker0_gateway() {
    # Gateway of the default docker bridge (build containers reach the
    # host through it). Falls back to the conventional default.
    local gw=""
    gw="$(docker network inspect bridge -f '{{(index .IPAM.Config 0).Gateway}}' 2>/dev/null || true)"
    if [ -z "$gw" ]; then
        gw="172.17.0.1"
    fi
    printf '%s' "$gw"
}

ensure_egress_mirror() {
    command -v docker >/dev/null 2>&1 || return 0
    command -v iptables >/dev/null 2>&1 || { _egress_warn "iptables missing — skipping"; return 0; }
    local install_dir="${INSTALL_DIR:-/opt/smsly-hosting}"
    local conf_src="$install_dir/infrastructure/egress-mirror/nginx.conf"
    [ -f "$conf_src" ] || { _egress_warn "config missing at $conf_src — skipping"; return 0; }

    # 1. nginx on the host (Ubuntu archive; unrelated to the blocked CDNs).
    if ! command -v nginx >/dev/null 2>&1; then
        _egress_log "installing nginx for the egress mirror..."
        if ! timeout 300 apt-get update -qq >/dev/null 2>&1 || ! timeout 600 apt-get install -y -qq nginx >/dev/null 2>&1; then
            _egress_warn "nginx install failed — app builds needing dl-cdn may fail"
            return 0
        fi
    fi
    mkdir -p /etc/nginx/smsly 2>/dev/null || true
    cp "$conf_src" /etc/nginx/smsly/egress-mirror.conf 2>/dev/null || {
        _egress_warn "cannot write nginx config — skipping"
        return 0
    }
    # Include from the main context (once): nginx.conf ends with
    # `include /etc/nginx/conf.d/*.conf;` on stock Ubuntu — our file
    # must be reachable from there.
    if [ ! -e /etc/nginx/conf.d/smsly-egress-mirror.conf ]; then
        ln -sf /etc/nginx/smsly/egress-mirror.conf /etc/nginx/conf.d/smsly-egress-mirror.conf 2>/dev/null || true
    fi
    if ! nginx -t >/dev/null 2>&1; then
        _egress_warn "nginx config test failed — leaving existing state"
        return 0
    fi

    # 2. nginx must serve the gateway + loopback BEFORE traffic is steered.
    local gw=""
    gw="$(_docker0_gateway)"
    # Bind explicitly (never 0.0.0.0: no public exposure by accident).
    if ! ss -ltn 2>/dev/null | grep -qE "127\.0\.0\.1:${SMSLY_EGRESS_MIRROR_PORT} |${gw//./\\.}:${SMSLY_EGRESS_MIRROR_PORT} "; then
        _egress_log "starting nginx..."
        systemctl enable nginx >/dev/null 2>&1 || true
        systemctl restart nginx >/dev/null 2>&1 || systemctl start nginx >/dev/null 2>&1 || true
        sleep 2
    fi

    # 3. Steer port-80 TCP to the shim (PREROUTING covers containers,
    # OUTPUT covers host-local processes). REDIRECT keeps it local.
    if ! iptables -t nat -C PREROUTING -p tcp --dport 80 -j REDIRECT --to-port "$SMSLY_EGRESS_MIRROR_PORT" >/dev/null 2>&1; then
        iptables -t nat -A PREROUTING -p tcp --dport 80 -j REDIRECT --to-port "$SMSLY_EGRESS_MIRROR_PORT" 2>/dev/null || \
            _egress_warn "PREROUTING rule install failed"
    fi
    if ! iptables -t nat -C OUTPUT -p tcp --dport 80 -j REDIRECT --to-port "$SMSLY_EGRESS_MIRROR_PORT" >/dev/null 2>&1; then
        iptables -t nat -A OUTPUT -p tcp --dport 80 -j REDIRECT --to-port "$SMSLY_EGRESS_MIRROR_PORT" 2>/dev/null || \
            _egress_warn "OUTPUT rule install failed"
    fi

    # 4. End-to-end proof through the shim (host path). A container-path
    # proof runs separately before unblocking app builds.
    if timeout 25 curl -s -o /dev/null -w '%{http_code}' \
            http://dl-cdn.alpinelinux.org/alpine/v3.21/main/x86_64/APKINDEX.tar.gz 2>/dev/null | grep -q "^200$"; then
        _egress_log "egress mirror serving dl-cdn (200 OK)"
    else
        _egress_warn "shim probe failed — app builds may still fail on dl-cdn"
    fi
    return 0
}
