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
    if [ "$_smsly_apt_mirror_ok" != "true" ]; then echo "ERROR: no reachable Debian mirror (tried official + 3 fallbacks)" >&2; exit 1; fi; \
    rm -rf /etc/apt/sources.list.official /etc/apt/sources.list.d.smsly-official; \
    apt-get install -y --no-install-recommends \
    gcc \
    git \
    age \
    libpq-dev \
    postgresql-client \
    curl \
    wget \
    iputils-ping \
    dnsutils \
    net-tools \
    iptables \
    fail2ban \
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
    # Security: Trivy vulnerability scanner (pinned version + SHA256)
    && TRIVY_VERSION="0.76.2" \
    && case "${ARCH}" in \
        amd64) TRIVY_ARCH="64bit"; TRIVY_SHA256="bc0fc726e017b8927a62b2d3531d7a84ee01769daf5c99ed866dced54089fdbf" ;; \
        arm64) TRIVY_ARCH="ARM64"; TRIVY_SHA256="ab9d51f86e6a5fb936f605faff6b593ac51bc378fff4d3e8b9d58358b9c367c3" ;; \
        *) echo "Unsupported arch for trivy: ${ARCH}" >&2; exit 1 ;; \
       esac \
    && curl -fsSL -o /tmp/trivy.tar.gz "https://github.com/aquasecurity/trivy/releases/download/v${TRIVY_VERSION}/trivy_${TRIVY_VERSION}_Linux-${TRIVY_ARCH}.tar.gz" \
    && echo "${TRIVY_SHA256}  /tmp/trivy.tar.gz" | sha256sum -c - \
    && tar -xzf /tmp/trivy.tar.gz -C /usr/local/bin trivy \
    && chmod +x /usr/local/bin/trivy \
    && rm -f /tmp/trivy.tar.gz \
    # Security: Cosign image signing (pinned version + SHA256)
    && COSIGN_VERSION="v2.4.1" \
    && case "${ARCH}" in \
        amd64) COSIGN_SHA256="8b24b946dd5809c6bd93de08033bcf6bc0ed7d336b7785787c080f574b89249b" ;; \
        arm64) COSIGN_SHA256="3b2e2e3854d0356c45fe6607047526ccd04742d20bd44afb5be91fa2a6e7cb4a" ;; \
        *) echo "Unsupported arch for cosign: ${ARCH}" >&2; exit 1 ;; \
       esac \
    && curl -fsSL -o /usr/local/bin/cosign "https://github.com/sigstore/cosign/releases/download/${COSIGN_VERSION}/cosign-linux-${ARCH}" \
    && echo "${COSIGN_SHA256}  /usr/local/bin/cosign" | sha256sum -c - \
    && chmod +x /usr/local/bin/cosign \
    # Nixpacks buildpack CLI for nixpacks-strategy deployments (pinned + SHA256)
    && if [ "${ARCH}" = "amd64" ]; then \
         curl -fsSL -o /tmp/nixpacks.deb "https://github.com/railwayapp/nixpacks/releases/download/v1.40.0/nixpacks-v1.40.0-amd64.deb" \
         && echo "be0430d2e8faed285b65830e15cc9b53d078360955d6dde387edd40c03d15acc  /tmp/nixpacks.deb" | sha256sum -c - \
         && dpkg -i /tmp/nixpacks.deb && rm -f /tmp/nixpacks.deb; \
       elif [ "${ARCH}" = "arm64" ]; then \
         curl -fsSL -o /tmp/nixpacks.tar.gz "https://github.com/railwayapp/nixpacks/releases/download/v1.40.0/nixpacks-v1.40.0-aarch64-unknown-linux-musl.tar.gz" \
         && echo "691fdf680a3bd5240b87ffa8d3198d5c8fdf365603351e47f5314ff82c80bed9  /tmp/nixpacks.tar.gz" | sha256sum -c - \
         && tar -xzf /tmp/nixpacks.tar.gz -C /usr/local/bin nixpacks \
         && chmod +x /usr/local/bin/nixpacks && rm -f /tmp/nixpacks.tar.gz; \
       else \
         echo "Unsupported arch for nixpacks: ${ARCH}" >&2; exit 1; \
       fi \
    # SOPS file-based secrets (default backend; Infisical is opt-in, pinned + SHA256).
    # age comes from apt above (keygen + fallback decrypt).
    && case "${ARCH}" in \
        amd64) SOPS_SHA256="79b0f844237bd4b0446e4dc884dbc1765fc7dedc3968f743d5949c6f2e701739" ;; \
        arm64) SOPS_SHA256="e91ddc04e6a78f5aed9e4fc347a279b539c43b74d99e6b8078e2f2f6f5b309f5" ;; \
        *) echo "Unsupported arch for sops: ${ARCH}" >&2; exit 1 ;; \
       esac \
    && curl -fsSL -o /usr/local/bin/sops "https://github.com/getsops/sops/releases/download/v3.10.2/sops-v3.10.2.linux.${ARCH}" \
    && echo "${SOPS_SHA256}  /usr/local/bin/sops" | sha256sum -c - \
    && chmod +x /usr/local/bin/sops \
    && sops --version 2>&1 | head -n 1 \
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
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "config.asgi:application", "-c", "/app/gunicorn.conf.py", "--workers", "4", "--worker-class", "uvicorn.workers.UvicornWorker", "--timeout", "600"]
