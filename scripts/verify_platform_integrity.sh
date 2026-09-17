#!/bin/bash
# /opt/smsly-hosting/scripts/verify_platform_integrity.sh
#
# Self-heal guard for host-level state that code fixes cannot reach.
# Runs from the platform's startup hooks and can be cronned:
#   5 * * * * /opt/smsly-hosting/scripts/verify_platform_integrity.sh >> /var/log/smsly-integrity.log 2>&1
#
# Covers the two incidents from 2026-08-31:
#   1. registry.crt regenerated without registry.key -> the registry
#      crash-looped on "tls: private key does not match public key"
#      (1372 restarts, every build push/pull failing).
#   2. egress isolation rules must keep internet RETURNs for the NIC
#      names that exist on THIS host (OVH uses ens3).

set -u
INSTALL_DIR="${INSTALL_DIR:-/opt/smsly-hosting}"
CERTS="$INSTALL_DIR/certs"
LOG_PREFIX="[smsly-integrity]"

log() { echo "$LOG_PREFIX $(date -Is) $*"; }

# ── 7. Caddy log volume must stay writable by uid 1000 ──────────────
# The Caddy container runs as uid 1000 and logs to the NAMED caddy_logs
# volume. A root-owned volume makes EVERY `caddy reload` fail with
# "open /var/log/caddy/access.log: permission denied" — routing then
# silently goes stale while the on-disk Caddyfile keeps changing
# (2026-09-12: wildcard site + redirects never went live). Fresh installs
# chown it; this heals hosts that predate the fix.
ensure_caddy_logs_writable() {
    command -v docker >/dev/null 2>&1 || return 0
    local vol
    vol=$(docker volume ls --format '{{.Name}}' 2>/dev/null | grep -E 'caddy_logs$' | head -n 1) || true
    [ -n "$vol" ] || { log "no caddy_logs volume — skipping log-perm check"; return 0; }
    local mnt="" owner=""
    mnt=$(docker volume inspect "$vol" --format '{{.Mountpoint}}' 2>/dev/null) || {
        log "ALERT: cannot inspect volume $vol"; return 0
    }
    owner=$(stat -c '%u:%g' "$mnt" 2>/dev/null) || owner="unknown"
    if [ "$owner" = "1000:1000" ]; then
        log "caddy_logs volume writable (1000:1000)"
        return 0
    fi
    log "ALERT: caddy_logs volume owned by $owner — routing reloads are failing; repairing"
    if docker run --rm -v "$vol:/logs" alpine chown -R 1000:1000 /logs >/dev/null 2>&1; then
        log "caddy_logs ownership repaired to 1000:1000"
    else
        log "ALERT: caddy_logs ownership repair FAILED — future Caddy reloads will fail"
    fi
}

# ── 8. Traefik dynamic dir must exist (weighted canary files) ─────
# The backend writes per-service canary WRR files to the host path
# backing the traefik_dynamic volume (/opt/smsly-hosting/traefik-dynamic,
# mounted into Traefik at /etc/traefik/dynamic). A missing dir breaks the
# bind mount and silently disables every canary split — ensure it here.
ensure_traefik_dynamic_dir() {
    local dir="/opt/smsly-hosting/traefik-dynamic"
    if [ -d "$dir" ]; then
        log "traefik-dynamic dir OK"
        return 0
    fi
    log "ALERT: $dir missing — creating (canary splits need it)"
    if mkdir -p "$dir" 2>/dev/null; then
        log "traefik-dynamic dir created"
    else
        log "ALERT: traefik-dynamic dir creation FAILED — canary splits will fail"
    fi
}

# ── 1. Registry TLS pair ─────────────────────────────────────────────
ensure_registry_pair() {
    local crt="$CERTS/registry.crt" key="$CERTS/registry.key"
    [ -f "$crt" ] && [ -f "$key" ] || { log "registry cert files missing — skipping (install.sh will create)"; return 0; }

    local cmod="" kmod=""
    cmod=$(openssl x509 -in "$crt" -noout -pubkey 2>/dev/null | sha256sum | awk '{print $1}')
    kmod=$(openssl pkey -in "$key" -pubout 2>/dev/null        | sha256sum | awk '{print $1}')
    if [ -n "$cmod" ] && [ "$cmod" = "$kmod" ]; then
        log "registry TLS pair OK"
        return 0
    fi

    log "ALERT: registry cert/key MISMATCH — regenerating matched pair"
    local tmp
    tmp=$(mktemp -d)
    openssl req -newkey rsa:2048 -nodes -keyout "$tmp/registry.key" \
        -x509 -days 3650 -out "$tmp/registry.crt" \
        -subj "/CN=registry" \
        -addext "subjectAltName=DNS:registry,DNS:localhost,IP:127.0.0.1" >/dev/null 2>&1 || {
        log "openssl regeneration FAILED — manual fix required"; rm -rf "$tmp"; return 1
    }
    cp "$crt" "$crt.bak" 2>/dev/null || true
    cp "$key" "$key.bak" 2>/dev/null || true
    mv "$tmp/registry.key" "$key"
    mv "$tmp/registry.crt" "$crt"
    rm -rf "$tmp"
    chmod 644 "$crt"; chmod 600 "$key"
    if docker ps --format '{{.Names}}' | grep -q smsly-hosting-registry-1; then
        docker restart smsly-hosting-registry-1 >/dev/null 2>&1 && log "registry restarted with matched pair"
    fi
}

# ── 2. Egress RETURN rules cover this host's real NICs ───────────────
ensure_egress_nic_rules() {
    command -v iptables >/dev/null 2>&1 || return 0
    # Collect comment-tagged bridges we manage
    local rules="" br="" ifaces="" missing=""
    rules=$(iptables -S DOCKER-USER 2>/dev/null | grep -oP '(?<=-i )br-[0-9a-f]+' | sort -u)
    [ -z "$rules" ] && { log "no smsly egress rules to guard"; return 0; }

    # All interfaces that have a default route (real egress NICs)
    ifaces=$(ip -o route show default 2>/dev/null | awk '{print $5}' | sort -u)
    [ -z "$ifaces" ] && { log "no default route found — skipping NIC guard"; return 0; }

    missing=0
    for br in $rules; do
        for ifc in $ifaces; do
            iptables -C DOCKER-USER -i "$br" -o "$ifc" -m comment --comment smsly-egress-$(echo "$br" | cut -d- -f2) -j RETURN >/dev/null 2>&1 && continue
            # Generic check: any RETURN from this bridge to this NIC
            iptables -S DOCKER-USER | grep -q -- "-i $br -o ${ifc}" && continue
            iptables -I DOCKER-USER 1 -i "$br" -o "$ifc" -m comment \
                --comment "smsly-egress-$(echo "$br" | cut -d- -f2)" -j RETURN 2>/dev/null \
                && log "added missing egress RETURN: $br -> $ifc" && missing=1
        done
    done
    [ "$missing" = "0" ] && log "egress NIC rules OK for: $(echo $ifaces | tr '\n' ' ')"
}

# ── 3. SPIRE (mTLS) containers should stay up if mtls_enabled ────────
ensure_spire_running() {
    command -v docker >/dev/null 2>&1 || return 0
    docker inspect smsly-spire-server --format '{{.State.Status}}' 2>/dev/null | grep -q running || return 0
    # Server is up; make sure the agent is too (restart unless-stopped
    # handles reboots, this catches crash-stopped agents).
    if ! docker inspect smsly-spire-agent --format '{{.State.Status}}' 2>/dev/null | grep -q running; then
        # Agent can't restart itself without a NEW join token; only
        # log loudly so the operator re-runs the mtls deploy endpoint.
        log "ALERT: spire-server running but spire-agent is DOWN — re-run POST /api/v1/mtls/spire/deploy/ to mint a fresh join token"
    else
        log "spire server+agent running"
    fi
}

# ── 3b. Falco must be CAPTURING, not just running ───────────────────
# 2026-09-15: 400+ restarts with status healthy — scap_init died ~15s
# after every start (probe predates kernel 7.x) while the healthcheck
# passed inside each crash window. Alert on the signature; never
# restart here (harden owns the lifecycle, this is the tripwire).
ensure_falco_capturing() {
    command -v docker >/dev/null 2>&1 || return 0
    docker ps --format '{{.Names}}' 2>/dev/null | grep -qx "smsly-falco" || return 0
    local restarts
    restarts=$(docker inspect -f '{{.RestartCount}}' smsly-falco 2>/dev/null || echo 0)
    if [ "${restarts:-0}" -ge 10 ] 2>/dev/null; then
        if docker logs --since 10m smsly-falco 2>/dev/null | grep -q "Initialization issues during scap_init"; then
            log "ALERT: falco crash-looping on scap_init (${restarts} restarts, 0 events captured) — probe incompatible with kernel $(uname -r), bump FALCO_VERSION"
            return 0
        fi
    fi
    log "falco capturing (restarts=${restarts:-?})"
}

# ── 4. Edge Shield lockdown must stay enforced ────────────────────────
# The 80/443 Cloudflare-only firewall is the anti-bypass layer of the
# BGP-hijack defense (deploy_edge_shield). If the rules vanish (reboot
# without persistence, operator flush), re-apply them immediately.
ensure_edge_lockdown() {
    [ -x "$INSTALL_DIR/scripts/cf_origin_lockdown.sh" ] || { log "cf_origin_lockdown.sh missing — skipping"; return 0; }
    if iptables -S INPUT 2>/dev/null | grep -q 'smsly-edge-shield'; then
        log "edge lockdown rules present"
    else
        log "ALERT: edge lockdown rules MISSING — re-applying"
        bash "$INSTALL_DIR/scripts/cf_origin_lockdown.sh" --on >> /dev/null 2>&1 \
            && log "edge lockdown re-applied" \
            || log "ALERT: edge lockdown re-apply FAILED"
    fi
}

# ── 5. Pending-migration check (2026-09-02 root cause) ───────────────
# Migration 0196 was never applied on the VPS → PlatformConfig missing
# a column → ProgrammingError on every ORM query → config loader fell
# to its ghost path → empty domain → Caddyfile lost the platform block.
# This check catches it at the shell level (beat task covers in-process).
ensure_migrations() {
    local backend_container="smsly-hosting-backend-1"
    if ! docker inspect "$backend_container" >/dev/null 2>&1; then
        log "backend container not running — skipping migration check"
        return 0
    fi
    local check_output
    check_output=$(timeout 120 docker exec -e DJANGO_SETTINGS_MODULE=config.settings \
        -w /app "$backend_container" \
        python manage.py migrate --check --noinput 2>&1) || true
    if echo "$check_output" | grep -q "Your models have changes"; then
        log "ALERT: pending migrations detected — applying now"
        timeout 300 docker exec -e DJANGO_SETTINGS_MODULE=config.settings \
            -w /app "$backend_container" \
            python manage.py migrate --noinput 2>&1 | tail -3
        log "migrations applied"
    else
        log "migrations up to date"
    fi
}

# ── 6. Traefik middleware references must resolve (2026-09-11 root cause)
# A router referencing an undefined middleware (e.g. crowdsec-bouncer
# declared on a container Traefik skips via exposedbydefault=false) makes
# Traefik drop EVERY affected router — all user traffic fell through to
# route-fallback 503 with exit 143 across the stack. Traefik logs the
# signature below; alert loudly so the operator fixes labels instead of
# debugging 503s. Detection only — never restart Traefik from here.
ensure_traefik_middlewares() {
    command -v docker >/dev/null 2>&1 || return 0
    docker inspect smsly-hosting-traefik-1 >/dev/null 2>&1 || { log "traefik not running — skipping middleware check"; return 0; }
    local bad
    bad=$(docker logs smsly-hosting-traefik-1 --since 60m 2>&1 | grep -i -e 'middleware.*does not exist' -e 'unknown middleware' | head -n 5) || true
    if [ -n "$bad" ]; then
        log "ALERT: Traefik reports undefined middleware(s) in the last 60m — user routers are being dropped (503 via route-fallback). Fix container labels:"
        echo "$bad" | while IFS= read -r line; do log "ALERT detail: $line"; done
    else
        log "traefik middleware refs OK (no undefined-middleware errors in 60m)"
    fi
}

# ── 6b. Traefik service conflicts must not exist (2026-09-12 root cause)
# Two containers declaring the same traefik.http.services.<name> with
# different definitions (e.g. a replica stamped with server.port only
# while the primary carries healthcheck labels too) makes Traefik drop
# the ENTIRE service ("defined multiple times with different
# configurations") — every request for that host silently falls through
# to route-fallback with a 200, so health monitors stay green while users
# get the wrong content. Detection only — never restart Traefik from here.
ensure_traefik_service_conflicts() {
    command -v docker >/dev/null 2>&1 || return 0
    docker inspect smsly-hosting-traefik-1 >/dev/null 2>&1 || { log "traefik not running — skipping service-conflict check"; return 0; }
    local bad
    bad=$(docker logs smsly-hosting-traefik-1 --since 60m 2>&1 | grep -e 'defined multiple times with different configurations' -e 'the service .* does not exist' | head -n 5) || true
    if [ -n "$bad" ]; then
        log "ALERT: Traefik reports dropped/conflicting service definition(s) in the last 60m — affected app routers are down, traffic is served by route-fallback. Destroy or relabel the conflicting replica container:"
        echo "$bad" | while IFS= read -r line; do log "ALERT detail: $line"; done
    else
        log "traefik service definitions OK (no dropped-service errors in 60m)"
    fi
}

# ── 6c. Edge proxies must join service scoped bridges (2026-09-17 root cause)
# Per-service isolation bridges (smsly-net-<hex>) island apps from the
# edge: the container is healthy but every domain 503s with "no available
# server" because Traefik was never attached. The deploy pipeline attaches
# at creation (network_scope.ensure_scoped_network), but proxy or network
# recreation drops attachments — this self-heals hourly. Intra-bridge
# traffic never traverses DOCKER-USER, so no firewall change is needed.
ensure_edge_on_scoped_networks() {
    command -v docker >/dev/null 2>&1 || return 0
    local edge
    for edge in smsly-hosting-traefik-1 smsly-hosting-caddy-1 traefik caddy; do
        docker inspect "$edge" >/dev/null 2>&1 || continue
        local net
        docker network ls --format '{{.Name}}' 2>/dev/null | grep -E '^smsly-net-[0-9a-f]{4,}$' | while IFS= read -r net; do
            [ -n "$net" ] || continue
            if docker network inspect "$net" 2>/dev/null | grep -q "\"Name\": *\"$edge\""; then
                continue
            fi
            if docker network connect "$net" "$edge" >/dev/null 2>&1; then
                log "attached $edge to scoped bridge $net (was islanded — its apps 503'd until now)"
            else
                log "ALERT: failed to attach $edge to $net — its apps will 503 with no available server"
            fi
        done
    done
    log "scoped-bridge edge attach sweep complete"
}

# ── 8. fail2ban must stay active with resolvable jail logpaths ──────
# 2026-09-13: the caddy-auth/caddy-dos jails pointed at the host path
# /var/log/caddy/access.log, which never exists (compose mounts the NAMED
# caddy_logs volume instead). fail2ban fails closed on a missing logpath
# and takes ALL jails down with it — sshd included. The installer now
# resolves the real volume path; this heals hosts that predate the fix
# (restart only — a broken jail.local is repaired by the next update).
ensure_fail2ban_running() {
    command -v fail2ban-client >/dev/null 2>&1 || { log "fail2ban not installed — skipping"; return 0; }
    if ! systemctl is-active --quiet fail2ban 2>/dev/null; then
        log "ALERT: fail2ban not active — attempting restart"
        systemctl restart fail2ban >/dev/null 2>&1 || true
        sleep 3
        if ! systemctl is-active --quiet fail2ban 2>/dev/null; then
            log "ALERT: fail2ban restart FAILED — likely an unresolvable jail logpath; run install.sh --update to regenerate jail.local"
            return 0
        fi
        log "fail2ban restart recovered the service"
    fi
    local enabled_logpath=""
    enabled_logpath=$(awk '/^\[caddy-auth\]/{injail=1; next} /^\[/{injail=0} injail && /^enabled[[:space:]]*=[[:space:]]*true/{e=1} injail && /^logpath[[:space:]]*=/{lp=$0} END{if(e) print lp}' /etc/fail2ban/jail.local 2>/dev/null) || true
    if [ -n "$enabled_logpath" ]; then
        local logfile=""
        logfile=$(echo "$enabled_logpath" | sed 's/^logpath[[:space:]]*=[[:space:]]*//' | awk '{print $1}')
        if [ -n "$logfile" ] && [ ! -e "$logfile" ]; then
            log "ALERT: caddy-auth jail enabled but logpath $logfile is missing — fail2ban will fail on next restart; run install.sh --update to regenerate jail.local"
        else
            log "fail2ban active (caddy-auth logpath OK)"
        fi
    else
        log "fail2ban active (caddy jails disabled or absent)"
    fi
}

# ── 9. Cloudflare bouncer must stay in sync when enabled ─────────────
# The bouncer pushes bans to Cloudflare account IP lists. If its config
# is missing (or the container died), edge enforcement silently stops
# while the Traefik plugin keeps working — worth an alert, not silence.
ensure_cf_bouncer_running() {
    command -v docker >/dev/null 2>&1 || return 0
    if ! docker ps --format '{{.Names}}' 2>/dev/null | grep -qx "smsly-cloudflare-bouncer"; then
        log "cloudflare bouncer not running (ok when disabled in Settings > Security)"
        return 0
    fi
    local last_pull=""
    last_pull=$(timeout 30 docker exec smsly-crowdsec cscli bouncers list -o json 2>/dev/null | python3 -c "import json,sys; d=json.load(sys.stdin); [print(x.get('last_pull','')) for x in (d if isinstance(d,list) else d.get('bouncers',d)) if str(x.get('name',''))=='cloudflare-bouncer']" 2>/dev/null | head -n 1) || true
    if [ -z "$last_pull" ]; then
        log "ALERT: cloudflare-bouncer registered but never pulled decisions — edge blocking is stale; run install.sh --update"
    else
        log "cloudflare bouncer active (last LAPI pull $last_pull)"
    fi
}

# ── 10. SPIRE socket volumes must not be shadowed by bare decoys ────
# 2026-09-15 incident: every ecosystem sidecar mounted the auto-created
# EMPTY bare volume spire-ecosystem-agent-socket instead of the real
# smsly-spire_spire-ecosystem-agent-socket — Envoy SDS never reached
# agent.sock, no SVID was ever issued, all mTLS-gated deploys failed.
# The resolver fix stops new mounts; this guard heals already-poisoned
# hosts by removing the decoy once nothing references it (AGENTS.md #24).
ensure_spire_volumes_not_shadowed() {
    command -v docker >/dev/null 2>&1 || return 0
    local all_vols
    all_vols=$(docker volume ls --format '{{.Name}}' 2>/dev/null || true)
    [ -z "$all_vols" ] && { log "no docker volumes — skipping spire shadow check"; return 0; }
    local v
    for v in $all_vols; do
        case "$v" in
            *spire-*socket|*spire-*svids) ;;
            *) continue ;;
        esac
        case "$v" in
            smsly-*) continue ;;
        esac
        local namespaced
        namespaced=$(echo "$all_vols" | grep -E "^smsly-[^_]+_${v}$" || true)
        [ -z "$namespaced" ] && continue
        log "ALERT: bare spire volume $v is shadowed by namespaced $namespaced (empty-decoy trap)"
        local users
        users=$(docker ps -aq --filter "volume=$v" 2>/dev/null || true)
        if [ -z "$users" ]; then
            if docker volume rm "$v" >/dev/null 2>&1; then
                log "removed unused shadow decoy volume $v"
            else
                log "ALERT: could not remove shadow decoy volume $v"
            fi
        else
            log "ALERT: shadow decoy $v still mounted by containers — redeploy affected services to pick up $namespaced, then re-run"
        fi
    done
    log "spire volume shadow check complete"
}

# ── 10b. Running sidecars must not mount a bare decoy socket ──────
# Alert-only (the repair-stale-sidecars beat does the healing): a
# running envoy-* sidecar whose /opt/spire/run source is a bare
# spire-* volume is SVID-less — SDS dials an empty dir. Namespaced
# (smsly-*) mounts are healthy.
ensure_sidecar_mounts_current() {
    command -v docker >/dev/null 2>&1 || return 0
    local sidecars
    sidecars=$(docker ps --format '{{.Names}}' 2>/dev/null | grep '^envoy-' || true)
    [ -z "$sidecars" ] && { log "no envoy sidecars — skipping mount check"; return 0; }
    local s
    local src
    for s in $sidecars; do
        src=$(docker inspect "$s" --format '{{range .Mounts}}{{if eq .Destination "/opt/spire/run"}}{{.Source}}|{{.Name}} {{end}}{{end}}' 2>/dev/null || true)
        [ -z "$src" ] && {
            log "ALERT: sidecar $s has no /opt/spire/run mount (SVID-less) — repair-stale-sidecars beat will remount"
            continue
        }
        case "$src" in
            *volumes/smsly-*|smsly-*)
                ;; # namespaced mount — healthy
            *volumes/spire-*|spire-*)
                log "ALERT: sidecar $s mounts bare spire decoy at /opt/spire/run (SVID-less) — repair-stale-sidecars beat will remount"
                ;;
        esac
    done
    log "sidecar mount check complete"
}

# ── 11. open-appsec shadow must track direct Caddy byte-for-byte ───
# Phase-1 WAF rides a loopback shadow port through the attachment
# filter. If the filter ever mangles/blocks legitimate traffic, the
# shadow response diverges from direct Caddy — fail the check LOUDLY
# long before any 80/443 cutover. Skipped when disabled (inert).
ensure_openappsec_shadow_parity() {
    command -v docker >/dev/null 2>&1 || return 0
    docker ps --format '{{.Names}}' 2>/dev/null | grep -qx "smsly-appsec-envoy" || return 0
    local shadow_port
    shadow_port=$(grep -E '^OPENAPPSEC_SHADOW_HTTP_PORT=' "$INSTALL_DIR/.env" 2>/dev/null | cut -d= -f2- || true)
    [ -z "$shadow_port" ] && shadow_port="18081"
    local domain
    domain=$(grep -E '^DOMAIN=' "$INSTALL_DIR/.env" 2>/dev/null | cut -d= -f2- || true)
    [ -z "$domain" ] && domain="localhost"
    # Deterministic probes only (no upstream timing involved):
    #  (a) unknown Host -> Caddy's fast 400, no proxying;
    #  (b) real domain over plain HTTP -> Caddy's 308 to https (we compare
    #      status + Location, never follow it — following leaves the box
    #      through Cloudflare and hangs the check).
    # Baselines are taken INSIDE the Caddy netns (docker exec): host
    # loopback to published :80 is flaky under load (docker-proxy
    # hairpin) while container-to-container is rock solid — and the
    # shadow path under test is container-to-container too.
    # BusyBox wget (Caddy image) has no --max-redirect: it follows the
    # 308, so we parse the FIRST HTTP block (Caddy's own answer) and
    # ignore followed hops; curl never follows without -L, matching it.
    local direct_a
    direct_a=$(timeout 20 docker exec smsly-hosting-caddy-1 wget -S --spider --header "Host: shadow-probe.invalid" http://127.0.0.1:80/ 2>&1 | grep 'HTTP/' | head -n 1 | awk '{print $2}' || true)
    # Shadow fetches retry: the loopback hairpin to the published shadow
    # port flakes under load — one bad attempt must not cry divergence.
    local shadow_a
    shadow_a=$(for _try in 1 2 3; do timeout 15 curl -s -o /dev/null -w '%{http_code}' -H "Host: shadow-probe.invalid" "http://127.0.0.1:$shadow_port/" 2>/dev/null && break || sleep 3; done | tail -n 1)
    local direct_raw
    direct_raw=$(timeout 20 docker exec smsly-hosting-caddy-1 wget -S --spider --header "Host: $domain" http://127.0.0.1:80/health 2>&1 || true)
    local shadow_b
    shadow_b=$(for _try in 1 2 3; do timeout 15 curl -s -o /dev/null -w '%{http_code} %{redirect_url}' -H "Host: $domain" "http://127.0.0.1:$shadow_port/health" 2>/dev/null && break || sleep 3; done | tail -n 1)
    # curl prints 000 on connection failure — normalise to empty so the
    # "not serving" branch (not the "diverges" branch) fires.
    [ "$shadow_a" = "000" ] && shadow_a=""
    case "$shadow_b" in 000*) shadow_b="" ;; esac
    if [ -z "$direct_a" ]; then
        log "openappsec shadow check skipped (Caddy netns unreachable)"
        return 0
    fi
    if [ -z "$shadow_a" ]; then
        log "ALERT: openappsec shadow envoy not serving on 127.0.0.1:$shadow_port — attachment may be down; edge cutover BLOCKED until fixed"
        return 0
    fi
    # Reduce the wget -S dump to "code location" to match curl's format —
    # first HTTP block / first Location only (later ones are followed hops).
    local direct_b_norm
    direct_b_norm="$(echo "$direct_raw" | grep 'HTTP/' | head -n 1 | awk '{print $2}') $(echo "$direct_raw" | grep -i '^  Location:' | head -n 1 | awk '{print $2}')"
    if [ "$direct_a" = "$shadow_a" ] && [ -n "$direct_b_norm" ] && [ "$shadow_b" = "$direct_b_norm" ]; then
        log "openappsec shadow parity OK (filter transparent: probe=$direct_a redirect='$direct_b_norm')"
    else
        log "ALERT: openappsec shadow DIVERGES from direct Caddy (direct probe=$direct_a redirect='$direct_b_norm' vs shadow probe=$shadow_a redirect='$shadow_b') — edge cutover BLOCKED until fixed"
    fi
}

# ── 12. Host memory tuning (KSM + zram) must stay applied ────────────
# KSM merging and zram swap are the idle-minimal half of the resource
# strategy. Both are best-effort (VPS kernels without them skip quietly),
# so this guard heals-or-reports rather than failing: re-run the setup
# script when the knobs exist but are off, log-and-skip otherwise.
ensure_memory_tuning() {
    local setup="$INSTALL_DIR/scripts/setup-memory-tuning.sh"
    # KSM: kernel present but merging off (and not operator-disabled)?
    if [ -f /sys/kernel/mm/ksm/run ] && [ "${SMSLY_DISABLE_KSM:-0}" != "1" ]; then
        if [ "$(cat /sys/kernel/mm/ksm/run 2>/dev/null || echo 0)" != "1" ]; then
            if [ -x "$setup" ] && bash "$setup" >/dev/null 2>&1; then
                log "KSM was off — re-applied via setup-memory-tuning.sh"
            else
                log "ALERT: KSM available but off and re-apply failed"
            fi
        else
            local shared=""
            shared=$(awk '{print $1}' /sys/kernel/mm/ksm/pages_sharing 2>/dev/null || echo 0)
            log "KSM active (sharing ${shared} pages)"
        fi
    else
        log "KSM not applicable (absent kernel support or disabled) — skipping"
    fi
    # zram: unit enabled but no zram swap active (and not disabled)?
    if [ "${SMSLY_DISABLE_ZRAM:-0}" != "1" ] && systemctl is-enabled smsly-memory-tuning.service >/dev/null 2>&1; then
        if ! grep -q '^/dev/zram' /proc/swaps 2>/dev/null; then
            if [ -x "$setup" ] && bash "$setup" >/dev/null 2>&1 && grep -q '^/dev/zram' /proc/swaps 2>/dev/null; then
                log "zram swap was missing — re-applied via setup-memory-tuning.sh"
            else
                log "ALERT: memory-tuning unit enabled but no zram swap active"
            fi
        else
            log "zram swap active"
        fi
    fi
}

# ── 13. Weekly unused-image prune (disk hygiene) ─────────────────────
# `docker image prune -a` removes only images no container references,
# so running services are safe by construction. The 7d `until` filter
# keeps a week of rollback/rebuild cache locally; anything older
# re-pulls on demand from registry:5000 (rollback and the build-skip
# check both pull on miss). Daemon images can't be managed from backend
# tasks (socket-proxy IMAGES=0 denies image endpoints), so this
# host-side sweep is the only automatic path — the manual
# `deployments/prune/` API stays for on-demand deep cleans.
# Marker-gated: the hourly script prunes at most weekly. Override the
# cadence with SMSLY_IMAGE_PRUNE_DAYS (0 disables).
ensure_weekly_image_prune() {
    command -v docker >/dev/null 2>&1 || return 0
    local days="${SMSLY_IMAGE_PRUNE_DAYS:-7}"
    case "$days" in ''|*[!0-9]*) days=7 ;; esac
    if [ "$days" -eq 0 ]; then
        log "image prune disabled (SMSLY_IMAGE_PRUNE_DAYS=0) — skipping"
        return 0
    fi
    local state_dir="$INSTALL_DIR/.state"
    local marker="$state_dir/last-image-prune"
    mkdir -p "$state_dir" 2>/dev/null || true
    if [ -f "$marker" ] && [ -z "$(find "$marker" -mtime "+$((days - 1))" 2>/dev/null)" ]; then
        return 0
    fi
    local out
    out=$(timeout 600 docker image prune -a -f --filter "until=$((days * 24))h" 2>&1) || {
        log "ALERT: weekly image prune failed (exit $?)"
        return 0
    }
    touch "$marker" 2>/dev/null || true
    local reclaimed
    reclaimed=$(printf '%s' "$out" | grep -a -i "reclaimed" | tail -n 1)
    log "weekly image prune complete ${reclaimed:+($reclaimed)}"
}

# ── 14. Celery beat must be dispatching (all hygiene depends on it) ──
# 2026-09-17: after a worker recreate, the new beat couldn't acquire
# redbeat::lock (dead predecessor held it, 25-min TTL) and dispatched
# NOTHING for ~25 min — every periodic cleanup/health task silently
# stopped, with zero errors in the log.
#
# Auto-heal (not just alert) when ALL of these hold, each load-bearing:
#   1. exactly one beat container runs locally — a second local beat
#      means an operator is debugging; never touch the lock then;
#   2. it started >15 min ago — a fresh beat may legitimately wait on a
#      live holder elsewhere (fleet) while converging;
#   3. zero dispatches in 15 min — the symptom;
#   4. the lock TTL proves staleness — a live holder EXTENDS every tick
#      (tick gap <= maxinterval 300s, verified in redbeat source), so a
#      TTL below 1200s means no refresh for 5+ minutes, i.e. the holder
#      is dead by redbeat's own cadence. Missing lock (-2) with silence
#      means a wedged process instead — restart still the fix.
# Recovery is DEL + container restart (fresh boot acquires immediately);
# never delete the lock while another beat container runs.
ensure_beat_dispatching() {
    command -v docker >/dev/null 2>&1 || return 0
    docker inspect smsly-hosting-celery-beat-1 >/dev/null 2>&1 || { log "beat not running — skipping dispatch check"; return 0; }
    local started
    started=$(docker inspect smsly-hosting-celery-beat-1 --format '{{.State.StartedAt}}' 2>/dev/null) || started=""
    if [ -n "$started" ]; then
        local started_epoch
        started_epoch=$(date -d "$started" +%s 2>/dev/null) || started_epoch=""
        if [ -n "$started_epoch" ]; then
            local age
            age=$(( $(date +%s) - started_epoch ))
            if [ "$age" -lt 900 ]; then
                log "beat restarted ${age}s ago — skipping dispatch check (schedules still warming up)"
                return 0
            fi
        fi
    fi
    local count
    count=$(docker logs smsly-hosting-celery-beat-1 --since 15m 2>&1 | grep -a -c "Sending due task") || count=0
    # grep -c prints 0 with exit 1 when nothing matches; normalize.
    case "$count" in ''|*[!0-9]*) count=0 ;; esac
    if [ "$count" -ne 0 ]; then
        log "beat dispatching ($count sends in 15m)"
        return 0
    fi
    local peers
    peers=$(docker ps --format '{{.Names}}' 2>/dev/null | grep -c -E 'celery-beat') || peers=0
    case "$peers" in ''|*[!0-9]*) peers=0 ;; esac
    if [ "$peers" -ne 1 ]; then
        log "ALERT: beat silent 15m but $peers beat containers run locally — refusing auto-heal (possible live peer); inspect redbeat::lock manually"
        return 0
    fi
    local ttl
    ttl=$(_redbeat_lock_ttl)
    case "$ttl" in ''|unknown) ttl="unknown" ;; esac
    if [ "$ttl" = "unknown" ]; then
        log "ALERT: beat silent 15m and lock state unreadable — manual check needed (DEL redbeat::lock on redis db 3, restart beat if stale)"
        return 0
    fi
    # TTL -2 = no lock (beat should hold one — wedged); -1 = persistent
    # (abnormal); 0..1199 = unrefreshed past a full tick gap (dead holder).
    # >= 1200 may be a live fleet peer converging — hands off.
    if [ "$ttl" = "-2" ] || [ "$ttl" = "-1" ] || { [ "$ttl" -ge 0 ] 2>/dev/null && [ "$ttl" -lt 1200 ]; }; then
        log "ALERT: beat silent 15m, stale lock (ttl=$ttl) — releasing lock and restarting beat"
        _redbeat_lock_del
        if docker restart smsly-hosting-celery-beat-1 >/dev/null 2>&1; then
            log "beat restarted after stale-lock release"
        else
            log "ALERT: beat restart FAILED after stale-lock release — manual intervention needed"
        fi
    else
        log "ALERT: beat silent 15m but lock looks live (ttl=$ttl) — possible fleet peer; manual check needed"
    fi
}

# Read redbeat::lock TTL from any redis container (replicas serve the
# replicated key). Prints seconds, -2 (missing), -1 (persistent), or
# "unknown". Never fails the caller.
_redbeat_lock_ttl() {
    local pass
    pass=$(grep -E '^REDIS_PASSWORD=' "$INSTALL_DIR/.env" 2>/dev/null | cut -d= -f2-)
    local container
    for container in $(docker ps --format '{{.Names}}' 2>/dev/null | grep -i -E 'redis' | grep -v -i 'sentinel'); do
        local ttl
        if [ -n "$pass" ]; then
            ttl=$(timeout 10 docker exec "$container" redis-cli -a "$pass" -n 3 TTL redbeat::lock 2>/dev/null | tail -n 1)
        else
            ttl=$(timeout 10 docker exec "$container" redis-cli -n 3 TTL redbeat::lock 2>/dev/null | tail -n 1)
        fi
        case "$ttl" in ''|*[!0-9-]*) continue ;; esac
        printf '%s' "$ttl"
        return 0
    done
    printf 'unknown'
    return 1
}

# Delete redbeat::lock (call only after the staleness gates above pass).
_redbeat_lock_del() {
    local pass
    pass=$(grep -E '^REDIS_PASSWORD=' "$INSTALL_DIR/.env" 2>/dev/null | cut -d= -f2-)
    local container
    for container in $(docker ps --format '{{.Names}}' 2>/dev/null | grep -i -E 'redis' | grep -v -i 'sentinel'); do
        if [ -n "$pass" ]; then
            timeout 10 docker exec "$container" redis-cli -a "$pass" -n 3 DEL redbeat::lock >/dev/null 2>&1 && return 0
        else
            timeout 10 docker exec "$container" redis-cli -n 3 DEL redbeat::lock >/dev/null 2>&1 && return 0
        fi
    done
    return 1
}

ensure_registry_pair
ensure_egress_nic_rules
ensure_spire_running
ensure_falco_capturing
ensure_edge_lockdown
ensure_migrations
ensure_traefik_middlewares
ensure_traefik_service_conflicts
ensure_edge_on_scoped_networks
ensure_caddy_logs_writable
ensure_traefik_dynamic_dir
ensure_spire_volumes_not_shadowed
ensure_sidecar_mounts_current
ensure_cf_bouncer_running
ensure_fail2ban_running
ensure_openappsec_shadow_parity
ensure_memory_tuning
ensure_weekly_image_prune
ensure_beat_dispatching
log "integrity check complete"
