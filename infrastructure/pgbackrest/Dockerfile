# pgBackRest sidecar: instance backups for the shared + platform servers.
# Built from Debian packages (no Hub image guessing): pgbackrest +
# postgresql-client for psql-level checks. Runs nothing by itself;
# host cron execs stanza-create/check/backup commands.
FROM debian:bookworm-slim

# pgbackrest + PG16 client (servers are pg16; pg_basebackup demands a
# matching major). PGDG apt for the -16 client; pgbackrest from bookworm.
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates curl gnupg lsb-release locales \
    && curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc | gpg --dearmor -o /usr/share/keyrings/pgdg.gpg \
    && echo "deb [signed-by=/usr/share/keyrings/pgdg.gpg] http://apt.postgresql.org/pub/repos/apt $(lsb_release -cs)-pgdg main" > /etc/apt/sources.list.d/pgdg.list \
    && apt-get update && apt-get install -y --no-install-recommends \
    pgbackrest postgresql-client-16 postgresql-16 \
    && rm -rf /var/lib/apt/lists/* \
    && sed -i 's/# en_US.UTF-8 UTF-8/en_US.UTF-8 UTF-8/' /etc/locale.gen && locale-gen en_US.UTF-8 \
    && pgbackrest version && pg_basebackup --version && /usr/lib/postgresql/16/bin/pg_verifybackup --version

# Idle supervisor: cron execs into this container.
CMD ["sh", "-c", "while true; do sleep 86400; done"]
