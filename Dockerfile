# syntax=docker/dockerfile:1

# Monolithic Dockerfile (single container) for Dockerfile-based PaaS deploys.
# Runs: Django backend + Next.js frontend + (optional) celery/beat.
#
# Build-time args:
#   INSTALL_BUILD_DEPS  Install Docker CLI + buildx + nixpacks (default: true).
#                       Required for addon provisioning and runtime container builds.

FROM node:20-bookworm-slim AS frontend_builder
WORKDIR /frontend

ARG NEXT_PUBLIC_API_URL=/api/v1
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL

RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    apt-get update && apt-get install -y --no-install-recommends \
    git python3 make g++

COPY frontend/package.json frontend/package-lock.json ./
RUN --mount=type=cache,target=/root/.npm \
    npm ci
COPY frontend/ ./
RUN npm run build


FROM python:3.11-slim AS runtime
WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PORT=8080

ARG INSTALL_BUILD_DEPS=true

# --- System packages + supervisor + PostgreSQL client ---
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates curl wget bash \
    gcc git libpq-dev postgresql-client \
    supervisor gettext-base gnupg libstdc++6

# --- Optional: Docker CLI + buildx + nixpacks + trivy + cosign (for runtime container provisioning & security scanning) ---
RUN if [ "$INSTALL_BUILD_DEPS" = "true" ]; then \
      install -m 0755 -d /etc/apt/keyrings \
      && curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc \
      && chmod a+r /etc/apt/keyrings/docker.asc \
      && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/debian $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
        | tee /etc/apt/sources.list.d/docker.list > /dev/null \
      && apt-get update \
      && apt-get install -y --no-install-recommends docker-ce-cli docker-buildx-plugin \
      && rm -rf /var/lib/apt/lists/* \
      && curl -sL https://nixpacks.com/install.sh | bash \
      && curl -sfL https://raw.githubusercontent.com/aquasecurity/trivy/main/contrib/install.sh | bash -s -- -b /usr/local/bin \
      && COSIGN_ARCH=$(dpkg --print-architecture | sed 's/x86_64/amd64/;s/aarch64/arm64/') \
      && curl -sSL -o /usr/local/bin/cosign "https://github.com/sigstore/cosign/releases/latest/download/cosign-linux-${COSIGN_ARCH}" \
      && chmod +x /usr/local/bin/cosign; \
    fi

# --- Caddy: reverse proxy for monolithic mode ---
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
    | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg \
    && curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
    | tee /etc/apt/sources.list.d/caddy-stable.list > /dev/null \
    && apt-get update \
    && apt-get install -y --no-install-recommends caddy

# --- Python dependencies (cached pip layer) ---
COPY backend/requirements.txt /app/requirements.txt
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --no-cache-dir -r /app/requirements.txt

# --- Backend source ---
COPY backend/ /app/

# --- Frontend standalone output ---
RUN mkdir -p /frontend
COPY --from=frontend_builder /frontend/.next/standalone/ /frontend/
COPY --from=frontend_builder /frontend/.next/static /frontend/.next/static
COPY --from=frontend_builder /frontend/public /frontend/public
COPY --from=frontend_builder /usr/local/bin/node /usr/local/bin/node

# --- Platform wiring ---
COPY infrastructure/caddy/Caddyfile.monolith.template /etc/caddy/Caddyfile.template
COPY scripts/entrypoint.platform.sh /entrypoint.platform.sh
RUN chmod +x /app/entrypoint.sh /entrypoint.platform.sh

# --- Drop privileges ---
RUN mkdir -p /app/backups /app/builds \
    && useradd -m -u 1000 smsly \
    && chown -R smsly:smsly /app /frontend
RUN setcap cap_net_bind_service=+ep /usr/bin/caddy

USER smsly
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=3s --start-period=30s --retries=3 \
    CMD wget -q -O /dev/null "http://127.0.0.1:${PORT}/health" || exit 1

ENTRYPOINT ["/entrypoint.platform.sh"]
