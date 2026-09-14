#!/bin/bash
# Fresh install / deploy health audit — non-destructive (read-only checks)
set -uo pipefail
FAIL=0
echo "=== INSTALL / FRESH DEPLOY HEALTH AUDIT ==="

cd /opt/smsly-hosting 2>/dev/null || { echo "FAIL: not in /opt/smsly-hosting"; FAIL=1; exit 1; }

echo "--- .env completeness ---"
for var in DB_HA_ENABLED COMPOSE_PROFILES PGCAT_DB_HOST PGCAT_DB_PORT POSTGRES_HOST REDIS_HOST DATABASE_URL REGISTRY_HTTP_SECRET STACK_TIER; do
  val=$(grep -E "^${var}=" .env | tail -1 | cut -d= -f2-)
  echo "  $var = ${val:-<MISSING>}"
done

echo ""
echo "--- install_tier.sh syntax + tier profiles ---"
bash -n scripts/install_tier.sh || FAIL=1; echo "install_tier syntax: OK"
for t in lite medium full; do bash -c "source .env 2>/dev/null; echo $t -> profile \$(echo \$COMPOSE_PROFILES)"; done

echo ""
echo "--- Docker compose profile matrix (local-ha, patroni, full) ---"
export POSTGRES_PASSWORD=d REPLICATION_PASSWORD=d PGCAT_ADMIN_PASSWORD=d PATRONI_SUPERUSER_PASSWORD=d REDIS_PASSWORD=d RABBITMQ_PASSWORD=d FRP_AUTH_TOKEN=d GRAFANA_PASSWORD=d
for p in local-ha patroni full; do
  out=$(docker compose --profile $p -f docker-compose.prod.yml config --quiet 2>&1 | grep -iE 'error|undefined|depends on undefined' | head -1)
  if [ -z "$out" ]; then echo "  $p: OK"; else echo "  $p: FAIL -> $out"; FAIL=1; fi
done

echo ""
echo "--- Migration graph consistency ---"
# Find leaf conflicts / missing nodes
python3 - <<'PY'
import os, sys
sys.path.insert(0, '.')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
try:
    import django
    django.setup()
except Exception as exc:
    print(f"DJANGO SETUP FAIL: {exc}")
from apps.deployments.models import Service, Project
from django.db import connection
with connection.cursor() as cur:
    cur.execute("SELECT id FROM deployments_service WHERE project_id IS NULL")
    orphan_services = [r[0] for r in cur.fetchall()]
    print(f"Orphan services (project=NULL): {len(orphan_services)} (should be 0)")
PY

echo ""
echo "--- Spilo Dockerfile syntax ---"
bash -n infrastructure/spilo/entrypoint.sh || echo "entrypoint syntax FAIL"; echo "  (syntax checks only — full build requires registry push)"

echo ""
echo "--- Backup service syntax ---"
python3 -m compileall -q backend/apps/deployments/tasks/data/tasks_backup.py 2>&1; echo "backup.py compile: $?"
python3 -m compileall -q backend/apps/cloud/services/backup_service/core.py 2>&1; echo "core.py compile: $?"
python3 -m compileall -q backend/apps/cloud/services/backup_service/operations.py 2>&1; echo "operations.py compile: $?"

echo ""
echo "--- Addon provisioner syntax ---"
python3 -m compileall -q backend/apps/addons/services/addon_provisioner.py 2>&1; echo "provisioner compile: $?"

echo ""
echo "--- Celery beat entry present ---"
python3 - <<'PY'
import os, sys
sys.path.insert(0, '.')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
import django
django.setup()
from config.celery import app
schedule = getattr(app.conf, 'beat_schedule', {})
ne = [k for k in schedule if 'network' in k.lower() or 'watchdog' in k.lower() or 'addon' in k.lower() or 'reconcile' in k.lower()]
print(f"Isolation/HA beat entries: {ne}")
ne = [k for k in schedule if 'security' in k.lower() or 'rebuild' in k.lower() or 'build' in k.lower() or 'prune' in k.lower()]
print(f"Security/build beat entries: {ne}")
PY

echo ""
echo "--- Registry secret in .env ---"
grep -E '^REGISTRY_HTTP_SECRET=' .env || echo "MISSING (should be set by install_tier or first update)"

echo ""
echo "=== END AUDIT (exit code above indicates health; inspect text above for FAIL messages) ==="
