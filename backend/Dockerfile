FROM python:3.11-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
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
    && echo \
    "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/debian \
    $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
    tee /etc/apt/sources.list.d/docker.list > /dev/null \
    && apt-get update \
    && apt-get install -y docker-ce-cli docker-buildx-plugin \
    # Install Nixpacks (Current safest method)
    && curl -sL https://nixpacks.com/install.sh | bash \
    # Security: Trivy vulnerability scanner
    && curl -sfL https://raw.githubusercontent.com/aquasecurity/trivy/main/contrib/install.sh | bash -s -- -b /usr/local/bin \
    # Security: Cosign image signing (Sigstore keyless)
    && COSIGN_ARCH=$(dpkg --print-architecture | sed 's/x86_64/amd64/;s/aarch64/arm64/') \
    && curl -sSL -o /usr/local/bin/cosign "https://github.com/sigstore/cosign/releases/latest/download/cosign-linux-${COSIGN_ARCH}" \
    && chmod +x /usr/local/bin/cosign \
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

