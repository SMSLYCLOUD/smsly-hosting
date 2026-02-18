# Full Platform Backup System

## Context
SMSLY Hosting needs a comprehensive backup system that can snapshot services, volumes, databases, addon data, and platform config. This is foundational — server transfer depends on it.

## Codebase Location
- Backend: `backend/apps/deployments/`
- Models: `backend/apps/deployments/models.py`, `models_addons.py`, `models_storage.py`
- Cloud adapter: `backend/apps/cloud/adapters/local.py`
- Addon provisioner: `backend/services/addon_provisioner.py`
- Frontend: `frontend/src/components/settings/`
- Existing backup model: `backend/apps/deployments/models_addons.py` → `Backup` class

## Phase 1: Per-Service Backup (Backend)

### 1.1 Create backup models
File: `backend/apps/deployments/models_backup.py` [NEW]

```python
class ServiceBackup(models.Model):
    """Full snapshot of a service: container state + volumes + env vars + addons."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    service = models.ForeignKey('Service', on_delete=models.CASCADE, related_name='backups')
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    status = models.CharField(choices=[
        ('PENDING', 'Pending'), ('IN_PROGRESS', 'In Progress'),
        ('COMPLETED', 'Completed'), ('FAILED', 'Failed'),
    ], default='PENDING', max_length=20)
    backup_type = models.CharField(choices=[
        ('MANUAL', 'Manual'), ('SCHEDULED', 'Scheduled'),
        ('PRE_TRANSFER', 'Pre-Transfer'),
    ], default='MANUAL', max_length=20)
    file_path = models.CharField(max_length=500, blank=True)  # path to tarball
    size_bytes = models.BigIntegerField(default=0)
    metadata = models.JSONField(default=dict)  # snapshot of env vars, resources, config
    error_message = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

class ServerBackup(models.Model):
    """Full server export: all services + platform config + Traefik + SSL certs."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    status = models.CharField(max_length=20, default='PENDING')
    file_path = models.CharField(max_length=500, blank=True)
    size_bytes = models.BigIntegerField(default=0)
    services_included = models.JSONField(default=list)
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

class BackupSchedule(models.Model):
    """Cron-based backup schedule per service or server-wide."""
    service = models.ForeignKey('Service', on_delete=models.CASCADE, null=True, blank=True)
    is_server_wide = models.BooleanField(default=False)
    cron_expression = models.CharField(max_length=100, default='0 3 * * *')  # daily 3am
    retention_days = models.IntegerField(default=7)
    enabled = models.BooleanField(default=True)
    last_run = models.DateTimeField(null=True)
    next_run = models.DateTimeField(null=True)
```

### 1.2 Create backup service
File: `backend/apps/deployments/services/backup_service.py` [NEW]

Implement:
- `backup_service(service_id)` — creates a ServiceBackup:
  1. Export Docker container image: `docker commit {container_id}` → `docker save` to tarball
  2. Export all volumes: `docker run --rm -v {vol}:/data -v /backups:/backup alpine tar czf /backup/{vol}.tar.gz /data`
  3. Snapshot env vars: `EnvironmentVariable.objects.filter(service=service).values('key', 'value', 'is_secret')`
  4. Snapshot addon data: for each addon, call `addon_provisioner.create_backup(addon)`
  5. Save metadata JSON: resources, deploy_type, git_url, public_domain, health check config
  6. Package everything into single tarball: `{service_name}_{timestamp}.tar.gz`
  7. Store in `/opt/smsly-hosting/backups/services/`

- `backup_server()` — creates a ServerBackup:
  1. Iterate all services, call `backup_service()` for each
  2. Export Traefik config: copy `docker-compose.traefik.yml` + acme.json (SSL certs)
  3. Export platform DB: `pg_dump` the Django database
  4. Export `.env` files
  5. Package into `/opt/smsly-hosting/backups/server/{timestamp}.tar.gz`

- `restore_service(backup_id, target_service_id=None)`:
  1. Extract tarball
  2. `docker load` the image
  3. Recreate env vars from metadata
  4. Restore volumes
  5. Restore addon data
  6. Deploy the container

- `restore_server(backup_id)`:
  1. Extract server tarball
  2. Restore platform DB
  3. Restore each service
  4. Restore Traefik config + SSL certs
  5. Restart all containers

### 1.3 Create Celery tasks
File: `backend/apps/deployments/tasks.py` — add:

```python
@shared_task(bind=True, soft_time_limit=3600, time_limit=3900)
def create_service_backup_task(self, service_id, backup_type='MANUAL'):
    ...

@shared_task(bind=True, soft_time_limit=7200, time_limit=7500)
def create_server_backup_task(self):
    ...

@shared_task(bind=True, soft_time_limit=3600)
def restore_service_backup_task(self, backup_id, target_service_id=None):
    ...

@shared_task
def cleanup_old_backups_task():
    """Delete backups older than retention_days per schedule."""
    ...
```

### 1.4 Create API endpoints
File: `backend/apps/deployments/views.py` — add to existing ViewSet or create new:

```
POST   /api/v1/services/{id}/backup/          → create service backup
GET    /api/v1/services/{id}/backups/          → list service backups
POST   /api/v1/backups/{id}/restore/           → restore from backup
DELETE /api/v1/backups/{id}/                   → delete backup
POST   /api/v1/server/backup/                  → full server backup
GET    /api/v1/server/backups/                 → list server backups
POST   /api/v1/server/backups/{id}/restore/    → restore full server
GET    /api/v1/backups/{id}/download/          → download backup tarball
```

### 1.5 Create serializers
File: `backend/apps/deployments/serializers.py` — add:
- `ServiceBackupSerializer`
- `ServerBackupSerializer`
- `BackupScheduleSerializer`

## Phase 2: Scheduled Backups

### 2.1 Celery Beat schedule
File: `backend/config/celery.py` — add periodic task:
```python
app.conf.beat_schedule['check-backup-schedules'] = {
    'task': 'apps.deployments.tasks.run_scheduled_backups',
    'schedule': crontab(minute='*/15'),  # check every 15min
}
```

### 2.2 Schedule management API
```
POST   /api/v1/backup-schedules/               → create schedule
GET    /api/v1/backup-schedules/               → list schedules
PATCH  /api/v1/backup-schedules/{id}/          → update schedule
DELETE /api/v1/backup-schedules/{id}/          → delete schedule
```

## Phase 3: Frontend UI

### 3.1 Backups Tab per service
File: `frontend/src/components/settings/BackupsTab.tsx` [NEW]

- List all backups for the service (table with status, size, date)
- "Create Backup" button
- "Restore" button per backup (with confirmation modal)
- "Download" button per backup
- "Delete" button per backup
- Schedule configuration card (cron picker, retention days, enable/disable)

### 3.2 Server Backups page
File: `frontend/src/app/backups/page.tsx` [NEW]

- "Full Server Backup" button
- List all server backups
- Restore/Download/Delete actions
- Server-wide schedule config

### 3.3 Wire into navigation
File: `frontend/src/components/layout/Sidebar.tsx` — add "Backups" nav item

## Validation
1. Create a service backup → verify tarball contains image + volumes + metadata
2. Restore from backup to same service → verify service runs identically
3. Restore to NEW service → verify it creates everything fresh
4. Create server backup → verify all services included
5. Test scheduled backup → verify cron fires and cleanup works
6. Test backup download → verify tarball is downloadable

## Anti-Crash Rules
- Always use `soft_time_limit` on backup tasks (large volumes take time)
- Stream backup progress via WebSocket using existing `broadcast_status()`
- Never delete the original service during restore — create alongside
- Encrypt backup tarballs at rest using service's encryption key
- Validate tarball integrity (checksum) before restore
