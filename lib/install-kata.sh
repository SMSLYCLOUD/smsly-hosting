#!/bin/bash
# ============================================================
# SMSLY Hosting — Kata Containers Runtime Installer
#
# Installs Kata Containers as a Docker runtime for VM-level
# isolation. Requires KVM (/dev/kvm) on the host.
#
# Usage: sudo bash lib/install-kata.sh
# ============================================================
set -euo pipefail

FORCE_DOCKER_RESTART="${FORCE_DOCKER_RESTART:-false}"
for _arg in "$@"; do
    [ "$_arg" = "--restart-docker" ] && FORCE_DOCKER_RESTART=true
done
unset _arg

_ensure_docker_kata_registration() {
    local kata_bin
    kata_bin="$(command -v kata-runtime 2>/dev/null || true)"
    if [ -z "$kata_bin" ]; then
        echo "ERROR: kata-runtime binary not found, cannot register with Docker"
        return 1
    fi

    local DAEMON_JSON="/etc/docker/daemon.json"
    if [ ! -f "$DAEMON_JSON" ]; then
        echo '{}' > "$DAEMON_JSON"
    fi

    local _daemon_before=""
    _daemon_before="$(cat "$DAEMON_JSON" 2>/dev/null || echo '')"

    local _kata_reg_out=""
    _kata_reg_out="$(python3 -c "
import json

daemon = '$DAEMON_JSON'
kata_path = '$kata_bin'

with open(daemon) as f:
    cfg = json.load(f)

runtimes = cfg.setdefault('runtimes', {})
current = runtimes.get('kata-runtime', {})
new_entry = {'path': kata_path}

if current != new_entry:
    runtimes['kata-runtime'] = new_entry
    with open(daemon, 'w') as f:
        json.dump(cfg, f, indent=2)
    print(f'  Registered kata-runtime at {kata_path} in {daemon}')
else:
    print(f'  kata-runtime already registered correctly at {kata_path}')
    ")"

    local _daemon_after=""
    _daemon_after="$(cat "$DAEMON_JSON" 2>/dev/null || echo '')"

    # Restart Docker only if registration changed daemon.json — an
    # unconditional restart kills all running containers on every re-run.
    if [ "$_daemon_before" = "$_daemon_after" ] || echo "$_kata_reg_out" | grep -q "already registered correctly"; then
        echo "  Docker restart skipped (kata-runtime registration unchanged)"
    elif command -v systemctl >/dev/null 2>&1; then
        timeout 30 systemctl daemon-reload
        if [ "$FORCE_DOCKER_RESTART" != "true" ] && command -v docker >/dev/null 2>&1 && docker ps -q 2>/dev/null | grep -q .; then
            echo "  WARN: daemon.json changed but containers are running — skipping live Docker restart (pass --restart-docker to apply now)"
        else
            timeout 120 systemctl restart docker
            echo "  Docker restarted with kata-runtime support"
            sleep 3
        fi
    fi
}

main() {
    [ "$EUID" -eq 0 ] || { echo "ERROR: Must run as root"; return 1; }
    if command -v kata-runtime ; then
        echo "  kata-runtime already installed at $(command -v kata-runtime) — ensuring Docker registration"
        _ensure_docker_kata_registration
        return 0
    fi
    [ -e /dev/kvm ] || {
        echo "  KVM not available (/dev/kvm missing) — skipping Kata Containers."
        echo "  gVisor (runsc) will be used for container sandboxing instead."
        echo "  To enable Kata later: enable virtualization in BIOS, then run:"
        echo "    modprobe kvm_intel   # Intel"
        echo "    modprobe kvm_amd     # AMD"
        echo "    sudo bash lib/install-kata.sh"
        return 0
    }

    local ARCH
    ARCH="$(uname -m)"
    case "$ARCH" in
        x86_64)  KATA_ARCH="amd64" ;;
        aarch64) KATA_ARCH="arm64" ;;
        *)       echo "ERROR: Unsupported architecture: $ARCH"; return 1 ;;
    esac

    local KATA_VERSION="${KATA_VERSION:-latest}"
    if [ "$KATA_VERSION" = "latest" ]; then
        KATA_VERSION="$(curl -fsSL --connect-timeout 15 --max-time 60 -o /dev/null -w '%{url_effective}' \
            "https://github.com/kata-containers/kata-containers/releases/latest"  \
            | sed 's|.*/||' || true)"
        KATA_VERSION="${KATA_VERSION:-3.14.0}"
    fi

    local KATA_TARBALL="kata-static-${KATA_VERSION}-${KATA_ARCH}.tar.xz"
    local KATA_URL="https://github.com/kata-containers/kata-containers/releases/download/${KATA_VERSION}/${KATA_TARBALL}"

    echo "=== Installing Kata Containers ${KATA_VERSION} ==="

    cd /tmp
    echo "  Downloading ${KATA_URL}..."
    if ! curl -fsSL --connect-timeout 15 --max-time 300 "$KATA_URL" -o "$KATA_TARBALL"; then
        echo "ERROR: Failed to download Kata tarball"
        echo "  URL: $KATA_URL"
        return 1
    fi

    local KATA_SUMS_URL="https://github.com/kata-containers/kata-containers/releases/download/${KATA_VERSION}/SHA256SUMS"
    local _kata_verified=0
    if [ -n "${KATA_TARBALL_SHA256:-}" ]; then
        if echo "${KATA_TARBALL_SHA256}  ${KATA_TARBALL}" | sha256sum -c - >/dev/null 2>&1; then
            _kata_verified=1
        else
            echo "ERROR: Kata tarball SHA256 verification failed against KATA_TARBALL_SHA256"
            rm -f "$KATA_TARBALL"
            return 1
        fi
    elif curl -fsSL --connect-timeout 15 --max-time 60 "$KATA_SUMS_URL" -o "${KATA_TARBALL}.sha256sums" 2>/dev/null; then
        local _expected_sha=""
        _expected_sha="$(grep -E "[[:space:]](\./)?${KATA_TARBALL}\$" "${KATA_TARBALL}.sha256sums" | awk '{print $1}' | head -n 1 || true)"
        rm -f "${KATA_TARBALL}.sha256sums"
        if [ -n "$_expected_sha" ]; then
            if echo "${_expected_sha}  ${KATA_TARBALL}" | sha256sum -c - >/dev/null 2>&1; then
                _kata_verified=1
            else
                echo "ERROR: Kata tarball SHA256 verification failed against release SHA256SUMS"
                rm -f "$KATA_TARBALL"
                return 1
            fi
        fi
    fi
    if [ "$_kata_verified" -ne 1 ] && [ "${SMSLY_ALLOW_UNVERIFIED_KATA:-0}" != "1" ]; then
        echo "ERROR: Refusing unverified root tar extraction for ${KATA_TARBALL} (set KATA_TARBALL_SHA256 or SMSLY_ALLOW_UNVERIFIED_KATA=1)"
        rm -f "$KATA_TARBALL"
        return 1
    fi

    echo "  Validating tarball contents..."
    if tar -tJf "$KATA_TARBALL" | grep -qE '^/|^\.\./|\.\./'; then
        echo "ERROR: tarball contains unsafe paths (absolute or path traversal)"
        return 1
    fi

    echo "  Extracting..."
    tar -xJf "$KATA_TARBALL" -C /

    local DAEMON_JSON="/etc/docker/daemon.json"
    if [ ! -f "$DAEMON_JSON" ]; then
        echo '{}' > "$DAEMON_JSON"
    fi

    if command -v kata-runtime ; then
        local KATA_PATH
        KATA_PATH="$(command -v kata-runtime)"
        local _kata_daemon_before=""
        _kata_daemon_before="$(cat "$DAEMON_JSON" 2>/dev/null || echo '')"
        python3 -c "
import json, sys
with open('$DAEMON_JSON') as f:
    cfg = json.load(f)
cfg.setdefault('runtimes', {})['kata-runtime'] = {
    'path': '$KATA_PATH'
}
with open('$DAEMON_JSON', 'w') as f:
    json.dump(cfg, f, indent=2)
"
        echo "  Added kata-runtime to Docker daemon.json"
    else
        echo "ERROR: kata-runtime binary not found after extraction"
        return 1
    fi

    if command -v systemctl ; then
        timeout 30 systemctl daemon-reload
        # Only restart Docker if daemon.json actually changed (avoids killing
        # containers on re-run; the old check ran after the write, so it
        # always saw the new entry and skipped the needed restart).
        if [ "$(cat "$DAEMON_JSON" 2>/dev/null || echo '')" = "$_kata_daemon_before" ]; then
            echo "  Docker already has kata-runtime registered — skipping restart"
        elif [ "$FORCE_DOCKER_RESTART" != "true" ] && command -v docker >/dev/null 2>&1 && docker ps -q 2>/dev/null | grep -q .; then
            echo "  WARN: daemon.json changed but containers are running — skipping live Docker restart (pass --restart-docker to apply now)"
        else
            timeout 120 systemctl restart docker
            echo "  Docker restarted with kata-runtime support"
        fi
    fi

    sleep 3

    if kata-runtime kata-check --verbose ; then
        echo "=== Kata Containers ${KATA_VERSION} installed successfully ==="
    else
        echo "WARNING: kata-check reported issues. KVM and hardware nesting required."
        echo "  Check: kata-runtime kata-check --verbose"
    fi
}

# NOTE: `if` (not `&&`) so sourcing this file under `set -e` never kills
# the parent shell — the false branch exits 0.
if [ "${BASH_SOURCE[0]}" = "$0" ]; then
    main "$@"
fi
