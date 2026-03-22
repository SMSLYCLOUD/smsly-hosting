#!/bin/bash
cat << 'CADDY' >> /etc/caddy/Caddyfile

ai-router-auth-6124bc.pcloud.linadeluxe.com {
    reverse_proxy localhost:8081
    encode gzip
    log {
        output file /var/log/caddy/access.log
    }
}
CADDY
systemctl restart caddy || caddy reload --config /etc/caddy/Caddyfile
