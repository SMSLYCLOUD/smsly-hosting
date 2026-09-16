FROM python:3.11-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1

# Mirror resilience: some provider networks cannot reach Debian's official
# CDN (persistent TCP timeouts while Hub/GitHub/PyPI work). Worse, apt
# exits 0 with empty indexes when every fetch fails, so the fallback
# cannot key off the update's exit code — it retries the INSTALL across
# official, then independent vendor mirrors. Official first: zero
# behavior change where it works. Hostname-only rewrite: codename
# agnostic, safe for classic and DEB822 layouts.
RUN cp /etc/apt/sources.list /etc/apt/sources.list.official 2>/dev/null || true; \
    _smsly_apt_fallback() { for _f in /etc/apt/sources.list /etc/apt/sources.list.d/*.list /etc/apt/sources.list.d/*.sources; do [ -f "$_f" ] && sed -i "$1" "$_f"; done; }; \
    _smsly_apt_use_mirror() { case "$1" in \
      leaseweb) _smsly_apt_fallback 's|https\?://deb.debian.org|http://mirror.leaseweb.com|g; s|https\?://security.debian.org/debian-security|http://debian.mirrors.ovh.net/debian-security|g';; \
      ovh) _smsly_apt_fallback 's|http://mirror.leaseweb.com|http://debian.mirrors.ovh.net|g';; \
    esac; }; \
    for _m in official leaseweb ovh; do \
      [ "$_m" != "official" ] && _smsly_apt_use_mirror "$_m"; \
      apt-get update; \
      if apt-get install -y --no-install-recommends \
        gcc \
        git \
        libpq-dev \
        postgresql-client \
        curl \
        wget \
        iputils-ping \
        dnsutils \
        net-tools \
        iptables; then break; fi; \
      [ "$_m" = "ovh" ] && exit 1; \
    done \
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
