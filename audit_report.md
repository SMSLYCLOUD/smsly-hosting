# SMSLY PaaS Security and Reliability Audit Report

[HIGH] ./fix-metrics.sh:10 — Hardcoded container name used
Current code: docker exec smsly-hosting-backend-1 wget -qO- http://127.0.0.1:8000/metrics 2>&1 | head -10
Fix: Use dynamic container resolution or configuration variable

[HIGH] ./install.sh:781 — Hardcoded container name used
Current code: local allowed_hosts=("localhost" "127.0.0.1" "backend" "smsly-hosting-backend-1")
Fix: Use dynamic container resolution or configuration variable

[HIGH] ./install.sh:1411 — Hardcoded container name used
Current code: backend_container="$(resolve_container_target "smsly-hosting-backend-1")"
Fix: Use dynamic container resolution or configuration variable

[HIGH] ./install.sh:3296 — Hardcoded container name used
Current code: ensure_container_on_network "smsly-net" "smsly-hosting-backend-1"
Fix: Use dynamic container resolution or configuration variable

[HIGH] ./install.sh:4509 — Hardcoded container name used
Current code: backend_container="$(resolve_container_target "smsly-hosting-backend-1")"
Fix: Use dynamic container resolution or configuration variable

[HIGH] ./install.sh:5063 — Hardcoded container name used
Current code: oom_containers="smsly-hosting-backend-1 smsly-hosting-db-1 smsly-hosting-pgcat-1 smsly-hosting-celery-1 smsly-hosting-celery-deploy-1 smsly-hosting-celery-fast-1 smsly-hosting-celery-beat-1 smsly-socket-proxy"
Fix: Use dynamic container resolution or configuration variable

[HIGH] ./install.sh:5065 — Hardcoded container name used
Current code: oom_containers="smsly-hosting-backend-1 smsly-hosting-celery-worker-1 smsly-hosting-socket-proxy-1"
Fix: Use dynamic container resolution or configuration variable

[HIGH] ./install.sh:6568 — Hardcoded container name used
Current code: CRITICAL_CONTAINERS=(smsly-hosting-traefik-1 smsly-hosting-backend-1 smsly-hosting-celery-worker-1 smsly-hosting-socket-proxy-1)
Fix: Use dynamic container resolution or configuration variable

[HIGH] ./install.sh:6570 — Hardcoded container name used
Current code: CRITICAL_CONTAINERS=(smsly-hosting-backend-1 smsly-hosting-db-1 smsly-hosting-pgcat-1)
Fix: Use dynamic container resolution or configuration variable

[HIGH] ./verify-obs.sh:2 — Hardcoded container name used
Current code: docker inspect --format='{{.State.Health.Status}}' smsly-hosting-backend-1
Fix: Use dynamic container resolution or configuration variable

[HIGH] ./verify-obs.sh:5 — Hardcoded container name used
Current code: docker exec smsly-hosting-backend-1 wget -qO- http://127.0.0.1:8000/metrics 2>&1 | head -10
Fix: Use dynamic container resolution or configuration variable

[HIGH] ./verify-obs.sh:19 — Hardcoded container name used
Current code: docker exec smsly-hosting-backend-1 wget -qO- 'http://127.0.0.1:8000/api/v1/observability/loki/query/?query=%7Bcompose_service%3D~%22.%2B%22%7D&start=now-15m&limit=5' 2>&1 | head -c 500
Fix: Use dynamic container resolution or configuration variable

[HIGH] ./deep_trace.py:50 — Hardcoded container name used
Current code: if 'smsly-hosting-backend-1' in line_str:
Fix: Use dynamic container resolution or configuration variable

[HIGH] ./backend/install.sh:781 — Hardcoded container name used
Current code: local allowed_hosts=("localhost" "127.0.0.1" "backend" "smsly-hosting-backend-1")
Fix: Use dynamic container resolution or configuration variable

[HIGH] ./backend/install.sh:1396 — Hardcoded container name used
Current code: backend_container="$(resolve_container_target "smsly-hosting-backend-1")"
Fix: Use dynamic container resolution or configuration variable

[HIGH] ./backend/install.sh:3136 — Hardcoded container name used
Current code: ensure_container_on_network "smsly-net" "smsly-hosting-backend-1"
Fix: Use dynamic container resolution or configuration variable

[HIGH] ./backend/install.sh:4303 — Hardcoded container name used
Current code: backend_container="$(resolve_container_target "smsly-hosting-backend-1")"
Fix: Use dynamic container resolution or configuration variable

[HIGH] ./backend/install.sh:4840 — Hardcoded container name used
Current code: oom_containers="smsly-hosting-backend-1 smsly-hosting-db-1 smsly-hosting-pgcat-1 smsly-hosting-celery-1 smsly-hosting-celery-deploy-1 smsly-hosting-celery-fast-1 smsly-hosting-celery-beat-1 smsly-socket-proxy"
Fix: Use dynamic container resolution or configuration variable

[HIGH] ./backend/install.sh:4842 — Hardcoded container name used
Current code: oom_containers="smsly-hosting-backend-1 smsly-hosting-celery-worker-1 smsly-hosting-socket-proxy-1"
Fix: Use dynamic container resolution or configuration variable

[HIGH] ./backend/install.sh:6282 — Hardcoded container name used
Current code: CRITICAL_CONTAINERS=(smsly-hosting-traefik-1 smsly-hosting-backend-1 smsly-hosting-celery-worker-1 smsly-hosting-socket-proxy-1)
Fix: Use dynamic container resolution or configuration variable

[HIGH] ./backend/install.sh:6284 — Hardcoded container name used
Current code: CRITICAL_CONTAINERS=(smsly-hosting-backend-1 smsly-hosting-db-1 smsly-hosting-pgcat-1)
Fix: Use dynamic container resolution or configuration variable

[MEDIUM] ./backend/services/addon_provisioner.py:1369 — Unhandled None response from Loki/Prometheus proxy
Current code: resp = requests.get(url, timeout=2)
Fix: Add explicit check for None or connection errors from metrics backend

[HIGH] ./backend/config/settings.py:146 — Hardcoded container name used
Current code: _BASE_HOSTS = ['localhost', '127.0.0.1', 'backend', 'smsly-hosting-backend-1']
Fix: Use dynamic container resolution or configuration variable

[LOW] ./backend/apps/intelligence/tasks.py:112 — Missing bind=True/max_retries on potentially critical retry task
Current code: @shared_task
Fix: Add bind=True, max_retries=3 to @shared_task

[LOW] ./backend/apps/billing/views.py:57 — Missing try/except block in write operation view
Current code: def create(self, validated_data):
Fix: Wrap business logic in try/except to handle errors gracefully

[LOW] ./backend/apps/billing/views.py:61 — Missing try/except block in write operation view
Current code: def update(self, instance, validated_data):
Fix: Wrap business logic in try/except to handle errors gracefully

[LOW] ./backend/apps/billing/views.py:70 — Missing try/except block in write operation view
Current code: def create(self, validated_data):
Fix: Wrap business logic in try/except to handle errors gracefully

[LOW] ./backend/apps/billing/views.py:74 — Missing try/except block in write operation view
Current code: def update(self, instance, validated_data):
Fix: Wrap business logic in try/except to handle errors gracefully

[LOW] ./backend/apps/notifications/tasks.py:427 — Missing bind=True/max_retries on potentially critical retry task
Current code: @shared_task(name='notifications.notify_deploy_event', queue='fast')
Fix: Add bind=True, max_retries=3 to @shared_task

[LOW] ./backend/apps/notifications/tasks.py:499 — Missing bind=True/max_retries on potentially critical retry task
Current code: @shared_task(name='notifications.notify_backup_completed', queue='fast')
Fix: Add bind=True, max_retries=3 to @shared_task

[MEDIUM] ./backend/apps/core/views_observability.py:81 — Unhandled None response from Loki/Prometheus proxy
Current code: resp = requests.get(
Fix: Add explicit check for None or connection errors from metrics backend

[MEDIUM] ./backend/apps/core/views_observability.py:182 — Unhandled None response from Loki/Prometheus proxy
Current code: resp = requests.get(
Fix: Add explicit check for None or connection errors from metrics backend

[MEDIUM] ./backend/apps/core/views_observability.py:222 — Unhandled None response from Loki/Prometheus proxy
Current code: resp = requests.get(
Fix: Add explicit check for None or connection errors from metrics backend

[MEDIUM] ./backend/apps/core/views_observability.py:248 — Unhandled None response from Loki/Prometheus proxy
Current code: resp = requests.get(
Fix: Add explicit check for None or connection errors from metrics backend

[MEDIUM] ./backend/apps/core/views_observability.py:1 — Missing health checks on observability service
Current code: N/A
Fix: Implement /health endpoint or logic to verify Loki/Prometheus connection

[LOW] ./backend/apps/core/views.py:212 — Missing try/except block in write operation view
Current code: def create(self, request):
Fix: Wrap business logic in try/except to handle errors gracefully

[LOW] ./backend/apps/core/views.py:247 — Missing try/except block in write operation view
Current code: def create(self, request):
Fix: Wrap business logic in try/except to handle errors gracefully

[LOW] ./backend/apps/deployments/views_webhooks.py:22 — Missing try/except block in write operation view
Current code: def post(self, request):
Fix: Wrap business logic in try/except to handle errors gracefully

[LOW] ./backend/apps/deployments/views_tunnels.py:119 — Missing try/except block in write operation view
Current code: def create(self, request):
Fix: Wrap business logic in try/except to handle errors gracefully

[LOW] ./backend/apps/deployments/views_analysis.py:502 — Missing try/except block in write operation view
Current code: def post(self, request):
Fix: Wrap business logic in try/except to handle errors gracefully

[LOW] ./backend/apps/deployments/views.py:4344 — Missing try/except block in write operation view
Current code: def post(self, request):
Fix: Wrap business logic in try/except to handle errors gracefully

[LOW] ./backend/apps/deployments/views.py:4647 — Missing try/except block in write operation view
Current code: def post(self, request):
Fix: Wrap business logic in try/except to handle errors gracefully

[LOW] ./backend/apps/deployments/views.py:4779 — Missing try/except block in write operation view
Current code: def create(self, request, *args, **kwargs):
Fix: Wrap business logic in try/except to handle errors gracefully

[LOW] ./backend/apps/deployments/views.py:5023 — Missing try/except block in write operation view
Current code: def create(self, request, *args, **kwargs):
Fix: Wrap business logic in try/except to handle errors gracefully

[LOW] ./backend/apps/deployments/views.py:5026 — Missing try/except block in write operation view
Current code: def update(self, request, *args, **kwargs):
Fix: Wrap business logic in try/except to handle errors gracefully

[LOW] ./backend/apps/deployments/views_safedeploy.py:68 — Missing try/except block in write operation view
Current code: def create(self, request, *args, **kwargs):
Fix: Wrap business logic in try/except to handle errors gracefully

[LOW] ./backend/apps/deployments/views_transfer.py:216 — Missing try/except block in write operation view
Current code: def create(self, request, *args, **kwargs):
Fix: Wrap business logic in try/except to handle errors gracefully

[MEDIUM] ./backend/apps/deployments/tasks.py:110 — Missing @shared_task decorator on Celery task
Current code: def enqueue_smart_deploy_task(
Fix: Add @shared_task decorator

[MEDIUM] ./backend/apps/deployments/tasks.py:1157 — Missing @shared_task decorator on Celery task
Current code: def smart_deploy_task(self, deployment_id: str, provider_id: str,
Fix: Add @shared_task decorator

[MEDIUM] ./backend/apps/deployments/tasks.py:1292 — Missing @shared_task decorator on Celery task
Current code: def resume_deploy_task(self, deployment_id: str, provider_id: str):
Fix: Add @shared_task decorator

[MEDIUM] ./backend/apps/deployments/tasks.py:2294 — Unhandled None response from Loki/Prometheus proxy
Current code: response = requests.get(
Fix: Add explicit check for None or connection errors from metrics backend

[LOW] ./backend/apps/deployments/tasks.py:4153 — Missing bind=True/max_retries on potentially critical retry task
Current code: @shared_task(bind=True)
Fix: Add bind=True, max_retries=3 to @shared_task

[LOW] ./backend/apps/deployments/tasks.py:4188 — Missing bind=True/max_retries on potentially critical retry task
Current code: @shared_task(bind=True, soft_time_limit=7200, time_limit=7500)
Fix: Add bind=True, max_retries=3 to @shared_task

[LOW] ./backend/apps/deployments/tasks.py:4193 — Missing bind=True/max_retries on potentially critical retry task
Current code: @shared_task
Fix: Add bind=True, max_retries=3 to @shared_task

[LOW] ./backend/apps/deployments/tasks.py:4227 — Missing bind=True/max_retries on potentially critical retry task
Current code: @shared_task
Fix: Add bind=True, max_retries=3 to @shared_task

[MEDIUM] ./backend/apps/deployments/services/scaling_ai.py:177 — Unhandled None response from Loki/Prometheus proxy
Current code: resp = requests.get(
Fix: Add explicit check for None or connection errors from metrics backend

[CRITICAL] ./backend/apps/deployments/services/backup_service.py:130 — Insecure use of subprocess for tar creation without input validation
Current code: subprocess.run(f'tar -czf {backup_path} {target_dir}', shell=True)
Fix: Use Python's built-in tarfile library or pass arguments as a list without shell=True to prevent command injection

[HIGH] ./backend/apps/deployments/services/backup_service.py:22 — Potential data loss risk in chunked backup due to lack of rollback on network failure
Current code: def create_backup(self):
Fix: Implement a rollback mechanism to clean up partial backups if the S3 upload fails mid-chunk

[HIGH] ./backend/apps/deployments/services/transfer_service.py:792 — Hardcoded container name used
Current code: settings, "REMOTE_BACKEND_CONTAINER_NAME", "smsly-hosting-backend-1"
Fix: Use dynamic container resolution or configuration variable

[CRITICAL] ./backend/apps/deployments/services/transfer_service.py:45 — Cross-platform migration failure due to missing domain remapping
Current code: def execute_transfer(self):
Fix: Add a domain remapping step to update Caddy/Traefik routing rules on the target server after migration

[MEDIUM] ./backend/apps/deployments/services/provisioner.py:550 — Unhandled None response from Loki/Prometheus proxy
Current code: response = requests.get(script_url, timeout=30)
Fix: Add explicit check for None or connection errors from metrics backend

[MEDIUM] ./backend/apps/deployments/metrics/adapter.py:287 — Unhandled None response from Loki/Prometheus proxy
Current code: resp = requests.get(
Fix: Add explicit check for None or connection errors from metrics backend

[HIGH] ./backend/apps/deployments/tests/test_multi_server_local_harness.py:122 — Hardcoded container name used
Current code: ssh.exec_command.return_value = "smsly-hosting-backend-1"
Fix: Use dynamic container resolution or configuration variable

[HIGH] ./backend/apps/deployments/tests/test_multi_server_local_harness.py:195 — Hardcoded container name used
Current code: return "smsly-hosting-backend-1\n"
Fix: Use dynamic container resolution or configuration variable

[HIGH] ./scripts/install-podman.sh:152 — Hardcoded container name used
Current code: echo "Note: Monitor logs with 'podman logs -f smsly-hosting-backend-1'"
Fix: Use dynamic container resolution or configuration variable

[HIGH] ./scripts/safe-update.sh:93 — Hardcoded container name used
Current code: smsly-hosting-backend-1 smsly-hosting-frontend-1 smsly-hosting-caddy-1
Fix: Use dynamic container resolution or configuration variable

[HIGH] ./scripts/safe-update.sh:22 — Missing capacity checks before resource creation (RAM/Disk)
Current code: docker-compose pull
Fix: Add pre-flight checks for available RAM and disk space on the target node before pulling new images

[HIGH] ./scripts/agent-lite.sh:251 — Hardcoded container name used
Current code: new_hosts="localhost,127.0.0.1,backend,smsly-hosting-backend-1"
Fix: Use dynamic container resolution or configuration variable

[HIGH] ./scripts/monitor_infra.sh:15 — Missing health checks on observability service
Current code: docker ps
Fix: Implement /health endpoint or logic to verify Loki/Prometheus connection

[LOW] ./frontend/src/components/topology/EcosystemTopology.tsx:34 — Unhandled promise rejection in API call
Current code: const res = await fetch('/api/v1/topology/ecosystem/', {
Fix: Add .catch() block or use try/catch to handle network errors

[LOW] ./frontend/src/components/layout/Navbar.tsx:46 — Unhandled promise rejection in API call
Current code: const adminRes = await fetch(`${window.location.origin}/api/v1/system/config/`, {
Fix: Add .catch() block or use try/catch to handle network errors

[LOW] ./frontend/src/app/register/page.tsx:77 — Unhandled promise rejection in API call
Current code: const tokenResponse = await fetch(`${BACKEND_URL}/api/v1/auth/session-token/`, {
Fix: Add .catch() block or use try/catch to handle network errors

[LOW] ./frontend/src/app/marketplace/page.tsx:61 — Unhandled promise rejection in API call
Current code: const servicesRes = await fetch("/api/v1/services/", {
Fix: Add .catch() block or use try/catch to handle network errors

[LOW] ./frontend/src/app/servers/page.tsx:43 — Unhandled promise rejection in API call
Current code: const res = await fetch(path, {
Fix: Add .catch() block or use try/catch to handle network errors

[LOW] ./frontend/src/app/ecosystem/page.tsx:96 — Unhandled promise rejection in API call
Current code: const res = await fetch(path, {
Fix: Add .catch() block or use try/catch to handle network errors

[LOW] ./frontend/src/app/ecosystem/page.tsx:111 — Unhandled promise rejection in API call
Current code: const res = await fetch(path, {
Fix: Add .catch() block or use try/catch to handle network errors

[LOW] ./frontend/src/app/login/page.tsx:52 — Unhandled promise rejection in API call
Current code: const tokenResponse = await fetch(`${BACKEND_URL}/api/v1/auth/session-token/`, {
Fix: Add .catch() block or use try/catch to handle network errors

[LOW] ./frontend/src/hooks/useGraphData.ts:61 — Unhandled promise rejection in API call
Current code: return fetch('/api/v1/topology/ecosystem/', {
Fix: Add .catch() block or use try/catch to handle network errors

[LOW] ./frontend/src/lib/api.ts:12 — Unhandled promise rejection in API call
Current code: export const api = axios.create({
Fix: Add .catch() block or use try/catch to handle network errors
