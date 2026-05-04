FROM ghcr.io/postgresml/pgcat:latest

# Install pg_isready for health checks
USER root
RUN apt-get update && apt-get install -y --no-install-recommends postgresql-client \
    && rm -rf /var/lib/apt/lists/*

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

COPY pgcat.toml /etc/pgcat/pgcat.toml

# Expose pooler port and metrics port
EXPOSE 5432 9930

ENTRYPOINT ["/entrypoint.sh"]
