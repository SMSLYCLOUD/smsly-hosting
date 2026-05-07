    # 4. Build the Caddyfile
    cat > "$candidate" <<SAFECADDY
# Auto-generated safe fallback (reason: $reason)
{
    on_demand_tls {
        ask http://localhost:8090/api/v1/services/check-domain/
    }
}

${domain} {
    reverse_proxy localhost:8090
    encode gzip
    log {
        output file /var/log/caddy/access.log
    }
}

:443 {
    tls {
        on_demand
    }
    reverse_proxy localhost:8090
}

:80 {
    handle {
        rewrite * /notice
        reverse_proxy localhost:8090
    }
}

${svc_blocks}
SAFECADDY
