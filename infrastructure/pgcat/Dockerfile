# Pinned to digest of levkk/pgcat:latest (2026-07-30)
FROM levkk/pgcat@sha256:b22ad4dc71970dcfc6de027d6219c07d39c43a1a97c0a01dad6c7a1a600416a5

USER root
# Buster (Debian 10) is EOL — repos moved to archive
RUN sed -i 's|deb.debian.org|archive.debian.org|g; s|http://security.debian.org/debian-security|http://archive.debian.org/debian-security|g' /etc/apt/sources.list && \
    apt-get update && apt-get install -y python3 python3-pip libpq-dev && rm -rf /var/lib/apt/lists/*
RUN pip3 install --upgrade pip && pip3 install psycopg2-binary

COPY entrypoint.sh /entrypoint.sh
COPY render_pgcat_config.py /scripts/render_pgcat_config.py
RUN chmod +x /entrypoint.sh /scripts/render_pgcat_config.py

# Drop privileges after setup.  PgCat binds to 10.100.0.1:5432 via
# docker-compose port mapping — it does not need root for binding.
RUN addgroup --system pgcat && adduser --disabled-password --ingroup pgcat --no-create-home pgcat
USER pgcat

ENTRYPOINT ["/entrypoint.sh"]
