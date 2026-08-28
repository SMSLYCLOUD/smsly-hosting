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

    python3 -c "
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
"

    if command -v systemctl ; then
        systemctl daemon-reload
        systemctl restart docker
        echo "  Docker restarted with kata-runtime support"
        sleep 3
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
        KATA_VERSION="$(curl -fsSL -o /dev/null -w '%{url_effective}' \
            "https://github.com/kata-containers/kata-containers/releases/latest"  \
            | sed 's|.*/||' || true)"
        KATA_VERSION="${KATA_VERSION:-3.14.0}"
    fi

    local KATA_TARBALL="kata-static-${KATA_VERSION}-${KATA_ARCH}.tar.xz"
    local KATA_URL="https://github.com/kata-containers/kata-containers/releases/download/${KATA_VERSION}/${KATA_TARBALL}"

    echo "=== Installing Kata Containers ${KATA_VERSION} ==="

    cd /tmp
    echo "  Downloading ${KATA_URL}..."
    if ! curl -fsSL "$KATA_URL" -o "$KATA_TARBALL"; then
        echo "ERROR: Failed to download Kata tarball"
        echo "  URL: $KATA_URL"
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
        systemctl daemon-reload
        # Only restart Docker if daemon.json actually changed (avoids killing containers on re-run)
        if python3 -c "
import json
with open('/etc/docker/daemon.json') as f:
    cfg = json.load(f)
runtimes = cfg.get('runtimes', {})
if 'kata-runtime' in runtimes and runtimes['kata-runtime'].get('path', '').strip():
    exit(0)
else:
    exit(1)
" ; then
            echo "  Docker already has kata-runtime registered — skipping restart"
        else
            systemctl restart docker
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

[ "${BASH_SOURCE[0]}" = "$0" ] && main "$@"
