# Pinned to digest of levkk/pgcat:latest (2026-07-30)
#
# No apt layer on purpose: the base is Debian Buster (EOL) whose repos
# live only on archive.debian.org, and that host is unreachable from
# several provider networks (persistent TCP timeouts on v4, no v6 route
# — 2026-09-17 fresh-install incident). Instead python3 is copied from
# an era-matched python image (buster glibc, no package manager
# involved) and psycopg2-binary comes from PyPI as a wheel (bundles its
# own libpq — no libpq-dev needed). postgresql-client/psql was dropped:
# nothing in-repo execs psql inside this container (entrypoint and the
# healthcheck are pure python3 stdlib).
FROM python:3.11-slim-buster AS py

FROM levkk/pgcat@sha256:b22ad4dc71970dcfc6de027d6219c07d39c43a1a97c0a01dad6c7a1a600416a5

USER root
COPY --from=py /usr/local/bin/python3* /usr/local/bin/
COPY --from=py /usr/local/lib/ /usr/local/lib/
RUN ln -sf /usr/local/bin/python3 /usr/local/bin/python && \
    python3 -c "import sys, ssl, hashlib, os; print('python ok', sys.version.split()[0])" && \
    pip3 install --no-cache-dir --upgrade pip && pip3 install --no-cache-dir psycopg2-binary && \
    python3 -c "import psycopg2; print('psycopg2 ok', psycopg2.__version__)"

COPY entrypoint.sh /entrypoint.sh
COPY render_pgcat_config.py /scripts/render_pgcat_config.py
RUN chmod +x /entrypoint.sh /scripts/render_pgcat_config.py

# Drop privileges after setup.  PgCat binds to 0.0.0.0:6432 (default
# port); docker-compose maps host 10.100.0.1:5432 to container 6432.
RUN addgroup --system pgcat && adduser --disabled-password --ingroup pgcat --no-create-home pgcat
USER pgcat

ENTRYPOINT ["/entrypoint.sh"]
