# Grid → Railway Parity Sprint (Jules Prompt)

> **Goal**: Bring Grid to full Railway-level feature parity across 15 features, organized into 5 sequential phases. Each phase MUST be completed and verified before proceeding to the next.

---

## CRITICAL RULES FOR JULES

1. **Read before writing** — Always read existing files before modifying. Use the project's established patterns.
2. **One migration per model change** — Create a single migration file per phase.
3. **Backend Django 5.0** — Follow existing patterns in `backend/apps/deployments/`.
4. **Frontend Next.js 14** — Follow existing patterns in `frontend/src/app/`. Use `'use client'` for interactive pages. Use existing `DashboardShell` layout wrapper, `lucide-react` icons, `framer-motion` animations.
5. **Styling** — Use Tailwind CSS classes matching existing dark-mode design (zinc/slate backgrounds, blue/cyan/emerald accents, border-border, bg-card patterns).
6. **No breaking changes** — All new features must be additive. Never remove existing endpoints or models.
7. **After each phase** — Run `python manage.py check` and verify no import errors.

---

## CODEBASE ORIENTATION

```
smsly-hosting/
├── backend/
│   ├── apps/
│   │   ├── deployments/       # Core app — services, deployments, servers, addons, templates
│   │   │   ├── models.py      # Service, Deployment, EnvVar models
│   │   │   ├── models_addons.py   # ServiceAddon (Postgres, Redis, etc.)
│   │   │   ├── models_servers.py  # ManagedServer
│   │   │   ├── tasks.py       # Celery tasks — deploy_service, _build_*, _run_*
│   │   │   ├── views.py       # ServiceViewSet, DeploymentViewSet
│   │   │   ├── views_addons.py
│   │   │   ├── views_servers.py
│   │   │   ├── views_templates.py
│   │   │   └── services/
│   │   │       ├── scanner.py     # RepoScanner — AI-powered repo analysis
│   │   │       ├── provisioner.py # Server auto-provisioning via SSH
│   │   │       ├── blueprint_manager.py
│   │   │       └── caddy_manager.py
│   │   ├── billing/           # Stripe billing, plans, usage
│   │   ├── cloud/             # Multi-cloud adapters (AWS, GCP, Azure, local)
│   │   ├── intelligence/      # AI analysis endpoints
│   │   └── teams/             # Team/org management
│   ├── config/
│   │   ├── settings.py
│   │   ├── urls.py
│   │   └── celery.py
│   └── requirements.txt
├── frontend/
│   ├── src/
│   │   ├── app/               # Next.js 14 app router pages
│   │   │   ├── dashboard/     # Main dashboard
│   │   │   ├── services/      # Service detail page
│   │   │   │   └── [id]/page.tsx
│   │   │   ├── servers/       # Server fleet management
│   │   │   ├── settings/      # Settings tabs (General, Infra, Env Vars, Domains)
│   │   │   ├── new/           # New service deployment wizard
│   │   │   ├── templates/     # Template marketplace
│   │   │   └── billing/       # Billing page
│   │   ├── components/
│   │   │   ├── layout/
│   │   │   │   └── DashboardShell.tsx
│   │   │   ├── sidebar.tsx
│   │   │   ├── settings/      # Settings tab components
│   │   │   └── ui/            # Reusable UI components (Button, Input, Card, etc.)
│   │   └── lib/
│   │       └── api.ts         # API client with types
│   └── package.json
├── docker-compose.prod.yml
└── install.sh                 # Universal installer script
```

**Key existing patterns to follow:**

- **Models**: See `models.py` for Service, `models_addons.py` for ServiceAddon
- **Views**: DRF ModelViewSets with `@action` decorators for custom endpoints
- **Serializers**: Inline in views files (e.g., `views_servers.py`)
- **Celery tasks**: See `tasks.py` — use `@shared_task`, broadcast logs via WebSocket
- **Frontend API**: See `lib/api.ts` — `servicesApi`, `systemApi` patterns
- **Frontend pages**: Each page is a `'use client'` component wrapped in `<DashboardShell>`

---

## PHASE 1: Infrastructure Essentials (Tier 1 Critical)

### Feature 1: Persistent Storage / Volumes

**Why**: Users deploying WordPress, file storage, or any stateful app need data persistence across redeployments.

**Backend changes:**

1. **`backend/apps/deployments/models.py`** — Add to Service model:
   ```python
   class ServiceVolume(models.Model):
       service = models.ForeignKey(Service, on_delete=models.CASCADE, related_name='volumes')
       name = models.CharField(max_length=100)  # e.g. "uploads", "data"
       mount_path = models.CharField(max_length=255)  # e.g. "/app/uploads"
       size_gb = models.IntegerField(default=1)
       host_path = models.CharField(max_length=255, blank=True)  # auto-generated
       created_at = models.DateTimeField(auto_now_add=True)
   ```

2. **`backend/apps/deployments/tasks.py`** — In `_run_container()`, mount volumes:
   ```python
   # Before creating container, set up volume mounts
   volumes = service.volumes.all()
   volume_binds = {}
   for vol in volumes:
       host_dir = vol.host_path or f"/opt/smsly-hosting/volumes/{service.id}/{vol.name}"
       os.makedirs(host_dir, exist_ok=True)
       volume_binds[host_dir] = {'bind': vol.mount_path, 'mode': 'rw'}
   ```

3. **`backend/apps/deployments/views.py`** — Add nested CRUD for service volumes:
   ```python
   @action(detail=True, methods=["get", "post"])
   def volumes(self, request, pk=None):
       # GET: list volumes, POST: create volume
   
   @action(detail=True, methods=["delete"], url_path="volumes/(?P<volume_id>[^/.]+)")
   def delete_volume(self, request, pk=None, volume_id=None):
       # DELETE a specific volume
   ```

**Frontend changes:**

4. **`frontend/src/components/settings/VolumesTab.tsx`** — New settings tab:
   - List existing volumes (name, mount path, size)
   - "Add Volume" form with name + mount path + size
   - Delete button per volume
   - Warning: "Changing volumes requires redeployment"

5. **`frontend/src/app/services/[id]/page.tsx`** — Add "Volumes" tab to service settings

**Migration**: Create `0009_servicevolume.py`

---

### Feature 2: Auto-Scaling & Resource Limits

**Why**: Without CPU/memory caps, one runaway service can take down the whole server.

**Backend changes:**

1. **`backend/apps/deployments/models.py`** — Service model already has `cpu_cores` and `memory_mb`. Add:
   ```python
   min_replicas = models.IntegerField(default=1)
   max_replicas = models.IntegerField(default=1)
   cpu_threshold = models.IntegerField(default=80, help_text="Scale up when CPU exceeds this %")
   ```

2. **`backend/apps/deployments/tasks.py`** — In `_run_container()`, enforce resource limits:
   ```python
   container = docker_client.containers.run(
       ...
       mem_limit=f"{service.memory_mb}m",
       cpu_period=100000,
       cpu_quota=int(service.cpu_cores * 100000),
       restart_policy={"Name": "unless-stopped"},
   )
   ```

3. **`backend/apps/deployments/services/autoscaler.py`** — New file:
   ```python
   @shared_task
   def check_autoscale():
       """Periodic task: check CPU usage and scale services up/down."""
       for service in Service.objects.filter(max_replicas__gt=1):
           stats = get_container_stats(service)  # docker stats API
           if stats['cpu_percent'] > service.cpu_threshold:
               scale_up(service)
           elif stats['cpu_percent'] < service.cpu_threshold * 0.5:
               scale_down(service)
   ```

4. Register `check_autoscale` in Celery beat schedule in `config/celery.py`.

**Frontend changes:**

5. **`frontend/src/components/settings/ResourcesTab.tsx`** — New tab:
   - Sliders for CPU cores (0.25 - 4), Memory (128MB - 8192MB)
   - Min/Max replicas inputs
   - CPU threshold slider
   - "Apply Changes" button (requires redeployment)

---

### Feature 3: Build Caching

**Why**: Docker builds currently rebuild everything from scratch. Build caching makes redeployments 5-10x faster.

**Backend changes:**

1. **`backend/apps/deployments/tasks.py`** — Modify `_build_image()`:
   ```python
   # Use buildx with cache
   build_kwargs = {
       'path': source_dir,
       'tag': image_tag,
       'buildargs': build_args,
       'cache_from': [f"{image_tag}:cache"],  # Pull cache from previous build
   }
   
   # After successful build, tag as cache
   # docker tag <image> <image>:cache
   ```

2. **Add Docker BuildKit support** — Set environment variable in `docker-compose.prod.yml`:
   ```yaml
   backend:
     environment:
       - DOCKER_BUILDKIT=1
   ```

3. **Cache cleanup task** — Periodic task to prune old build cache:
   ```python
   @shared_task
   def cleanup_build_cache():
       """Remove build cache older than 7 days."""
       docker_client.api.prune_builds(filters={'until': '168h'})
   ```

---

### Feature 4: Health Check & Auto-Restart

**Why**: If a container crashes, it should auto-restart. Users shouldn't need to manually redeploy.

**Backend changes:**

1. **`backend/apps/deployments/models.py`** — Add to Service:
   ```python
   health_check_path = models.CharField(max_length=255, default="/", blank=True)
   health_check_interval = models.IntegerField(default=30, help_text="Seconds between checks")
   health_check_timeout = models.IntegerField(default=10)
   health_check_retries = models.IntegerField(default=3)
   auto_restart = models.BooleanField(default=True)
   ```

2. **`backend/apps/deployments/tasks.py`** — In `_run_container()`:
   ```python
   healthcheck_config = {
       'test': ['CMD-SHELL', f'curl -sf http://localhost:{service.internal_port}{service.health_check_path} || exit 1'],
       'interval': service.health_check_interval * 1_000_000_000,  # nanoseconds
       'timeout': service.health_check_timeout * 1_000_000_000,
       'retries': service.health_check_retries,
   }
   
   container = docker_client.containers.run(
       ...
       healthcheck=healthcheck_config,
       restart_policy={"Name": "unless-stopped"} if service.auto_restart else {"Name": "no"},
   )
   ```

3. **`backend/apps/deployments/services/health_monitor.py`** — Periodic task:
   ```python
   @shared_task
   def monitor_service_health():
       """Check all running services and update their health status."""
       docker_client = docker.from_env()
       for service in Service.objects.all():
           container_name = f"svc-{service.id[:8]}"
           try:
               container = docker_client.containers.get(container_name)
               health = container.attrs.get('State', {}).get('Health', {})
               status = health.get('Status', 'unknown')
               # Update service status in DB
               # If unhealthy for too long and auto_restart, trigger redeploy
           except docker.errors.NotFound:
               pass
   ```

**Frontend changes:**

4. **Health check settings in service settings** — Add to `GeneralTab.tsx` or create `HealthTab.tsx`:
   - Health check path input
   - Interval / timeout / retries
   - Auto-restart toggle
   - Current health status indicator (green/yellow/red pulse)

---

### Feature 5: Usage Metrics Dashboard

**Why**: Users need to see CPU, memory, bandwidth, and disk usage per service.

**Backend changes:**

1. **`backend/apps/deployments/models.py`** — New model:
   ```python
   class ServiceMetrics(models.Model):
       service = models.ForeignKey(Service, on_delete=models.CASCADE, related_name='metrics')
       timestamp = models.DateTimeField(auto_now_add=True)
       cpu_percent = models.FloatField(default=0)
       memory_mb = models.FloatField(default=0)
       memory_limit_mb = models.FloatField(default=0)
       network_rx_bytes = models.BigIntegerField(default=0)
       network_tx_bytes = models.BigIntegerField(default=0)
       disk_read_bytes = models.BigIntegerField(default=0)
       disk_write_bytes = models.BigIntegerField(default=0)
       
       class Meta:
           ordering = ['-timestamp']
           indexes = [
               models.Index(fields=['service', 'timestamp']),
           ]
   ```

2. **`backend/apps/deployments/services/metrics_collector.py`** — Periodic task:
   ```python
   @shared_task
   def collect_service_metrics():
       """Run every 60s — collect docker stats for all running containers."""
       docker_client = docker.from_env()
       for service in Service.objects.all():
           try:
               container = docker_client.containers.get(f"svc-{service.id[:8]}")
               stats = container.stats(stream=False)
               ServiceMetrics.objects.create(
                   service=service,
                   cpu_percent=calculate_cpu_percent(stats),
                   memory_mb=stats['memory_stats']['usage'] / 1024 / 1024,
                   memory_limit_mb=stats['memory_stats']['limit'] / 1024 / 1024,
                   network_rx_bytes=sum(v['rx_bytes'] for v in stats.get('networks', {}).values()),
                   network_tx_bytes=sum(v['tx_bytes'] for v in stats.get('networks', {}).values()),
               )
           except Exception:
               pass
       
       # Cleanup: delete metrics older than 30 days
       cutoff = timezone.now() - timedelta(days=30)
       ServiceMetrics.objects.filter(timestamp__lt=cutoff).delete()
   ```

3. **`backend/apps/deployments/views.py`** — Add metrics endpoint:
   ```python
   @action(detail=True, methods=["get"])
   def metrics(self, request, pk=None):
       service = self.get_object()
       period = request.query_params.get('period', '24h')
       # Return aggregated metrics for the period
   ```

**Frontend changes:**

4. **`frontend/src/components/settings/MetricsTab.tsx`** — New tab with charts:
   - CPU usage line chart (last 24h)
   - Memory usage line chart
   - Network I/O chart
   - Period selector: 1h, 6h, 24h, 7d, 30d
   - Use a lightweight chart library (add `recharts` to package.json)
   - Current stats cards: CPU %, Memory MB/Limit, Network In/Out

5. **`frontend/src/app/dashboard/page.tsx`** — Add aggregate metrics widget:
   - Total CPU/Memory across all services
   - Mini sparkline charts per service

**Migration**: Create `0010_servicevolume_autoscale_health_metrics.py` (consolidate all Phase 1 model changes into one migration)

**Celery beat schedule** — Add to `config/celery.py`:
```python
CELERY_BEAT_SCHEDULE = {
    'collect-metrics': {
        'task': 'apps.deployments.services.metrics_collector.collect_service_metrics',
        'schedule': 60.0,
    },
    'check-autoscale': {
        'task': 'apps.deployments.services.autoscaler.check_autoscale',
        'schedule': 30.0,
    },
    'monitor-health': {
        'task': 'apps.deployments.services.health_monitor.monitor_service_health',
        'schedule': 30.0,
    },
}
```

**Verification for Phase 1:**
```bash
python manage.py check
python manage.py makemigrations --check  # Should say "No changes detected"
pytest -q backend/apps/deployments
```

---

## PHASE 2: Developer Experience (Tier 2 Expected)

### Feature 6: Cron Jobs / Scheduled Tasks

**Backend:**

1. **`backend/apps/deployments/models.py`** — New model:
   ```python
   class CronJob(models.Model):
       service = models.ForeignKey(Service, on_delete=models.CASCADE, related_name='cron_jobs')
       name = models.CharField(max_length=100)
       command = models.TextField(help_text="Shell command to run inside the service container")
       schedule = models.CharField(max_length=100, help_text="Cron expression e.g. '*/5 * * * *'")
       enabled = models.BooleanField(default=True)
       last_run = models.DateTimeField(null=True, blank=True)
       last_status = models.CharField(max_length=20, choices=[('SUCCESS','Success'),('FAILED','Failed')], blank=True)
       last_output = models.TextField(blank=True)
       created_at = models.DateTimeField(auto_now_add=True)
   ```

2. **`backend/apps/deployments/services/cron_runner.py`** — Celery beat task:
   - Check all enabled CronJobs every minute
   - For matching schedules, exec command inside the service's running container via `docker exec`
   - Capture output and update `last_run`, `last_status`, `last_output`

**Frontend:**

3. **`frontend/src/components/settings/CronTab.tsx`**:
   - List of cron jobs with name, schedule, last run, status
   - "Add Cron Job" form: name, command, schedule (with helper presets: every 5m, hourly, daily, weekly)
   - Toggle enable/disable
   - "Run Now" button
   - View last output in a modal

---

### Feature 7: Private Networking

**Backend:**

1. **`backend/apps/deployments/tasks.py`** — When creating containers:
   ```python
   # Create a user-scoped Docker network for internal communication
   network_name = f"smsly-user-{service.owner_id}"
   try:
       network = docker_client.networks.get(network_name)
   except docker.errors.NotFound:
       network = docker_client.networks.create(network_name, driver="bridge")
   
   # Connect container to the private network
   # Services can reach each other via container name: http://svc-<id>:port
   ```

2. **`backend/apps/deployments/views.py`** — Add `internal_hostname` to Service serializer:
   ```python
   # Return the internal hostname that other services can use
   internal_hostname = serializers.SerializerMethodField()
   def get_internal_hostname(self, obj):
       return f"svc-{str(obj.id)[:8]}"
   ```

**Frontend:**

3. Show "Internal URL" in service settings: `http://svc-abc12345:3000`
4. Add tooltip explaining private networking

---

### Feature 8: Deployment Rollback

**Backend:**

1. **`backend/apps/deployments/views.py`** — Add rollback endpoint:
   ```python
   @action(detail=True, methods=["post"], url_path="rollback")
   def rollback(self, request, pk=None):
       deployment_id = request.data.get("deployment_id")
       # Get the previous deployment's image
       # Stop current container
       # Start container with previous image
       # Create a new Deployment record marking it as "rollback"
   ```

2. **`backend/apps/deployments/tasks.py`** — Keep previous 5 Docker images per service (don't prune them).

**Frontend:**

3. **Deployment history view** — In service detail page:
   - List of past deployments with commit hash, timestamp, status
   - "Rollback" button on each previous deployment
   - Confirmation modal: "Roll back to deployment from [date]?"

---

### Feature 9: Template Marketplace Enhancement

The template marketplace already exists at `frontend/src/app/templates/`. Enhance it:

**Backend:**

1. **`backend/apps/deployments/models.py`** — Enhance `ServiceTemplate`:
   ```python
   class ServiceTemplate(models.Model):
       # Add if not present:
       category = models.CharField(max_length=50)  # "CMS", "Database", "Analytics", etc.
       one_click = models.BooleanField(default=True)
       popularity = models.IntegerField(default=0)
       docker_compose = models.TextField(blank=True)  # For multi-service templates
   ```

2. **Seed popular templates** — Create a management command or data migration:
   - WordPress (PHP + MySQL)
   - Ghost Blog
   - Supabase (Postgres + Auth + API)
   - Plausible Analytics
   - Uptime Kuma
   - n8n (workflow automation)
   - Gitea (git hosting)
   - MinIO (S3-compatible storage)

**Frontend:**

3. Enhance `frontend/src/app/templates/page.tsx`:
   - Category filter tabs
   - Search bar
   - Popularity sorting
   - One-click deploy button that auto-fills the new service form

---

### Feature 10: Billing / Usage-Based Pricing

The billing app already exists at `backend/apps/billing/`. Enhance:

**Backend:**

1. **Usage metering** — Create task that aggregates `ServiceMetrics` into billing-hours:
   ```python
   @shared_task
   def calculate_usage_billing():
       """Run hourly — calculate compute-hours per service for billing."""
       for service in Service.objects.all():
           hours = calculate_running_hours(service)
           cpu_hours = hours * service.cpu_cores
           memory_gb_hours = hours * (service.memory_mb / 1024)
           UsageRecord.objects.create(
               service=service,
               period=timezone.now(),
               cpu_hours=cpu_hours,
               memory_gb_hours=memory_gb_hours,
           )
   ```

**Frontend:**

2. **`frontend/src/app/billing/page.tsx`** — Enhance billing page:
   - Current month usage breakdown per service
   - Cost estimation based on resource usage
   - Usage graph over time

---

## PHASE 3: Power Features (Tier 3 Differentiators)

### Feature 11: PR Preview Environments

**Backend:**

1. **`backend/apps/deployments/services/preview_manager.py`**:
   - Webhook endpoint for GitHub PR events
   - On PR open: deploy a preview service from the PR branch
   - On PR close/merge: destroy the preview service
   - Preview URL: `pr-{number}.{service-name}.cloud.smsly.cloud`

2. **`backend/apps/deployments/models.py`**:
   ```python
   class PreviewDeployment(models.Model):
       service = models.ForeignKey(Service, on_delete=models.CASCADE)
       pr_number = models.IntegerField()
       branch = models.CharField(max_length=255)
       preview_url = models.URLField()
       status = models.CharField(max_length=20)
       created_at = models.DateTimeField(auto_now_add=True)
   ```

**Frontend:**

3. In service settings, add "Enable PR Previews" toggle
4. Show active preview deployments with their URLs

---

### Feature 12: CI/CD Webhooks

**Backend:**

1. **`backend/apps/deployments/views.py`**:
   ```python
   class WebhookView(APIView):
       """GitHub webhook receiver for auto-deploy on push."""
       authentication_classes = []  # Webhook uses signature verification
       
       def post(self, request):
           # Verify GitHub signature (X-Hub-Signature-256)
           # Parse push event
           # Find matching service by repo URL + branch
           # Trigger deployment
   ```

2. **Service model additions**:
   ```python
   webhook_secret = models.CharField(max_length=255, blank=True)
   auto_deploy_branch = models.CharField(max_length=100, default="main")
   auto_deploy_enabled = models.BooleanField(default=False)
   ```

**Frontend:**

3. In service settings: "Auto Deploy" section
   - Enable/disable toggle
   - Branch selector
   - Webhook URL display with copy button
   - Webhook secret display

---

### Feature 13: Database GUI (Query Editor)

**Backend:**

1. **`backend/apps/deployments/views_addons.py`** — Add query endpoint:
   ```python
   @action(detail=True, methods=["post"], url_path="query")
   def run_query(self, request, pk=None):
       addon = self.get_object()
       if addon.addon_type != 'postgres':
           return Response({"error": "Only Postgres addons support queries"}, status=400)
       # Connect to the addon's database
       # Execute read-only query (SELECT only for safety)
       # Return results as JSON table
   ```

**Frontend:**

2. **`frontend/src/components/settings/DatabaseTab.tsx`** — New tab for DB addons:
   - SQL editor textarea with syntax highlighting
   - "Run Query" button (SELECT only)
   - Results table with column headers
   - Connection string display
   - Warning: "Only SELECT queries are allowed for safety"

---

### Feature 14: Team/Org Management

The teams app already exists at `backend/apps/teams/`. Enhance:

**Backend:**

1. Review existing Team/TeamMember models and enhance with roles:
   ```python
   class TeamMember(models.Model):
       ROLE_CHOICES = [
           ('owner', 'Owner'),
           ('admin', 'Admin'),
           ('developer', 'Developer'),
           ('viewer', 'Viewer'),
       ]
       team = models.ForeignKey(Team, on_delete=models.CASCADE)
       user = models.ForeignKey(User, on_delete=models.CASCADE)
       role = models.CharField(max_length=20, choices=ROLE_CHOICES, default='developer')
   ```

2. **Permission checks** — Add `TeamPermissionMixin` to all views

**Frontend:**

3. **`frontend/src/app/settings/page.tsx`** → Add "Team" settings tab:
   - Invite members by email
   - Role selector (Owner/Admin/Developer/Viewer)
   - Member list with role badges
   - Remove member button

---

### Feature 15: CLI Tool

**This is a separate project. Create a new directory:**

1. **`cli/`** — Python CLI tool using `click`:
   ```
   cli/
   ├── setup.py
   ├── Grid/
   │   ├── __init__.py
   │   ├── cli.py        # Main CLI entry point
   │   ├── api.py        # API client
   │   └── config.py     # Local config (~/.Grid/config.json)
   ```

2. **Commands**:
   ```bash
   Grid login           # Authenticate with API token
   Grid services list   # List all services
   Grid deploy          # Deploy current directory
   Grid logs <service>  # Stream logs
   Grid env set KEY=VAL # Set environment variable
   Grid ssh <service>   # SSH into service container
   ```

3. **Publish to PyPI** as `Grid-cli`

---

## VERIFICATION AFTER ALL PHASES

```bash
# Backend checks
cd backend
python manage.py check --deploy
python manage.py makemigrations --check
pytest -q

# Frontend checks
cd frontend
npm run build
npx tsc --noEmit

# Integration
docker compose -f docker-compose.prod.yml build
```

## PRIORITY ORDER

Execute phases in this exact order: Phase 1 → Phase 2 → Phase 3. Within each phase, implement features in the numbered order. Do NOT skip ahead.

Each feature should be committed separately with a descriptive commit message following the pattern:
```
feat(deployments): add persistent volumes support
feat(deployments): add auto-scaling and resource limits
feat(deployments): add build caching for faster deploys
...
```
