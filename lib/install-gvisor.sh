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

GVISOR_VERSION="${GVISOR_VERSION:-latest}"
ARCH="$(uname -m)"
case "$ARCH" in
    x86_64)  ARCH="amd64" ;;
    aarch64) ARCH="arm64" ;;
    *)       echo "ERROR: Unsupported architecture: $ARCH"; exit 1 ;;
esac

GVISOR_URL="https://storage.googleapis.com/gvisor/releases/release/${GVISOR_VERSION}/${ARCH}"

echo "=== Installing gVisor (runsc) ${GVISOR_VERSION} ==="

# Download runsc binary
cd /tmp
curl -fsSL "${GVISOR_URL}/runsc" -o runsc
curl -fsSL "${GVISOR_URL}/runsc.sha512" -o runsc.sha512
sha512sum -c runsc.sha512 || { echo "ERROR: Checksum verification failed"; exit 1; }
chmod +x runsc
mv runsc /usr/local/bin/runsc

# Register with Docker
if [ ! -f /etc/docker/runsc.json ]; then
    cat > /etc/docker/runsc.json <<'DOCKEREOF'
{
    "runtimes": {
        "runsc": {
            "path": "/usr/local/bin/runsc",
            "runtimeArgs": [
                "--platform=kvm"
            ]
        }
    }
}
DOCKEREOF
    echo "  Created /etc/docker/runsc.json"
fi

# Merge into daemon.json if not already present
DAEMON_JSON="/etc/docker/daemon.json"
if [ ! -f "$DAEMON_JSON" ]; then
    echo '{}' > "$DAEMON_JSON"
fi

if ! grep -q '"runsc"' "$DAEMON_JSON" 2>/dev/null; then
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

# Install containerd-shim-runsc-v1 for proper containerd integration
cd /tmp
curl -fsSL "${GVISOR_URL}/containerd-shim-runsc-v1" -o containerd-shim-runsc-v1
curl -fsSL "${GVISOR_URL}/containerd-shim-runsc-v1.sha512" -o containerd-shim-runsc-v1.sha512
sha512sum -c containerd-shim-runsc-v1.sha512 || { echo "ERROR: containerd-shim checksum verification failed"; exit 1; }
chmod +x containerd-shim-runsc-v1
mv containerd-shim-runsc-v1 /usr/local/bin/containerd-shim-runsc-v1

# Set up systemd mount for /etc/docker/daemon.json
if command -v systemctl &>/dev/null; then
    systemctl daemon-reload
    systemctl restart docker
    echo "  Docker restarted with runsc support"
fi

# Verify
sleep 3
if runsc --version &>/dev/null; then
    echo "=== gVisor (runsc) installed successfully ==="
else
    echo "ERROR: runsc verification failed"
    exit 1
fi
