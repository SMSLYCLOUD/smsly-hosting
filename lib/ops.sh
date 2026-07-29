source "${BASH_SOURCE[0]%/*}/ops_wipe.sh"
source "${BASH_SOURCE[0]%/*}/ops_domain.sh"
source "${BASH_SOURCE[0]%/*}/ops_recovery.sh"
source "${BASH_SOURCE[0]%/*}/ops_debug.sh"

# =============================================================================
# ops.sh — Function library for install/update/ops operations
# Mode dispatch is handled by install.sh, NOT here.
# This file only defines functions and sources sub-modules.
# =============================================================================

# ─── VERIFY MODE — Run endpoint checks only (no changes) ──────────────────────
# Called from install.sh when VERIFY_MODE=true
verify_endpoints() {
    if [ "$EUID" -ne 0 ]; then
        echo -e "${RED}x Please run as root (sudo bash install.sh --verify)${NC}"
        exit 1
    fi
    cd "$INSTALL_DIR"  || { echo -e "${RED}x $INSTALL_DIR not found. Run fresh install first.${NC}"; exit 1; }

    DOMAIN="$(env_get_value "$INSTALL_DIR/.env" "DOMAIN"  || echo "")"

    if should_manage_caddy; then
        echo -e "\n${BLUE}  ⟳ Syncing Proxy Configurations...${NC}"
        reload_container_caddy  || true
        install_caddy_health_guard "$DOMAIN"
    fi


    sleep 3

    echo -e "\n${BLUE}  → Running endpoint verification...${NC}"
    PASS_COUNT=0
    FAIL_COUNT=0

    # Backend health (internal) — docker exec into backend container
    EP1_FALLBACK_URL="http://127.0.0.1:8000/health"
    _LITE_HOST_HEADER=""
    if [ "$MODE_AGENT_LITE" = "true" ]; then
        _ep1_domain="$(grep -m1 '^DOMAIN=' "$INSTALL_DIR/.env"  | cut -d= -f2- | tr -d '[:space:]' || true)"
        if [ -n "$_ep1_domain" ] && [ "$_ep1_domain" != "localhost" ]; then
            _LITE_HOST_HEADER="$_ep1_domain"
        fi
    fi
    if [ "$MODE_AGENT_LITE" = "true" ]; then
        if [ -n "${_LITE_HOST_HEADER:-}" ]; then
            EP1_CODE=$(curl -so /dev/null -w '%{http_code}' --max-time 5 -H "Host: ${_LITE_HOST_HEADER}" "http://127.0.0.1/health" ) || EP1_CODE="000"
        else
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
        echo -e "${GREEN}  ✓ Backend (local): HTTP $EP1_CODE${NC}"; PASS_COUNT=$((PASS_COUNT + 1))
        ;;
    *)
        echo -e "${RED}  ✗ Backend (local): HTTP $EP1_CODE${NC}"; FAIL_COUNT=$((FAIL_COUNT + 1))
        ;;
    esac

    # Platform domain (public-facing — tests Caddy → Traefik → backend chain)
    if [ -n "$DOMAIN" ] && [ "$DOMAIN" != "localhost" ]; then
        EP_PUB_URL="http://${DOMAIN}/health"
        if is_node_mode; then
            EP_PUB_URL="http://${DOMAIN}/health/live"
        fi
        EP_PUB_CODE=$(curl -so /dev/null -w '%{http_code}' --max-time 10 "$EP_PUB_URL" ) || EP_PUB_CODE="000"
        if [ "$EP_PUB_CODE" = "200" ] || [ "$EP_PUB_CODE" = "301" ] || [ "$EP_PUB_CODE" = "308" ]; then
            echo -e "${GREEN}  ✓ Platform (${DOMAIN}): HTTP $EP_PUB_CODE${NC}"; PASS_COUNT=$((PASS_COUNT + 1))
        else
            echo -e "${RED}  ✗ Platform (${DOMAIN}): HTTP $EP_PUB_CODE${NC}"; FAIL_COUNT=$((FAIL_COUNT + 1))
        fi
    fi

    # HTTPS domain (skip for raw IP addresses — certs can't be issued for IPs)
    if ! should_manage_caddy; then
        echo -e "${YELLOW}  ⊘ HTTPS: Skipped (Caddy/HTTPS is master-only in this mode)${NC}"
    elif [ -n "$DOMAIN" ] && [ "$DOMAIN" != "localhost" ] && ! echo "$DOMAIN" | grep -qE '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$'; then
        EP2_URL="https://${DOMAIN}/health"
        EP2_CODE=$(curl -so /dev/null -w '%{http_code}' --max-time 10 "$EP2_URL" ) || EP2_CODE="000"
        case "$EP2_CODE" in
            2*|3*)
            echo -e "${GREEN}  ✓ HTTPS: HTTP $EP2_CODE${NC}"; PASS_COUNT=$((PASS_COUNT + 1))
            ;;
        *)
            echo -e "${RED}  ✗ HTTPS: HTTP $EP2_CODE ($EP2_URL)${NC}"; FAIL_COUNT=$((FAIL_COUNT + 1))
            ;;
        esac
    elif echo "$DOMAIN" | grep -qE '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$' ; then
        echo -e "${YELLOW}  ⊘ HTTPS: Skipped (IP Mode — SSL requires a domain name)${NC}"
    fi

    # Traefik
    EP3_URL="http://127.0.0.1:8081/"
    if is_node_mode; then
        EP3_URL="http://127.0.0.1/health/live"
    fi
    EP3_CODE=$(curl -so /dev/null -w '%{http_code}' --max-time 5 "$EP3_URL" ) || EP3_CODE="000"
    if [ "$EP3_CODE" != "000" ] && [ "$EP3_CODE" != "502" ]; then
        echo -e "${GREEN}  ✓ Traefik: HTTP $EP3_CODE${NC}"; PASS_COUNT=$((PASS_COUNT + 1))
    else
        echo -e "${RED}  ✗ Traefik: HTTP $EP3_CODE${NC}"; FAIL_COUNT=$((FAIL_COUNT + 1))
    fi

    # Post-install smoke (HTTP/HTTPS/wildcard) if domain provided
    if [ -n "${DOMAIN:-}" ] && [ -x "/opt/smsly-hosting/scripts/smoke_routes.sh" ]; then
        echo -e "${YELLOW}  ⟳ Smoke-testing routes for ${DOMAIN}${NC}"
        /opt/smsly-hosting/scripts/smoke_routes.sh "$DOMAIN" "*.$DOMAIN" || true
    fi

    # Deployed service domains
    ALL_SVC_DOMAINS="$(timeout -k 5 30 docker compose -f "$COMPOSE_FILE" exec -T backend python manage.py shell -c "
from apps.deployments.models import Service
for s in Service.objects.exclude(public_domain__isnull=True).exclude(public_domain=''):
    print(f'{s.name}|{s.public_domain.strip()}')
"  | tr -d '\r' || true)"

    if [ -n "$ALL_SVC_DOMAINS" ]; then
        while IFS='|' read -r svc_name svc_domain; do
            [ -z "$svc_domain" ] && continue
            if should_manage_caddy; then
                svc_url="https://${svc_domain}/"
            else
                svc_url="http://${svc_domain}/"
            fi
            svc_code=$(curl -so /dev/null -w '%{http_code}' --max-time 8 "$svc_url" ) || svc_code="000"
            if [ "$svc_code" != "000" ] && [ "$svc_code" != "502" ] && [ "$svc_code" != "503" ]; then
                echo -e "${GREEN}  ✓ $svc_name ($svc_domain): HTTP $svc_code${NC}"; PASS_COUNT=$((PASS_COUNT + 1))
            else
                echo -e "${RED}  ✗ $svc_name ($svc_domain): HTTP $svc_code${NC}"; FAIL_COUNT=$((FAIL_COUNT + 1))
            fi
        done <<< "$ALL_SVC_DOMAINS"
    fi

    TOTAL=$((PASS_COUNT + FAIL_COUNT))
    if [ "$FAIL_COUNT" -eq 0 ]; then
        echo -e "\n${GREEN}  ✓ All $PASS_COUNT/$TOTAL checks passed${NC}"
    else
        echo -e "\n${YELLOW}  ⚠ $PASS_COUNT passed, $FAIL_COUNT failed out of $TOTAL checks${NC}"
    fi

    docker compose -f "$COMPOSE_FILE" ps --format "table {{.Name}}\t{{.Status}}"  || \
        docker compose -f "$COMPOSE_FILE" ps  || true
    exit 0
}
