#!/bin/bash

_harden_container_runtime_bootstrap() {
    local install_dir="${INSTALL_DIR:-/opt/smsly-hosting}"
    local env_file="$install_dir/.env"

    # If CONTAINER_RUNTIME is already persisted in .env, skip install
    # but still ensure Docker daemon.json registration is correct.
    if [ -f "$env_file" ] && grep -q '^CONTAINER_RUNTIME=' "$env_file" ; then
        local existing_runtime
        existing_runtime="$(grep '^CONTAINER_RUNTIME=' "$env_file" | cut -d= -f2)"
        case "$existing_runtime" in
            kata)
                if command -v kata-runtime ; then
                    bash "$install_dir/lib/install-kata.sh" || true
                fi
                ;;
            runsc)
                if command -v runsc ; then
                    bash "$install_dir/lib/install-gvisor.sh" || true
                fi
                ;;
        esac
        return 0
    fi

    # Try Kata first (stronger isolation, requires KVM)
    if [ -e /dev/kvm ] && ! command -v kata-runtime ; then
        if [ -f "$install_dir/lib/install-kata.sh" ]; then
            echo -e "${BLUE}  → [harden] Kata Containers (KVM available) — installing...${NC}"
            bash "$install_dir/lib/install-kata.sh" || true
        fi
    fi

    if command -v kata-runtime ; then
        env_set_value "$env_file" "CONTAINER_RUNTIME" "kata"
        echo -e "${BLUE}  → [harden] Persisted CONTAINER_RUNTIME=kata in .env${NC}"
        return 0
    fi

    # Fall back to gVisor (lighter, no KVM required)
    if ! command -v runsc ; then
        if [ -f "$install_dir/lib/install-gvisor.sh" ]; then
            echo -e "${BLUE}  → [harden] gVisor (runsc) — installing...${NC}"
            bash "$install_dir/lib/install-gvisor.sh" || true
        fi
    fi

    if command -v runsc ; then
        env_set_value "$env_file" "CONTAINER_RUNTIME" "runsc"
        echo -e "${BLUE}  → [harden] Persisted CONTAINER_RUNTIME=runsc in .env${NC}"
        return 0
    fi
}

_harden_container_runtime_verify() {
    local found=0

    if command -v runsc ; then
        _harden_log ok "gVisor (runsc) installed"
        found=1
    fi

    if command -v kata-runtime ; then
        _harden_log ok "Kata Containers installed"
        found=1
    fi

    if [ "$found" -eq 0 ]; then
        _harden_log warn "container runtime sandboxing — install gVisor or Kata for VM-level isolation"
        return 1
    fi

    # Check Docker runtime registration
    if [ -f /etc/docker/daemon.json ]; then
        if python3 -c "import json; import sys; cfg=json.load(open('/etc/docker/daemon.json')); sys.exit(0 if 'runsc' in cfg.get('runtimes',{}) else 1)" ; then
            _harden_log ok "gVisor registered with Docker"
        elif python3 -c "import json; import sys; cfg=json.load(open('/etc/docker/daemon.json')); sys.exit(0 if 'kata-runtime' in cfg.get('runtimes',{}) else 1)" ; then
            _harden_log ok "Kata registered with Docker"
        else
            _harden_log warn "container runtime NOT registered in Docker daemon.json — re-run install to fix"
        fi
    else
        _harden_log warn "Docker daemon.json missing — cannot verify runtime registration"
    fi

    # `found` is a 0/1 FLAG, not an exit code — returning it turns a successful
    # gVisor/Kata install into a FAILED security check (found=1 -> return 1).
    return 0
}
