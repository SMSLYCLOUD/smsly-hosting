# Embedded Database: SQLite + Litestream
# SMSLY Hosting — Paid Tier Feature (P0 Priority)

## Problem
Managed databases are the #1 hidden cost in hosting:
- AWS RDS PostgreSQL: $30-100/mo
- PlanetScale MySQL: $29-399/mo
- Supabase PostgreSQL: $25-599/mo
- Even a simple DigitalOcean DB: $15/mo

For 90% of hosted websites, a full client-server database is overkill.

## Solution: Per-Site Embedded SQLite

Each hosted site gets its own SQLite database file. No shared database server. No connection pooling. No network latency.

### Architecture
```
┌── Customer Site (Unikernel) ──────────────┐
│                                           │
│  Django/FastAPI App                       │
│    ↓ (direct file I/O, 0ms latency)      │
│  SQLite: /data/site_abc123/db.sqlite3    │
│    ↓ (continuous streaming)               │
│  Litestream → S3 (every 10 seconds)      │
│    ↓ (cross-region replication)           │
│  LiteFS → Edge nodes                     │
│                                           │
└───────────────────────────────────────────┘
```

### Why SQLite Is Enough
Per sqlite.org:
- Handles **281 TB** max database size
- **Billions** of rows
- **100K reads/sec** on modest hardware
- **1000+ writes/sec** (WAL mode)
- Used by: Airbnb, Expensify, all iPhones, all Android devices

### Litestream Backup
```yaml
# litestream.yml per customer site
dbs:
  - path: /data/site_abc123/db.sqlite3
    replicas:
      - type: s3
        bucket: smsly-backups
        path: sites/abc123/
        retention: 720h  # 30 days
        sync-interval: 10s
```

### Point-in-Time Recovery
Litestream stores WAL segments. Restore to any moment:
```bash
litestream restore -o /data/restored.db \
  -timestamp "2026-02-08T15:00:00Z" \
  s3://smsly-backups/sites/abc123/
```

### Cost Comparison
| Service | Monthly Cost | SMSLY Equivalent |
|:--------|:-------------|:-----------------|
| AWS RDS | $30-100 | $0.02 (S3 storage) |
| PlanetScale | $29-399 | $0.02 |
| Supabase | $25-599 | $0.02 |
| **Savings** | | **99.9%** |

### Customer Experience
```python
# Django settings.py — ZERO config change needed
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'db.sqlite3',  # Just works
    }
}
# Litestream handles backup transparently
# Customer never thinks about database management
```

### Tier Features
| Tier | Database Size | Backup Retention | Replicas |
|:-----|:-------------|:----------------|:---------|
| Free | 100MB | 7 days | 0 |
| Starter ($9) | 1GB | 30 days | 0 |
| Pro ($29) | 10GB | 90 days | 1 edge replica |
| Enterprise ($99) | 100GB | 365 days | All edge replicas |

## Implementation Steps
1. [ ] Install Litestream on hosting VPS
2. [ ] Create per-site SQLite directory: `/data/sites/{site_id}/`
3. [ ] Configure Litestream supervisor per site
4. [ ] S3 bucket setup with lifecycle rules
5. [ ] Dashboard UI: "Database" tab showing size, last backup time
6. [ ] Restore UI: calendar picker for point-in-time recovery
7. [ ] Django template: auto-set `DATABASES` to site SQLite path

## Status: CONCEPT → Ready for Sprint 1
## Estimated Effort: 1 sprint (1-2 weeks)
## Dependencies: S3-compatible storage (Backblaze B2 = cheapest at $0.005/GB/mo)
