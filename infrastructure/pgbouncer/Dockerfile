# Tenants pooler engine: PgBouncer (migrated 2026-09-25).
#
# The pinned pgcat fork could not complete SASL ("Unsupported
# authentication mechanism: 10" on every new server connection against
# SCRAM-only servers, plus ban/unban churn). PgBouncer speaks SCRAM
# properly on both sides. Renamed to pgbouncer-tenants 2026-09-26
# (service, container, volume, config paths).
FROM pgbouncer/pgbouncer:1.24.1

USER root
COPY entrypoint-tenants.sh /entrypoint-tenants.sh
RUN chmod +x /entrypoint-tenants.sh && /opt/pgbouncer/pgbouncer --version
USER postgres

ENTRYPOINT ["/entrypoint-tenants.sh"]
