# syntax=docker/dockerfile:1

# Monolithic Dockerfile (single container) for Dockerfile-based PaaS deploys.
# Runs: Django backend + Next.js frontend + (optional) celery/beat.
#
# Build-time args:
#   INSTALL_BUILD_DEPS  Install Docker CLI + buildx + nixpacks (default: true).
#                       Required for addon provisioning and runtime container builds.

FROM node:26-bookworm-slim AS frontend_builder
WORKDIR /frontend

ARG NEXT_PUBLIC_API_URL=/api/v1
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL

# Mirror resilience: some provider networks cannot reach Debian's official
# CDN, and apt exits 0 with empty indexes when every fetch fails — so no
# fixed vendor order is reliable either. Every candidate mirror must
# PROVE itself (clean update, zero Err lines) before use; official first
# so healthy hosts see zero behavior change. Candidates are plain
# "MAIN|SEC" pairs — vendor-neutral, extend freely. Rewrites regenerate
# from the pristine backup (never chained) and cover classic + DEB822.
# (Keep in sync: backend/Dockerfile, infrastructure/spilo/Dockerfile.)
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    cp /etc/apt/sources.list /etc/apt/sources.list.official 2>/dev/null || true; \
    cp -a /etc/apt/sources.list.d /etc/apt/sources.list.d.smsly-official 2>/dev/null || true; \
    _smsly_apt_restore_official() { \
      cp /etc/apt/sources.list.official /etc/apt/sources.list 2>/dev/null || true; \
      if [ -d /etc/apt/sources.list.d.smsly-official ]; then rm -rf /etc/apt/sources.list.d; cp -a /etc/apt/sources.list.d.smsly-official /etc/apt/sources.list.d; fi; \
    }; \
    _smsly_apt_rewrite() { \
      for _f in /etc/apt/sources.list /etc/apt/sources.list.d/*.list /etc/apt/sources.list.d/*.sources; do \
        [ -f "$_f" ] || continue; \
        sed -i "s|https\\?://deb\\.debian\\.org/debian-security|$2|g; s|https\\?://deb\\.debian\\.org|$1|g; s|https\\?://security\\.debian\\.org/debian-security|$2|g" "$_f"; \
      done; \
    }; \
    _smsly_apt_updated_ok() { \
      _out="$(apt-get update 2>&1)"; _rc=$?; printf '%s\n' "$_out"; \
      [ $_rc -eq 0 ] && ! printf '%s\n' "$_out" | grep -qE "^(Err?:[^ ]*|E:|Err) "; \
    }; \
    _smsly_apt_mirror_ok=false; \
    for _spec in \
      "|" \
      "http://debian.mirrors.ovh.net|http://debian.mirrors.ovh.net/debian-security" \
      "http://mirrors.edge.kernel.org|http://mirrors.edge.kernel.org/debian-security" \
      "http://mirror.netcologne.de|http://mirror.netcologne.de/debian-security" \
    ; do \
      _main="${_spec%%|*}"; _sec="${_spec#*|}"; \
      if [ -n "$_main" ]; then _smsly_apt_restore_official; _smsly_apt_rewrite "$_main" "$_sec"; fi; \
      if _smsly_apt_updated_ok; then _smsly_apt_mirror_ok=true; break; fi; \
    done; \
    if [ "$_smsly_apt_mirror_ok" != "true" ]; then echo "ERROR: no reachable Debian mirror" >&2; exit 1; fi; \
    rm -rf /etc/apt/sources.list.official /etc/apt/sources.list.d.smsly-official; \
    apt-get install -y --no-install-recommends \
    git python3 make g++

COPY frontend/package.json frontend/package-lock.json ./
RUN --mount=type=cache,target=/root/.npm \
    npm ci
COPY frontend/ ./
RUN npm run build


FROM python:3.12-slim AS runtime
WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PORT=8080

ARG INSTALL_BUILD_DEPS=true

# --- System packages + supervisor + PostgreSQL client ---
# (Mirror resilience: same live-probed selection as the frontend stage.)
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    cp /etc/apt/sources.list /etc/apt/sources.list.official 2>/dev/null || true; \
    cp -a /etc/apt/sources.list.d /etc/apt/sources.list.d.smsly-official 2>/dev/null || true; \
    _smsly_apt_restore_official() { \
      cp /etc/apt/sources.list.official /etc/apt/sources.list 2>/dev/null || true; \
      if [ -d /etc/apt/sources.list.d.smsly-official ]; then rm -rf /etc/apt/sources.list.d; cp -a /etc/apt/sources.list.d.smsly-official /etc/apt/sources.list.d; fi; \
    }; \
    _smsly_apt_rewrite() { \
      for _f in /etc/apt/sources.list /etc/apt/sources.list.d/*.list /etc/apt/sources.list.d/*.sources; do \
        [ -f "$_f" ] || continue; \
        sed -i "s|https\\?://deb\\.debian\\.org/debian-security|$2|g; s|https\\?://deb\\.debian\\.org|$1|g; s|https\\?://security\\.debian\\.org/debian-security|$2|g" "$_f"; \
      done; \
    }; \
    _smsly_apt_updated_ok() { \
      _out="$(apt-get update 2>&1)"; _rc=$?; printf '%s\n' "$_out"; \
      [ $_rc -eq 0 ] && ! printf '%s\n' "$_out" | grep -qE "^(Err?:[^ ]*|E:|Err) "; \
    }; \
    _smsly_apt_mirror_ok=false; \
    for _spec in \
      "|" \
      "http://debian.mirrors.ovh.net|http://debian.mirrors.ovh.net/debian-security" \
      "http://mirrors.edge.kernel.org|http://mirrors.edge.kernel.org/debian-security" \
      "http://mirror.netcologne.de|http://mirror.netcologne.de/debian-security" \
    ; do \
      _main="${_spec%%|*}"; _sec="${_spec#*|}"; \
      if [ -n "$_main" ]; then _smsly_apt_restore_official; _smsly_apt_rewrite "$_main" "$_sec"; fi; \
      if _smsly_apt_updated_ok; then _smsly_apt_mirror_ok=true; break; fi; \
    done; \
    if [ "$_smsly_apt_mirror_ok" != "true" ]; then echo "ERROR: no reachable Debian mirror" >&2; exit 1; fi; \
    rm -rf /etc/apt/sources.list.official /etc/apt/sources.list.d.smsly-official; \
    apt-get install -y --no-install-recommends \
    ca-certificates curl wget bash \
    gcc git libpq-dev postgresql-client \
    supervisor gettext-base gnupg libcap2-bin libstdc++6

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
