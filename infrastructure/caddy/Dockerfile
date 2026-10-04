# Stage 1: Build Caddy with xcaddy
# CADDY_VERSION pins the base (default 2 = latest). Pin explicitly when
# building for nodes: a newer binary once refused the node Caddyfile
# while the older one served it (2026-10-04). Master tracks latest.
ARG CADDY_VERSION=2
FROM caddy:${CADDY_VERSION}-builder AS builder

RUN xcaddy build --output /usr/bin/caddy \
    --with github.com/caddy-dns/cloudflare@v0.2.4 \
    --with github.com/corazawaf/coraza-caddy/v2

# Stage 2: Final Image
FROM caddy:${CADDY_VERSION}-alpine

COPY --from=builder /usr/bin/caddy /usr/bin/caddy

# Ensure Caddy can listen on low ports as non-root (if needed)
RUN setcap cap_net_bind_service=+ep /usr/bin/caddy

# Run as non-root user
RUN adduser -D -u 1000 caddy
USER caddy
