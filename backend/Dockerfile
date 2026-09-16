FROM python:3.11-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1

# Mirror resilience: some provider networks cannot reach Debian's official
# CDN (persistent TCP timeouts while Hub/GitHub/PyPI work). Worse, apt
# exits 0 with empty indexes when every fetch fails — so neither the
# exit code nor any fixed vendor order is a reliable signal. Instead
# every candidate mirror must PROVE itself: its update has to fetch with
# zero Err lines before it is used. Official first: zero behavior change
# where it works. Candidates are plain "MAIN|SEC" pairs — vendor-neutral,
# extend freely. Rewrites always regenerate from the pristine backup
# (never chained), and are codename-agnostic for classic + DEB822 layouts.
# (Keep in sync: Dockerfile, infrastructure/spilo/Dockerfile.)
RUN cp /etc/apt/sources.list /etc/apt/sources.list.official 2>/dev/null || true; \
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
      [ $_rc -eq 0 ] && ! printf '%s\n' "$_out" | grep -qE "^(Err|E): "; \
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
    if [ "$_smsly_apt_mirror_ok" != "true" ]; then echo "ERROR: no reachable Debian mirror (tried official + 3 fallbacks)" >&2; exit 1; fi; \
    rm -rf /etc/apt/sources.list.official /etc/apt/sources.list.d.smsly-official; \
    apt-get install -y --no-install-recommends \
    gcc \
    git \
    libpq-dev \
    postgresql-client \
    curl \
    wget \
    iputils-ping \
    dnsutils \
    net-tools \
    iptables \
    # SECURITY: the docker CLI is installed so the celery worker can
    # provision addons (POSTGRES/REDIS/RABBITMQ) via the socket-proxy
    # (DOCKER_HOST=tcp://socket-proxy:2375). The CLI talks to the proxy
    # over the internal smsly-net network, never directly to the host
    # docker socket. The CLI binary is read-only at runtime (no docker
    # daemon is started in this container). The previous build skipped
    # this and addon provisioning failed with
    # "No such file or directory: 'docker'".
    && install -m 0755 -d /etc/apt/keyrings \
    && curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc \
    && chmod a+r /etc/apt/keyrings/docker.asc \
    && . /etc/os-release \
    && CODENAME="${VERSION_CODENAME}" \
    && ARCH="$(dpkg --print-architecture)" \
    && echo "deb [arch=${ARCH} signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/debian ${CODENAME} stable" \
    | tee /etc/apt/sources.list.d/docker.list > /dev/null \
    && apt-get update \
    && apt-get install -y --no-install-recommends docker-ce-cli docker-buildx-plugin docker-compose-plugin \
    # Security: Trivy vulnerability scanner
    && curl -sfL https://raw.githubusercontent.com/aquasecurity/trivy/main/contrib/install.sh | bash -s -- -b /usr/local/bin \
    # Security: Cosign image signing (Sigstore keyless)
    && COSIGN_ARCH=$(dpkg --print-architecture | sed 's/x86_64/amd64/;s/aarch64/arm64/') \
    && curl -sSL -o /usr/local/bin/cosign "https://github.com/sigstore/cosign/releases/latest/download/cosign-linux-${COSIGN_ARCH}" \
    && chmod +x /usr/local/bin/cosign \
    # Nixpacks buildpack CLI for nixpacks-strategy deployments
    && NIXPACKS_ARCH=$(dpkg --print-architecture) \
    && curl -sSL -o /tmp/nixpacks.deb "https://github.com/railwayapp/nixpacks/releases/download/v1.40.0/nixpacks-v1.40.0-${NIXPACKS_ARCH}.deb" \
    && dpkg -i /tmp/nixpacks.deb && rm /tmp/nixpacks.deb \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Security: Run as non-root user.
# NOTE: The container starts as root so the entrypoint can chown bind-mounted
# volumes (/caddy-config, prometheus-targets). It drops to the smsly user
# before exec'ing the application (see entrypoint.sh).
RUN chmod +x entrypoint.sh && \
    mkdir -p /app/staticfiles /app/backups/services /app/backups/server /caddy-config && \
    useradd -m -u 1000 smsly && \
    chown -R smsly:smsly /app && \
    chown smsly:smsly /caddy-config

# Use Entrypoint (handles migrations + static files)
ENTRYPOINT ["./entrypoint.sh"]
# Healthcheck for container orchestration
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD curl -f http://127.0.0.1:8000/healthz || exit 1
# Default CMD: gunicorn for backend. Celery/celery-beat override via docker-compose command:
# Use ASGI so Django Channels WebSockets work consistently (dev + prod).
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "config.asgi:application", "--workers", "4", "--worker-class", "uvicorn.workers.UvicornWorker", "--timeout", "600"]
