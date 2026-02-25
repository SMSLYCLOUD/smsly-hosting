## Task: Implement features from 58 deleted stale branches

The monorepo had 58+ stale unmerged PR branches. They were deleted because they were duplicates (11 IP-spoofing variants, 5 tabs-accessibility variants etc.) from automated tools. This task captures the GOALS of those branches so the work can be done properly.

Read `jules_feature_prompts.md` for the main 7 features. This file covers the ADDITIONAL improvements those deleted branches were attempting.

### 1. Content Security Policy (CSP) Middleware — Backend

**From branch:** `sentinel/add-csp-middleware-backend` (4 commits, deleted)

Add CSP headers to Django backend responses.

**File: `backend/middleware/csp.py`** (new)
```python
"""Content Security Policy middleware for Django."""
from django.conf import settings


class CSPMiddleware:
    """Add Content-Security-Policy headers to all responses."""

    def __init__(self, get_response):
        self.get_response = get_response
        self.csp = getattr(settings, 'CSP_POLICY', self._default_csp())

    def __call__(self, request):
        response = self.get_response(request)
        response['Content-Security-Policy'] = self.csp
        response['X-Content-Type-Options'] = 'nosniff'
        response['X-Frame-Options'] = 'DENY'
        response['Referrer-Policy'] = 'strict-origin-when-cross-origin'
        response['Permissions-Policy'] = 'camera=(), microphone=(), geolocation=()'
        return response

    @staticmethod
    def _default_csp() -> str:
        return (
            "default-src 'self'; "
            "script-src 'self'; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: https:; "
            "font-src 'self'; "
            "connect-src 'self'; "
            "frame-ancestors 'none'"
        )
```

**Modify `backend/config/settings.py`:**
Add `'middleware.csp.CSPMiddleware'` to the MIDDLEWARE list, after SecurityMiddleware.

---

### 2. Enhanced Security Headers — Backend

**From branch:** `sentinel-security-headers-enhancement` (3 commits, deleted)

Already partially covered by #1 above. Additionally:
- Add `Strict-Transport-Security: max-age=31536000; includeSubDomains` when `USE_SSL=true`
- Add `X-XSS-Protection: 0` (modern browsers, rely on CSP instead)
- Ensure `X-Content-Type-Options: nosniff` on ALL responses including API

---

### 3. Fix Committed Secrets — Repo Scan

**From branch:** `sentinel/fix-committed-secrets` (3 commits, deleted)

Scan the repo for any accidentally committed secrets. Steps:
1. Run: `git log -p -- '*.env' '*.env.*' '*.key' '*.pem' | grep -E '(password|secret|token|api_key)='` on the smsly-hosting repo
2. If any secrets found in git history, rotate them
3. Add `.env`, `*.key`, `*.pem` to `.gitignore` if not already present
4. Ensure `.env.example` has placeholder values only, never real secrets

---

### 4. OTP Bypass Vulnerability Fix

**From branch:** `fix-otp-bypass-vulnerability` (1 commit, deleted)

This was for smsly-backend (identity service), not smsly-hosting. Skip if not applicable.

---

### 5. Globe3D Performance Optimization — Frontend

**From branch:** `bolt/optimize-globe3d` (4 commits, deleted)

Optimize the 3D globe/Force Graph component in `frontend/src/components/topology/ServiceTopologyTab.tsx`:
1. Use `React.memo()` on the component
2. Throttle the `onEngineStop` and `onNodeClick` callbacks
3. Reduce particle count and link opacity for large graphs (>50 nodes)
4. Add `suspense` loading boundary around the dynamic import
5. Set `enableNodeDrag={false}` when in read-only/overview mode

---

### 6. Async Visit Logging — Frontend

**From branch:** `bolt-async-visit-log` (3 commits, deleted)

Add lightweight analytics tracking:
1. Log page visits to the audit log API (`POST /api/v1/audit-logs/`) with action `PAGE_VISIT`
2. Use `navigator.sendBeacon()` for non-blocking, fire-and-forget logging
3. Only log authenticated page visits (don't log login/register pages)
4. Include: page path, timestamp, user agent

---

### 7. Blueprint System — Deploy SMSLY Ecosystem as One-Click Stacks

**From branch:** `ecosystem-income-report` (4 commits, 241 files in smsly-hosting)

This branch added a **Blueprint system** that lets admins deploy the entire SMSLY product suite (voice, video, sms, identity, security-gateway) as managed services with shared infrastructure. This is the income-generation feature.

#### 7a. Blueprint Manager Service

**New file: `backend/apps/deployments/services/blueprint_manager.py`**
```python
"""Blueprint Manager module."""
import json
import os
import logging
import secrets
from string import Template
from django.conf import settings
from apps.deployments.models import Service, Deployment, EnvironmentVariable
from apps.deployments.models_addons import Addon
from apps.deployments.tasks import smart_deploy_task, provision_addon_task
from apps.cloud.models import CloudProvider

logger = logging.getLogger(__name__)


class BlueprintManager:
    def __init__(self, provider: CloudProvider, user):
        self.provider = provider
        self.user = user

    def load_blueprint(self, name: str):
        path = os.path.join(settings.BASE_DIR, 'blueprints', f'{name}.json')
        with open(path, 'r') as f:
            data = json.load(f)
        self.validate_schema(data)
        return data

    def validate_schema(self, data: dict):
        required_fields = ['name', 'description', 'version', 'category', 'services']
        for field in required_fields:
            if field not in data:
                raise ValueError(f"Blueprint missing required field: {field}")
        for service in data.get('services', []):
            if 'name' not in service or 'image' not in service:
                raise ValueError(f"Service definition incomplete: {service}")

    def deploy(self, blueprint_name: str, user_env_vars: dict = None):
        if user_env_vars is None:
            user_env_vars = {}

        data = self.load_blueprint(blueprint_name)
        logger.info(f"Deploying blueprint: {data['name']}")

        # Auto-generate secrets if not provided
        context = {
            'POSTGRES_PASSWORD': user_env_vars.get('POSTGRES_PASSWORD', secrets.token_urlsafe(16)),
            'RABBITMQ_PASSWORD': user_env_vars.get('RABBITMQ_PASSWORD', secrets.token_urlsafe(16)),
            'MINIO_PASSWORD': user_env_vars.get('MINIO_PASSWORD', secrets.token_urlsafe(16)),
            'WHATSAPP_VERIFY_TOKEN': user_env_vars.get('WHATSAPP_VERIFY_TOKEN', secrets.token_urlsafe(24)),
            'JWT_SECRET': user_env_vars.get('JWT_SECRET', secrets.token_urlsafe(32)),
        }

        # Load shared infra passwords from existing services
        for dep in data.get('dependencies', []):
            if dep == 'infra-shared':
                self._load_shared_infra(context)

        # 1. Provision Addons (shared Postgres, Redis)
        for addon_def in data.get('addons', []):
            addon = Addon.objects.create(
                service=None,
                name=f"{addon_def['name']}-{self.user.username}",
                addon_type=addon_def['type'],
                status=Addon.Status.PROVISIONING
            )
            if addon.addon_type == 'POSTGRES':
                context['DATABASE_URL'] = f"postgres://user:pass@db-{addon.id}:5432/db"
            elif addon.addon_type == 'REDIS':
                context['REDIS_URL'] = f"redis://redis-{addon.id}:6379/0"
            provision_addon_task.delay(str(addon.id))

        # 2. Deploy Services in dependency order
        for service_def in data['services']:
            service = Service.objects.create(
                name=f"{service_def['name']}-{self.user.username}",
                deploy_type='DOCKER',
                docker_image=service_def['image'],
                internal_port=service_def['port'],
                provider=self.provider,
                owner=self.user
            )

            full_context = {**user_env_vars, **context}
            for key, value in service_def['env'].items():
                if isinstance(value, str):
                    value = Template(value).safe_substitute(full_context)
                EnvironmentVariable.objects.create(service=service, key=key, value=value)

            deployment = Deployment.objects.create(
                service=service,
                status=Deployment.Status.QUEUED,
                commit_message=f"Blueprint: {data['name']}"
            )
            smart_deploy_task.delay(str(deployment.id), str(self.provider.id))

        return True

    def _load_shared_infra(self, context):
        """Load passwords from existing shared infra services."""
        mappings = [
            ('postgres-shared', 'POSTGRES_PASSWORD', 'POSTGRES_PASSWORD'),
            ('rabbitmq-shared', 'RABBITMQ_DEFAULT_PASS', 'RABBITMQ_PASSWORD'),
            ('minio-shared', 'MINIO_ROOT_PASSWORD', 'MINIO_PASSWORD'),
        ]
        for prefix, env_key, ctx_key in mappings:
            svc = Service.objects.filter(owner=self.user, name__startswith=prefix).first()
            if svc:
                ev = EnvironmentVariable.objects.filter(service=svc, key=env_key).first()
                if ev:
                    context[ctx_key] = ev.value
```

#### 7b. Blueprint Views API

**New file: `backend/apps/deployments/views_blueprints.py`**
```python
"""Views Blueprints module."""
import os
import json
from django.conf import settings
from rest_framework import viewsets, permissions, status
from rest_framework.decorators import action
from rest_framework.response import Response
from apps.deployments.services.blueprint_manager import BlueprintManager
from apps.cloud.models import CloudProvider


class BlueprintViewSet(viewsets.ViewSet):
    permission_classes = [permissions.IsAuthenticated]

    def list(self, request):
        """List available blueprints from backend/blueprints/*.json"""
        blueprints = []
        blueprint_dir = os.path.join(settings.BASE_DIR, 'blueprints')
        if not os.path.exists(blueprint_dir):
            return Response([])

        icon_map = {
            'infrastructure': 'database', 'communication': 'phone-call',
            'ai': 'cpu', 'business': 'briefcase',
            'security': 'shield', 'full-stack': 'layers'
        }

        for filename in sorted(os.listdir(blueprint_dir)):
            if not filename.endswith('.json'):
                continue
            try:
                with open(os.path.join(blueprint_dir, filename), 'r') as f:
                    data = json.load(f)
                blueprints.append({
                    "id": filename.replace('.json', ''),
                    "name": data.get('name', 'Unknown'),
                    "description": data.get('description', ''),
                    "version": data.get('version', '1.0.0'),
                    "category": data.get('category', 'custom'),
                    "icon": icon_map.get(data.get('category'), 'box'),
                    "constraints": data.get('constraints', {})
                })
            except Exception:
                continue
        return Response(blueprints)

    @action(detail=False, methods=['post'])
    def deploy(self, request):
        """Deploy a blueprint. Admin-only."""
        if not request.user.is_staff:
            return Response({'error': 'Only admins can deploy blueprints.'},
                            status=status.HTTP_403_FORBIDDEN)

        blueprint_id = request.data.get('blueprint_id')
        provider_id = request.data.get('provider_id')
        user_env_vars = request.data.get('env_vars', {})

        try:
            if not provider_id:
                provider = CloudProvider.objects.first()
                if not provider:
                    return Response({'error': 'No cloud provider configured'},
                                   status=status.HTTP_400_BAD_REQUEST)
            else:
                provider = CloudProvider.objects.get(id=provider_id)

            manager = BlueprintManager(provider, request.user)
            manager.deploy(blueprint_id, user_env_vars)
            return Response({'message': 'Blueprint deployment started'},
                            status=status.HTTP_202_ACCEPTED)
        except CloudProvider.DoesNotExist:
            return Response({'error': 'Provider not found'}, status=status.HTTP_404_NOT_FOUND)
        except Exception as e:
            return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
```

#### 7c. SMSLY Ecosystem Blueprint JSON

**New file: `backend/blueprints/smsly-ecosystem.json`**
```json
{
  "name": "SMSLY Full Ecosystem",
  "description": "The complete SMSLY communication stack including Voice, Video, SMS, and AI services.",
  "version": "2.0.0",
  "category": "full-stack",
  "services": [
    {"name": "smsly-identity-service", "image": "smsly/identity-service:latest", "port": 8001,
     "env": {"DATABASE_URL": "${DATABASE_URL}", "REDIS_URL": "${REDIS_URL}"}, "depends_on": []},
    {"name": "smsly-backend", "image": "smsly/backend:latest", "port": 8000,
     "env": {"IDENTITY_SERVICE_URL": "http://smsly-identity-service:8001", "DATABASE_URL": "${DATABASE_URL}", "REDIS_URL": "${REDIS_URL}"},
     "depends_on": ["smsly-identity-service"]},
    {"name": "smsly-video", "image": "smsly/video:latest", "port": 8002,
     "env": {"BACKEND_URL": "http://smsly-backend:8000"}, "depends_on": ["smsly-backend"]},
    {"name": "smsly-voice", "image": "smsly/voice:latest", "port": 8003,
     "env": {"BACKEND_URL": "http://smsly-backend:8000"}, "depends_on": ["smsly-backend"]},
    {"name": "smsly-sms", "image": "smsly/sms:latest", "port": 8004,
     "env": {"BACKEND_URL": "http://smsly-backend:8000"}, "depends_on": ["smsly-backend"]},
    {"name": "smsly-security-gateway", "image": "smsly/security-gateway:latest", "port": 8080,
     "env": {"UPSTREAM_BACKEND": "http://smsly-backend:8000", "UPSTREAM_IDENTITY": "http://smsly-identity-service:8001"},
     "depends_on": ["smsly-backend", "smsly-identity-service"]}
  ],
  "addons": [
    {"type": "POSTGRES", "name": "shared-postgres"},
    {"type": "REDIS", "name": "shared-redis"}
  ]
}
```

#### 7d. Register in URLs

**Modify `backend/apps/deployments/urls.py`:**
```python
from .views_blueprints import BlueprintViewSet
router.register(r'blueprints', BlueprintViewSet, basename='blueprints')
```

#### 7e. Frontend Blueprints Page

**New file: `frontend/src/app/blueprints/page.tsx`**
Create a page that:
1. Fetches from `GET /api/v1/blueprints/`
2. Shows blueprint cards with icon, name, description, category
3. "Deploy" button (admin only) that calls `POST /api/v1/blueprints/deploy/`
4. Shows deployment status after triggering
5. Dark theme (bg-zinc-950, text-zinc-100)
6. Uses Lucide icons mapped to categories

---

### Verification
```bash
cd backend && python manage.py check
cd frontend && npm run build
```

### Critical Rules
- Read `jules_feature_prompts.md` — many of these features depend on Prompt 2 (addon credentials)
- Follow existing codebase patterns
- Never hardcode secrets
- Filter querysets by request.user
