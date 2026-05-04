# PgCat Migration Audit

## Overview
This document outlines the findings from the repository audit regarding the migration from PgBouncer to PgCat in CloudNeuron.

## Entrypoints & Workloads

### 1. Web/API Traffic (Django)
*   **Current State:** Uses `DATABASE_URL` routed to PgCat (`@pgcat:5432`) or fallback to DB. PgBouncer configs still exist.
*   **Required Connection Path:** PgCat Transaction Pool Mode.
*   **Risk Level:** Medium.
*   **Django Settings:** `DISABLE_SERVER_SIDE_CURSORS = True` is already set. `CONN_MAX_AGE = 0` is set.
*   **Issues:** No explicit health checks on pool checkout, relies solely on `dj-database-url`. Need to ensure Django `CONN_HEALTH_CHECKS = True` for PgCat.

### 2. Celery Workers
*   **Current State:** Four queues exist (`celery`, `celery-beat`, `celery-fast`, `celery-deploy`). They use the same `DATABASE_URL` as Django (Transaction Pool).
*   **Required Connection Path:**
    *   **Normal Workers (`celery`, `celery-fast`, `celery-beat`):** PgCat Transaction Pool Mode is generally safe for short operations, but we must enforce connection closing at task boundaries to prevent stale connections/leaks (`CELERY_WORKER_MAX_TASKS_PER_CHILD`, signal handlers to `close_old_connections`).
    *   **Deployment Worker (`celery-deploy`):** Uses `--pool=solo --concurrency=1`. It executes deployments (long-running). If it relies on persistent state or runs long ORM queries, it should use PgCat Session Mode or direct Postgres to avoid starving the transaction pool.
*   **Risk Level:** High (Connection exhaustion from long deployment tasks or fork leaks).

### 3. Migrations & Admin (Django `manage.py migrate`)
*   **Current State:** `config.settings.py` sets a `direct` database routing using `DIRECT_DATABASE_URL` to bypass transaction pooling for migrations because it "doesn't support the SET/SAVEPOINT statements".
*   **Required Connection Path:** Direct Postgres connection.
*   **Risk Level:** Low, as the bypass is already defined, but needs to be rigorously enforced across all deployment scripts (`run_manual.py`, `install.sh`, etc).

### 4. Special Query Behavior
*   **Advisory Locks:** No direct usage of Postgres advisory locks found via grep.
*   **Select_for_update:** Used in `platform_updater.py`, `remediator.py`, `billing/utils.py`, `provisioner.py`, `models_audit.py`. Transaction pooling supports `SELECT FOR UPDATE` *within a transaction* as long as the ORM keeps it in the same transaction block (`transaction.atomic`). Must verify.
*   **LISTEN/NOTIFY:** Used in Patroni config (HA), but not in Django application layer.
*   **Prepared Statements:** Disabled/not used by default in Django.
*   **Server-Side Cursors:** Explicitly disabled in Django settings.

### 5. Deployment Orchestration & Scripts
*   **Current State:** PgBouncer is heavily referenced in `install.sh`, `tmp/install_sanitized.sh`, `docker-compose.ha-postgres.yml`, `docs/ZERO_DOWNTIME_UPDATES.md`, etc.
*   **Issues:** PgBouncer is still spun up in standard `docker-compose up` loops. Need to replace PgBouncer with PgCat everywhere. PgBouncer should be an optional `docker-compose.pgbouncer.yml` or removed entirely as a primary path.

### 6. Health Checks
*   **Current State:** No dedicated PgCat health check script. Docker-compose uses basic `pg_isready` on the DB but we need to check PgCat directly.
*   **Required:** Create explicit PgCat readiness/liveness checks, and update Django's database health check.

## Required Changes
1.  **Remove PgBouncer:** Purge PgBouncer from `docker-compose.prod.yml`, `install.sh`, and start scripts. Retain it ONLY as a documented rollback.
2.  **Dynamic PgCat Config:** Create `scripts/render_pgcat_config.py` to generate `pgcat.toml` handling Transaction (API) and Session (Celery-Deploy) pools.
3.  **Connection Budgeting:** Implement `scripts/check_db_pool_budget.sh` based on Postgres `max_connections` (usually 100 on standard VPS) vs sum of PgCat pools.
4.  **Celery Hardening:** Add DB connection lifecycle hooks in `backend/config/celery.py` to `close_old_connections`.
5.  **Direct Route Enforcement:** Ensure `DIRECT_DATABASE_URL` is populated correctly and used reliably for migrations in entrypoints.
