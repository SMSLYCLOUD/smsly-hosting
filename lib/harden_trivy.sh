#!/bin/bash

_harden_trivy_bootstrap() {
    if command -v trivy ; then
        return 0  # already installed
    fi

    _harden_log info "Installing Trivy vulnerability scanner..."
    local trivy_version="v0.54.1"
    local arch
    arch="$(uname -m)"
    case "$arch" in
        x86_64)  arch="64bit" ;;
        aarch64) arch="ARM64" ;;
        *)       _harden_log warn "Trivy — unsupported architecture: $arch"; return 1 ;;
    esac

    local deb_url="https://github.com/aquasecurity/trivy/releases/download/${trivy_version}/trivy_${trivy_version#v}_Linux-${arch}.deb"
    local tmp_deb
    tmp_deb="$(mktemp /tmp/trivy.XXXXXX.deb)"

    # Attempt 1: Direct DEB download with retries and timeouts
    if curl --retry 3 --retry-delay 2 --connect-timeout 15 -fsSL "$deb_url" -o "$tmp_deb" ; then
        if ! dpkg -i "$tmp_deb" ; then
            apt-get install -f -y  || true
            dpkg -i "$tmp_deb"  || true
        fi
        rm -f "$tmp_deb"
    else
        rm -f "$tmp_deb"
        _harden_log info "Direct DEB download failed — trying official APT repo and install script..."
    fi

    # Attempt 2: Official APT Repository fallback
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

    # Attempt 3: Official Contrib script fallback
    if ! command -v trivy ; then
        curl --retry 2 --connect-timeout 10 -sfL https://raw.githubusercontent.com/aquasecurity/trivy/main/contrib/install.sh | sh -s -- -b /usr/local/bin  || true
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
