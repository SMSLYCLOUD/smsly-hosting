#!/bin/bash

_harden_trivy_bootstrap() {
    if command -v trivy ; then
        return 0  # already installed
    fi

    _harden_log info "Installing Trivy vulnerability scanner..."
    local trivy_version="v0.76.2"
    local arch="" expected_deb_sha="" expected_tgz_sha=""
    arch="$(uname -m)"
    case "$arch" in
        x86_64)
            arch="64bit"
            expected_deb_sha="7edaf64c8a2a0ae02137f776c6e56adf285fc36ec76b045274afdc462f20f092"
            expected_tgz_sha="bc0fc726e017b8927a62b2d3531d7a84ee01769daf5c99ed866dced54089fdbf"
            ;;
        aarch64)
            arch="ARM64"
            expected_deb_sha="0c576cd78352a2084435d393ea31565a3637cc129dd052c422f1e72fd678ee76"
            expected_tgz_sha="ab9d51f86e6a5fb936f605faff6b593ac51bc378fff4d3e8b9d58358b9c367c3"
            ;;
        *)       _harden_log warn "Trivy — unsupported architecture: $arch"; return 1 ;;
    esac

    local deb_url="https://github.com/aquasecurity/trivy/releases/download/${trivy_version}/trivy_${trivy_version#v}_Linux-${arch}.deb"
    local tmp_deb=""
    tmp_deb="$(mktemp /tmp/trivy.XXXXXX.deb)"

    # Attempt 1: Direct DEB download with pinned SHA256 verification
    if curl --retry 3 --retry-delay 2 --connect-timeout 15 -fsSL "$deb_url" -o "$tmp_deb" ; then
        if printf '%s  %s\n' "$expected_deb_sha" "$tmp_deb" | sha256sum -c - >/dev/null 2>&1; then
            if ! dpkg -i "$tmp_deb" ; then
                apt-get install -f -y  || true
                dpkg -i "$tmp_deb"  || true
            fi
        else
            _harden_log warn "Trivy DEB checksum mismatch — rejecting unverified artifact"
        fi
        rm -f "$tmp_deb"
    else
        rm -f "$tmp_deb"
        _harden_log info "Direct DEB download failed — trying official verified tarball and APT repo..."
    fi

    # Attempt 2: Direct tar.gz release download with pinned SHA256 verification
    if ! command -v trivy >/dev/null 2>&1; then
        local tgz_url="https://github.com/aquasecurity/trivy/releases/download/${trivy_version}/trivy_${trivy_version#v}_Linux-${arch}.tar.gz"
        local tmp_tgz="" tmp_dir=""
        tmp_tgz="$(mktemp /tmp/trivy.XXXXXX.tar.gz)"
        tmp_dir="$(mktemp -d /tmp/trivy-extract.XXXXXX)"
        if curl --retry 3 --retry-delay 2 --connect-timeout 15 -fsSL "$tgz_url" -o "$tmp_tgz"; then
            if printf '%s  %s\n' "$expected_tgz_sha" "$tmp_tgz" | sha256sum -c - >/dev/null 2>&1; then
                tar -xzf "$tmp_tgz" -C "$tmp_dir" trivy >/dev/null 2>&1 && \
                    install -m 0755 "$tmp_dir/trivy" /usr/local/bin/trivy >/dev/null 2>&1 || true
            else
                _harden_log warn "Trivy tarball checksum mismatch — rejecting unverified artifact"
            fi
        fi
        rm -rf "$tmp_tgz" "$tmp_dir"
    fi

    # Attempt 3: Official GPG-signed APT Repository fallback
    if ! command -v trivy ; then
        apt-get update -qq  || true
        if ! apt-get install -y trivy ; then
            if command -v gpg ; then
                curl --retry 2 --connect-timeout 10 -fsSL https://aquasecurity.github.io/trivy-repo/deb/public.key  | gpg --dearmor -o /usr/share/keyrings/trivy.gpg  || true
                echo "deb [signed-by=/usr/share/keyrings/trivy.gpg] https://aquasecurity.github.io/trivy-repo/deb $(lsb_release -sc  || echo stable) main" > /etc/apt/sources.list.d/trivy.list  || true
                apt-get update -qq  || true
                apt-get install -y trivy  || true
            fi
        fi
    fi

    if command -v trivy ; then
        _harden_log ok "Trivy installed successfully"
        return 0
    fi
    _harden_log warn "Trivy — download and installation fallbacks failed"
    return 1
}

_harden_trivy_verify() {
    if command -v trivy ; then
        local ver
        ver="$(trivy --version  | head -1 || true)"
        _harden_log ok "Trivy available: ${ver}"
        return 0
    fi
    _harden_log warn "Trivy — not installed (image vulnerability scanning unavailable)"
    return 1
}
