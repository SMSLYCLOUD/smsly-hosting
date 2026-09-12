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
    check_output=$(docker exec -e DJANGO_SETTINGS_MODULE=config.settings \
        -w /app "$backend_container" \
        python manage.py migrate --check --noinput 2>&1) || true
    if echo "$check_output" | grep -q "Your models have changes"; then
        log "ALERT: pending migrations detected — applying now"
        docker exec -e DJANGO_SETTINGS_MODULE=config.settings \
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

ensure_registry_pair
ensure_egress_nic_rules
ensure_spire_running
ensure_edge_lockdown
ensure_migrations
ensure_traefik_middlewares
ensure_caddy_logs_writable
log "integrity check complete"
