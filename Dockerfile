# syntax=docker/dockerfile:1.7

# Monolithic Dockerfile (single container) for Dockerfile-based PaaS deploys.
# Runs: Django backend + Next.js frontend + (optional) celery/beat.

FROM node:20-bookworm-slim AS frontend_builder
WORKDIR /frontend

ARG NEXT_PUBLIC_API_URL=/api/v1
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL

RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    python3 \
    make \
    g++ \
    && rm -rf /var/lib/apt/lists/*

COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build


FROM python:3.14-slim AS runtime
WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# System deps + supervisor + gettext for envsubst.
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    wget \
    bash \
    gcc \
    git \
    libpq-dev \
    postgresql-client \
    supervisor \
    gettext-base \
    gnupg \
    libstdc++6 \
    && rm -rf /var/lib/apt/lists/*

# Docker CLI + buildx (required by backend build/provisioning services).
RUN install -m 0755 -d /etc/apt/keyrings \
    && curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc \
    && chmod a+r /etc/apt/keyrings/docker.asc \
    && echo \
    "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/debian \
    $(. /etc/os-release && echo \"$VERSION_CODENAME\") stable" | \
    tee /etc/apt/sources.list.d/docker.list > /dev/null \
    && apt-get update \
    && apt-get install -y --no-install-recommends docker-ce-cli docker-buildx-plugin \
    && curl -sL https://nixpacks.com/install.sh | bash \
    && rm -rf /var/lib/apt/lists/*

# Caddy (reverse proxy for monolithic mode)
RUN curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg \
    && curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | tee /etc/apt/sources.list.d/caddy-stable.list > /dev/null \
    && apt-get update \
    && apt-get install -y --no-install-recommends caddy \
    && rm -rf /var/lib/apt/lists/*

# Backend deps
COPY backend/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# Backend source (copied into /app to match existing backend Dockerfile layout)
COPY backend/ /app/

# Frontend runtime output (standalone)
RUN mkdir -p /frontend
COPY --from=frontend_builder /frontend/.next/standalone/ /frontend/
COPY --from=frontend_builder /frontend/.next/static /frontend/.next/static
COPY --from=frontend_builder /frontend/public /frontend/public

# Node runtime (for running Next standalone server)
COPY --from=frontend_builder /usr/local/bin/node /usr/local/bin/node

# Platform runtime wiring
COPY infrastructure/caddy/Caddyfile.monolith.template /etc/caddy/Caddyfile.template
COPY scripts/entrypoint.platform.sh /entrypoint.platform.sh
RUN chmod +x /app/entrypoint.sh /entrypoint.platform.sh

# Create non-root app user (processes run as this user via supervisor)
RUN mkdir -p /app/backups /app/builds && \
    useradd -m -u 1000 smsly \
    && chown -R smsly:smsly /app /frontend

ENV PORT=8080
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=3s --start-period=30s --retries=3 \
    CMD wget -q -O /dev/null "http://127.0.0.1:${PORT}/health" || exit 1

# SECURITY: drop root for the runtime process. Caddy needs to bind 80/443 in
# monolithic mode but the cap_net_bind_service file capability lets a non-root
# user bind low ports without requiring the whole container to run as root.
RUN setcap cap_net_bind_service=+ep /usr/bin/caddy
USER smsly

ENTRYPOINT ["/entrypoint.platform.sh"]
