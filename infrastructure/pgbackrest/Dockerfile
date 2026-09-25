# pgBackRest sidecar: instance backups for the shared + platform servers.
# Built from Debian packages (no Hub image guessing): pgbackrest +
# postgresql-client for psql-level checks. Runs nothing by itself;
# host cron execs stanza-create/check/backup commands.
FROM debian:bookworm-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    pgbackrest postgresql-client ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && pgbackrest version

# Idle supervisor: cron execs into this container.
CMD ["sh", "-c", "while true; do sleep 86400; done"]
