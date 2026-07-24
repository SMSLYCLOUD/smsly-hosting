_merge_daemon_json() {
    # Merge new keys into /etc/docker/daemon.json without clobbering existing
    # settings (runtimes, log-driver, live-restore, etc.) that other installer
    # modules may have written.
    # Usage: _merge_daemon_json '{"insecure-registries":[...],"dns":[...]}'
    local new_json="$1"
    local daemon_json="/etc/docker/daemon.json"
    python3 - "$daemon_json" "$new_json" <<'PY'
import json, sys
from pathlib import Path

daemon_path = Path(sys.argv[1])
new_cfg = json.loads(sys.argv[2])

if daemon_path.exists():
    try:
        cfg = json.loads(daemon_path.read_text())
    except (json.JSONDecodeError, ValueError):
        cfg = {}
else:
    cfg = {}

cfg.update(new_cfg)
daemon_path.write_text(json.dumps(cfg, indent=2) + "\n")
PY
}

configure_docker_mirror() {
    if [ "${MODE_AGENT_LITE:-false}" = "true" ] && [ -f "${INSTALL_DIR:-/opt/smsly-hosting}/.env" ]; then
        [ -n "${MASTER_IP:-}" ] || MASTER_IP="$(env_get_value "${INSTALL_DIR:-/opt/smsly-hosting}/.env" "MASTER_IP"  || true)"
        [ -n "${MASTER_MESH_IP:-}" ] || MASTER_MESH_IP="$(env_get_value "${INSTALL_DIR:-/opt/smsly-hosting}/.env" "MASTER_MESH_IP"  || true)"
    fi

    # Detect if we need custom DNS fallback (test resolution inside Docker)
    local use_dns_fallback=false
    if command -v docker  && systemctl is-active --quiet docker; then
        echo -e "${BLUE}  → Checking Docker DNS resolution for npm registry...${NC}"
        local test_img="node:20-alpine"
        if ! docker image inspect "$test_img" ; then
            test_img="alpine"
        fi
        if ! timeout -k 5 15 docker run --rm "$test_img" nslookup registry.npmjs.org ; then
            echo -e "${YELLOW}  ⚠ Docker container DNS test failed. Enabling public DNS fallback (8.8.8.8, 1.1.1.1)...${NC}"
            use_dns_fallback=true
        else
            echo -e "${GREEN}  ✓ Docker container DNS resolution verified.${NC}"
        fi
    fi

    local changed=false
    local daemon_json="{}"

    if [ -n "${MASTER_IP:-}" ] && [ "$MASTER_IP" != "127.0.0.1" ] && [ "$MASTER_IP" != "$(detect_public_ip)" ]; then
        # Follower node: trust master's registry for self-signed certs
        echo -e "${BLUE}  → Configuring insecure registry (Master: $MASTER_IP)...${NC}"
        mkdir -p /etc/docker
        local trust_list="\"${MASTER_IP}:5000\""
        if [ -n "${MASTER_MESH_IP:-}" ]; then
            trust_list="${trust_list}, \"${MASTER_MESH_IP}:5000\""
        fi
        daemon_json="{\"registry-mirrors\":[\"http://${MASTER_IP}:5001\"],\"insecure-registries\":[${trust_list}]}"
        if [ "$use_dns_fallback" = "true" ]; then
            daemon_json="{\"registry-mirrors\":[\"http://${MASTER_IP}:5001\"],\"insecure-registries\":[${trust_list}],\"dns\":[\"8.8.8.8\",\"1.1.1.1\"]}"
        fi
        changed=true
    else
        # Master node (or MASTER_IP matches local IP): trust own registry
        local my_ip
        my_ip="$(detect_public_ip)"
        if [ "$my_ip" != "127.0.0.1" ]; then
            echo -e "${BLUE}  → Configuring Master insecure registry (registry:5000, ${my_ip}:5000)...${NC}"
            mkdir -p /etc/docker
            local master_trust_list="\"127.0.0.1:5000\", \"registry:5000\", \"${my_ip}:5000\""
            if [ -n "${MASTER_MESH_IP:-}" ]; then
                master_trust_list="${master_trust_list}, \"${MASTER_MESH_IP}:5000\""
            fi
            daemon_json="{\"insecure-registries\":[${master_trust_list}]}"
            if [ "$use_dns_fallback" = "true" ]; then
                daemon_json="{\"insecure-registries\":[${master_trust_list}],\"dns\":[\"8.8.8.8\",\"1.1.1.1\"]}"
            fi
            changed=true
        elif [ "$use_dns_fallback" = "true" ]; then
            echo -e "${BLUE}  → Configuring Docker DNS fallback...${NC}"
            mkdir -p /etc/docker
            daemon_json='{"dns":["8.8.8.8","1.1.1.1"]}'
            changed=true
        fi
    fi

    if [ "$changed" = "true" ]; then
        local prev
        prev="$(cat /etc/docker/daemon.json  || echo '')"
        _merge_daemon_json "$daemon_json"
        local new
        new="$(cat /etc/docker/daemon.json  || echo '')"
        if [ "$prev" != "$new" ]; then
            systemctl restart docker || true
        fi
    fi

    # Install registry self-signed cert into Docker's cert trust store so
    # the daemon connects via HTTPS (not HTTP fallback) to registry:5000
    # and 127.0.0.1:5000. This avoids the 400 error that occurs when a
    # TLS-configured registry rejects plain HTTP.
    install_registry_docker_certs
}