# Addon Maintenance & Database Management Services

## Context
SMSLY Hosting provisions addons (Postgres, Redis, MongoDB, Qdrant) but provides no management UI. Users need to view tables, run queries, manage connections, monitor performance, and handle maintenance tasks — all from the dashboard.

## Codebase Location
- Addon models: `backend/apps/deployments/models_addons.py`
- Addon provisioner: `backend/services/addon_provisioner.py`
- Existing backup: `backend/apps/deployments/models_addons.py` → `Backup` model
- Frontend addons tab: `frontend/src/components/addons/AddonsTab.tsx`

## Phase 1: Database Explorer (Backend)

### 1.1 Create DB proxy service
File: `backend/apps/addons/services/db_proxy.py` [NEW]

Provides safe, read/write access to user databases without exposing ports:

```python
class DatabaseProxy:
    """Connects to user addon databases through internal Docker network."""

    def __init__(self, addon: Addon):
        self.addon = addon
        self.connection_url = addon.connection_url

    def get_connection(self):
        """Create connection based on addon type."""
        if self.addon.addon_type == 'POSTGRES':
            import psycopg2
            return psycopg2.connect(self.connection_url)
        elif self.addon.addon_type == 'REDIS':
            import redis
            return redis.from_url(self.connection_url)
        elif self.addon.addon_type == 'MONGODB':
            from pymongo import MongoClient
            return MongoClient(self.connection_url)

    # ── Postgres / MySQL ──
    def list_tables(self) -> list[dict]:
        """Returns table names, row counts, and sizes."""
        ...

    def describe_table(self, table_name: str) -> list[dict]:
        """Returns columns, types, constraints for a table."""
        ...

    def query(self, sql: str, limit: int = 100) -> dict:
        """Execute read-only SQL query. Returns {columns, rows, row_count, duration_ms}."""
        # CRITICAL: Wrap in read-only transaction
        # SET TRANSACTION READ ONLY
        # Enforce LIMIT to prevent huge result sets
        # Timeout after 10 seconds
        ...

    def execute(self, sql: str) -> dict:
        """Execute write SQL (INSERT/UPDATE/DELETE/DDL). Returns {affected_rows}."""
        # Require explicit confirmation from frontend
        # Log all mutations for audit trail
        ...

    # ── Redis ──
    def redis_info(self) -> dict:
        """Redis INFO command — memory, clients, keyspace."""
        ...

    def redis_keys(self, pattern: str = '*', limit: int = 100) -> list:
        """SCAN keys matching pattern."""
        ...

    def redis_get(self, key: str) -> dict:
        """Get key value + type + TTL."""
        ...

    def redis_flush(self, confirm: bool = False):
        """FLUSHDB — requires explicit confirmation."""
        ...

    # ── MongoDB ──
    def mongo_collections(self) -> list[dict]:
        """List collections with doc counts and sizes."""
        ...

    def mongo_find(self, collection: str, query: dict, limit: int = 100) -> list:
        """Find documents in collection."""
        ...

    # ── Stats (all types) ──
    def get_stats(self) -> dict:
        """Database size, connections, uptime, memory usage."""
        ...
```

### 1.2 Create maintenance service
File: `backend/apps/addons/services/maintenance.py` [NEW]

```python
class AddonMaintenanceService:
    """Scheduled and on-demand maintenance for addons."""

    def __init__(self, addon: Addon):
        self.addon = addon

    # ── Health ──
    def health_check(self) -> dict:
        """Check addon container health + connection test."""
        # Returns {status, latency_ms, version, uptime}

    # ── Postgres Maintenance ──
    def vacuum_analyze(self):
        """Run VACUUM ANALYZE on all tables."""

    def reindex(self, table_name: str = None):
        """Reindex specific table or all tables."""

    def get_slow_queries(self) -> list:
        """Get pg_stat_statements slow queries."""

    def get_table_bloat(self) -> list:
        """Check table/index bloat levels."""

    def kill_idle_connections(self, older_than_minutes: int = 30):
        """Terminate idle connections hogging resources."""

    # ── Redis Maintenance ──
    def redis_memory_analysis(self) -> dict:
        """MEMORY DOCTOR + big key analysis."""

    def redis_slowlog(self) -> list:
        """Get SLOWLOG entries."""

    # ── Connection Pool Stats ──
    def connection_stats(self) -> dict:
        """Active connections, idle, max, pool utilization."""

    # ── Credentials ──
    def rotate_credentials(self) -> dict:
        """Generate new password, update addon + service env vars."""
        # 1. Generate new password
        # 2. ALTER USER in database
        # 3. Update addon.connection_url
        # 4. Update EnvironmentVariable (DATABASE_URL etc)
        # 5. Restart service container to pick up new creds
        # Returns {new_connection_url, affected_env_vars}

    # ── Scaling ──
    def resize(self, memory_mb: int = None, cpu_cores: float = None):
        """Resize addon container resources."""

    def get_resource_usage(self) -> dict:
        """Current CPU, memory, disk usage of addon container."""
```

### 1.3 Celery tasks for scheduled maintenance
File: `backend/apps/addons/tasks.py` [NEW]

```python
@shared_task
def addon_health_check_all():
    """Run health checks on all active addons — every 5 min via Beat."""
    for addon in Addon.objects.filter(status='ACTIVE'):
        ...

@shared_task
def addon_auto_vacuum():
    """Weekly VACUUM ANALYZE on all Postgres addons."""
    ...

@shared_task
def addon_metric_snapshot():
    """Collect addon metrics every minute for the Metrics dashboard."""
    ...

@shared_task
def rotate_addon_credentials_task(addon_id):
    """Rotate DB credentials and restart dependent service."""
    ...
```

Add to Celery Beat in `backend/config/celery.py`:
```python
'addon-health-check': {'task': '...addon_health_check_all', 'schedule': crontab(minute='*/5')}
'addon-auto-vacuum': {'task': '...addon_auto_vacuum', 'schedule': crontab(day_of_week=0, hour=4)}
'addon-metrics': {'task': '...addon_metric_snapshot', 'schedule': crontab(minute='*/1')}
```

## Phase 2: API Endpoints

File: `backend/apps/addons/views.py` [NEW]

```
# Database Explorer
POST   /api/v1/addons/{id}/query/              → execute SQL/query
GET    /api/v1/addons/{id}/tables/             → list tables/collections
GET    /api/v1/addons/{id}/tables/{name}/      → describe table/collection
GET    /api/v1/addons/{id}/stats/              → DB stats + size + connections

# Redis specific
GET    /api/v1/addons/{id}/redis/info/         → Redis INFO
GET    /api/v1/addons/{id}/redis/keys/         → SCAN keys
POST   /api/v1/addons/{id}/redis/flush/        → FLUSHDB (requires confirm)

# Maintenance
POST   /api/v1/addons/{id}/vacuum/             → VACUUM ANALYZE
POST   /api/v1/addons/{id}/reindex/            → REINDEX
GET    /api/v1/addons/{id}/slow-queries/       → pg_stat_statements
GET    /api/v1/addons/{id}/connections/        → connection pool stats
POST   /api/v1/addons/{id}/kill-idle/          → kill idle connections
POST   /api/v1/addons/{id}/rotate-credentials/ → rotate password
POST   /api/v1/addons/{id}/resize/             → resize addon resources
GET    /api/v1/addons/{id}/health/             → health check result
GET    /api/v1/addons/{id}/metrics/            → time-series metrics
```

## Phase 3: Frontend UI

### 3.1 Database Explorer tab
File: `frontend/src/components/addons/DatabaseExplorer.tsx` [NEW]

- **SQL editor** with syntax highlighting (use `@uiw/react-codemirror` with SQL language)
- **Table browser** sidebar: list tables, click to see schema + sample rows
- **Query results** table with pagination, sorting, CSV export
- **Read-only toggle**: safety switch to prevent accidental writes
- **Query history**: last 50 queries with re-run button

### 3.2 Addon Dashboard (enhanced AddonsTab)
File: `frontend/src/components/addons/AddonsTab.tsx` [MODIFY]

Add to each addon card:
- **Connection info** card: host, port, database name, username (password hidden)
- **Resource usage** gauge: CPU, memory, disk (ring charts)
- **Quick actions**: Vacuum, Reindex, Kill Idle, Rotate Credentials
- **Health indicator**: green/yellow/red dot with latency

### 3.3 Addon Metrics page
File: `frontend/src/components/addons/AddonMetrics.tsx` [NEW]

- **Time-series charts** (use existing chart library):
  - Connection count over time
  - Query latency (p50, p95, p99)
  - Memory usage
  - Disk usage
  - Operations/sec (for Redis)
- **Slow query log** table
- **Table bloat** warnings

### 3.4 Redis Explorer
File: `frontend/src/components/addons/RedisExplorer.tsx` [NEW]

- **Key browser**: search/filter keys by pattern
- **Key viewer**: show value, type, TTL, size
- **Memory analysis**: big keys, memory breakdown
- **Slowlog viewer**
- **Flush button** with double-confirmation modal

### 3.5 Credential Rotation UI
File: `frontend/src/components/addons/CredentialRotation.tsx` [NEW]

- Show current connection string (masked)
- "Rotate Credentials" button → confirmation modal
- Progress indicator (rotate → update env → restart service)
- Copy new connection string

## Phase 4: Addon Alerts

### 4.1 Alert rules
File: `backend/apps/addons/services/alerts.py` [NEW]

```python
ADDON_ALERT_RULES = [
    {'metric': 'connection_count', 'threshold': 90, 'percent_of_max': True,
     'severity': 'warning', 'message': 'Approaching max connections'},
    {'metric': 'disk_usage_percent', 'threshold': 85,
     'severity': 'critical', 'message': 'Disk usage critically high'},
    {'metric': 'memory_usage_percent', 'threshold': 90,
     'severity': 'warning', 'message': 'Memory pressure detected'},
    {'metric': 'replication_lag_seconds', 'threshold': 30,
     'severity': 'critical', 'message': 'Replication lag too high'},
]
```

Alerts fire via WebSocket to frontend + optional webhook/email.

## Validation
1. Connect to Postgres addon → list tables → describe → query → verify results
2. Connect to Redis addon → list keys → get value → verify
3. Run VACUUM ANALYZE → verify it completes without error
4. Rotate credentials → verify service restarts with new creds and still works
5. Kill idle connections → verify they're terminated
6. Check metrics → verify charts render with real data
7. Trigger alert rule → verify notification appears in UI
8. SQL injection test: attempt `DROP TABLE` in read-only mode → must be blocked

## Anti-Crash Rules
- **SQL injection prevention**: Read-only mode wraps queries in `SET TRANSACTION READ ONLY`
- **Query timeout**: 10s hard limit on all user queries via `SET statement_timeout = 10000`
- **Result limit**: Cap all queries at 1000 rows regardless of user LIMIT
- **Audit logging**: Log every write query with user, timestamp, and SQL text
- **Credential rotation**: Never show plaintext password in API response — only in initial create
- **Connection pooling**: Use a single connection per request, close immediately after
- **For psycopg2**: use `with conn.cursor()` context manager for auto-cleanup
- **For Redis**: use `decode_responses=True` to avoid byte string issues
