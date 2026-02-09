---
description: Backup and restore production database
---

# Database Backup Workflow

## Create Backup

### 1. Create backup directory

// turbo

```bash
mkdir -p /opt/backups
```

### 2. Create PostgreSQL dump

// turbo

```bash
docker compose -f docker-compose.prod.yml exec -T db pg_dump -U smsly_admin smsly_hosting > /opt/backups/smsly_$(date +%Y%m%d_%H%M%S).sql
```

### 3. Compress backup

// turbo

```bash
gzip /opt/backups/smsly_*.sql
```

### 4. Verify backup

// turbo

```bash
ls -la /opt/backups/
```

---

## Restore from Backup

### 1. Stop backend services

```bash
docker compose -f docker-compose.prod.yml stop backend celery celery-beat
```

### 2. Decompress backup (if compressed)

```bash
gunzip /opt/backups/smsly_YYYYMMDD_HHMMSS.sql.gz
```

### 3. Restore database

```bash
docker compose -f docker-compose.prod.yml exec -T db psql -U smsly_admin -d smsly_hosting < /opt/backups/smsly_YYYYMMDD_HHMMSS.sql
```

### 4. Restart backend services

```bash
docker compose -f docker-compose.prod.yml start backend celery celery-beat
```

### 5. Verify restore

```bash
docker compose -f docker-compose.prod.yml exec backend python manage.py shell -c "from django.contrib.auth import get_user_model; print(f'Users: {get_user_model().objects.count()}')"
```

---

## Automated Daily Backup (cron)

Add to crontab (`crontab -e`):

```cron
# Daily backup at 2:00 AM
0 2 * * * cd /opt/smsly-hosting && docker compose -f docker-compose.prod.yml exec -T db pg_dump -U smsly_admin smsly_hosting | gzip > /opt/backups/smsly_$(date +\%Y\%m\%d).sql.gz

# Keep only last 7 days of backups
0 3 * * * find /opt/backups -name "smsly_*.sql.gz" -mtime +7 -delete
```

---

## Copy Backup Offsite

```bash
# Using rsync
rsync -avz /opt/backups/ user@backup-server:/backups/smsly/

# Using scp
scp /opt/backups/smsly_*.sql.gz user@backup-server:/backups/
```
