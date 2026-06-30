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

if [ "$EUID" -ne 0 ]; then
    echo "ERROR: Must run as root"
    exit 1
fi

if [ ! -e /dev/kvm ]; then
    echo "ERROR: KVM not available (/dev/kvm missing)."
    echo "  Enable virtualization in BIOS/UEFI and load the kvm module:"
    echo "    modprobe kvm_intel   # Intel"
    echo "    modprobe kvm_amd     # AMD"
    exit 1
fi

KATA_VERSION="${KATA_VERSION:-3.14.0}"
ARCH="$(uname -m)"
case "$ARCH" in
    x86_64)  KATA_ARCH="amd64" ;;
    aarch64) KATA_ARCH="arm64" ;;
    *)       echo "ERROR: Unsupported architecture: $ARCH"; exit 1 ;;
esac

KATA_TARBALL="kata-static-${KATA_VERSION}-${KATA_ARCH}.tar.xz"
KATA_URL="https://github.com/kata-containers/kata-containers/releases/download/${KATA_VERSION}/${KATA_TARBALL}"

echo "=== Installing Kata Containers ${KATA_VERSION} ==="

cd /tmp
curl -fsSL "$KATA_URL" -o "$KATA_TARBALL"

echo "  Extracting..."
tar -xJf "$KATA_TARBALL" -C /

# Register with Docker
DAEMON_JSON="/etc/docker/daemon.json"
if [ ! -f "$DAEMON_JSON" ]; then
    echo '{}' > "$DAEMON_JSON"
fi

if command -v kata-runtime &>/dev/null; then
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
    exit 1
fi

# Set up systemd
if command -v systemctl &>/dev/null; then
    systemctl daemon-reload
    systemctl restart docker
    echo "  Docker restarted with kata-runtime support"
fi

sleep 3

# Verify
if kata-runtime kata-check --verbose 2>/dev/null; then
    echo "=== Kata Containers ${KATA_VERSION} installed successfully ==="
else
    echo "WARNING: kata-check reported issues. KVM and hardware nesting required."
    echo "  Check: kata-runtime kata-check --verbose"
fi
