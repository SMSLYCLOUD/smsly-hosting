#!/bin/bash
# SMSLY Domain Verification Script
# Uses the backend API to check health and trigger domain verification
# via Celery tasks. Avoids brittle docker exec + Django import overhead.

cd /opt/smsly-hosting

log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') - $1" >> /var/log/smsly-domain-ssl.log
}

log "Starting domain verification process"

# Check if backend is running and healthy via HTTP (fast, no Python import overhead)
BACKEND_CID=$(docker compose ps -q backend || true)
if [ -z "$BACKEND_CID" ]; then
    log "ERROR: backend container is not running"
    exit 1
fi

# Wait for the backend to be truly ready (gunicorn workers started).
# Curl may succeed on /health/live before Django is fully bootstrapped.
# Use the public edge (port 80 -> Caddy -> backend); port 8000 is not
# published on the host.
for i in $(seq 1 12); do
    if curl -sS --max-time 5 http://localhost/health/live; then
        break
    fi
    log "Backend not ready yet (attempt $i/12)..."
    sleep 5
done

log "Backend API is responding — dispatching domain verification tasks..."

# Dispatch pending domain verification via Django management command.
# Uses the backend's own manage.py context so DJANGO_SETTINGS_MODULE
# and the Python path are always correct.
# `< /dev/null` is REQUIRED: under a detached screen the docker exec would
# otherwise try to read stdin, get SIGTTIN and stop forever.
docker exec "$BACKEND_CID" python manage.py shell -c "
from apps.domains.models import Domain
from apps.domains.tasks import verify_dns_and_provision_ssl_task

domains = Domain.objects.filter(status__in=['pending', 'dns_pending'])
count = domains.count()
print(f'Processing {count} pending domains...')
for domain in domains:
    print(f'  Queueing verification for: {domain.domain_name}')
    verify_dns_and_provision_ssl_task.delay(domain.id)
print(f'Queued {count} domain verification tasks.')
" < /dev/null 2>&1 | tee -a /var/log/smsly-domain-ssl.log

log "Domain verification process completed"
