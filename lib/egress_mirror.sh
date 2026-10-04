#!/usr/bin/env bash
# Egress mirror for CDN-blocked networks (dl-cdn.alpinelinux.org).
#
# Installs a host-local nginx that rewrites dl-cdn requests to working
# vendor mirrors, and steers port-80 TCP to it via REDIRECT.
#
# Why REDIRECT-all instead of matching dl-cdn only: netfilter NAT
# decisions happen on the SYN (no HTTP payload yet), so Host-based
# matching cannot steer — only the proxy itself can route by Host.
# The PREROUTING steer is scoped to the default docker bridge subnet:
# REDIRECT maps to the incoming interface address and the shim listens
# only on 127.0.0.1 + the docker0 gateway, so steering any OTHER bridge
# blackholes its :80 (nothing listens on its gateway; 2026-09-29: node
# smsly-net containers could not reach mesh :80). App builds run on
# docker0, so nothing is lost; every other bridge now routes directly.
# Host-local OUTPUT still takes the local hop, EXCEPT destinations on
# local/docker/mesh ranges (see _egress_local_return_cidrs below): the
# transparent proxy resolves the Host header, so traffic aimed at a
# container IP with an unresolvable Host (published :80 ports via
# docker-proxy, host diagnostics, Caddy/traefik upstreams) 502s instead
# of passing through. Those destinations bypass the shim (direct);
# real internet egress still steers. HTTPS is untouched, and
# nginx is monitored like any platform service. This is the standard
# transparent-proxy pattern, not a hack.
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
    # Render the template: gateway for container builds, public IP because
    # REDIRECT preserves the original (public) source address on host-local
    # connections, which must pass the allow rules.
    _egress_gw="$(_docker0_gateway)"
    _egress_pub="$(ip -4 route get 1.1.1.1 2>/dev/null | awk '{for (i=1; i<=NF; i++) if ($i == "src") print $(i+1)}' | head -n 1)"
    [ -n "$_egress_pub" ] || _egress_pub="127.0.0.1"
    sed -e "s|__GATEWAY_IP__|${_egress_gw}|g" -e "s|__HOST_PUBLIC_IP__|${_egress_pub}|g" \
        "$conf_src" > /etc/nginx/smsly/egress-mirror.conf 2>/dev/null || {
        _egress_warn "cannot write nginx config — skipping"
        return 0
    }
    # Include from the main context (once): nginx.conf ends with
    # `include /etc/nginx/conf.d/*.conf;` on stock Ubuntu — our file
    # must be reachable from there.
    if [ ! -e /etc/nginx/conf.d/smsly-egress-mirror.conf ]; then
        ln -sf /etc/nginx/smsly/egress-mirror.conf /etc/nginx/conf.d/smsly-egress-mirror.conf 2>/dev/null || true
    fi
    # Ubuntu ships a default site on :80 that collides with the edge
    # proxy (docker-proxy/Caddy own port 80) and takes nginx down
    # entirely — including our 8888 listeners. This box never serves
    # HTTP from host nginx; drop the default site.
    rm -f /etc/nginx/sites-enabled/default 2>/dev/null || true
    # FD ceiling: hung upstream connects (dead CDN IPs) pile concurrent
    # connections until the stock 1024 nofile turns EVERYTHING into 500s
    # (observed live). Systemd drop-in + daemon-reload; takes effect on
    # the (re)start below.
    _nofile_override="/etc/systemd/system/nginx.service.d/smsly-egress-mirror.conf"
    _nofile_want="$(printf '[Service]\nLimitNOFILE=32768\n')"
    _nofile_dirty=""
    mkdir -p "$(dirname "$_nofile_override")" 2>/dev/null || true
    if [ ! -f "$_nofile_override" ] || [ "$(cat "$_nofile_override" 2>/dev/null)" != "$_nofile_want" ]; then
        printf '%s\n' "$_nofile_want" > "$_nofile_override" 2>/dev/null || true
        systemctl daemon-reload >/dev/null 2>&1 || true
        _nofile_dirty=1
    fi
    if ! nginx -t >/dev/null 2>&1; then
        _egress_warn "nginx config test failed — leaving existing state"
        return 0
    fi

    # 2. nginx must serve the gateway + loopback BEFORE traffic is steered.
    # Always reload: the rendered config changes across runs (template
    # placeholders, mirror list) while the daemon keeps old config.
    local gw=""
    gw="$(_docker0_gateway)"
    # Bind explicitly (never 0.0.0.0: no public exposure by accident).
    # Restart (not reload) when the FD override changed — rlimits apply
    # at process start only.
    if [ -n "${_nofile_dirty:-}" ] || ! ss -ltn 2>/dev/null | grep -qE "127\.0\.0\.1:${SMSLY_EGRESS_MIRROR_PORT} |${gw//./\\.}:${SMSLY_EGRESS_MIRROR_PORT} "; then
        _egress_log "starting nginx..."
        systemctl enable nginx >/dev/null 2>&1 || true
        systemctl restart nginx >/dev/null 2>&1 || systemctl start nginx >/dev/null 2>&1 || true
        sleep 2
    else
        systemctl reload nginx >/dev/null 2>&1 || systemctl restart nginx >/dev/null 2>&1 || true
        sleep 1
    fi
    if ! ss -ltn 2>/dev/null | grep -q ":${SMSLY_EGRESS_MIRROR_PORT} "; then
        _egress_warn "nginx not listening on ${SMSLY_EGRESS_MIRROR_PORT} after start (see nginx -t / journalctl -u nginx)"
    fi

    # 3. Steer port-80 TCP from the default docker bridge to the shim
    # (PREROUTING covers build containers, OUTPUT covers host-local
    # processes). REDIRECT keeps it local.
    #
    # LOOP-BREAKER (observed live, full platform outage class): the OUTPUT
    # rule also catches nginx's OWN upstream connections (it proxies TO
    # port 80). Without an exemption each shimmed request re-enters the
    # shim recursively until worker_connections/FDs exhaust and EVERYTHING
    # 500s. nginx workers run as the `user` from nginx.conf (www-data on
    # Ubuntu) — their port-80 traffic must leave the host directly.
    _nginx_user="$(grep -E '^[[:space:]]*user[[:space:]]' /etc/nginx/nginx.conf 2>/dev/null | awk '{print $2}' | tr -d ';' | head -n 1)"
    [ -n "$_nginx_user" ] || _nginx_user="www-data"
    if ! iptables -t nat -C OUTPUT -p tcp --dport 80 -m owner --uid-owner "$_nginx_user" -j RETURN >/dev/null 2>&1; then
        iptables -t nat -I OUTPUT 1 -p tcp --dport 80 -m owner --uid-owner "$_nginx_user" -j RETURN 2>/dev/null || \
            _egress_warn "nginx OUTPUT exemption failed — shim will loop on itself"
    fi
    # Scoped to the docker0 subnet: the shim binds 127.0.0.1 + the docker0
    # gateway only, and REDIRECT targets the incoming interface address —
    # an unscoped steer sends every other bridge's :80 to a gateway where
    # nothing listens (UFW DROPs it: silent blackhole). Builds run on
    # docker0, so scoping loses nothing.
    _docker_steer_src="$(_docker0_gateway | cut -d. -f1-2).0.0/16"
    if ! iptables -t nat -C PREROUTING -s "$_docker_steer_src" -p tcp --dport 80 -j REDIRECT --to-port "$SMSLY_EGRESS_MIRROR_PORT" >/dev/null 2>&1; then
        iptables -t nat -A PREROUTING -s "$_docker_steer_src" -p tcp --dport 80 -j REDIRECT --to-port "$SMSLY_EGRESS_MIRROR_PORT" 2>/dev/null || \
            _egress_warn "PREROUTING rule install failed"
    fi
    # Converge: remove the legacy unscoped steer if present (it hijacked
    # :80 from EVERY bridge, including mesh and project networks).
    if iptables -t nat -C PREROUTING -p tcp --dport 80 -j REDIRECT --to-port "$SMSLY_EGRESS_MIRROR_PORT" >/dev/null 2>&1; then
        iptables -t nat -D PREROUTING -p tcp --dport 80 -j REDIRECT --to-port "$SMSLY_EGRESS_MIRROR_PORT" 2>/dev/null || true
    fi
    if ! iptables -t nat -C OUTPUT -p tcp --dport 80 -j REDIRECT --to-port "$SMSLY_EGRESS_MIRROR_PORT" >/dev/null 2>&1; then
        iptables -t nat -A OUTPUT -p tcp --dport 80 -j REDIRECT --to-port "$SMSLY_EGRESS_MIRROR_PORT" 2>/dev/null || \
            _egress_warn "OUTPUT rule install failed"
    fi
    # Converge: local/docker/mesh destinations must bypass the shim.
    # The transparent default server proxies by Host header, so any
    # :80 connection aimed at a container/bridge/mesh IP whose Host
    # does not publicly resolve there (every docker-proxy upstream for
    # published :80 ports, host-to-container diagnostics, Caddy ->
    # traefik:80 wake path probes from the host) 502s instead of
    # passing through (2026-10-05: node :8081 always nginx-502 while
    # container-path traffic was fine). RETURNs are inserted ahead of
    # the REDIRECT above; internet egress still steers.
    for _cidr in 127.0.0.0/8 10.0.0.0/8 172.16.0.0/12 192.168.0.0/16; do
        if ! iptables -t nat -C OUTPUT -d "$_cidr" -p tcp --dport 80 -j RETURN >/dev/null 2>&1; then
            iptables -t nat -I OUTPUT 2 -d "$_cidr" -p tcp --dport 80 -j RETURN 2>/dev/null || \
                _egress_warn "OUTPUT local bypass failed for $_cidr"
        fi
    done

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
