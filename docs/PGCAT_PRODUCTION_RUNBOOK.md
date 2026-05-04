# PgCat Production Runbook

This document describes how to handle database and connection pool operations in a production CloudNeuron cluster.

## 1. Inspecting PgCat Pools

PgCat provides an admin interface that you can connect to using `psql`.

```bash
# Connect to PgCat admin interface
docker compose -f docker-compose.prod.yml exec pgcat psql -h 127.0.0.1 -p 5432 -U pgcat_admin -d pgcat

# Inside the prompt:
> SHOW POOLS;
> SHOW DATABASES;
```

## 2. Diagnosing Exhausted Connections

If you see `sorry, too many clients already` or API timeouts:

1. Connect to PgCat admin and run `SHOW POOLS`. Observe if the `cl_active` or `sv_active` connections are maxed out.
2. Run `scripts/check_db_pool_budget.sh` to ensure the config isn't pushing more connections than Postgres can handle.
3. Check the Celery worker logs. A stuck worker might be holding a database connection without releasing it.
4. Scale up the Postgres `max_connections` if memory permits, and increase `PGCAT_APP_POOL_SIZE`.

## 3. Rotating Credentials

If the database password changes:
1. Update `.env` with the new `POSTGRES_PASSWORD`.
2. Update the downstream Postgres instance password.
3. Restart the PgCat container:
   `docker compose -f docker-compose.prod.yml up -d pgcat`
   The entrypoint script will automatically regenerate `pgcat.toml` with the new password.

## 4. Recovering After Postgres Restart

PgCat automatically reconnects to the upstream Postgres instance once it is available again.
If your application experiences errors, it should recover as soon as PgCat establishes the new connections.
No manual intervention is required.

## 5. Recovering After PgCat Restart

The Django and Celery containers use standard connection retry logic. A brief API error may occur, but once PgCat is back up, traffic resumes normally.
Deployment jobs currently in a database transaction during a restart will fail and will need to be retried (via Celery retry mechanisms).

## 6. Switching to Rollback Profile

If PgCat exhibits catastrophic failure, we have retained the direct Postgres routing as a fallback.

1. Edit `.env` and set `DATABASE_URL=postgresql://user:pass@db:5432/smsly_hosting`
2. Restart the backend and celery containers:
   `docker compose -f docker-compose.prod.yml up -d backend celery celery-fast celery-beat celery-deploy`
