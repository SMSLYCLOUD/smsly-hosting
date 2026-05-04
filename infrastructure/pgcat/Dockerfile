FROM ghcr.io/postgresml/pgcat:latest

USER root
RUN apt-get update && apt-get install -y python3 && rm -rf /var/lib/apt/lists/*

COPY entrypoint.sh /entrypoint.sh
COPY render_pgcat_config.py /scripts/render_pgcat_config.py
RUN chmod +x /entrypoint.sh /scripts/render_pgcat_config.py

ENTRYPOINT ["/entrypoint.sh"]
