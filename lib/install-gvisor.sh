#!/bin/bash
# ============================================================
# SMSLY Hosting — gVisor (runsc) Runtime Installer
#
# Installs gVisor's user-space kernel as a Docker runtime.
# No KVM required. Works on any x86_64/arm64 Linux host.
#
# Usage: sudo bash lib/install-gvisor.sh
# ============================================================
set -euo pipefail

main() {
    local rc=0

    command -v runsc  && echo "  runsc already installed — skipping" && return 0

    local ARCH
    ARCH="$(uname -m)"
    case "$ARCH" in
        x86_64)  ARCH="amd64" ;;
        aarch64) ARCH="arm64" ;;
        *)       echo "ERROR: Unsupported architecture: $ARCH"; return 1 ;;
    esac

    echo "=== Installing gVisor (runsc) ==="

    # ---- Prefer apt (reliable) over the legacy download bucket (often dead) ----
    _install_via_apt() {
        apt-get update -qq
        apt-get install -y -qq apt-transport-https ca-certificates curl gnupg 
        curl -fsSL https://gvisor.dev/archive.key | gpg --dearmor -o /usr/share/keyrings/gvisor-archive-keyring.gpg 
        echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/gvisor-archive-keyring.gpg] https://storage.googleapis.com/gvisor/releases release main" \
            > /etc/apt/sources.list.d/gvisor.list
        apt-get update -qq
        apt-get install -y -qq runsc
        command -v runsc 
    }

    if _install_via_apt; then
        echo "  Installed via apt"
        SKIP_DOWNLOAD=true
    fi

    # ---- Fall back to direct download if apt failed (legacy bucket) ----
    download_via_curl() {
        local base_url="$1"
        local tmp
        tmp="$(mktemp -d)"

        curl -fsSL "${base_url}/runsc" -o "${tmp}/runsc" || return 1
        curl -fsSL "${base_url}/runsc.sha512" -o "${tmp}/runsc.sha512" || return 1
        (cd "$tmp" && sha512sum -c runsc.sha512) || return 1
        chmod +x "${tmp}/runsc"
        mv "${tmp}/runsc" /usr/local/bin/runsc

        # containerd shim (optional — best-effort)
        if curl -fsSL "${base_url}/containerd-shim-runsc-v1" -o "${tmp}/containerd-shim-runsc-v1" && \
           curl -fsSL "${base_url}/containerd-shim-runsc-v1.sha512" -o "${tmp}/containerd-shim-runsc-v1.sha512"; then
            (cd "$tmp" && sha512sum -c containerd-shim-runsc-v1.sha512) && {
                chmod +x "${tmp}/containerd-shim-runsc-v1"
                mv "${tmp}/containerd-shim-runsc-v1" /usr/local/bin/containerd-shim-runsc-v1
            }
        fi

        rm -rf "$tmp"
        return 0
    }

    if [ -z "${SKIP_DOWNLOAD:-}" ]; then
        for url in \
            "https://storage.googleapis.com/gvisor/releases/release/latest/${ARCH}" \
            "https://storage.googleapis.com/gvisor/releases/nightly/latest/${ARCH}" \
            "https://storage.googleapis.com/gvisor/releases/master/latest/${ARCH}"; do
            if download_via_curl "$url"; then
                echo "  Installed from ${url}"
                SKIP_DOWNLOAD=true
                break
            fi
        done
    fi

    command -v runsc  || { echo "ERROR: gVisor installation failed"; return 1; }

    # ---- Docker runtime registration ----
    DAEMON_JSON="/etc/docker/daemon.json"
    if [ ! -f "$DAEMON_JSON" ]; then
        echo '{}' > "$DAEMON_JSON"
    fi

    if ! grep -q '"runsc"' "$DAEMON_JSON" ; then
        python3 -c "
import json
with open('$DAEMON_JSON') as f:
    cfg = json.load(f)
cfg.setdefault('runtimes', {})['runsc'] = {
    'path': '/usr/local/bin/runsc'
}
with open('$DAEMON_JSON', 'w') as f:
    json.dump(cfg, f, indent=2)
"
        echo "  Added runsc runtime to Docker daemon.json"
    fi

    # Restart Docker (only if runsc runtime not already registered)
    if command -v systemctl ; then
        systemctl daemon-reload
        if grep -q '"runsc"' /etc/docker/daemon.json ; then
            echo "  runsc already registered in daemon.json — skipping Docker restart"
        else
            systemctl restart docker
            echo "  Docker restarted with runsc support"
        fi
    fi

    # Verify
    sleep 3
    if runsc --version ; then
        echo "=== gVisor (runsc) installed successfully ==="
    else
        echo "ERROR: runsc verification failed"
        return 1
    fi
}

[ "${BASH_SOURCE[0]}" = "$0" ] && main "$@"
