#!/bin/bash
# lib/tiers_sleep.sh — install the tier sleep/wake mechanism:
# scripts/smsly-tier.sh, the smsly-tier@ units, and tiers.d definitions.
# Sourced (not executed) by install/update flows.
#
# Tiers are OPT-IN per host: this installs the mechanism and enables the
# smsly-paas.target parent only. No tier is enabled or started here — a
# fresh install behaves exactly as before until the operator opts in
# (see infrastructure/systemd/README.md).
#
# Warn loudly, never abort the update (taste: installer never blocks).
# shellcheck disable=SC2317

_smsly_tiers_log() {
    local level="$1"; shift
    echo -e "${BLUE:-}  [tiers:$level] $*${NC:-}" || echo "  [tiers:$level] $*"
}

_smsly_tiers_warn() {
    echo -e "${YELLOW:-}  ⚠ tiers: $*${NC:-}" || echo "  ⚠ tiers: $*"
}

# Install script + units + tier definitions. Never fails the update.
smsly_tiers_install() {
    local src_dir="${INSTALL_DIR:-/opt/smsly-hosting}"
    local fail=0

    if [ -f "$src_dir/scripts/smsly-tier.sh" ]; then
        # The script ships in the repo checkout — just ensure it is
        # executable (git pulls do not always preserve the bit).
        chmod 0755 "$src_dir/scripts/smsly-tier.sh" 2>/dev/null || true
    else
        _smsly_tiers_warn "scripts/smsly-tier.sh missing in repo — skipping"
        return 0
    fi

    local unit
    for unit in smsly-tier@.service smsly-tier-request@.path smsly-tier-autowake@.timer smsly-paas.target smsly-idle.target; do
        if [ -f "$src_dir/infrastructure/systemd/$unit" ]; then
            cp "$src_dir/infrastructure/systemd/$unit" "/etc/systemd/system/$unit" 2>/dev/null || {
                _smsly_tiers_warn "could not install $unit"
                fail=1
            }
        else
            _smsly_tiers_warn "unit source missing: $unit — skipping"
        fi
    done

    if [ -d "$src_dir/infrastructure/systemd/tiers" ]; then
        mkdir -p /etc/smsly/tiers.d || true
        install -m 0644 "$src_dir"/infrastructure/systemd/tiers/*.conf /etc/smsly/tiers.d/ 2>/dev/null || {
            _smsly_tiers_warn "could not install tiers.d definitions"
            fail=1
        }
    else
        _smsly_tiers_warn "tiers.d source dir missing — skipping definitions"
    fi

    mkdir -p /var/lib/smsly/tiers /run/smsly/tier-wake || true
    chmod 0755 /run/smsly/tier-wake 2>/dev/null || true

    systemctl daemon-reload 2>/dev/null || true
    # Parent target only — enables the mechanism, starts nothing.
    systemctl enable smsly-paas.target 2>/dev/null || \
        _smsly_tiers_warn "enable smsly-paas.target failed (non-fatal)"

    if [ "$fail" -eq 0 ]; then
        _smsly_tiers_log info "tier sleep mechanism installed (opt-in per host, nothing started)"
    fi
    return 0
}
