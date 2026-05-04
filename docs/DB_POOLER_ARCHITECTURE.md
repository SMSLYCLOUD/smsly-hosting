# Database Pooler Architecture

CloudNeuron uses PgCat for high-performance PostgreSQL connection pooling.

## Logical Flow

1. **Client (Django / Celery)**
   -> connects to `pgcat:5432`
   -> requests pool `smsly_hosting` (transaction mode) or `smsly_hosting_session` (session mode)
2. **PgCat**
   -> authenticates client against local `pgcat.toml`
   -> routes query to upstream `postgres` instances
3. **Upstream (PostgreSQL)**
   -> Executes query and returns result.

## Why PgCat?
- Supports advanced load balancing and sharding natively.
- Handles failover transparently.
- Much higher throughput than PgBouncer for transaction workloads.

## Pool Configurations

| Pool Name | Mode | Purpose | Size |
| :--- | :--- | :--- | :--- |
| `smsly_hosting` | Transaction | Main Django API web traffic | Scalable (`PGCAT_APP_POOL_SIZE`) |
| `smsly_hosting_session` | Session | Long running Celery workers / Deployments | Controlled (`PGCAT_WORKER_POOL_SIZE`) |

Migrations bypass PgCat entirely to ensure safe schema execution.
