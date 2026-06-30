#!/bin/bash
# ============================================================
# SMSLY Hosting — Cosign Image Verification
# Verifies that an image pulled from the registry carries a
# valid Cosign signature from the platform CI pipeline.
#
# Usage:
#   source scripts/cosign-verify.sh
#   cosign_verify_image "registry:5000/smsly/backend:latest"
# ============================================================
set -euo pipefail

COSIGN_BINARY=""
COSIGN_VERSION="v2.4.1"

_cosign_ensure_binary() {
    if command -v cosign &>/dev/null; then
        COSIGN_BINARY="cosign"
        return 0
    fi
    local cosign_path="/usr/local/bin/cosign"
    if [ -x "$cosign_path" ]; then
        COSIGN_BINARY="$cosign_path"
        return 0
    fi
    echo "[cosign] Installing cosign ${COSIGN_VERSION}..."
    local arch
    arch="$(uname -m)"
    case "$arch" in
        x86_64)  arch="amd64" ;;
        aarch64) arch="arm64" ;;
        *)       echo "[cosign] ERROR: unsupported architecture: $arch"; return 1 ;;
    esac
    local url="https://github.com/sigstore/cosign/releases/download/${COSIGN_VERSION}/cosign-linux-${arch}"
    curl -fsSL "$url" -o "$cosign_path" || {
        echo "[cosign] ERROR: failed to download cosign"
        return 1
    }
    chmod +x "$cosign_path"
    COSIGN_BINARY="$cosign_path"
    echo "[cosign] Installed cosign ${COSIGN_VERSION}"
}

_cosign_get_repo() {
    local repo_url
    repo_url="$(git -C /opt/smsly-hosting remote get-url origin 2>/dev/null || echo "")"
    if [ -z "$repo_url" ]; then
        repo_url="https://github.com/SMSLYCLOUD/smsly-hosting"
    fi
    echo "$repo_url" | sed 's|^https://github.com/||' | sed 's|\.git$||'
}

cosign_verify_image() {
    local image="$1"
    if [ -z "$image" ]; then
        echo "[cosign] ERROR: no image specified"
        return 1
    fi

    _cosign_ensure_binary || return 0

    local repo_identity
    repo_identity="$(_cosign_get_repo)"
    local issuer="https://token.actions.githubusercontent.com"
    local identity="https://github.com/${repo_identity}/.github/workflows/deploy.yml@refs/heads/main"

    echo "[cosign] Verifying $image..."
    if "$COSIGN_BINARY" verify \
        --certificate-oidc-issuer "$issuer" \
        --certificate-identity "$identity" \
        "$image" 2>/dev/null; then
        echo "[cosign] ✓ Signature verified for $image"
        return 0
    fi

    # Try the cosign-sign workflow identity as fallback
    identity="https://github.com/${repo_identity}/.github/workflows/cosign-sign.yml@refs/heads/main"
    if "$COSIGN_BINARY" verify \
        --certificate-oidc-issuer "$issuer" \
        --certificate-identity "$identity" \
        "$image" 2>/dev/null; then
        echo "[cosign] ✓ Signature verified for $image"
        return 0
    fi

    echo "[cosign] ⚠ WARNING: Could not verify signature for $image (non-fatal)"
    return 1
}
