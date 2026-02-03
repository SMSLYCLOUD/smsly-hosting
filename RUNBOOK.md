# SMSLY Hosting Operations Runbook

## Quick Reference

### Service Access
- **Dashboard**: `http://<YOUR_IP>:8090/`
- **Admin**: `http://<YOUR_IP>:8090/admin/`
- **API**: `http://<YOUR_IP>:8090/api/v1/`

### Installation Details
- **Root Directory**: `/opt/smsly-hosting`
- **Config File**: `/opt/smsly-hosting/.env`
- **Compose File**: `/opt/smsly-hosting/docker-compose.prod.yml`

### Container Names
| Service | Container Service Name |
|---------|------------------------|
| Backend | `backend` |
| Frontend | `frontend` |
| Database | `db` |
| Redis | `redis` |
| Nginx | `nginx` |
| Celery | `celery` |

---

## Common Operations

### View Logs

```bash
cd /opt/smsly-hosting

# All services
docker compose -f docker-compose.prod.yml logs -f

# Specific service (e.g., backend)
docker compose -f docker-compose.prod.yml logs -f backend
```

### Restart Services

```bash
cd /opt/smsly-hosting

# Restart all
docker compose -f docker-compose.prod.yml restart

# Restart specific service
docker compose -f docker-compose.prod.yml restart backend
```

### Apply Updates

To update the platform to the latest version:

```bash
cd /opt/smsly-hosting
git pull origin main
docker compose -f docker-compose.prod.yml up -d --build
docker compose -f docker-compose.prod.yml exec backend python manage.py migrate
```

### Database Backup

```bash
# Run the backup script
bash /opt/smsly-hosting/scripts/backup.sh
```

Backups are stored in `/opt/smsly-hosting/backups`.

### Restore Database

```bash
# Decompress and pipe to psql
gunzip -c /opt/smsly-hosting/backups/smsly_hosting_YYYYMMDD.sql.gz | \
  docker compose -f docker-compose.prod.yml exec -T db psql -U smsly_admin -d smsly_hosting
```

---

## Troubleshooting

### Dashboard Not Accessible on Port 8090

1.  **Check Containers**:
    ```bash
    docker compose -f docker-compose.prod.yml ps
    ```
    Ensure `nginx` is Up and mapped `0.0.0.0:8090->80/tcp`.

2.  **Check Firewall**:
    Ensure port 8090 is allowed on your VPS firewall (Security Group).
    ```bash
    ufw status
    # If active, allow 8090
    ufw allow 8090/tcp
    ```

3.  **Check Logs**:
    ```bash
    docker compose -f docker-compose.prod.yml logs nginx
    ```

### Database Connection Error

1.  **Check Backend Logs**:
    ```bash
    docker compose -f docker-compose.prod.yml logs backend
    ```
    Look for "connection refused" or "password authentication failed".

2.  **Verify .env**:
    Ensure `DATABASE_URL` matches the `POSTGRES_PASSWORD` in `.env`.

### Reset Admin Password

If you lose access to the `admin` account:

```bash
cd /opt/smsly-hosting
docker compose -f docker-compose.prod.yml exec backend python manage.py shell -c "from django.contrib.auth import get_user_model; User = get_user_model(); u = User.objects.get(username='admin'); u.set_password('new_password_here'); u.save()"
```

---

## Monitoring

- **Disk Space**: Monitor `df -h` to ensure Docker volumes have space.
- **Memory**: Monitor `docker stats` for high usage by `backend` or `celery`.
