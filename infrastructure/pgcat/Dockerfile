FROM ghcr.io/postgresml/pgcat:latest

USER root
RUN apt-get update && apt-get install -y python3 python3-pip && rm -rf /var/lib/apt/lists/*
RUN pip3 install --break-system-packages psycopg2-binary

COPY entrypoint.sh /entrypoint.sh
COPY render_pgcat_config.py /scripts/render_pgcat_config.py
RUN chmod +x /entrypoint.sh /scripts/render_pgcat_config.py

# Drop privileges after setup.  PgCat binds to 10.100.0.1:5432 via
# docker-compose port mapping — it does not need root for binding.
RUN addgroup pgcat && adduser -D -G pgcat pgcat
USER pgcat

ENTRYPOINT ["/entrypoint.sh"]
