# Database Connection Budget

When running Grid PaaS, configuring database connection pools is critical. Too many connections will overwhelm Postgres, causing `FATAL: sorry, too many clients already`.

## Budget Calculation

Your PostgreSQL instance has a `max_connections` limit. Default is typically `100`.

The total number of connections to Postgres is the sum of:
1. `PGCAT_APP_POOL_SIZE` (Django Web/API workers using Transaction Mode)
2. `PGCAT_WORKER_POOL_SIZE` (Celery deployment and background workers using Session Mode)
3. Direct/Reserved Connections (Health checks, admin tooling, migrations). Usually budget `5` for this.

**Formula:**
`Total = App Pool + Worker Pool + 5`

**Requirement:**
`Total <= POSTGRES_MAX_CONNECTIONS`

## Examples by Instance Size

### 1GB / 2GB VPS (Low Memory)
- `POSTGRES_MAX_CONNECTIONS` = 100
- `PGCAT_APP_POOL_SIZE` = 20
- `PGCAT_WORKER_POOL_SIZE` = 5
- Budget = 30 / 100 (Safe)

### 4GB VPS
- `POSTGRES_MAX_CONNECTIONS` = 200
- `PGCAT_APP_POOL_SIZE` = 60
- `PGCAT_WORKER_POOL_SIZE` = 20
- Budget = 85 / 200 (Safe)

### 8GB+ Production Node
- `POSTGRES_MAX_CONNECTIONS` = 500
- `PGCAT_APP_POOL_SIZE` = 150
- `PGCAT_WORKER_POOL_SIZE` = 50
- Budget = 205 / 500 (Safe)

## Budget Validation Checks
The system explicitly verifies the pool sizing upon startup inside the PgCat container (`scripts/render_pgcat_config.py`). If the calculated total exceeds `POSTGRES_MAX_CONNECTIONS`, the container will fail to start to protect the database.

You can also run pre-flight checks manually via `scripts/check_db_pool_budget.sh`.
