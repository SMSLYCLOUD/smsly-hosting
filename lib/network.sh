detect_public_ip() {
    local candidate=""
    local endpoint=""
    local endpoints=(
        "https://api.ipify.org"
        "https://ifconfig.me/ip"
        "https://ipv4.icanhazip.com"
    )

    for endpoint in "${endpoints[@]}"; do
        candidate="$(curl -4 -fsS -m 5 "$endpoint"  | tr -d '\r\n' || true)"
        if is_valid_ipv4 "$candidate"; then
            echo "$candidate"
            return 0
        fi
    done

    candidate="$(hostname -I  | awk '{print $1}' | tr -d '\r\n' || true)"
    if is_valid_ipv4 "$candidate"; then
        echo "$candidate"
        return 0
    fi

    echo "127.0.0.1"
    return 0
}

ensure_update_networks() {
    docker network inspect smsly-net  || docker network create smsly-net || echo -e "${YELLOW}    ⚠ smsly-net create failed (may already exist)${NC}"
    docker network inspect smsly-proxy  || docker network create smsly-proxy || echo -e "${YELLOW}    ⚠ smsly-proxy create failed (may already exist)${NC}"
    docker network inspect socket-proxy  || docker network create --driver bridge --internal socket-proxy || echo -e "${YELLOW}    ⚠ socket-proxy create failed (may already exist)${NC}"
}

https_listener_active() {
    if command -v ss ; then
        ss -H -tln  | awk '{print $4}' | grep -Eq ':443$'
    else
        lsof -iTCP:443 -sTCP:LISTEN
    fi
}
