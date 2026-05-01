# Jules Feature Prompts — SMSLY Hosting Platform

> **7 self-contained prompts for Google Jules.** Each prompt is designed to be run independently. Run them in the listed order. Each produces a functional, testable PR.

---

## Prompt 1: Real Addon & Template Logos (SVG Assets)

```
## Task: Replace emoji addon icons with real SVG logos

### Context
The file `frontend/src/components/addons/AddonsTab.tsx` defines addon types with emoji icons:
```typescript
const ADDON_TYPES = [
    { value: 'POSTGRES', label: 'PostgreSQL', icon: '🐘', ... },
    { value: 'REDIS', label: 'Redis', icon: '🔴', ... },
    { value: 'MYSQL', label: 'MySQL', icon: '🐬', ... },
    { value: 'MONGODB', label: 'MongoDB', icon: '🍃', ... },
    { value: 'MINIO', label: 'MinIO', icon: '📦', ... },
    { value: 'QDRANT', label: 'Qdrant', icon: '🧠', ... },
];
```

### Instructions
1. Create SVG logo files in `frontend/public/logos/addons/`:
   - `postgres.svg` — PostgreSQL elephant logo (blue #336791)
   - `redis.svg` — Redis logo (red #DC382D)
   - `mysql.svg` — MySQL dolphin logo (teal #4479A1)
   - `mongodb.svg` — MongoDB leaf logo (green #47A248)
   - `minio.svg` — MinIO logo (red #C72C48)
   - `qdrant.svg` — Qdrant logo (purple #7B68EE)
   - `elasticsearch.svg` — Elasticsearch logo (teal #005571)

   Each SVG must be a simple, recognizable silhouette/logo that works at 24x24px and 48x48px.
   Use minimal paths, no external dependencies, viewBox="0 0 48 48".

2. Modify `frontend/src/components/addons/AddonsTab.tsx`:
   - Import `Image` from `next/image`
   - Change the `ADDON_TYPES` array: replace `icon: '🐘'` with `logo: '/logos/addons/postgres.svg'`
   - Everywhere the emoji `icon` was rendered, render `<Image src={addonMeta.logo} alt={addonMeta.label} width={24} height={24} />` instead

3. Also update `frontend/src/components/topology/ServiceTopologyTab.tsx`:
   - In the `COLORS` map, keep the color assignments as-is (they're used for 3D rendering, not icons).

4. Do NOT change any backend files.
5. Do NOT install any new npm packages.
6. Verify: `cd frontend && npx tsc --noEmit` must pass (note: `next.config.js` has `ignoreBuildErrors: true`, but try to keep types clean). Run `npm run build` — it must exit 0.
```

---

## Prompt 2: Addon Environment Variable Visibility + Shortcodes

```
## Task: Make addon connection URLs visible as env vars + add shortcode support

### Codebase Context (CRITICAL — read these files first)

**Models:**
- `backend/apps/deployments/models_addons.py`: `Addon` model has `connection_url` (EncryptedCharField), `addon_type` (POSTGRES/REDIS/MYSQL/MONGODB/QDRANT/ELASTICSEARCH), `service` FK, `coolify_uuid`, `name`.
- `backend/apps/deployments/models.py`: `EnvironmentVariable` model has `service` FK, `key` (CharField), `value` (EncryptedCharField), `is_secret` (BooleanField). `TimeStampedModel` is the abstract base with `created_at`/`updated_at`. The model has `unique_together = ('service', 'key')`.
- `backend/apps/deployments/models.py`: `Service` model is the parent. `owner` FK to AUTH_USER_MODEL.

**Views:**
- `backend/apps/deployments/views_addons.py`: `AddonViewSet` with `queryset = Addon.objects.all()`, `permission_classes = [IsAuthenticated]`, ownership filter in `get_queryset()` via `self.queryset.filter(service__owner=self.request.user)`. `perform_create()` saves the addon then calls `provision_addon_task.delay(str(addon.id))`.
- `backend/apps/deployments/serializers.py`: `EnvVarSerializer` masks secret values with `'********'` in `to_representation()`.

**Provisioner:**
- `backend/services/addon_provisioner.py`: `AddonProvisioner.provision(self, addon)` returns `(container_id, connection_url)`. The connection URLs look like:
  - POSTGRES: `postgresql://postgres:<password>@<container_name>:5432/postgres`
  - REDIS: `redis://:<password>@<container_name>:6379/0`
  - MYSQL: `mysql://root:<password>@<container_name>:3306/addon_db`
  - MONGODB: `mongodb://root:<password>@<container_name>:27017/addon_db?authSource=admin`
  - QDRANT: `http://<container_name>:6333`
  - ELASTICSEARCH: `http://<container_name>:9200`

**URLs:**
- `backend/apps/deployments/urls.py`: Uses `DefaultRouter`. Addons registered: `router.register(r'addons', AddonViewSet, basename='addon')`. URL prefix for all routes is `/api/v1/` (configured in the project-level urls.py).

**Frontend:**
- `frontend/src/components/addons/AddonsTab.tsx`: Shows addons with status badges, backup/deprovision buttons. No connection URL display currently.
- `frontend/src/components/settings/EnvVarsTab.tsx`: Full env var editor with add/edit/delete/bulk-import/show-hide.

### Part A: Backend — Auto-inject addon env vars after provisioning

**File: `backend/apps/deployments/models_addons.py`**
Add a `parsed_credentials` property to `Addon`:
```python
@property
def parsed_credentials(self) -> dict:
    """Parse connection_url into individual credential components."""
    from urllib.parse import urlparse
    if not self.connection_url:
        return {}
    parsed = urlparse(self.connection_url)
    slug = self.name.upper().replace('-', '_').replace(' ', '_')
    result = {
        f'{slug}_URL': self.connection_url,
    }
    if parsed.hostname:
        result[f'{slug}_HOST'] = parsed.hostname
    if parsed.port:
        result[f'{slug}_PORT'] = str(parsed.port)
    if parsed.username:
        result[f'{slug}_USER'] = parsed.username
    if parsed.password:
        result[f'{slug}_PASSWORD'] = parsed.password
    if parsed.path and parsed.path != '/':
        result[f'{slug}_DATABASE'] = parsed.path.lstrip('/')
    return result
```

Also add a `source` field to `EnvironmentVariable` model in `backend/apps/deployments/models.py`:
```python
class EnvironmentVariable(TimeStampedModel):
    # ... existing fields ...
    SOURCE_CHOICES = [
        ('USER', 'User Defined'),
        ('ADDON', 'Addon Auto-Injected'),
        ('SHORTCODE', 'Shortcode Resolved'),
    ]
    source = models.CharField(
        max_length=20, choices=SOURCE_CHOICES,
        default='USER',
        help_text="Origin of this env var")
```

After adding the `source` field, create a migration:
```bash
cd backend && python manage.py makemigrations deployments -n "add_env_var_source_field"
```

**File: `backend/apps/deployments/tasks.py`**
Find the `provision_addon_task` function. After the line where `addon.connection_url` is set and `addon.save()` is called, add:
```python
# Auto-inject addon credentials as env vars
from .models import EnvironmentVariable
creds = addon.parsed_credentials
for key, value in creds.items():
    EnvironmentVariable.objects.update_or_create(
        service=addon.service,
        key=key,
        defaults={
            'value': value,
            'is_secret': key.endswith('_PASSWORD') or key.endswith('_URL'),
            'source': 'ADDON',
        }
    )
```

**File: `backend/apps/deployments/views_addons.py`**
Add a new action to `AddonViewSet`:
```python
@action(detail=True, methods=['get'])
def credentials(self, request, pk=None):
    """Return parsed connection credentials for this addon."""
    addon = self.get_object()
    if addon.status != 'ACTIVE':
        return Response(
            {'error': 'Addon not active'},
            status=status.HTTP_400_BAD_REQUEST)
    return Response(addon.parsed_credentials)
```

### Part B: Backend — Shortcode resolver

**New file: `backend/services/env_resolver.py`**
```python
"""
Resolves {{addon-name.KEY}} shortcodes in environment variable values.

Supported shortcode keys:
  {{addon-name.URL}}      -> full connection URL
  {{addon-name.HOST}}     -> hostname
  {{addon-name.PORT}}     -> port
  {{addon-name.USER}}     -> username
  {{addon-name.PASSWORD}} -> password
  {{addon-name.DATABASE}} -> database name
"""
import re
import logging
from apps.deployments.models_addons import Addon

logger = logging.getLogger(__name__)

SHORTCODE_RE = re.compile(r'\{\{([a-zA-Z0-9_-]+)\.(URL|HOST|PORT|USER|PASSWORD|DATABASE)\}\}')


def resolve_shortcodes(service_id: str, value: str) -> str:
    """Replace all {{addon-name.KEY}} shortcodes with real values."""
    if '{{' not in value:
        return value

    def replacer(match):
        addon_name = match.group(1)
        key_suffix = match.group(2)
        try:
            addon = Addon.objects.get(
                service_id=service_id,
                name__iexact=addon_name,
                status='ACTIVE',
            )
        except Addon.DoesNotExist:
            logger.warning(f"Shortcode references unknown addon: {addon_name}")
            return match.group(0)  # leave unresolved

        creds = addon.parsed_credentials
        slug = addon.name.upper().replace('-', '_').replace(' ', '_')
        full_key = f'{slug}_{key_suffix}'
        resolved = creds.get(full_key, match.group(0))
        return resolved

    return SHORTCODE_RE.sub(replacer, value)
```

### Part C: Frontend — Show addon credentials + shortcode hints

**File: `frontend/src/components/addons/AddonsTab.tsx`**
In the expanded section of each addon card (when `expandedId === addon.id`), add a "Connection Details" section:
- Add state: `const [credentials, setCredentials] = useState<Record<string, Record<string, string>>>({});`
- When expanding an addon, if `addon.status === 'ACTIVE'`, fetch credentials:
  ```typescript
  const res = await fetch(apiUrl(`/addons/${addon.id}/credentials/`), { headers: getHeaders() });
  if (res.ok) {
    const data = await res.json();
    setCredentials(prev => ({ ...prev, [addon.id]: data }));
  }
  ```
- Render a list of key-value pairs with:
  - Key name on the left (monospace, blue-300)
  - Value on the right with a "copy" button (masked by default, click eye icon to reveal)
  - Each row has `bg-zinc-900/30 rounded-lg border border-zinc-800/50 p-3`
  - Show a "Shortcode" hint below each credential: `{{addon-name.KEY}}` in a muted pill

**File: `frontend/src/components/settings/EnvVarsTab.tsx`**
- In the env var list, if `env.source === 'ADDON'`, show a small badge: `<span className="text-[9px] bg-purple-500/20 text-purple-300 px-1.5 py-0.5 rounded">ADDON</span>`
- Make ADDON-sourced vars read-only (disable edit/delete buttons)

**File: `frontend/src/lib/api.ts`**
Add to the `servicesApi` object (or wherever addons API methods live):
```typescript
addonCredentials: async (addonId: string): Promise<Record<string, string>> => {
  const res = await fetch(apiUrl(`/addons/${addonId}/credentials/`), { headers: getHeaders() });
  if (!res.ok) throw new Error('Failed to fetch credentials');
  return res.json();
},
```

### Part D: Update EnvVarSerializer
In `backend/apps/deployments/serializers.py`, update `EnvVarSerializer`:
```python
class Meta:
    model = EnvironmentVariable
    fields = ['id', 'key', 'value', 'is_secret', 'source']
```

### Verification
1. Django checks: `cd backend && python manage.py check --deploy` (ignore SECURE_HSTS_SECONDS and similar warnings — those are expected in dev)
2. Migration: `python manage.py makemigrations --check` must show no new migrations needed
3. Frontend build: `cd frontend && npm run build` must exit 0
4. Type check: `cd frontend && npx tsc --noEmit` (advisory — `ignoreBuildErrors: true` is set)
```

---

## Prompt 3: GitHub Repository Caching

```
## Task: Cache cloned GitHub repos to speed up repeat deployments

### Context
The deployment pipeline clones repositories fresh every time. This is slow for large repos. Implement a repo cache that uses `git fetch` for subsequent deploys.

Read these files first:
- `backend/services/deployer.py` — find where `git clone` happens
- `backend/apps/deployments/tasks.py` — find the build pipeline task

### Instructions

**New file: `backend/services/repo_cache.py`**
```python
"""
Git repository cache for faster repeat deployments.

Strategy:
  - First deploy: full `git clone --bare` into cache dir
  - Subsequent deploys: `git fetch` (seconds vs minutes)
  - LRU eviction: repos not used in 7 days get cleaned up
  - Thread-safe: uses file locks to prevent concurrent clone/fetch races

Cache location: /opt/smsly-cache/repos/<host>/<owner>/<repo>/
"""
import os
import time
import shutil
import subprocess
import logging
import hashlib
from pathlib import Path
from filelock import FileLock  # add 'filelock' to requirements.txt

logger = logging.getLogger(__name__)

CACHE_DIR = os.environ.get('REPO_CACHE_DIR', '/opt/smsly-cache/repos')
CACHE_MAX_AGE_DAYS = int(os.environ.get('REPO_CACHE_MAX_AGE_DAYS', '7'))


def _cache_path(repo_url: str) -> Path:
    """Deterministic cache path from repo URL."""
    # Normalize: strip .git suffix, lowercase
    url = repo_url.rstrip('/').lower()
    if url.endswith('.git'):
        url = url[:-4]
    # Extract host/owner/repo from URL
    # Handles: https://github.com/owner/repo, git@github.com:owner/repo
    if '://' in url:
        parts = url.split('://')[1].split('/')
    elif ':' in url:
        host_part, path_part = url.split(':', 1)
        host = host_part.split('@')[-1]
        parts = [host] + path_part.split('/')
    else:
        parts = [hashlib.sha256(url.encode()).hexdigest()[:12]]

    return Path(CACHE_DIR) / '/'.join(parts[-3:]) if len(parts) >= 3 else Path(CACHE_DIR) / '/'.join(parts)


def get_or_clone(repo_url: str, branch: str = 'main', token: str = None) -> str:
    """
    Get cached repo or clone fresh. Returns path to worktree checkout.

    Args:
        repo_url: Git repository URL
        branch: Branch to check out
        token: Optional GitHub token for private repos

    Returns:
        Absolute path to a directory with the checked-out code
    """
    cache = _cache_path(repo_url)
    bare_dir = cache / 'bare.git'
    lock_file = cache / '.lock'

    cache.mkdir(parents=True, exist_ok=True)

    # Inject token into URL for private repos
    auth_url = repo_url
    if token and '://' in repo_url:
        auth_url = repo_url.replace('https://', f'https://x-access-token:{token}@')

    with FileLock(str(lock_file), timeout=300):
        if (bare_dir / 'HEAD').exists():
            # Cached — just fetch
            logger.info(f"Cache HIT: fetching {repo_url}")
            subprocess.run(
                ['git', 'fetch', '--all', '--prune'],
                cwd=str(bare_dir),
                check=True,
                capture_output=True,
                timeout=120,
                env={**os.environ, 'GIT_TERMINAL_PROMPT': '0'},
            )
        else:
            # Cache MISS — full bare clone
            logger.info(f"Cache MISS: cloning {repo_url}")
            subprocess.run(
                ['git', 'clone', '--bare', auth_url, str(bare_dir)],
                check=True,
                capture_output=True,
                timeout=300,
                env={**os.environ, 'GIT_TERMINAL_PROMPT': '0'},
            )

    # Touch last_used timestamp for LRU
    (cache / '.last_used').write_text(str(time.time()))

    # Create a fresh worktree checkout for this build
    worktree_dir = cache / f'worktree-{branch}-{int(time.time())}'
    if worktree_dir.exists():
        shutil.rmtree(str(worktree_dir))

    subprocess.run(
        ['git', 'clone', '--local', '--branch', branch,
         '--single-branch', '--depth', '1',
         str(bare_dir), str(worktree_dir)],
        check=True,
        capture_output=True,
        timeout=60,
    )

    return str(worktree_dir)


def cleanup_old_caches():
    """Remove repos not used in CACHE_MAX_AGE_DAYS days."""
    if not os.path.exists(CACHE_DIR):
        return

    cutoff = time.time() - (CACHE_MAX_AGE_DAYS * 86400)
    cleaned = 0

    for root, dirs, files in os.walk(CACHE_DIR, topdown=False):
        last_used_file = os.path.join(root, '.last_used')
        if os.path.exists(last_used_file):
            try:
                ts = float(open(last_used_file).read().strip())
                if ts < cutoff:
                    shutil.rmtree(root)
                    cleaned += 1
                    logger.info(f"Evicted cache: {root}")
            except (ValueError, OSError):
                pass

    logger.info(f"Cache cleanup complete: evicted {cleaned} repos")


def cleanup_worktrees(repo_url: str, keep_latest: int = 2):
    """Clean up old worktree checkouts, keeping the N most recent."""
    cache = _cache_path(repo_url)
    worktrees = sorted(
        [d for d in cache.iterdir() if d.name.startswith('worktree-')],
        key=lambda d: d.stat().st_mtime,
        reverse=True,
    )
    for old in worktrees[keep_latest:]:
        shutil.rmtree(str(old), ignore_errors=True)
```

**Modify `backend/services/deployer.py`:**
Find the function that performs `git clone`. Replace the `git clone` subprocess call with:
```python
from services.repo_cache import get_or_clone, cleanup_worktrees
# Replace: subprocess.run(['git', 'clone', ...])
# With:
source_dir = get_or_clone(
    repo_url=service.repository_url,
    branch=service.branch,
    token=github_token,  # however the token is currently obtained
)
# After build completes, clean up old worktrees:
cleanup_worktrees(service.repository_url)
```

**Add `filelock` to requirements:**
In `backend/requirements.txt`, add: `filelock>=3.12`

**New management command: `backend/apps/deployments/management/commands/cleanup_repo_cache.py`**
```python
"""Management command to clean up old cached repos."""
from django.core.management.base import BaseCommand
from services.repo_cache import cleanup_old_caches


class Command(BaseCommand):
    help = 'Clean up cached git repos not used in 7 days'

    def handle(self, *args, **options):
        cleanup_old_caches()
        self.stdout.write(self.style.SUCCESS('Repo cache cleanup complete'))
```
Create the directory structure: `backend/apps/deployments/management/__init__.py` and `backend/apps/deployments/management/commands/__init__.py` (both empty `__init__.py` files) if they don't already exist.

### Verification
1. `cd backend && python manage.py check` must pass
2. `cd backend && python -c "from services.repo_cache import get_or_clone, cleanup_old_caches; print('OK')"` must print OK
3. `cd frontend && npm run build` must still pass (no frontend changes)
```

---

## Prompt 4: Resilient Platform Update & Upgrade System

```
## Task: Build a resilient self-update system for the SMSLY Hosting platform

### Context
The platform is installed on VPS servers via `install.sh`. Currently, updates are done by re-running `install.sh --update`. If an update fails mid-way (e.g., Docker build fails, migration fails), the platform can be left in a broken state with no easy rollback.

### Requirements
1. Pre-update snapshot of all running containers and their images
2. Atomic Docker Compose switch (blue-green for the platform itself)
3. Automatic rollback if health check fails after update
4. Update progress tracking via a new Django model
5. Update history with logs viewable in the dashboard

### Instructions

#### Part A: Backend Model

**New file: `backend/apps/deployments/models_updates.py`**
```python
"""Platform self-update tracking model."""
import uuid
from django.db import models


class PlatformUpdate(models.Model):
    """Tracks platform self-updates with rollback capability."""

    class Status(models.TextChoices):
        PENDING = 'PENDING', 'Pending'
        PULLING = 'PULLING', 'Pulling Images'
        BACKING_UP = 'BACKING_UP', 'Backing Up'
        MIGRATING = 'MIGRATING', 'Running Migrations'
        RESTARTING = 'RESTARTING', 'Restarting Services'
        HEALTH_CHECK = 'HEALTH_CHECK', 'Health Check'
        COMPLETED = 'COMPLETED', 'Completed'
        FAILED = 'FAILED', 'Failed'
        ROLLED_BACK = 'ROLLED_BACK', 'Rolled Back'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.PENDING)

    # Version tracking
    from_version = models.CharField(max_length=50, blank=True)
    to_version = models.CharField(max_length=50, blank=True)
    from_commit = models.CharField(max_length=40, blank=True)
    to_commit = models.CharField(max_length=40, blank=True)

    # Progress
    progress_percent = models.IntegerField(default=0)
    current_step = models.CharField(max_length=200, blank=True)
    logs = models.TextField(blank=True)

    # Rollback data
    snapshot_data = models.JSONField(
        default=dict, blank=True,
        help_text="Snapshot of container image tags before update")
    can_rollback = models.BooleanField(default=True)
    rollback_deadline = models.DateTimeField(null=True, blank=True)

    # Error
    error_message = models.TextField(blank=True)

    # Timing
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    initiated_by = models.CharField(
        max_length=50, default='manual',
        help_text="'manual', 'auto', or 'api'")

    class Meta:
        ordering = ['-created_at']

    def append_log(self, message: str):
        """Thread-safe log append."""
        import datetime
        ts = datetime.datetime.now().strftime('%H:%M:%S')
        self.logs += f"[{ts}] {message}\n"
        self.save(update_fields=['logs'])

    def __str__(self):
        return f"Update {self.id} ({self.status})"
```

Create migration: `python manage.py makemigrations deployments -n "add_platform_update_model"`

Register the import in `backend/apps/deployments/models.py`:
Add at the top imports section (near line 19, after the other model imports):
```python
from .models_updates import PlatformUpdate  # Platform update tracking
```

#### Part B: Update Engine

**New file: `backend/services/platform_updater.py`**
```python
"""
Resilient platform self-updater.

Update flow:
  1. Snapshot current state (image tags, container IDs)
  2. Git pull latest code
  3. Build new images
  4. Run Django migrations (with backup)
  5. Blue-green restart: start new containers, verify health, stop old
  6. If health check fails → automatic rollback

Rollback:
  - Revert to snapshot image tags
  - Re-tag and restart old containers
  - Revert migrations if possible
"""
import os
import subprocess
import logging
import time
from datetime import timedelta
from django.utils import timezone

logger = logging.getLogger(__name__)

INSTALL_DIR = os.environ.get('INSTALL_DIR', '/opt/smsly-hosting')
COMPOSE_FILE = os.path.join(INSTALL_DIR, 'docker-compose.prod.yml')
HEALTH_CHECK_URL = 'http://localhost:8090/api/v1/system/config/'
HEALTH_CHECK_RETRIES = 10
HEALTH_CHECK_INTERVAL = 5  # seconds


def _run(cmd: list, cwd: str = INSTALL_DIR, timeout: int = 300) -> tuple:
    """Run a command, return (success, output)."""
    try:
        result = subprocess.run(
            cmd, cwd=cwd, capture_output=True, text=True,
            timeout=timeout,
        )
        output = result.stdout + result.stderr
        return result.returncode == 0, output
    except subprocess.TimeoutExpired:
        return False, f"Command timed out after {timeout}s: {' '.join(cmd)}"
    except Exception as e:
        return False, str(e)


def snapshot_current_state() -> dict:
    """Capture current container image tags for rollback."""
    ok, output = _run(['docker', 'compose', '-f', COMPOSE_FILE, 'ps', '--format', 'json'])
    if not ok:
        return {}

    # Get image tags for each service
    ok, output = _run(['docker', 'compose', '-f', COMPOSE_FILE, 'config', '--images'])
    images = {}
    if ok:
        for line in output.strip().split('\n'):
            if line.strip():
                images[line.strip()] = True  # Store image names

    # Get current git commit
    ok, commit = _run(['git', 'rev-parse', 'HEAD'])

    return {
        'images': images,
        'commit': commit.strip() if ok else '',
        'timestamp': timezone.now().isoformat(),
    }


def check_health() -> bool:
    """Check if platform is healthy after update."""
    import urllib.request
    for attempt in range(HEALTH_CHECK_RETRIES):
        try:
            req = urllib.request.urlopen(HEALTH_CHECK_URL, timeout=5)
            if req.status == 200:
                return True
        except Exception:
            pass
        time.sleep(HEALTH_CHECK_INTERVAL)
    return False


def perform_update(update_record) -> bool:
    """
    Execute platform update with rollback protection.

    Args:
        update_record: PlatformUpdate model instance

    Returns:
        True if update succeeded, False if failed/rolled back
    """
    try:
        # Step 1: Snapshot
        update_record.status = 'BACKING_UP'
        update_record.current_step = 'Creating pre-update snapshot'
        update_record.progress_percent = 10
        update_record.save()
        update_record.append_log('Creating snapshot of current state...')

        snapshot = snapshot_current_state()
        update_record.snapshot_data = snapshot
        update_record.from_commit = snapshot.get('commit', '')
        update_record.save()
        update_record.append_log(f"Snapshot created: commit={snapshot.get('commit', 'unknown')}")

        # Step 2: Git pull
        update_record.status = 'PULLING'
        update_record.current_step = 'Pulling latest code'
        update_record.progress_percent = 20
        update_record.save()

        ok, output = _run(['git', 'pull', '--ff-only', 'origin', 'main'])
        if not ok:
            raise Exception(f"Git pull failed: {output}")
        update_record.append_log(f"Git pull complete: {output[:200]}")

        # Get new commit
        ok, new_commit = _run(['git', 'rev-parse', 'HEAD'])
        if ok:
            update_record.to_commit = new_commit.strip()
            update_record.save()

        # Step 3: Build new images
        update_record.current_step = 'Building new Docker images'
        update_record.progress_percent = 40
        update_record.save()
        update_record.append_log('Building Docker images...')

        ok, output = _run(
            ['docker', 'compose', '-f', COMPOSE_FILE, 'build', '--no-cache'],
            timeout=600,
        )
        if not ok:
            raise Exception(f"Docker build failed: {output[-500:]}")
        update_record.append_log('Docker build complete')

        # Step 4: Run migrations
        update_record.status = 'MIGRATING'
        update_record.current_step = 'Running database migrations'
        update_record.progress_percent = 60
        update_record.save()

        ok, output = _run([
            'docker', 'compose', '-f', COMPOSE_FILE,
            'run', '--rm', 'backend',
            'python', 'manage.py', 'migrate', '--noinput',
        ])
        if not ok:
            raise Exception(f"Migration failed: {output[-500:]}")
        update_record.append_log('Migrations complete')

        # Step 5: Restart services
        update_record.status = 'RESTARTING'
        update_record.current_step = 'Restarting services'
        update_record.progress_percent = 75
        update_record.save()

        ok, output = _run([
            'docker', 'compose', '-f', COMPOSE_FILE,
            'up', '-d', '--remove-orphans',
        ])
        if not ok:
            raise Exception(f"Restart failed: {output[-500:]}")
        update_record.append_log('Services restarted')

        # Step 6: Health check
        update_record.status = 'HEALTH_CHECK'
        update_record.current_step = 'Verifying platform health'
        update_record.progress_percent = 90
        update_record.save()

        if not check_health():
            raise Exception('Health check failed after update')
        update_record.append_log('Health check passed!')

        # Success
        update_record.status = 'COMPLETED'
        update_record.current_step = 'Update completed successfully'
        update_record.progress_percent = 100
        update_record.completed_at = timezone.now()
        update_record.rollback_deadline = timezone.now() + timedelta(hours=1)
        update_record.save()
        update_record.append_log('✓ Update completed successfully')
        return True

    except Exception as e:
        error_msg = str(e)
        update_record.append_log(f'✗ Update failed: {error_msg}')
        update_record.error_message = error_msg
        update_record.save()

        # Automatic rollback
        return _rollback(update_record)


def _rollback(update_record) -> bool:
    """Roll back to the snapshot state."""
    update_record.append_log('Starting automatic rollback...')

    snapshot = update_record.snapshot_data
    old_commit = snapshot.get('commit', '')

    if old_commit:
        ok, output = _run(['git', 'checkout', old_commit])
        update_record.append_log(
            f"Git rollback: {'OK' if ok else 'FAILED'}")

    # Rebuild with old code
    ok, output = _run(
        ['docker', 'compose', '-f', COMPOSE_FILE, 'build'],
        timeout=600,
    )
    update_record.append_log(
        f"Rebuild old images: {'OK' if ok else 'FAILED'}")

    # Restart old containers
    ok, output = _run([
        'docker', 'compose', '-f', COMPOSE_FILE,
        'up', '-d', '--remove-orphans',
    ])
    update_record.append_log(
        f"Restart old containers: {'OK' if ok else 'FAILED'}")

    if check_health():
        update_record.status = 'ROLLED_BACK'
        update_record.append_log('✓ Rollback successful, platform is healthy')
    else:
        update_record.status = 'FAILED'
        update_record.append_log('✗ Rollback failed — manual intervention required')

    update_record.can_rollback = False
    update_record.completed_at = timezone.now()
    update_record.save()
    return False
```

#### Part C: API Views

**New file: `backend/apps/deployments/views_updates.py`**
```python
"""Views for platform self-update management."""
from rest_framework import viewsets, permissions, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework import serializers as drf_serializers
from .models_updates import PlatformUpdate


class PlatformUpdateSerializer(drf_serializers.ModelSerializer):
    class Meta:
        model = PlatformUpdate
        fields = [
            'id', 'status', 'from_version', 'to_version',
            'from_commit', 'to_commit', 'progress_percent',
            'current_step', 'logs', 'error_message',
            'can_rollback', 'rollback_deadline',
            'created_at', 'completed_at', 'initiated_by',
        ]
        read_only_fields = fields


class PlatformUpdateViewSet(viewsets.ReadOnlyModelViewSet):
    """List and view platform updates. Admin-only trigger for update/rollback."""
    queryset = PlatformUpdate.objects.all()
    serializer_class = PlatformUpdateSerializer
    permission_classes = [permissions.IsAdminUser]

    @action(detail=False, methods=['post'])
    def trigger(self, request):
        """Trigger a platform update."""
        # Check no update is already in progress
        in_progress = PlatformUpdate.objects.filter(
            status__in=['PENDING', 'PULLING', 'BACKING_UP',
                       'MIGRATING', 'RESTARTING', 'HEALTH_CHECK']
        ).exists()
        if in_progress:
            return Response(
                {'error': 'An update is already in progress'},
                status=status.HTTP_409_CONFLICT)

        update = PlatformUpdate.objects.create(initiated_by='api')

        # Run async
        from .tasks import platform_update_task
        platform_update_task.delay(str(update.id))

        return Response(
            PlatformUpdateSerializer(update).data,
            status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'])
    def rollback(self, request, pk=None):
        """Manually trigger rollback for a completed update."""
        update = self.get_object()
        if not update.can_rollback:
            return Response(
                {'error': 'Rollback not available'},
                status=status.HTTP_400_BAD_REQUEST)

        from services.platform_updater import _rollback
        _rollback(update)
        return Response(PlatformUpdateSerializer(update).data)
```

#### Part D: Celery Task

Add to `backend/apps/deployments/tasks.py`:
```python
@shared_task(bind=True, max_retries=0)
def platform_update_task(self, update_id: str):
    """Execute platform update in background."""
    from .models_updates import PlatformUpdate
    from services.platform_updater import perform_update

    try:
        update = PlatformUpdate.objects.get(id=update_id)
    except PlatformUpdate.DoesNotExist:
        return

    perform_update(update)
```

#### Part E: URL Registration

In `backend/apps/deployments/urls.py`:
Add import: `from .views_updates import PlatformUpdateViewSet`
Add registration: `router.register(r'platform-updates', PlatformUpdateViewSet, basename='platform-update')`

#### Part F: Frontend — Update Management Page

**New file: `frontend/src/app/settings/updates/page.tsx`**

Create a page at `/settings/updates` that:
1. Lists all PlatformUpdate records in reverse chronological order
2. Shows progress bars for in-progress updates
3. Shows "Trigger Update" button (only visible to admin users)
4. Shows "Rollback" button for updates that have `can_rollback: true`
5. Shows logs in a monospace scrollable terminal-style viewer
6. Uses the existing dark theme (bg-zinc-950, text-zinc-100, etc.)

Use the existing settings page layout pattern from `frontend/src/app/settings/page.tsx`.

API endpoints:
- `GET /api/v1/platform-updates/` — list updates
- `POST /api/v1/platform-updates/trigger/` — trigger update
- `POST /api/v1/platform-updates/{id}/rollback/` — rollback

### Verification
1. `cd backend && python manage.py makemigrations --check` → no new migrations needed
2. `cd backend && python manage.py check` → passes
3. `cd frontend && npm run build` → exits 0
4. `python -c "from apps.deployments.models_updates import PlatformUpdate; print('OK')"` → OK
5. `python -c "from services.platform_updater import perform_update; print('OK')"` → OK
```

---

## Prompt 5: Transfer Engine (Zero-Downtime Service Migration)

```
## Task: Implement the migration orchestration engine for the existing ServerTransfer model

### Context — Read These Files (MANDATORY)
- `backend/apps/deployments/models_transfer.py`: ServerTransfer model — ALREADY EXISTS. Status choices: PREPARING → UPLOADING → RESTORING → DNS_CUTOVER → VERIFYING → COMPLETED / FAILED / ROLLED_BACK.  Fields: source_server_ip, target_server_ip, target_ssh_key, transfer_type (SERVICE/FULL), service FK, progress_percent, current_step, logs, error_message, can_rollback, rollback_deadline. source_backup FK to ServiceBackup.
- `backend/apps/deployments/views_transfer.py`: ServerTransferViewSet — ALREADY EXISTS. Has create() and rollback() actions. create() calls `execute_server_transfer_task.delay(str(transfer.id))`. rollback() calls `rollback_transfer_task.delay(str(transfer.id))`.
- `backend/apps/deployments/serializers_transfer.py`: ServerTransferSerializer + ServerTransferCreateSerializer — ALREADY EXIST.
- `backend/apps/deployments/tasks.py`: MUST contain `execute_server_transfer_task` and `rollback_transfer_task` — these are imported in views_transfer.py but may not be implemented yet.
- `backend/apps/deployments/models_backup.py`: ServiceBackup model — ALREADY EXISTS. Has file_path, metadata (JSONField), status, backup_type choices including 'PRE_TRANSFER'.
- `backend/services/addon_provisioner.py`: AddonProvisioner — has create_backup() and restore_backup() methods. create_backup() returns backup file path as string.
- `backend/apps/deployments/models_addons.py`: Addon model — has connection_url, addon_type, service FK.
- `backend/apps/deployments/models_servers.py`: ManagedServer model — has host, api_url, api_token, ssh_key, ssh_port, ssh_user.
- `backend/apps/deployments/models.py`: Service model, EnvironmentVariable model (has service FK, key, value EncryptedCharField, is_secret).

### Instructions

**New file: `backend/services/transfer_engine.py`**

Implement a `TransferEngine` class that orchestrates the full migration:

```python
"""
Service migration engine for zero-downtime transfer between servers.

Transfer flow:
  1. PREPARING: Create ServiceBackup (container snapshot + volumes + addon backups)
  2. UPLOADING: SCP the backup tarball to target server via SSH
  3. RESTORING: Call target server's API to import the backup
  4. DNS_CUTOVER: Update Caddy config to route to target
  5. VERIFYING: Health check on target
  6. COMPLETED: Mark done, keep source as standby
  7. On failure at any step → FAILED with error logs
  8. On rollback → restart source, revert DNS
"""
```

The class must:
1. Use `paramiko` for SSH operations (add `paramiko>=3.4` to `requirements.txt`)
2. Use the `target_ssh_key` from the ServerTransfer model for SSH auth
3. Create a `ServiceBackup` with `backup_type='PRE_TRANSFER'` before starting
4. Use `AddonProvisioner.create_backup()` for each addon attached to the service
5. Package everything into a tarball: container image, volumes, env vars (decrypted JSON), addon backups
6. SCP the tarball to the target server
7. Call the target server's REST API to trigger restore (the target must also be running SMSLY Hosting)
8. Verify health on target by hitting the service's health check endpoint
9. Update the `ServerTransfer` model's status, progress_percent, current_step, and logs at each step
10. Implement rollback: restart source container, revert DNS

**Modify `backend/apps/deployments/tasks.py`:**
Find or add `execute_server_transfer_task` and `rollback_transfer_task`:
```python
@shared_task(bind=True, max_retries=0)
def execute_server_transfer_task(self, transfer_id: str):
    from .models_transfer import ServerTransfer
    from services.transfer_engine import TransferEngine

    transfer = ServerTransfer.objects.get(id=transfer_id)
    engine = TransferEngine(transfer)
    engine.execute()


@shared_task(bind=True, max_retries=0)
def rollback_transfer_task(self, transfer_id: str):
    from .models_transfer import ServerTransfer
    from services.transfer_engine import TransferEngine

    transfer = ServerTransfer.objects.get(id=transfer_id)
    engine = TransferEngine(transfer)
    engine.rollback()
```

**Add `paramiko` to `backend/requirements.txt`.**

### Verification
1. `cd backend && python manage.py check` → passes
2. `python -c "from services.transfer_engine import TransferEngine; print('OK')"` → OK
3. Import check: `python -c "from apps.deployments.tasks import execute_server_transfer_task, rollback_transfer_task; print('OK')"` → OK
4. `cd frontend && npm run build` → exits 0 (no frontend changes)
```

---

## Prompt 6: Activity Feed / Audit Log UI

```
## Task: Build a real-time activity feed page for platform events

### Context
- `backend/apps/deployments/models_audit.py`: `AuditLog` model ALREADY EXISTS with `action`, `service`, `user`, `details`, `ip_address`, `created_at`. Already has serializer and viewset registered at `/api/v1/audit-logs/`.
- `backend/apps/deployments/urls.py`: `router.register(r'audit-logs', AuditLogViewSet, basename='auditlog')` — already registered.

### Instructions

**New file: `frontend/src/app/activity/page.tsx`**

Create a full-page activity feed at `/activity`:
1. Fetch from `GET /api/v1/audit-logs/?ordering=-created_at&limit=50`
2. Show events as a timeline with:
   - Colored dot (green=deployment, blue=addon, yellow=env change, red=delete, purple=transfer)
   - Action description with service name linked to `/services/{id}`
   - Relative timestamp ("2 minutes ago")
   - User avatar + name
   - Expanding detail panel (click to show `details` field)
3. Auto-refresh every 10 seconds
4. Filter bar: "All", "Deployments", "Addons", "Env Vars", "Transfers"
5. Dark theme matching the platform (bg-zinc-950, border-zinc-800, etc.)
6. Import Lucide icons: Activity, Clock, Filter, ChevronDown

**Add navigation link:**
In the main layout/sidebar/nav (find it in `frontend/src/components/` or `frontend/src/app/layout.tsx`), add an "Activity" link to `/activity` with the Activity icon.

### Verification
1. `cd frontend && npm run build` → exits 0
2. Navigate to `/activity` — page renders without errors
```

---

## Prompt 7: Resource Usage Alerts

```
## Task: Add threshold-based resource alerts for services

### Context
- `backend/apps/deployments/views_metrics.py`: MetricsViewSet — retrieves CPU/memory metrics
- `frontend/src/app/services/[id]/page.tsx`: Service detail page

### Instructions

**Backend: `backend/apps/notifications/models.py`**
If this file doesn't exist, create it:
```python
import uuid
from django.db import models
from django.conf import settings
from apps.deployments.models import Service


class ResourceAlert(models.Model):
    """Tracks resource usage alerts for services."""
    class Severity(models.TextChoices):
        INFO = 'INFO', 'Info'
        WARNING = 'WARNING', 'Warning'
        CRITICAL = 'CRITICAL', 'Critical'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    service = models.ForeignKey(
        Service, on_delete=models.CASCADE, related_name='resource_alerts')
    severity = models.CharField(
        max_length=20, choices=Severity.choices, default=Severity.WARNING)
    metric = models.CharField(max_length=50)  # 'cpu', 'memory', 'disk'
    threshold = models.FloatField()  # percentage
    current_value = models.FloatField()
    message = models.TextField()
    acknowledged = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
```

Register the `notifications` app in Django settings if not already present.
Create migrations.

**Frontend: `frontend/src/components/dashboard/ResourceAlerts.tsx`**
A component that:
1. Fetches alerts for the current service
2. Shows a banner at the top of the service detail page when alerts exist
3. Color-coded: yellow for WARNING, red for CRITICAL
4. Dismiss button to acknowledge
5. Shows: "CPU usage at 92% (threshold: 80%)"

### Verification
1. `cd backend && python manage.py check` → passes
2. `cd backend && python manage.py makemigrations --check` → no pending
3. `cd frontend && npm run build` → exits 0
```

---

## General Rules for ALL Prompts

1. **NEVER** use `cd` inside a script or chain. Always use `cwd` argument.
2. **NEVER** hardcode secrets. Use `os.environ[]` (crash if missing is good).
3. **ALWAYS** filter querysets by `request.user` — never expose other users' data.
4. **ALWAYS** use `EncryptedCharField` for sensitive data (passwords, tokens, keys).
5. **imports follow the existing pattern**: `from .models import ...`, `from .models_addons import ...`, `from services.xxx import ...`
6. **Frontend theme**: bg-zinc-950, text-zinc-100, border-zinc-800, accent colors from existing palette. All Lucide icons.
7. **Do NOT install new npm packages** unless explicitly required. The project uses: next 14, lucide-react, tailwindcss.
8. **Do NOT modify** any file not explicitly mentioned in the prompt.
9. **Every prompt's verification section MUST pass** before creating the PR.
