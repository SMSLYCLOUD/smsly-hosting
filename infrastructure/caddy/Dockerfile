# Stage 1: Build Caddy with xcaddy
FROM caddy:2-builder AS builder

RUN xcaddy build \
    --with github.com/caddy-dns/cloudflare

# Stage 2: Final Image
FROM caddy:2-alpine

COPY --from=builder /usr/bin/caddy /usr/bin/caddy

# Ensure Caddy can listen on low ports as non-root (if needed)
RUN setcap cap_net_bind_service=+ep /usr/bin/caddy
