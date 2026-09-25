# Tenants pooler engine: PgBouncer (migrated 2026-09-25).
#
# The pinned pgcat fork could not complete SASL ("Unsupported
# authentication mechanism: 10" on every new server connection against
# SCRAM-only servers, plus ban/unban churn). PgBouncer speaks SCRAM
# properly on both sides. Container/service/volume names intentionally
# unchanged (install.sh, monitors, aliases, docs all key off them).
FROM pgbouncer/pgbouncer:1.24.1

# Image already ships netcat (/usr/bin/nc) for the TCP healthcheck and
# runs as the postgres user. Binary lives outside PATH.
COPY entrypoint-tenants.sh /entrypoint-tenants.sh
RUN chmod +x /entrypoint-tenants.sh && /opt/pgbouncer/pgbouncer --version

ENTRYPOINT ["/entrypoint-tenants.sh"]
