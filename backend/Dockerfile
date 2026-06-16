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
    # SECURITY: removed from runtime - install via builder stage
    # TODO: move docker-ce-cli + docker-buildx-plugin to a dedicated builder
    # stage and COPY --from=builder only the static binaries needed at
    # runtime. Installing the Docker apt repo into the runtime image enlarges
    # the attack surface (extra signed keys, persistent apt source) for a
    # client we only invoke against a remote socket-proxy.
    # && install -m 0755 -d /etc/apt/keyrings \
    # && curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc \
    # && chmod a+r /etc/apt/keyrings/docker.asc \
    # && echo \
    # "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/debian \
    # $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
    # tee /etc/apt/sources.list.d/docker.list > /dev/null \
    # && apt-get update \
    # && apt-get install -y docker-ce-cli docker-buildx-plugin \
    # Install Nixpacks (Current safest method)
    && curl -sL https://nixpacks.com/install.sh | bash \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Security: Run as non-root user
RUN chmod +x entrypoint.sh && \
    mkdir -p /app/staticfiles /app/backups/services /app/backups/server /caddy-config && \
    useradd -m -u 1000 smsly && \
    chown -R smsly:smsly /app
USER smsly

# Use Entrypoint (handles migrations + static files)
ENTRYPOINT ["./entrypoint.sh"]
# Default CMD: gunicorn for backend. Celery/celery-beat override via docker-compose command:
# Use ASGI so Django Channels WebSockets work consistently (dev + prod).
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "config.asgi:application", "--workers", "4", "--worker-class", "uvicorn.workers.UvicornWorker", "--timeout", "600"]

