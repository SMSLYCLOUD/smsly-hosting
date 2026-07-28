# ─── Vulnerability scan of freshly built images ────────────────────────
if command -v trivy ; then
    echo -e "${BLUE}  → Scanning rebuilt images for vulnerabilities...${NC}"
    for _trivy_img in backend frontend; do
        _trivy_tag="smsly/${_trivy_img}:latest"
        if docker image inspect "$_trivy_tag" ; then
            echo -e "${BLUE}    ↳ Scanning $_trivy_tag...${NC}"
            trivy image --scanners vuln --severity CRITICAL,HIGH --exit-code 0 --no-progress "$_trivy_tag"  || \
                echo -e "${YELLOW}    ⚠ $_trivy_tag scan reported warnings — review output above${NC}"
        fi
    done
    unset _trivy_img _trivy_tag
fi

# ─── Safe Update: Post-Deploy Verification ─────────────────────────────
if command -v safe_update_post_verify ; then
    echo -e "${BLUE}  → Running post-deploy health checks...${NC}"
    sleep 30  # wait for containers to warm up
    if safe_update_post_verify; then
        echo -e "${GREEN}  ✓ All health checks passed — update successful${NC}"
        trap - ERR  # clear rollback trap on success
        if command -v safe_update_cleanup ; then
            safe_update_cleanup
        fi
        rm -f "$SNAPSHOT_FILE"  || true
    else
        echo -e "${RED}  ✗ Post-deploy health checks failed — initiating rollback${NC}"
        safe_update_rollback
        exit 1
    fi
fi

    # ─── Ensure Local Docker cloud provider exists ──────────────────────────
    echo -e "${BLUE}  → Ensuring Local Docker cloud provider exists...${NC}"
    echo "
from apps.cloud.models import CloudProvider
cp, created = CloudProvider.objects.get_or_create(
    provider_type='LOCAL',
    defaults={'name': 'Local Docker', 'is_active': True}
)
if not created and not cp.is_active:
    cp.is_active = True
    cp.save()
" | timeout -k 5 30 docker compose -f "$COMPOSE_FILE" exec -T backend python manage.py shell || echo -e "${YELLOW}    ⚠ Local Docker provider setup failed (non-fatal)${NC}"
    # ─── Self-Healing: Docker Socket Permissions ──────────────────────────────
    echo -e "${BLUE}  → Hardening Docker socket permissions...${NC}"
    # NOTE: Removed chmod 666 — world-writable docker.sock is a security risk.
    # Group membership (docker group) is the correct access control mechanism.
    if ! groups smsly  | grep -q "docker"; then
        usermod -aG docker smsly || echo -e "${YELLOW}    ⚠ usermod docker group failed (non-fatal)${NC}"
    fi

    # ─── Self-Healing: Cleanup Stale Resources ──────────────────────────────
    echo -e "${BLUE}  → Pruning stale deployment containers and BuildKit caches...${NC}"
    # Prune orphaned containers created by the deployment system (labeled)
    docker container prune -f --filter "label=com.smsly.managed=true" --filter "status=created"  || true
    docker container prune -f --filter "label=com.docker.compose.project" --filter "status=exited"  || true
    # Prune BuildKit build cache (saves significant disk space)
    docker builder prune -f --filter "until=24h"  || true
    # Prune stale rollback backup containers left from failed blue-green promotions
    docker container prune -f --filter "status=exited"  || true
    # Prune dangling images left over after the new images were tagged
    docker image prune -f  || true
    for ctr in $(docker ps -a --filter "status=exited" --filter "name=-rollback-" --format '{{.Names}}'  || true); do
        docker rm -f "$ctr"  || true
    done
    for ctr in $(docker ps -a --filter "status=created" --filter "name=-rollback-" --format '{{.Names}}'  || true); do
        docker rm -f "$ctr"  || true
    done

    # ─── Self-Healing: Automatic Queue Restoration ──────────────────────────
    echo -e "${BLUE}  → Checking for stalled deployments/addons in QUEUED state...${NC}"
    backend_container="$(resolve_container_target "smsly-hosting-backend-1")"
    timeout -k 5 120 docker exec -i "$backend_container" python manage.py shell -c "
from apps.deployments.models import Deployment, Service
from apps.deployments.models_addons import Addon
from apps.deployments.tasks import provision_addon_task, recover_stalled_queued_deployments
from django.db.models import Count

# Re-queue deployments
q_count = Deployment.objects.filter(status='QUEUED').count()
if q_count > 0:
    print(f'  [Jump-Start] Re-queueing {q_count} stalled deployments...')
    result = recover_stalled_queued_deployments(limit=q_count)
    print(
        '  [Jump-Start] Deployments restored: queued={queued} '
        'skipped={skipped} failed={failed}'.format(**result)
    )

# Re-queue addons
a_count = Addon.objects.filter(status='QUEUED').count()
if a_count > 0:
    print(f'  [Jump-Start] Re-queueing {a_count} stalled addons...')
    for a in Addon.objects.filter(status='QUEUED'):
        provision_addon_task.delay(str(a.id))

# Re-queue stalled service deletions (lost during worker restart)
d_count = Service.objects.filter(status='DELETION_PENDING').count()
if d_count > 0:
    print(f'  [Jump-Start] Re-queueing {d_count} stalled deletion tasks...')
    from apps.deployments.tasks import delete_service_task
    for s in Service.objects.filter(status='DELETION_PENDING'):
        delete_service_task.delay(str(s.id))
"  || true

    # ─── Verification: Celery Worker Health ─────────────────────────────────
    echo -e "${BLUE}  → Verifying worker connectivity and queue bindings...${NC}"
    # Give workers a moment to connect to Redis and report active queues
    sleep 15
    raw_worker="smsly-hosting-celery-deploy-1"
    if [ "$MODE_AGENT_LITE" = "true" ]; then
        raw_worker="smsly-hosting-celery-worker-1"
    fi
    worker_container="$(resolve_container_target "$raw_worker")"
    DEPLOY_WORKER_HEALTH="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$worker_container"  || echo "")"
    if timeout 20 docker exec -i "$worker_container" celery -A config inspect active_queues --timeout=10  | grep -q "deploy"; then
        echo -e "${GREEN}  ✓ Deployment worker successfully bound to 'deploy' queue${NC}"
    elif [ "$DEPLOY_WORKER_HEALTH" = "healthy" ] || [ "$DEPLOY_WORKER_HEALTH" = "running" ]; then
        echo -e "${GREEN}  ✓ Deployment worker container is healthy/running (queue inspect timed out)${NC}"
    else
        echo -e "${YELLOW}  ⚠ WARNING: Deployment worker not detected on 'deploy' queue. Check logs.${NC}"
    fi

    echo -e "\n${GREEN}  ✨ Update complete. Self-healing applied.${NC}"

    timeout -k 5 120 bash -c "
export COMPOSE_FILE='$COMPOSE_FILE'
source '$INSTALL_DIR/lib/env.sh'
source '$INSTALL_DIR/lib/common.sh'
source '$INSTALL_DIR/lib/platform.sh'
sync_platform_domain_state '$INSTALL_DIR/.env'
" || echo -e "${YELLOW}  ⚠ Domain state sync timed out (non-fatal)${NC}"

    # Refresh proxy/runtime edge stack so routing and TLS state is always clean.
    # NOTE: restart_edge_stack now handles Caddy validation internally (H1+H2 fix).
    restart_edge_stack
    wait_for_traefik_api 30 || true

    sleep 2

    # ─── Fix .env permissions (must be writable by Docker container UID 1000) ──
    if [ -f "$INSTALL_DIR/.env" ]; then
        chown root:1000 "$INSTALL_DIR/.env"  || true
        chmod 640 "$INSTALL_DIR/.env"  || true
    fi

    # ─── Caddy: Generate self-signed cert + regenerate Caddyfile ──
    if should_manage_caddy; then
    ensure_selfsigned_cert
    if command -v caddy ; then
        echo -e "${BLUE}  → Regenerating Caddyfile with current service domains...${NC}"

        # ── Step 1: Find the Cloudflare token FIRST (before generating Caddyfile) ──
        CF_TOKEN=""

        # Priority: .env file > PlatformConfig DB
        if [ -z "$CF_TOKEN" ] && [ -f "$INSTALL_DIR/.env" ]; then
            CF_TOKEN="$(grep -m1 '^CLOUDFLARE_API_TOKEN=' "$INSTALL_DIR/.env"  | cut -d= -f2- || true)"
        fi
        # Fallback: read from PlatformConfig in the database (set via Settings UI)
        if [ -z "$CF_TOKEN" ] || [ "$CF_TOKEN" = "fake" ]; then
            DB_TOKEN="$(timeout -k 5 30 docker compose -f "$COMPOSE_FILE" exec -T backend python manage.py shell -c "
from apps.deployments.models import PlatformConfig
config = PlatformConfig.load()
token = (getattr(config, 'cloudflare_api_token', '') or '').strip()
if token and token.lower() not in ('fake', 'changeme', 'test', ''):
    print(token)
"  || true)"
            DB_TOKEN="$(echo "$DB_TOKEN" | tr -d '[:space:]')"
            if [ -n "$DB_TOKEN" ]; then
                CF_TOKEN="$DB_TOKEN"
                echo -e "${GREEN}  ✓ Cloudflare token found in Settings DB${NC}"
                # Sync back to .env so it persists
                if grep -q 'CLOUDFLARE_API_TOKEN' "$INSTALL_DIR/.env" ; then
                    sed -i "s/CLOUDFLARE_API_TOKEN=.*/CLOUDFLARE_API_TOKEN=$CF_TOKEN/" "$INSTALL_DIR/.env"
                else
                    echo "CLOUDFLARE_API_TOKEN=$CF_TOKEN" >> "$INSTALL_DIR/.env"
                fi
            fi
        fi

        # ── Step 2: Generate Caddyfile WITH dns cloudflare if token exists ──
        if [ -n "$CF_TOKEN" ] && [ "$CF_TOKEN" != "fake" ]; then
            echo -e "${GREEN}  ✓ Cloudflare token available — generating Caddyfile with wildcard SSL${NC}"


            # Discover domain
            cf_domain=""
            cf_domain="$(timeout -k 5 30 docker compose -f "$COMPOSE_FILE" exec -T backend python manage.py shell -c "
from apps.deployments.models import PlatformConfig
c = PlatformConfig.load()
d = (c.domain or '').strip()
if d and d != 'localhost':
    print(d)
"  | tr -d '[:space:]' || true)"
            if [ -z "$cf_domain" ]; then
                cf_domain="$(grep -m1 '^DOMAIN=' "$INSTALL_DIR/.env"  | cut -d= -f2- || true)"
            fi

            cf_server_ip="$(detect_public_ip)"

            # Discover wildcard-covered hosts and non-wildcard service blocks.
            # - Wildcard-covered hosts route through Traefik via matcher.
            # - Unknown wildcard hosts route to /notice on frontend.
            # - External custom domains keep explicit direct on-demand TLS blocks with Host rewrite.
            cf_wildcard_known_hosts=""
            cf_wildcard_known_hosts="$(timeout -k 5 30 docker compose -f "$COMPOSE_FILE" exec -T backend python manage.py shell -c "
from apps.deployments.models import Service
from apps.domains.models import Domain, DomainStatus
from django.db.models import Q
suffix = '.${cf_domain}'.lower().strip()
hosts = set()
for svc in Service.objects.all():
    d = (svc.public_domain or '').strip().lower()
    if d and suffix and d.endswith(suffix):
        hosts.add(d)
for domain in Domain.objects.filter(
    status__in=[DomainStatus.ACTIVE, DomainStatus.DNS_VERIFIED, DomainStatus.SSL_PROVISIONING],
).filter(Q(verified=True) | Q(status=DomainStatus.ACTIVE)):
    cd = (domain.domain_name or '').strip().lower()
    if cd and suffix and cd.endswith(suffix):
        hosts.add(cd)
print(' '.join(sorted(hosts)))
"  | tr -d '\r' | tr -d '\n' || true)"

            cf_svc_blocks=""
            cf_svc_blocks="$(timeout -k 5 30 docker compose -f "$COMPOSE_FILE" exec -T backend python manage.py shell -c "
import os
upstream = os.environ.get('SMSLY_SERVICE_PROXY_UPSTREAM', 'traefik:80')
from apps.deployments.models import Service
from apps.domains.models import Domain, DomainStatus
from django.db.models import Q
suffix = '.${cf_domain}'.lower().strip()
seen = set()
for svc in Service.objects.all():
    public_domain = (svc.public_domain or '').strip().lower()
    if public_domain and (not suffix or not public_domain.endswith(suffix)) and public_domain not in seen:
        seen.add(public_domain)
        print(f'{public_domain} {{\n    reverse_proxy {upstream}\n    encode gzip\n}}\n')

for domain in Domain.objects.select_related('service').filter(
    status__in=[DomainStatus.ACTIVE, DomainStatus.DNS_VERIFIED, DomainStatus.SSL_PROVISIONING],
).filter(Q(verified=True) | Q(status=DomainStatus.ACTIVE)):
    custom_domain = (domain.domain_name or '').strip().lower()
    svc = domain.service
    public_domain = (svc.public_domain or '').strip().lower() if svc else ''
    if not custom_domain:
        continue
    if suffix and custom_domain.endswith(suffix):
        continue
    if custom_domain in seen:
        continue
    seen.add(custom_domain)

    if public_domain and public_domain != custom_domain:
        print(f'{custom_domain} {{\n    tls {{\n        on_demand\n    }}\n    reverse_proxy {upstream} {{\n        header_up Host {public_domain}\n    }}\n    encode gzip\n}}\n')
    else:
        print(f'{custom_domain} {{\n    tls {{\n        on_demand\n    }}\n    reverse_proxy {upstream}\n    encode gzip\n}}\n')
"  | tr -d '\r' || true)"

            # Only generate wildcard Caddyfile for real domains
            cf_is_real_domain=false
            if [ -n "$cf_domain" ] && [ "$cf_domain" != "localhost" ]; then
                if ! echo "$cf_domain" | grep -qE '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$'; then
                    cf_is_real_domain=true
                fi
            fi

            if [ "$cf_is_real_domain" = "true" ]; then
                cf_known_stanza=""
                if [ -n "$cf_wildcard_known_hosts" ]; then
                    cf_known_stanza="    @known_hosts host ${cf_wildcard_known_hosts}
    handle @known_hosts {
        reverse_proxy ${SMSLY_SERVICE_PROXY_UPSTREAM:-traefik:80}
    }"
                fi

                cat > /tmp/Caddyfile.tmp <<CFCADDY
# Auto-generated with Cloudflare DNS challenge (wildcard SSL)
{
    on_demand_tls {
        ask http://backend:8000/api/v1/services/check-domain/
    }
}

${cf_domain} {
    reverse_proxy ${SMSLY_SERVICE_PROXY_UPSTREAM:-traefik:80}
    encode gzip
    log {
        output file /var/log/caddy/access.log
    }
}

*.${cf_domain} {
    tls {
        dns cloudflare {env.CLOUDFLARE_API_TOKEN}
    }
${cf_known_stanza}
    handle {
        reverse_proxy ${SMSLY_SERVICE_PROXY_UPSTREAM:-traefik:80}
    }
}

${cf_server_ip} {
    tls internal
    redir http://${cf_server_ip}{uri} 308
}

${cf_svc_blocks}
CFCADDY
                if install_caddyfile_atomically /tmp/Caddyfile.tmp "wildcard Caddyfile"; then
                    echo -e "${GREEN}  ✓ Caddyfile generated with wildcard SSL for *.${cf_domain}${NC}"
                else
                    echo -e "${YELLOW}  ⚠ Wildcard Caddyfile could not be applied. Falling back to standard HTTPS for ${cf_domain}.${NC}"
                    generate_safe_caddyfile "wildcard Caddyfile apply failed"
                fi
                rm -f /tmp/Caddyfile.tmp
            else
                # IP mode or no domain — fall back to safe Caddyfile
                generate_safe_caddyfile "update flow (IP mode)"
            fi
        else
            # No valid token — generate safe Caddyfile (no dns cloudflare)
            generate_safe_caddyfile "update flow caddy regen"

            # NOTE: Cloudflare dns-challenge stripping is now handled by
            # generate_safe_caddyfile itself, which never emits 'dns cloudflare'
            # blocks when no token is present. (Removed dead 'if false' block.)
        fi

        # Final validation — if still broken, regenerate safe fallback
        if caddy_needs_fix; then
            generate_safe_caddyfile "post-update validation"
        fi

        reload_container_caddy  || true

        # ─── Python-based Caddyfile overlay (preview-aware routing) ─────────────
        # The bash heredoc above generates a static template without preview
        # environment routing. Django's generate_caddyfile() includes direct
        # container routing for local preview environments, so we overlay it.
        echo -e "${BLUE}  → Overlaying preview-aware Caddyfile from Django...${NC}"
        timeout -k 5 30 docker compose -f "$COMPOSE_FILE" exec -T backend python manage.py shell -c "
from apps.deployments.models import PlatformConfig
from services.caddy_manager import generate_caddyfile, apply_caddyfile
config = PlatformConfig.load()
content = generate_caddyfile(config)
token = (getattr(config, 'cloudflare_api_token', '') or '').strip()
result = apply_caddyfile(content, cloudflare_token=token, preserve_existing_token=True)
print(result.get('message', 'ok'))
"  && echo -e "${GREEN}  ✓ Preview-aware Caddyfile applied${NC}" || \
            echo -e "${YELLOW}  ⚠ Python Caddyfile overlay failed (non-fatal, static template still active)${NC}"

        reload_container_caddy  || true

        # Verify Caddy is running
        sleep 2
        if docker compose -f "$COMPOSE_FILE" ps -q caddy  | grep -q .; then
            echo -e "${GREEN}  ✓ Caddy config regenerated and running${NC}"
        else
            echo -e "${YELLOW}  ⚠ Caddy failed to start. Run: journalctl -u caddy --no-pager -n 20${NC}"
        fi

        POST_CADDY_DOMAIN="$(timeout -k 5 30 docker compose -f "$COMPOSE_FILE" exec -T backend python manage.py shell -c "
from apps.deployments.models import PlatformConfig
c = PlatformConfig.load()
d = (c.domain or '').strip()
if d and d != 'localhost':
    print(d)
"  | tr -d '[:space:]' || true)"
        if [ -z "$POST_CADDY_DOMAIN" ]; then
            POST_CADDY_DOMAIN="$(grep -m1 '^DOMAIN=' "$INSTALL_DIR/.env"  | cut -d= -f2- || true)"
        fi

        install_caddy_health_guard "$POST_CADDY_DOMAIN"
    fi
    fi

    timeout -k 5 600 bash -c "
export COMPOSE_FILE='$COMPOSE_FILE'
source '$INSTALL_DIR/lib/common.sh' 
safe_refresh_runtime_services
" || true
    timeout -k 5 300 bash -c "
export COMPOSE_FILE='$COMPOSE_FILE'
source '$INSTALL_DIR/lib/common.sh' 
ensure_celery_workers_running
" || true

    # ─── Auto-redeploy active services when platform code or domain state changes ──
    PRE_HEAD="$(cat "$INSTALL_DIR/.pre-update-head"  || true)"
    CURRENT_HEAD="$(cd "$INSTALL_DIR" && git rev-parse HEAD  || true)"
    CODE_CHANGED=false
    if [ -n "$PRE_HEAD" ] && [ "$PRE_HEAD" != "$CURRENT_HEAD" ]; then
        CODE_CHANGED=true
        echo -e "${BLUE}  → Platform code changed (${PRE_HEAD:0:7} → ${CURRENT_HEAD:0:7})${NC}"
    fi
    if [ "$CODE_CHANGED" = "true" ] || [ "$FORCE_REDEPLOY" = "true" ]; then
        echo -e "${BLUE}  → Auto-redeploying active services (platform code changed)...${NC}"
        if ! queue_active_service_redeploys "Platform update auto-redeploy" ""; then
            echo -e "${YELLOW}  ⚠ Auto-redeploy encountered issues (check logs above)${NC}"
        fi
    elif [ "${DOMAIN_SYNC_REDEPLOY_REQUIRED:-0}" = "1" ]; then
        echo -e "${BLUE}  → Auto-redeploying rewritten services (platform domain changed)...${NC}"
        if ! queue_active_service_redeploys "Platform domain change auto-redeploy" "${DOMAIN_SYNC_SERVICE_IDS}"; then
            echo -e "${YELLOW}  ⚠ Domain-change redeploy encountered issues (check logs above)${NC}"
        fi
    else
        echo -e "${GREEN}  ✓ No platform code or domain-driven redeploys required${NC}"
    fi
    # Clean up marker
    rm -f "$INSTALL_DIR/.pre-update-head"  || true

    # ─── Endpoint Verification (3 checks) ──────────────────────────────────
    echo -e "\n${BLUE}  → Running endpoint verification (3 checks)...${NC}"
    sleep 5
    PASS_COUNT=0
    FAIL_COUNT=0

    # ── Check 1: Backend API health (docker exec into backend container) ──
    EP1_FALLBACK_URL="http://127.0.0.1:8000/health"
    _LITE_HOST_HEADER=""
    if [ "$MODE_AGENT_LITE" = "true" ]; then
        _ep1_domain="$(grep -m1 '^DOMAIN=' "$INSTALL_DIR/.env"  | cut -d= -f2- | tr -d '[:space:]' || true)"
        if [ -n "$_ep1_domain" ] && [ "$_ep1_domain" != "localhost" ]; then
            _LITE_HOST_HEADER="$_ep1_domain"
        fi
    fi
    echo -e "${BLUE}  [1/3] Backend API health...${NC}"
    echo -e "${BLUE}        Endpoint: backend:8000/health (via docker exec)${NC}"
    BACKEND_OK=false
    EP1_CODE="000"
    for attempt in 1 2 3 4 5; do
        if [ "$MODE_AGENT_LITE" = "true" ]; then
            if [ -n "${_LITE_HOST_HEADER:-}" ]; then
                # Route through Traefik with the correct Host header
                EP1_CODE=$(curl -so /dev/null -w '%{http_code}' --max-time 5 -H "Host: ${_LITE_HOST_HEADER}" "http://127.0.0.1/health" ) || EP1_CODE="000"
            else
                # No domain — route through Traefik on port 80
                EP1_CODE=$(curl -so /dev/null -w '%{http_code}' --max-time 5 "http://127.0.0.1/health" ) || EP1_CODE="000"
            fi
        else
            if timeout 15 docker compose -f "$COMPOSE_FILE" exec -T backend curl -fsS --max-time 5 http://127.0.0.1:8000/health ; then
                EP1_CODE="200"
            elif curl -fsS --max-time 5 "$EP1_FALLBACK_URL" ; then
                EP1_CODE="200"
            else
                EP1_CODE=$(curl -so /dev/null -w '%{http_code}' --max-time 5 "$EP1_FALLBACK_URL" ) || EP1_CODE="000"
            fi
        fi
        case "$EP1_CODE" in
            2*|3*)
            BACKEND_OK=true
            break
            ;;
        esac
        sleep 3
    done
    if [ "$BACKEND_OK" = "true" ]; then
        EP1_RESULT="${GREEN}PASS${NC}"
        echo -e "${GREEN}  ✓ [1/3] PASS — HTTP $EP1_CODE${NC}"
        PASS_COUNT=$((PASS_COUNT + 1))
    else
        EP1_RESULT="${RED}FAIL${NC}"
        echo -e "${RED}  ✗ [1/3] FAIL — HTTP $EP1_CODE${NC}"
        echo -e "${YELLOW}        Fix: docker compose -f $COMPOSE_FILE logs --tail=30 backend${NC}"
        FAIL_COUNT=$((FAIL_COUNT + 1))
    fi

    # ── Check 2: HTTPS platform domain (auto-discovered from DB → through Caddy) ──
    echo -e "${BLUE}  [2/3] HTTPS platform domain...${NC}"
    # Auto-discover domain from PlatformConfig in DB — zero config needed
    EP_DOMAIN="$(timeout -k 5 30 docker compose -f "$COMPOSE_FILE" exec -T backend python manage.py shell -c "
from apps.deployments.models import PlatformConfig
config = PlatformConfig.load()
d = (config.domain or '').strip()
if d and d != 'localhost':
    print(d)
"  | tr -d '[:space:]' || true)"
    # Fallback to .env if DB query failed
    if [ -z "$EP_DOMAIN" ]; then
        EP_DOMAIN="$(grep -m1 '^DOMAIN=' "$INSTALL_DIR/.env"  | cut -d= -f2- || true)"
    fi
    HTTPS_OK=false
    EP2_CODE="---"
    EP2_URL="(skipped)"
    if ! should_manage_caddy; then
        EP2_RESULT="${YELLOW}SKIP${NC}"
        echo -e "${YELLOW}  [2/3] SKIPPED (Caddy/HTTPS is master-only in this mode)${NC}"
    elif [ -n "$EP_DOMAIN" ] && [ "$EP_DOMAIN" != "localhost" ] && ! echo "$EP_DOMAIN" | grep -qE '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$'; then
        EP2_URL="https://${EP_DOMAIN}/health"
        echo -e "${BLUE}        Endpoint: $EP2_URL${NC}"
        for attempt in 1 2 3; do
            EP2_CODE=$(curl -so /dev/null -w '%{http_code}' --max-time 8 "$EP2_URL" ) || EP2_CODE="000"
            case "$EP2_CODE" in
                2*|3*)
                    HTTPS_OK=true
                    break
                    ;;
            esac
            sleep 3
        done
        if [ "$HTTPS_OK" = "true" ]; then
            EP2_RESULT="${GREEN}PASS${NC}"
            echo -e "${GREEN}  ✓ [2/3] PASS — HTTP $EP2_CODE${NC}"
            PASS_COUNT=$((PASS_COUNT + 1))
        else
            EP2_RESULT="${RED}FAIL${NC}"
            echo -e "${RED}  ✗ [2/3] FAIL — HTTP $EP2_CODE${NC}"
            echo -e "${YELLOW}        Fix: docker compose -f $COMPOSE_FILE logs --tail=15 caddy${NC}"
            FAIL_COUNT=$((FAIL_COUNT + 1))
        fi
    elif [ -n "$EP_DOMAIN" ] && echo "$EP_DOMAIN" | grep -qE '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$'; then
        EP2_URL="(skipped: IP mode)"
        EP2_RESULT="${YELLOW}SKIP${NC}"
        echo -e "${YELLOW}  [2/3] SKIPPED (HTTPS requires a domain name, not raw IP $EP_DOMAIN)${NC}"
    else
        EP2_RESULT="${YELLOW}SKIP${NC}"
        echo -e "${YELLOW}  ⊘ [2/3] SKIPPED (no domain configured)${NC}"
    fi

    # ── Check 3+: ALL deployed services (auto-discovered from DB) ──
    echo -e "${BLUE}  [3/N] Deployed services routing...${NC}"

    # Query ALL active service domains from the DB (public + custom)
    ALL_SVC_DOMAINS="$(timeout -k 5 30 docker compose -f "$COMPOSE_FILE" exec -T backend python manage.py shell -c "
from apps.deployments.models import Service
for svc in Service.objects.exclude(public_domain__isnull=True).exclude(public_domain='').order_by('name'):
    print(f'{svc.name}|{svc.public_domain.strip()}')
    for cd in (svc.custom_domains or []):
        cd = cd.strip()
        if cd:
            print(f'{svc.name} (custom)|{cd}')
"  | tr -d '\r' || true)"

    # Also check Traefik port directly
    if [ "$MODE_AGENT_LITE" = "true" ]; then
        EP3_URL="http://127.0.0.1/"
    else
        EP3_URL="http://127.0.0.1:8081/"
    fi
    EP3_CODE=$(curl -so /dev/null -w '%{http_code}' --max-time 5 "$EP3_URL" ) || EP3_CODE="000"
    if [ "$EP3_CODE" != "000" ] && [ "$EP3_CODE" != "502" ]; then
        EP3_RESULT="${GREEN}PASS${NC}"
        echo -e "${GREEN}  ✓ Traefik proxy ($EP3_URL) — HTTP $EP3_CODE${NC}"
        PASS_COUNT=$((PASS_COUNT + 1))
    else
        EP3_RESULT="${RED}FAIL${NC}"
        echo -e "${RED}  ✗ Traefik proxy ($EP3_URL) — HTTP $EP3_CODE${NC}"
        echo -e "${YELLOW}        Fix: docker compose -f $COMPOSE_FILE logs --tail=20 traefik${NC}"
        FAIL_COUNT=$((FAIL_COUNT + 1))
    fi

    # Collect service results for the table
    SVC_RESULTS=""
    SVC_COUNT=0
    if [ -n "$ALL_SVC_DOMAINS" ]; then
        while IFS='|' read -r svc_name svc_domain; do
            [ -z "$svc_domain" ] && continue
            SVC_COUNT=$((SVC_COUNT + 1))
            if should_manage_caddy; then
                svc_url="https://${svc_domain}/"
            else
                svc_url="http://${svc_domain}/"
            fi
            echo -e "${BLUE}        Testing: $svc_name → $svc_url${NC}"
            svc_code="000"
            svc_ok=false
            for attempt in 1 2 3; do
                svc_code=$(curl -so /dev/null -w '%{http_code}' --max-time 8 "$svc_url" ) || svc_code="000"
                if [ "$svc_code" != "000" ] && [ "$svc_code" != "502" ] && [ "$svc_code" != "503" ]; then
                    svc_ok=true
                    break
                fi
                sleep 2
            done
            if [ "$svc_ok" = "true" ]; then
                svc_result="${GREEN}PASS${NC}"
                echo -e "${GREEN}  ✓ $svc_name: HTTP $svc_code${NC}"
                PASS_COUNT=$((PASS_COUNT + 1))
            else
                svc_result="${RED}FAIL${NC}"
                echo -e "${RED}  ✗ $svc_name: HTTP $svc_code${NC}"
                FAIL_COUNT=$((FAIL_COUNT + 1))
            fi
            SVC_RESULTS="${SVC_RESULTS}${svc_name}|${svc_url}|${svc_code}|${svc_result}\n"
        done <<< "$ALL_SVC_DOMAINS"
    fi
    if [ "$SVC_COUNT" -eq 0 ]; then
        echo -e "${YELLOW}        No active services deployed${NC}"
    fi

    # ── Results Table ──
    TOTAL_CHECKS=$((PASS_COUNT + FAIL_COUNT))
    echo ""
    echo -e "${BLUE}  ╔══════════════════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${BLUE}  ║                        ENDPOINT VERIFICATION REPORT                     ║${NC}"
    echo -e "${BLUE}  ╠════════════════════════════════════════════════════════╦══════╦══════════╣${NC}"
    echo -e "${BLUE}  ║  Endpoint                                            ║ HTTP ║  Result  ║${NC}"
    echo -e "${BLUE}  ╠════════════════════════════════════════════════════════╬══════╬══════════╣${NC}"
    printf "  ║  %-52.52s ║ %-4s ║ " "Backend (docker exec):8000/health" "$EP1_CODE"
    echo -e " $EP1_RESULT  ║"
    printf "  ║  %-52.52s ║ %-4s ║ " "HTTPS: $EP2_URL" "$EP2_CODE"
    echo -e " $EP2_RESULT  ║"
    printf "  ║  %-52.52s ║ %-4s ║ " "Traefik: $EP3_URL" "$EP3_CODE"
    echo -e " $EP3_RESULT  ║"
    # Print each deployed service row
    if [ -n "$SVC_RESULTS" ]; then
        echo -e "${BLUE}  ╠════════════════════════════════════════════════════════╬══════╬══════════╣${NC}"
        while IFS='|' read -r s_name s_url s_code s_result; do
            [ -z "$s_name" ] && continue
            printf "  ║  %-52.52s ║ %-4s ║ " "$s_name" "$s_code"
            echo -e " $s_result  ║"
        done <<< "$(echo -e "$SVC_RESULTS")"
    fi
    echo -e "${BLUE}  ╚════════════════════════════════════════════════════════╩══════╩══════════╝${NC}"

    # ── Summary ──
    if [ "$FAIL_COUNT" -eq 0 ]; then
        echo -e "\n${GREEN}  ✓ All $PASS_COUNT/$TOTAL_CHECKS endpoint checks passed${NC}"
    else
        echo -e "\n${YELLOW}  ⚠ $PASS_COUNT passed, $FAIL_COUNT failed out of $TOTAL_CHECKS checks${NC}"
    fi

    # Show container status
    echo -e "\n${BLUE}Container Status:${NC}"
    docker compose -f "$COMPOSE_FILE" ps --format "table {{.Name}}\t{{.Status}}"  || \
        docker compose -f "$COMPOSE_FILE" ps  || true

    # ─── Update autoscaler service (picks up code changes + new token) ────────
    if [ -f "$INSTALL_DIR/scripts/smsly-autoscaler.py" ]; then
        echo -e "${BLUE}  → Updating smsly-autoscaler service...${NC}"
        mkdir -p /opt/smsly
        cp "$INSTALL_DIR/scripts/smsly-autoscaler.py" /opt/smsly/autoscaler.py
        chmod +x /opt/smsly/autoscaler.py

        AUTOSCALER_API_TOKEN="$(env_get_value "$INSTALL_DIR/.env" "AUTOSCALER_API_TOKEN")"
        if [ -n "$AUTOSCALER_API_TOKEN" ] && [ -f /etc/systemd/system/smsly-autoscaler.service ]; then
            # Update token in existing service file
            sed -i "s|^Environment=AUTOSCALER_API_TOKEN=.*|Environment=AUTOSCALER_API_TOKEN=${AUTOSCALER_API_TOKEN}|" \
                /etc/systemd/system/smsly-autoscaler.service
            systemctl daemon-reload
        fi
        systemctl restart smsly-autoscaler || echo -e "${YELLOW}    ⚠ Autoscaler restart failed${NC}"
        echo -e "${GREEN}  ✓ Autoscaler updated${NC}"
    fi

    # ─── Re-apply OOM protection (scores reset when containers restart) ──────
    echo -e "${BLUE}  → Re-applying OOM protection for critical containers...${NC}"
    oom_containers="smsly-hosting-backend-1 $(get_db_service | sed 's|^|smsly-hosting-|' || echo smsly-hosting-postgres-primary) smsly-hosting-pgcat-1 smsly-hosting-celery-1 smsly-hosting-celery-deploy-1 smsly-hosting-celery-fast-1 smsly-hosting-celery-beat-1 smsly-hosting-socket-proxy-1"
    if [ "$MODE_AGENT_LITE" = "true" ]; then
        oom_containers="smsly-hosting-backend-1 smsly-hosting-celery-worker-1 smsly-hosting-socket-proxy-1"
    fi
    for CONTAINER in $oom_containers; do
        resolved_container="$(resolve_container_target "$CONTAINER")"
        CPID=$(docker inspect --format '{{.State.Pid}}' "$resolved_container"  || echo "")
        if [ -n "$CPID" ] && [ "$CPID" != "0" ] && [ -f "/proc/$CPID/oom_score_adj" ]; then
            echo -500 > "/proc/$CPID/oom_score_adj"  || true
        fi
    done
    echo -e "${GREEN}  ✓ OOM protection set (core, database, celery, proxy)${NC}"

    # ─── Ensure iptables-restore systemd service exists ─────────────────────
    if command -v iptables-save ; then
        mkdir -p /etc/iptables
        iptables-save > /etc/iptables/rules.v4  || true
        if [ ! -f /etc/systemd/system/iptables-restore.service ]; then
            echo -e "${BLUE}  → Installing iptables-restore systemd service...${NC}"
            cat > /etc/systemd/system/iptables-restore.service <<'RESTORE_EOF'
[Unit]
Description=Restore iptables rules
Before=docker.service
After=network-pre.target

[Service]
Type=oneshot
ExecStart=/sbin/iptables-restore /etc/iptables/rules.v4
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
RESTORE_EOF
            systemctl daemon-reload || echo -e "${YELLOW}    ⚠ systemctl daemon-reload failed (non-fatal)${NC}"
            systemctl enable iptables-restore || echo -e "${YELLOW}    ⚠ systemctl enable iptables-restore failed (non-fatal)${NC}"
            echo -e "${GREEN}  ✓ iptables-restore service installed and enabled${NC}"
        fi
    fi

    # ─── Ensure platform update watcher and caddy watcher services exist ───
    if [ -f "$INSTALL_DIR/scripts/smsly-update-watcher.service" ]; then
        echo -e "${BLUE}  → Ensuring platform update and Caddy config watcher services...${NC}"
        chmod +x "$INSTALL_DIR/scripts/platform-update.sh" "$INSTALL_DIR/scripts/caddy-reload.sh"  || true
        cp "$INSTALL_DIR/scripts/smsly-update-watcher.service" /etc/systemd/system/smsly-update-watcher.service  || true
        cp "$INSTALL_DIR/scripts/caddy-watcher.service" /etc/systemd/system/caddy-watcher.service  || true
        systemctl daemon-reload || echo -e "${YELLOW}    ⚠ systemctl daemon-reload failed (non-fatal)${NC}"
        systemctl enable smsly-update-watcher caddy-watcher || echo -e "${YELLOW}    ⚠ systemctl enable watchers failed (non-fatal)${NC}"
        systemctl restart smsly-update-watcher caddy-watcher || echo -e "${YELLOW}    ⚠ systemctl restart watchers failed (non-fatal)${NC}"
        echo -e "${GREEN}  ✓ smsly-update-watcher and caddy-watcher services updated and started${NC}"
    fi

    # ─── Ensure Celery Worker Autoscaler service exists and is configured ──
    if [ -f "$INSTALL_DIR/scripts/celery-worker-autoscaler.sh" ]; then
        echo -e "${BLUE}  → Ensuring Celery Worker Autoscaler service...${NC}"
        chmod +x "$INSTALL_DIR/scripts/celery-worker-autoscaler.sh"
        cp "$INSTALL_DIR/infrastructure/docker/celery-autoscaler.service" /etc/systemd/system/celery-autoscaler.service  || true
        systemctl daemon-reload || true
        if [ "${CELERY_AUTOSCALE_ENABLED:-true}" = "true" ]; then
            systemctl enable celery-autoscaler || echo -e "${YELLOW}    ⚠ celery-autoscaler enable failed (non-fatal)${NC}"
            systemctl restart celery-autoscaler || echo -e "${YELLOW}    ⚠ celery-autoscaler restart failed (non-fatal)${NC}"
            echo -e "${GREEN}  ✓ celery-autoscaler service updated and running${NC}"
        else
            systemctl disable celery-autoscaler 2>/dev/null || true
            systemctl stop celery-autoscaler 2>/dev/null || true
            echo -e "${BLUE}  → celery-autoscaler disabled (CELERY_AUTOSCALE_ENABLED=false)${NC}"
        fi
    fi

    # ─── Ensure WireGuard mesh service is enabled ───────────────────────────
    if [ -d /etc/wireguard ]; then
        for wg_conf in /etc/wireguard/*.conf; do
            [ -f "$wg_conf" ] || continue
            wg_iface=$(basename "$wg_conf" .conf)
            if ! systemctl is-enabled "wg-quick@${wg_iface}" ; then
                echo -e "${BLUE}  → Re-enabling WireGuard mesh ($wg_iface)...${NC}"
                systemctl enable --now "wg-quick@${wg_iface}" || echo -e "${YELLOW}    ⚠ systemctl enable wg-quick failed (non-fatal)${NC}"
                echo -e "${GREEN}  ✓ WireGuard $wg_iface re-enabled${NC}"
            fi
            if ! systemctl is-active "wg-quick@${wg_iface}" ; then
                echo -e "${YELLOW}  ⚠ WireGuard $wg_iface is not running, attempting restart...${NC}"
                systemctl start "wg-quick@${wg_iface}" || echo -e "${YELLOW}    ⚠ systemctl start wg-quick failed (non-fatal)${NC}"
            fi
        done
    fi

    trap - EXIT
    release_install_lock
    echo -e "\n${GREEN}════════════════════════════════════════════════════════════${NC}"
    # Infrastructure Diagnostic & Auto-Fix
    # Infrastructure Handshake & Health Stabilization
    echo -e "\n${BLUE}  🔄 Running infrastructure handshake and stabilization...${NC}"
    chmod +x scripts/grid-handshake.sh  || true
    SMSLY_MIGRATIONS_DONE=1 bash scripts/grid-handshake.sh || \
        echo -e "${YELLOW}  ⚠️ Handshake stabilization failed (non-fatal). You can run it manually later.${NC}"

    # ─── Fix .env permissions (ensures domain signal can write back) ─────
    fix_env_permissions "$INSTALL_DIR/.env" || true

    # ─── Install/update infrastructure monitor timer ─────────────────────
    if [ -f "$INSTALL_DIR/scripts/monitor_infra.sh" ]; then
        echo -e "${BLUE}  → Installing critical infrastructure monitoring timer...${NC}"
        chmod +x "$INSTALL_DIR/scripts/monitor_infra.sh"
        cp "$INSTALL_DIR/scripts/smsly-infra-monitor.service" /etc/systemd/system/smsly-infra-monitor.service  || true
        cp "$INSTALL_DIR/scripts/smsly-infra-monitor.timer" /etc/systemd/system/smsly-infra-monitor.timer  || true
        systemctl daemon-reload
        systemctl enable smsly-infra-monitor.timer || echo -e "${YELLOW}    ⚠ systemctl enable infra timer failed (non-fatal)${NC}"
        systemctl restart smsly-infra-monitor.timer || echo -e "${YELLOW}    ⚠ systemctl restart infra timer failed (non-fatal)${NC}"
        echo -e "${GREEN}  ✓ smsly-infra-monitor timer installed and started${NC}"
    fi

    echo -e "${GREEN}   ✓ UPDATE SUCCESSFUL ($UPDATE_MODE)${NC}"

    # ─── Security verify ──────────────────────────────────────────────────
    if [ -f "$INSTALL_DIR/lib/harden.sh" ]; then
        harden_security_verify
    fi

    # ─── Image signature verification ────────────────────────────────────
    if command -v cosign  && [ -f "$INSTALL_DIR/scripts/cosign-verify.sh" ]; then
        echo -e "${BLUE}  → Verifying production image signatures...${NC}"
        source "$INSTALL_DIR/scripts/cosign-verify.sh"
        cosign_verify_image "smsly/backend:latest" || \
            echo -e "${YELLOW}  ⚠ Backend image signature verification failed (non-fatal on existing installs)${NC}"
    fi

    echo -e "${GREEN}════════════════════════════════════════════════════════════${NC}"
    echo -e "${YELLOW}  Debug snapshot:    sudo bash install.sh --debug${NC}"
    echo -e "${YELLOW}  Runtime recovery:  sudo bash install.sh --recover${NC}"
    echo -e "${YELLOW}  Fix permissions:   sudo bash install.sh --fix-permissions${NC}"
    exit 0
