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

# ── 1. Registry TLS pair ─────────────────────────────────────────────
ensure_registry_pair() {
    local crt="$CERTS/registry.crt" key="$CERTS/registry.key"
    [ -f "$crt" ] && [ -f "$key" ] || { log "registry cert files missing — skipping (install.sh will create)"; return 0; }

    local cmod kmod
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
    local rules br ifaces missing
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

ensure_registry_pair
ensure_egress_nic_rules
ensure_spire_running
ensure_edge_lockdown
log "integrity check complete"
