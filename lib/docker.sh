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

    install_registry_docker_certs
}

install_registry_docker_certs() {
    local cert="${INSTALL_DIR:-/opt/smsly-hosting}/certs/registry.crt"
    if [ ! -f "$cert" ]; then
        return 0
    fi
    local my_ip
    my_ip="$(detect_public_ip)"
    local dirs=(
        "/etc/docker/certs.d/registry:5000"
        "/etc/docker/certs.d/127.0.0.1:5000"
    )
    if [ -n "$my_ip" ] && [ "$my_ip" != "127.0.0.1" ]; then
        dirs+=("/etc/docker/certs.d/${my_ip}:5000")
    fi
    local installed=false
    for d in "${dirs[@]}"; do
        mkdir -p "$d"
        cp "$cert" "$d/ca.crt"
        installed=true
    done
    if [ "$installed" = "true" ]; then
        echo -e "${BLUE}  → Installed registry TLS cert for Docker trust (${#dirs[@]} endpoints)${NC}"
    fi
}

docker_login() {
    local registry="${CONTAINER_REGISTRY_URL:-127.0.0.1:5000}"
    local user="${REGISTRY_USER:-smsly-registry}"
    local pass="${REGISTRY_PASSWORD:-}"
    if [ -z "$pass" ]; then
        return 0
    fi
    local _cacert="${INSTALL_DIR:-/opt/smsly-hosting}/certs/registry.crt"
    local _curl_args="--insecure"
    if [ -f "$_cacert" ]; then
        _curl_args="--cacert $_cacert"
    fi
    local _code=""
    _code="$(timeout 10 curl -s -o /dev/null -w '%{http_code}' $_curl_args "https://${registry}/v2/" 2>/dev/null)"
    if [ -n "$_code" ] && [ "$_code" != "401" ]; then
        if [ "$_code" = "200" ]; then
            echo -e "${BLUE}     -> Registry $registry allows anonymous access - skipping login${NC}"
        else
            echo -e "${YELLOW}    [warn] Registry $registry returned HTTP $_code on /v2/ probe - check registry config${NC}"
        fi
        return 0
    fi
    if echo "$pass" | docker login "$registry" -u "$user" --password-stdin 2>&1; then
        return 0
    fi
    echo -e "${YELLOW}    [warn] Docker login failed for $registry (see error above)${NC}"
    return 0
}

compose_stack_services() {
    local services=""
    services="$(docker compose -f "$COMPOSE_FILE" config --services)" || return $?
    if is_node_mode; then
        printf '%s\n' "$services" | grep -Ev '^(frontend|caddy)$'
    else
        printf '%s\n' "$services"
    fi
}

compose_stack_service_args() {
    compose_stack_services | tr '\n' ' '
}

compose_stack_build_service_args() {
    local candidates="pgcat backend celery celery-beat frontend celery-fast celery-deploy caddy"
    local svc=""
    if is_node_mode; then
        candidates="pgcat backend celery celery-beat celery-fast celery-deploy"
    fi
    for svc in $candidates; do
        if docker compose -f "$COMPOSE_FILE" config --services  | grep -qx "$svc"; then
            printf '%s\n' "$svc"
        fi
    done | tr '\n' ' '
}

stop_node_excluded_services() {
    is_node_mode || return 0
    docker compose -f "$COMPOSE_FILE" stop --timeout 15 frontend caddy || echo -e "${YELLOW}    ⚠ frontend/caddy stop failed${NC}"
    docker compose -f "$COMPOSE_FILE" rm -f frontend caddy || echo -e "${YELLOW}    ⚠ frontend/caddy rm failed${NC}"
}

prune_stopped_conflicting() {
    local pattern="$1"
    local c_id=""
    local c_name=""
    local removed=0
    for c_id in $(docker ps -a -q --filter "name=${pattern}" --filter "status=exited" --filter "status=created"  || true); do
        c_name=$(docker inspect "$c_id" --format='{{.Name}}'  | sed 's/^\///')
        if [ -n "$c_name" ]; then
            docker rm "$c_id"  && removed=$((removed + 1))
        fi
    done
    [ "$removed" -gt 0 ] && echo -e "  \033[0;32m✓\033[0m Removed $removed stopped container(s)" || true
}

cleanup_stale_containers() {
    local compose_f="${COMPOSE_FILE:-docker-compose.prod.yml}"
    timeout -k 5 30 docker compose -f "$compose_f" down --remove-orphans  || true
    prune_stopped_conflicting "smsly-hosting"
    prune_stopped_conflicting "smsly-"
}

compose_stack_build() {
    docker_login
    local services=""
    if is_node_mode; then
        stop_node_excluded_services
        services="$(compose_stack_build_service_args)"
        [ -n "$services" ] || return 1
        timeout -k 5 300 docker compose -f "$COMPOSE_FILE" build "$@" $services
    else
        timeout -k 5 300 docker compose -f "$COMPOSE_FILE" build "$@"
    fi
}

compose_stack_up() {
    local services=""
    if is_node_mode; then
        stop_node_excluded_services
        services="$(compose_stack_service_args)"
        [ -n "$services" ] || return 1
        timeout -k 10 300 docker compose -f "$COMPOSE_FILE" up -d "$@" $services
    else
        timeout -k 10 300 docker compose -f "$COMPOSE_FILE" up -d "$@"
    fi
}

get_pgcat_if_exists() {
    local compose_target="${COMPOSE_FILE:-docker-compose.prod.yml}"
    if [ -f "$compose_target" ] && grep -q "^  *pgcat:" "$compose_target" ; then
        echo "pgcat"
    fi
}

get_db_service() {
    echo "db"
}

get_redis_service() {
    local ct="${COMPOSE_FILE:-docker-compose.prod.yml}"
    if [ -f "$ct" ] && grep -q "^  *redis-replica:" "$ct" ; then
        echo "redis-primary"
    else
        echo "redis"
    fi
}

ensure_infrastructure_permissions() {
    local caddy_config_dir="/opt/smsly-hosting/caddy-config"
    local staticfiles_dir="/opt/smsly-hosting/backend/staticfiles"
    local builds_dir="/opt/smsly-hosting/builds"
    local prometheus_targets_dir="/opt/smsly-hosting/prometheus-targets"

    echo -e "${BLUE}  -> Ensuring infrastructure permissions...${NC}"

    mkdir -p "$caddy_config_dir"
    mkdir -p "$staticfiles_dir"
    mkdir -p "$builds_dir"
    mkdir -p "$prometheus_targets_dir"

    _chown_owner="1000:1000"
    for _dir in "$caddy_config_dir" "$staticfiles_dir" "$builds_dir" "$prometheus_targets_dir"; do
        if [ -d "$_dir" ]; then
            if ! chown -R "$_chown_owner" "$_dir"; then
                echo -e "${YELLOW}     ⚠ Could not chown $_dir to $_chown_owner (see error above)${NC}"
            fi
        fi
    done

    chmod -R u+rwX,g+rwX "$caddy_config_dir" "$staticfiles_dir" "$builds_dir" "$prometheus_targets_dir" || echo -e "${YELLOW}     ⚠ chmod failed on bind-mount dirs${NC}"
    find "$caddy_config_dir" -type d -exec chmod 2775 {} + || true
    find "$staticfiles_dir" -type d -exec chmod 2775 {} + || true
    find "$builds_dir" -type d -exec chmod 2775 {} + || true
    find "$prometheus_targets_dir" -type d -exec chmod 2777 {} + || echo -e "${YELLOW}     ⚠ chmod failed on $prometheus_targets_dir${NC}"
    chmod 2777 "$prometheus_targets_dir" || echo -e "${YELLOW}     ⚠ chmod failed on $prometheus_targets_dir${NC}"

    [ -f "$caddy_config_dir/Caddyfile" ] && chmod 664 "$caddy_config_dir/Caddyfile" || true
    [ -f "$caddy_config_dir/.reload" ] && chmod 664 "$caddy_config_dir/.reload" || true

    if command -v docker ; then
        _vol_match="$(docker volume ls -q 2>/dev/null | grep -E '(^|_)backups_data$' | head -n1)"
        for vol in ${_vol_match:-backups_data}; do
            if docker volume inspect "$vol" >/dev/null 2>&1; then
                echo -e "${BLUE}     ↳ Setting permissions for volume: $vol...${NC}"
                docker run --rm -v "${vol}:/data" alpine chown -R 1000:1000 /data || echo -e "${YELLOW}     ⚠ Could not chown volume $vol${NC}"
            else
                echo -e "${YELLOW}     ⚠ backups_data volume not found — skipping chown${NC}"
            fi
        done
    fi

    local probe_failed=0
    for probe_dir in "$caddy_config_dir" "$staticfiles_dir" "$builds_dir" "$prometheus_targets_dir"; do
        if ! echo "perm-ok" > "$probe_dir/.perm_probe"; then
            echo -e "${YELLOW}  ⚠ Write probe failed for $probe_dir — retrying with chown...${NC}"
            chown -R 1000:1000 "$probe_dir" || true
            chmod -R u+rwX,g+rwX "$probe_dir" || true
            if echo "perm-ok" > "$probe_dir/.perm_probe"; then
                echo -e "${GREEN}    ✓ Fixed${NC}"
            else
                echo -e "${RED}    ✗ Still cannot write to $probe_dir — check host permissions${NC}"
                probe_failed=1
            fi
        fi
        rm -f "$probe_dir/.perm_probe" || true
    done
    if [ -f "/opt/smsly-hosting/.env" ] && ! touch "/opt/smsly-hosting/.env"; then
        echo -e "${YELLOW}  ⚠ .env not writable — fixing...${NC}"
        chown 1000:1000 "/opt/smsly-hosting/.env" || true
        chmod 640 "/opt/smsly-hosting/.env" || true
    fi
    if [ "$probe_failed" -ne 0 ]; then
        echo -e "${RED}  ✗ Some bind-mount directories are not writable — containers may fail${NC}"
    fi
}

resolve_container_target() {
    local target="$1"

    [ -z "$target" ] && return 0

    if timeout -k 5 10 docker container inspect "$target" ; then
        echo "$target"
        return 0
    fi

    local compose_f="${COMPOSE_FILE:-docker-compose.prod.yml}"
    if [ -f "$compose_f" ]; then
        local services
        services="$(timeout -k 5 10 docker compose -f "$compose_f" config --services )"
        if [ -n "$services" ]; then
            for svc in $services; do
                if [[ "$target" == *"-${svc}-"* || "$target" == *"_${svc}_"* || "$target" == *"-${svc}" || "$target" == *"_${svc}" || "$target" == "$svc" ]]; then
                    local cid
                    cid="$(timeout -k 5 10 docker compose -f "$compose_f" ps -q "$svc"  | head -n 1 || true)"
                    if [ -n "$cid" ]; then
                        echo "$cid"
                        return 0
                    fi
                fi
            done
        fi
    fi

    local cid_svc
    cid_svc="$(docker compose -f "$compose_f" ps -q "$target"  | head -n 1 || true)"
    if [ -n "$cid_svc" ]; then
        echo "$cid_svc"
        return 0
    fi

    local cid_fuzzy
    local fuzzy_pattern
    fuzzy_pattern="${target//-/*}"
    fuzzy_pattern="${fuzzy_pattern//_/*}"
    cid_fuzzy="$(docker ps -a --filter "name=${fuzzy_pattern}" -q  | head -n 1 || true)"
    if [ -n "$cid_fuzzy" ]; then
        echo "$cid_fuzzy"
        return 0
    fi

    echo "$target"
}

ensure_container_on_network() {
    local network_name="$1"
    local raw_target="$2"

    [ -z "$network_name" ] && return 0
    [ -z "$raw_target" ] && return 0

    local container_name
    container_name="$(resolve_container_target "$raw_target")"

    docker container inspect "$container_name"  || return 0
    docker network inspect "$network_name"  || return 0

    if docker network inspect "$network_name" --format '{{range $k, $v := .Containers}}{{$k}}{{end}}'  | grep -q "$container_name"; then
        return 0
    fi

    docker network connect "$network_name" "$container_name" || echo -e "${YELLOW}    ⚠ Network connect $container_name to $network_name failed${NC}"
}

recreate_traefik_preserving_certs() {
    local compose_f="${COMPOSE_FILE:-docker-compose.prod.yml}"
    local acme_src="/var/lib/docker/volumes/smsly-hosting_letsencrypt_data/_data/acme.json"
    local acme_backup=""

    if ! docker compose -f "$compose_f" ps -q traefik  | grep -q .; then
        echo -e "${YELLOW}  WARN traefik not running; skipping one-time recreate.${NC}"
        return 1
    fi

    echo -e "${BLUE}  → Verifying socket-proxy is healthy (traefik Docker provider depends on it)...${NC}"
    local i=0
    while [ $i -lt 30 ]; do
        if docker inspect --format='{{.State.Health.Status}}' smsly-hosting-socket-proxy-1  | grep -q healthy; then
            break
        fi
        sleep 2
        i=$((i + 1))
    done
    if [ $i -ge 30 ]; then
        echo -e "${RED}  x socket-proxy not healthy; aborting to avoid 503 on deployed services.${NC}"
        echo -e "${RED}    Fix: docker logs smsly-hosting-socket-proxy-1${NC}"
        return 1
    fi

    echo -e "${BLUE}  → Backing up acme.json...${NC}"
    if [ -f "$acme_src" ]; then
        acme_backup="/tmp/smsly-acme-$(date +%s).json"
        cp "$acme_src" "$acme_backup" && chmod 600 "$acme_backup"
        echo -e "${GREEN}    OK saved to $acme_backup${NC}"
    else
        echo -e "${YELLOW}    WARN no existing acme.json; new container will request fresh certs.${NC}"
    fi

    echo -e "${BLUE}  → Recording pre-recreate router count from Traefik API...${NC}"
    sleep 2
    local pre_routers=0
    if timeout 10 docker exec smsly-hosting-traefik-1 sh -c 'command -v wget ' ; then
        pre_routers=$(timeout 10 docker exec smsly-hosting-traefik-1 wget -qO- http://127.0.0.1:8080/api/http/routers  | grep -o '"name"' | wc -l)
    else
        pre_routers=$(timeout 10 docker exec smsly-hosting-traefik-1 curl -s http://127.0.0.1:8080/api/http/routers  | grep -o '"name"' | wc -l)
    fi
    echo -e "${BLUE}    pre-recreate routers: $pre_routers${NC}"
    if [ "$pre_routers" -le 1 ]; then
        echo -e "${YELLOW}    WARN only $pre_routers router(s) before recreate (expected route-fallback + deployed services).${NC}"
        echo -e "${YELLOW}          Deployed services may already have stale labels.${NC}"
    fi

    echo -e "${BLUE}  → Recreating traefik (preserves letsencrypt_data volume + acme.json)...${NC}"
    timeout -k 5 60 docker compose -f "$compose_f" up -d --no-deps traefik 2>&1 | sed 's/^/    /'

    echo -e "${BLUE}  → Reconnecting traefik to smsly-proxy network (recreate can drop external nets)...${NC}"
    ensure_container_on_network "smsly-proxy" "smsly-hosting-traefik-1"

    if [ -n "$acme_backup" ] && [ -f "$acme_backup" ]; then
        sleep 3
        if [ -f "$acme_src" ]; then
            cp "$acme_backup" "$acme_src" && chmod 600 "$acme_src"
            echo -e "${GREEN}    OK restored acme.json perms to 0600${NC}"
        fi
        rm -f "$acme_backup"
    fi

    echo -e "${BLUE}  → Waiting for traefik healthcheck...${NC}"
    i=0
    while [ $i -lt 30 ]; do
        if docker inspect --format='{{.State.Health.Status}}' smsly-hosting-traefik-1  | grep -q healthy; then
            break
        fi
        sleep 2
        i=$((i + 1))
    done
    if [ $i -ge 30 ]; then
        echo -e "${YELLOW}  WARN traefik healthcheck timeout; check 'docker logs smsly-hosting-traefik-1'${NC}"
    fi

    echo -e "${BLUE}  → Waiting for Traefik routing table to repopulate (CRITICAL — prevents 503 on deployed services)...${NC}"
    i=0
    local post_routers=0
    while [ $i -lt 60 ]; do
        if timeout 10 docker exec smsly-hosting-traefik-1 sh -c 'command -v wget ' ; then
            post_routers=$(timeout 10 docker exec smsly-hosting-traefik-1 wget -qO- http://127.0.0.1:8080/api/http/routers  | grep -o '"name"' | wc -l)
        else
            post_routers=$(timeout 10 docker exec smsly-hosting-traefik-1 curl -s http://127.0.0.1:8080/api/http/routers  | grep -o '"name"' | wc -l)
        fi
        if [ "$post_routers" -ge "$pre_routers" ] && [ "$post_routers" -gt 0 ]; then
            echo -e "${GREEN}    OK post-recreate routers: $post_routers (matches or exceeds pre-recreate)${NC}"

            local eps
            if timeout 10 docker exec smsly-hosting-traefik-1 sh -c 'command -v wget ' ; then
                eps=$(timeout 10 docker exec smsly-hosting-traefik-1 wget -qO- http://127.0.0.1:8080/api/entrypoints )
            else
                eps=$(timeout 10 docker exec smsly-hosting-traefik-1 curl -s http://127.0.0.1:8080/api/entrypoints )
            fi
            if echo "$eps" | grep -q '"name":"websecure"'; then
                echo -e "${GREEN}    OK websecure entrypoint is active${NC}"
            else
                echo -e "${YELLOW}    WARN websecure entrypoint not detected${NC}"
            fi
            if echo "$eps" | grep -q '"name":"metrics"'; then
                echo -e "${GREEN}    OK metrics entrypoint is active${NC}"
            else
                echo -e "${YELLOW}    WARN metrics entrypoint not detected${NC}"
            fi

            return 0
        fi
        sleep 2
        i=$((i + 1))
    done
    echo -e "${YELLOW}  WARN Traefik has fewer routers than before ($post_routers vs $pre_routers).${NC}"
    echo -e "${YELLOW}        Deployed services have stale Traefik labels (from before the routing fix).${NC}"
    echo -e "${YELLOW}        Redeploy them via the SMSLY dashboard to refresh labels.${NC}"
    return 1
}

bust_core_build_cache() {
    echo -e "${BLUE}  -> Busting frontend/backend build cache (safe mode)...${NC}"

    local core_svcs="frontend backend celery celery-deploy celery-fast celery-beat"
    if [ "$MODE_AGENT_LITE" = "true" ]; then
        core_svcs="backend celery-worker"
    elif [ "$MODE_NODE" = "true" ]; then
        core_svcs="backend celery celery-deploy celery-fast celery-beat"
    fi

    for svc in $core_svcs; do
        local image_ids=""
        image_ids="$(docker compose -f "$COMPOSE_FILE" images -q "$svc"  | awk 'NF' | sort -u || true)"
        if [ -n "$image_ids" ]; then
            while read -r image_id; do
                [ -n "$image_id" ] && docker rmi -f "$image_id" || echo -e "${YELLOW}    ⚠ docker rmi $image_id failed${NC}"
            done <<< "$image_ids"
        fi
    done

    docker builder prune -af || echo -e "${YELLOW}    ⚠ docker builder prune failed${NC}"

    echo -e "${BLUE}  -> Pruning deeply stale images (>7 days old)...${NC}"
    docker image prune -a -f --filter "until=168h" || echo -e "${YELLOW}    ⚠ docker image prune failed${NC}"

    echo -e "${GREEN}  OK Cache bust complete (targeted images + build cache + deep prune)${NC}"
}

restart_edge_stack() {
    local all_edge_services="socket-proxy traefik"
    if [ "$MODE_AGENT_LITE" != "true" ]; then
        all_edge_services="socket-proxy traefik route-fallback"
    fi

    echo -e "${BLUE}  -> Checking edge proxy stack (traefik/socket-proxy/route-fallback)...${NC}"
    local down_services=""
    for svc in $all_edge_services; do
        if docker compose -f "$COMPOSE_FILE" ps "$svc"  | grep -q "Up"; then
            echo -e "${GREEN}    ✓ $svc already running${NC}"
        else
            echo -e "${YELLOW}    ⚠ $svc is down — starting...${NC}"
            down_services="$down_services $svc"
        fi
    done

    if [ -n "$down_services" ]; then
        timeout -k 5 30 docker compose -f "$COMPOSE_FILE" up -d --no-deps $down_services || \
            timeout -k 5 30 docker compose -f "$COMPOSE_FILE" up -d $down_services || echo -e "${YELLOW}    ⚠ Service restart failed${NC}"
    fi

    echo -e "${BLUE}  -> Re-attaching external networks...${NC}"
    ensure_container_on_network "smsly-net" "smsly-hosting-traefik-1"
    if [ "$MODE_AGENT_LITE" != "true" ]; then
        ensure_container_on_network "smsly-net" "smsly-hosting-route-fallback-1"
    fi
    ensure_container_on_network "smsly-proxy" "smsly-hosting-traefik-1"
    ensure_container_on_network "smsly-proxy" "smsly-hosting-socket-proxy-1"

    if should_manage_caddy && docker compose -f "$COMPOSE_FILE" ps caddy  | grep -q "Up"; then
        if caddy_needs_fix; then
            generate_safe_caddyfile "restart_edge_stack validation"
        fi
        echo -e "${BLUE}  -> Reloading Caddy...${NC}"
        reload_container_caddy  || true
    fi
    echo -e "${GREEN}  OK Edge stack healthy${NC}"
}

wait_for_traefik_api() {
    local max_wait="${1:-30}"
    local waited=0
    local interval=2
    echo -e "${BLUE}  → Waiting for Traefik API to be ready...${NC}"
    while [ "$waited" -lt "$max_wait" ]; do
        if curl -sf --max-time 3 http://127.0.0.1:8082/api/version ; then
            echo -e "${GREEN}  ✓ Traefik API ready (${waited}s)${NC}"
            return 0
        fi
        sleep "$interval"
        waited=$((waited + interval))
    done
    echo -e "${YELLOW}  ⚠ Traefik API not ready after ${max_wait}s — services may be unreachable${NC}"
    return 1
}

refresh_runtime_services() {
    configure_docker_mirror

    local app_services_requested=(
        pgcat
        backend
        celery
        celery-deploy
        celery-fast
        celery-beat
        frontend
        frps
    )
    local edge_services_requested=(
        socket-proxy
        route-fallback
        traefik
    )
    local app_services=()
    local edge_services=()
    local runtime_services=()
    local failed_services=()
    local svc=""
    local container_name=""
    local timeout_seconds=120

    echo -e "${BLUE}  -> Performing clean runtime refresh (non-data services only)...${NC}"
    ensure_update_networks
    ensure_infrastructure_permissions
    stop_node_excluded_services

    for svc in "${app_services_requested[@]}"; do
        if is_node_mode && [ "$svc" = "frontend" ]; then
            continue
        fi
        if docker compose -f "$COMPOSE_FILE" config --services  | grep -qx "$svc"; then
            app_services+=("$svc")
        fi
    done

    for svc in "${edge_services_requested[@]}"; do
        if docker compose -f "$COMPOSE_FILE" config --services  | grep -qx "$svc"; then
            edge_services+=("$svc")
        fi
    done

    runtime_services=("${app_services[@]}" "${edge_services[@]}")

    if [ "${#runtime_services[@]}" -eq 0 ]; then
        echo -e "${YELLOW}  ⚠ No runtime services found to refresh${NC}"
        return 0
    fi

    if [ "${#app_services[@]}" -gt 0 ]; then
        timeout -k 5 60 docker compose -f "$COMPOSE_FILE" up -d --force-recreate --no-deps "${app_services[@]}" || \
            timeout -k 5 60 docker compose -f "$COMPOSE_FILE" up -d --force-recreate "${app_services[@]}" || echo -e "${YELLOW}    ⚠ App services restart failed${NC}"
    fi

    ensure_container_on_network "smsly-net" "smsly-hosting-pgcat-1"
    ensure_container_on_network "smsly-net" "smsly-hosting-backend-1"
    ensure_container_on_network "smsly-net" "smsly-hosting-celery-1"
    ensure_container_on_network "smsly-net" "smsly-hosting-celery-beat-1"
    ensure_container_on_network "smsly-net" "smsly-hosting-celery-deploy-1"
    ensure_container_on_network "smsly-net" "smsly-hosting-celery-fast-1"
    if [ "$MODE_NODE" != "true" ]; then
        ensure_container_on_network "smsly-net" "smsly-hosting-frontend-1"
    fi
    ensure_container_on_network "smsly-net" "smsly-hosting-route-fallback-1"
    ensure_container_on_network "smsly-net" "smsly-hosting-traefik-1"
    ensure_container_on_network "smsly-net" "smsly-hosting-frps-1"
    ensure_container_on_network "smsly-proxy" "smsly-hosting-traefik-1"
    ensure_container_on_network "smsly-proxy" "smsly-hosting-socket-proxy-1"

    for svc in "${app_services[@]}"; do
        container_name="smsly-hosting-${svc}-1"
        case "$svc" in
            backend|frontend)
                timeout_seconds=180
                ;;
            *)
                timeout_seconds=120
                ;;
        esac
        if ! wait_for_container_ready "$container_name" "$timeout_seconds"; then
            failed_services+=("$svc")
        fi
    done

    if [ "${#failed_services[@]}" -eq 0 ] && [ "${#edge_services[@]}" -gt 0 ]; then
        local down_edge=()
        for svc in "${edge_services[@]}"; do
            container_name="smsly-hosting-${svc}-1"
            if docker compose -f "$COMPOSE_FILE" ps "$svc"  | grep -q "Up"; then
                echo -e "${GREEN}  ✓ $svc already running${NC}"
            else
                echo -e "${YELLOW}  ⚠ $svc is down — starting...${NC}"
                down_edge+=("$svc")
            fi
        done
        if [ "${#down_edge[@]}" -gt 0 ]; then
            timeout -k 5 30 docker compose -f "$COMPOSE_FILE" up -d --no-deps "${down_edge[@]}" || \
                timeout -k 5 30 docker compose -f "$COMPOSE_FILE" up -d "${down_edge[@]}" || echo -e "${YELLOW}    ⚠ Edge services restart failed${NC}"
        fi

        ensure_container_on_network "smsly-net" "smsly-hosting-route-fallback-1"
        ensure_container_on_network "smsly-net" "smsly-hosting-traefik-1"
        ensure_container_on_network "smsly-proxy" "smsly-hosting-traefik-1"
        ensure_container_on_network "smsly-proxy" "smsly-hosting-socket-proxy-1"

        for svc in "${edge_services[@]}"; do
            container_name="smsly-hosting-${svc}-1"
            if ! wait_for_container_ready "$container_name" 120; then
                failed_services+=("$svc")
            fi
        done
    fi

    if [ "${#failed_services[@]}" -gt 0 ]; then
        echo -e "${YELLOW}  WARN Runtime refresh left services unready: ${failed_services[*]}${NC}"
        docker compose -f "$COMPOSE_FILE" ps "${failed_services[@]}"  || true
        docker compose -f "$COMPOSE_FILE" logs --tail=80 "${failed_services[@]}"  || true
        return 1
    fi

    if should_manage_caddy; then
        install_caddy_health_guard "${DOMAIN:-}"
        reload_container_caddy  || true
    fi

    if [ "$MODE_AGENT_LITE" != "true" ]; then
        echo -e "${BLUE}  → Refreshing Observability Stack...${NC}"
        if [ -f "infrastructure/docker/docker-compose.observability.yml" ]; then
            docker compose -f infrastructure/docker/docker-compose.observability.yml pull || echo -e "${YELLOW}    ⚠ Observability pull failed${NC}"
            docker compose -f infrastructure/docker/docker-compose.observability.yml up -d || echo -e "${YELLOW}    ⚠ Observability up failed${NC}"
            for obs_ctr in smsly-loki smsly-promtail smsly-prometheus smsly-cadvisor smsly-node-exporter smsly-grafana; do
                i=0
                while [ $i -lt 30 ]; do
                    if docker inspect --format='{{.State.Health.Status}}' "$obs_ctr"  | grep -qE 'healthy|^$'; then
                        break
                    fi
                    sleep 2
                    i=$((i + 1))
                done
            done
        fi
    fi

    if systemctl is-active --quiet smsly-autoscaler; then
        systemctl restart smsly-autoscaler || echo -e "${YELLOW}    ⚠ smsly-autoscaler restart failed${NC}"
    else
        echo -e "${BLUE}  → smsly-autoscaler not running, skipping restart${NC}"
    fi
    echo -e "${GREEN}  OK Clean runtime refresh complete${NC}"
}

safe_refresh_runtime_services() {
    if refresh_runtime_services; then
        return 0
    fi

    echo -e "${YELLOW}  -> Runtime refresh incomplete. Running one recovery pass...${NC}"
    recover_runtime_stack || true
    refresh_runtime_services
}

ensure_celery_workers_running() {
    local celery_services=()
    local down_services=()
    local base_workers=(celery celery-deploy celery-fast celery-beat)
    if [ "${CELERY_AUTOSCALE_ENABLED:-false}" != "true" ]; then
        base_workers+=(celery-2 celery-3)
    fi
    for svc in "${base_workers[@]}"; do
        if docker compose -f "$COMPOSE_FILE" config --services  | grep -qx "$svc"; then
            celery_services+=("$svc")
        fi
    done
    if [ "${#celery_services[@]}" -eq 0 ]; then
        echo -e "${BLUE}  → No celery services configured, skipping celery check${NC}"
        return 0
    fi
    for svc in "${celery_services[@]}"; do
        if ! docker compose -f "$COMPOSE_FILE" ps "$svc"  | grep -q "Up"; then
            down_services+=("$svc")
        fi
    done
    if [ "${#down_services[@]}" -eq 0 ]; then
        echo -e "${GREEN}  ✓ All celery workers are running${NC}"
        return 0
    fi
    echo -e "${YELLOW}  ⚠ Celery workers down: ${down_services[*]}. Restarting...${NC}"
    local base_down=()
    local extra_down=()
    for svc in "${down_services[@]}"; do
        case "$svc" in
            celery-2|celery-3) extra_down+=("$svc") ;;
            *) base_down+=("$svc") ;;
        esac
    done
    if [ "${#base_down[@]}" -gt 0 ]; then
        timeout -k 5 60 docker compose -f "$COMPOSE_FILE" up -d --force-recreate --no-deps "${base_down[@]}" || \
            timeout -k 5 60 docker compose -f "$COMPOSE_FILE" up -d --force-recreate "${base_down[@]}" || echo -e "${YELLOW}    ⚠ Celery workers restart failed${NC}"
    fi
    if [ "${#extra_down[@]}" -gt 0 ]; then
        timeout -k 5 60 docker compose -f "$COMPOSE_FILE" --profile extra-workers up -d --force-recreate --no-deps "${extra_down[@]}" || \
            timeout -k 5 60 docker compose -f "$COMPOSE_FILE" --profile extra-workers up -d --force-recreate "${extra_down[@]}" || echo -e "${YELLOW}    ⚠ Extra celery workers restart failed${NC}"
    fi
    local all_ok=true
    for svc in "${down_services[@]}"; do
        if wait_for_container_ready "smsly-hosting-${svc}-1" 120; then
            echo -e "${GREEN}    ✓ $svc is running${NC}"
        else
            echo -e "${RED}    ✗ $svc failed to start${NC}"
            all_ok=false
        fi
    done
    if [ "$all_ok" = true ]; then
        echo -e "${GREEN}  ✓ All celery workers recovered${NC}"
    fi
}

wait_for_container_ready() {
    local raw_target="$1"
    local timeout_seconds="${2:-180}"
    local elapsed=0
    local state=""

    [ -z "$raw_target" ] && return 1

    local container_name
    container_name="$(resolve_container_target "$raw_target")"

    while [ "$elapsed" -lt "$timeout_seconds" ]; do
        state="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$container_name"  || echo "missing")"
        if [ "$state" = "healthy" ] || [ "$state" = "running" ]; then
            echo -e "${GREEN}  OK $raw_target is $state${NC}"
            return 0
        fi
        sleep 5
        elapsed=$((elapsed + 5))
    done

    echo -e "${YELLOW}  WARN $raw_target not ready after ${timeout_seconds}s (state=$state)${NC}"
    return 1
}
