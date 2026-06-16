# Grid 100/100 Readiness Sprint — Implementation Plan

## Objective
Bring Grid from **85/100** to **100/100** production readiness by executing a comprehensive security hardening, testing, documentation, and operational excellence sprint.

---

## Execution Strategy

### Approach
This implementation follows a **phased, sequential execution** model with verification gates between each phase. All changes must be tested and verified before proceeding to the next phase.

### Timeline
- **Phase 1 (Security):** 2-3 days
- **Phase 2 (Testing):** 2-3 days
- **Phase 3 (Documentation):** 1-2 days
- **Phase 4 (Operations):** 1-2 days
- **Phase 5 (Scalability):** 2-3 days (optional, can be deferred)
- **Phase 6 (Compliance):** 1 day
- **Phase 7 (Performance):** 1 day
- **Phase 8 (Verification):** 1 day

**Total Estimated Time:** 8-12 days (with full scalability phase)

---

## Phase 1: Critical Security Hardening

### 1.1 SSRF Protection in Health Checks

#### Location
`backend/apps/deployments/models.py` → `Service` model

#### Implementation

**Step 1:** Create a URL validator utility

```python
# backend/apps/deployments/validators.py (NEW FILE)
"""URL validation utilities."""
import ipaddress
from urllib.parse import urlparse
from django.core.exceptions import ValidationError
import socket

BLOCKED_IPS = [
    ipaddress.IPv4Network('10.0.0.0/8'),      # RFC 1918
    ipaddress.IPv4Network('172.16.0.0/12'),   # RFC 1918
    ipaddress.IPv4Network('192.168.0.0/16'),  # RFC 1918
    ipaddress.IPv4Network('127.0.0.0/8'),     # Loopback
    ipaddress.IPv4Network('169.254.0.0/16'),  # Link-local (AWS metadata)
    ipaddress.IPv6Network('::1/128'),         # IPv6 loopback
    ipaddress.IPv6Network('fe80::/10'),       # IPv6 link-local
]

BLOCKED_HOSTNAMES = [
    'metadata.google.internal',
    'metadata',
    'localhost',
]

def validate_health_check_url(url: str) -> None:
    """Validate health check URL to prevent SSRF attacks."""
    if not url:
        return
    
    parsed = urlparse(url)
    
    # Only allow HTTP/HTTPS
    if parsed.scheme not in ['http', 'https']:
        raise ValidationError(f"Invalid scheme: {parsed.scheme}. Only HTTP/HTTPS allowed.")
    
    hostname = parsed.hostname
    if not hostname:
        raise ValidationError("Invalid URL: missing hostname")
    
    # Block dangerous hostnames
    if hostname.lower() in BLOCKED_HOSTNAMES:
        raise ValidationError(f"Blocked hostname: {hostname}")
    
    # Resolve hostname to IP and check against blocklist
    try:
        ip_str = socket.gethostbyname(hostname)
        ip_obj = ipaddress.ip_address(ip_str)
        
        for blocked_network in BLOCKED_IPS:
            if ip_obj in blocked_network:
                raise ValidationError(
                    f"Health check URL resolves to blocked IP: {ip_str} (network: {blocked_network})"
                )
    except socket.gaierror:
        raise ValidationError(f"Cannot resolve hostname: {hostname}")
```

**Step 2:** Apply validator to `Service.health_check_url`

```python
# backend/apps/deployments/models.py
from .validators import validate_health_check_url

class Service(TimeStampedModel):
    # ... existing fields ...
    
    health_check_url = models.URLField(
        max_length=500,
        blank=True,
        null=True,
        validators=[validate_health_check_url],  # ADD THIS
        help_text="Health check endpoint URL (e.g. /health or /api/status)"
    )
```

**Step 3:** Add unit tests

```python
# backend/apps/deployments/tests/test_validators.py (NEW FILE)
"""Tests for URL validators."""
import pytest
from django.core.exceptions import ValidationError
from apps.deployments.validators import validate_health_check_url

def test_ssrf_blocks_private_ips():
    """Ensure private IPs are blocked."""
    with pytest.raises(ValidationError, match="blocked IP"):
        validate_health_check_url("http://192.168.1.1/admin")
    
    with pytest.raises(ValidationError, match="blocked IP"):
        validate_health_check_url("http://10.0.0.1/metadata")
    
    with pytest.raises(ValidationError, match="blocked IP"):
        validate_health_check_url("http://127.0.0.1/internal")

def test_ssrf_blocks_metadata_endpoints():
    """Ensure cloud metadata endpoints are blocked."""
    with pytest.raises(ValidationError, match="blocked IP"):
        validate_health_check_url("http://169.254.169.254/latest/meta-data")

def test_ssrf_allows_public_urls():
    """Ensure public URLs are allowed."""
    validate_health_check_url("https://example.com/health")  # Should not raise
    validate_health_check_url("https://api.smsly.cloud/status")  # Should not raise
```

---

### 1.2 AI Prompt Injection Protection

#### Location
`backend/apps/intelligence/` (AI diagnostic service)

#### Implementation

**Step 1:** Create log sanitizer

```python
# backend/apps/intelligence/sanitizers.py (NEW FILE)
"""Sanitizers for AI input."""
import re

MAX_LOG_LENGTH = 4000  # Max tokens ~= 4000 chars

def sanitize_log_for_ai(log_text: str) -> str:
    """Sanitize deployment logs before sending to AI."""
    # Remove potential injection patterns
    sanitized = log_text
    
    # Remove system prompts injection attempts
    sanitized = re.sub(r'(ignore previous instructions|system:|assistant:)', '[REDACTED]', sanitized, flags=re.IGNORECASE)
    
    # Truncate to max length
    if len(sanitized) > MAX_LOG_LENGTH:
        sanitized = sanitized[:MAX_LOG_LENGTH] + "\n[LOG TRUNCATED]"
    
    return sanitized
```

**Step 2:** Add rate limiting decorator

```python
# backend/apps/intelligence/decorators.py (NEW FILE)
"""Rate limiting for AI endpoints."""
from functools import wraps
from django.core.cache import cache
from rest_framework.exceptions import Throttled

def ai_rate_limit(max_requests=5, window_seconds=60):
    """Rate limit AI diagnostic requests."""
    def decorator(func):
        @wraps(func)
        def wrapper(request, *args, **kwargs):
            user_id = request.user.id
            cache_key = f"ai_limit:{user_id}"
            
            request_count = cache.get(cache_key, 0)
            if request_count >= max_requests:
                raise Throttled(detail=f"AI diagnostic rate limit exceeded. Max {max_requests} requests per {window_seconds}s.")
            
            cache.set(cache_key, request_count + 1, window_seconds)
            return func(request, *args, **kwargs)
        return wrapper
    return decorator
```

**Step 3:** Apply to AI diagnostic views

```python
# backend/apps/intelligence/views.py
from .sanitizers import sanitize_log_for_ai
from .decorators import ai_rate_limit

class DiagnosticViewSet(viewsets.ViewSet):
    @ai_rate_limit(max_requests=5, window_seconds=60)
    def analyze_deployment(self, request, pk=None):
        deployment = get_object_or_404(Deployment, pk=pk)
        
        # Sanitize logs before sending to AI
        sanitized_log = sanitize_log_for_ai(deployment.build_log)
        
        # Call AI API with sanitized input
        response = ai_client.analyze(sanitized_log)
        
        return Response(response)
```

---

### 1.3 Content Security Policy (CSP)

#### Location
`frontend/src/app/layout.tsx` or Next.js middleware

#### Implementation

**Step 1:** Add CSP headers in Next.js config

```javascript
// frontend/next.config.mjs
const securityHeaders = [
  {
    key: 'Content-Security-Policy',
    value: `
      default-src 'self';
      script-src 'self' 'unsafe-eval' 'unsafe-inline';
      style-src 'self' 'unsafe-inline';
      img-src 'self' data: https:;
      font-src 'self' data:;
      connect-src 'self' https://api.smsly.cloud;
      frame-ancestors 'none';
    `.replace(/\s{2,}/g, ' ').trim()
  },
  {
    key: 'X-Frame-Options',
    value: 'DENY'
  },
  {
    key: 'X-Content-Type-Options',
    value: 'nosniff'
  },
  {
    key: 'Referrer-Policy',
    value: 'strict-origin-when-cross-origin'
  },
  {
    key: 'Permissions-Policy',
    value: 'camera=(), microphone=(), geolocation=()'
  }
];

export default {
  async headers() {
    return [
      {
        source: '/:path*',
        headers: securityHeaders,
      },
    ];
  },
};
```

---

## Phase 2: Testing & Quality Assurance

### 2.1 Run Existing Test Suite

```bash
# Backend tests
cd backend
pytest --cov=apps --cov-report=html --cov-report=term

# Frontend tests (if they exist)
cd frontend
npm test -- --coverage
```

### 2.2 Add Load Tests

**Create `tests/load/locustfile.py`:**

```python
from locust import HttpUser, task, between

class GridUser(HttpUser):
    wait_time = between(1, 3)
    
    def on_start(self):
        """Login before tests."""
        self.client.post("/api/v1/auth/login/", json={
            "username": "test@example.com",
            "password": "testpass123"
        })
    
    @task(1)
    def list_services(self):
        self.client.get("/api/v1/services/")
    
    @task(2)
    def create_deployment(self):
        self.client.post("/api/v1/deployments/", json={
            "service_id": "test-service-123",
            "branch": "main",
            "commit_sha": "abc123"
        })
    
    @task(1)
    def get_deployment_logs(self):
        self.client.get("/api/v1/deployments/latest/logs/")
```

**Run load test:**

```bash
locust -f tests/load/locustfile.py --host=http://localhost --users 100 --spawn-rate 10
```

---

## Phase 3: Documentation

### 3.1 OpenAPI Specification

**Install drf-spectacular:**

```bash
pip install drf-spectacular
```

**Configure in `settings.py`:**

```python
INSTALLED_APPS = [
    # ...
    'drf_spectacular',
]

REST_FRAMEWORK = {
    'DEFAULT_SCHEMA_CLASS': 'drf_spectacular.openapi.AutoSchema',
}

SPECTACULAR_SETTINGS = {
    'TITLE': 'Grid API',
    'DESCRIPTION': 'Self-healing multi-cloud PaaS API',
    'VERSION': '1.0.0',
    'SERVE_INCLUDE_SCHEMA': False,
}
```

**Add URLs:**

```python
# backend/config/urls.py
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView

urlpatterns = [
    path('api/schema/', SpectacularAPIView.as_view(), name='schema'),
    path('api/docs/', SpectacularSwaggerView.as_view(url_name='schema'), name='swagger-ui'),
    # ... existing patterns
]
```

---

## Phase 4: Operational Excellence

### 4.1 Slack Notifications

**Create `backend/apps/deployments/notifications.py`:**

```python
"""Deployment notification handlers."""
import requests
from django.conf import settings

def send_slack_notification(deployment):
    """Send deployment event to Slack."""
    webhook_url = settings.SLACK_WEBHOOK_URL
    if not webhook_url:
        return
    
    color = {
        'ACTIVE': 'good',
        'FAILED': 'danger',
        'QUEUED': 'warning',
    }.get(deployment.status, '#808080')
    
    payload = {
        "attachments": [{
            "color": color,
            "title": f"Deployment {deployment.status}",
            "text": f"Service: {deployment.service.name}\\nCommit: {deployment.commit_sha[:7]}",
            "footer": "Grid",
        }]
    }
    
    requests.post(webhook_url, json=payload)
```

**Add to deployment save hook:**

```python
# backend/apps/deployments/models.py
class Deployment(models.Model):
    # ... fields ...
    
    def save(self, *args, **kwargs):
        old_status = None
        if self.pk:
            old_status = Deployment.objects.get(pk=self.pk).status
        
        super().save(*args, **kwargs)
        
        # Send notification if status changed
        if old_status != self.status:
            from .notifications import send_slack_notification
            send_slack_notification(self)
```

---

## Phase 5: Scalability (Kubernetes Support)

### 5.1 Helm Chart Creation

**Create `charts/Grid/Chart.yaml`:**

```yaml
apiVersion: v2
name: Grid
description: Self-healing multi-cloud PaaS
type: application
version: 1.0.0
appVersion: "1.0.0"
```

**Create `charts/Grid/values.yaml`:**

```yaml
replicaCount: 3

backend:
  image:
    repository: smsly/Grid-backend
    tag: latest
  resources:
    limits:
      cpu: 2000m
      memory: 2Gi
    requests:
      cpu: 500m
      memory: 512Mi

postgresql:
  enabled: true
  auth:
    database: Grid
    username: Grid
    password: CHANGE_ME

redis:
  enabled: true
  auth:
    password: CHANGE_ME
```

**Deploy to K8s:**

```bash
helm install Grid ./charts/Grid -f values.production.yaml
```

---

## Phase 6: Compliance & Audit

### 6.1 Audit Log Verification

**Ensure all sensitive actions are logged:**

```python
# backend/apps/deployments/views.py
from apps.deployments.models import AuditLog

class DeploymentViewSet(viewsets.ModelViewSet):
    def perform_create(self, serializer):
        instance = serializer.save()
        
        # Log deployment creation
        AuditLog.objects.create(
            user=self.request.user,
            action='deployment.create',
            resource_type='deployment',
            resource_id=str(instance.id),
            metadata={'service': instance.service.name}
        )
```

---

## Phase 7: Performance Optimization

### 7.1 Docker Image Optimization

**Optimize backend Dockerfile:**

```dockerfile
# Use slim base image
FROM python:3.11-slim AS builder

# Install only build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Install Python packages
COPY requirements.txt /tmp/
RUN pip install --no-cache-dir -r /tmp/requirements.txt

# Final stage (smaller image)
FROM python:3.11-slim
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Copy application code
COPY . /app
WORKDIR /app

CMD ["gunicorn", "config.wsgi:application"]
```

---

## Phase 8: Final Verification

### Pre-Production Checklist

Run the following verification script:

```bash
#!/bin/bash
# verify_readiness.sh

echo "🔍 Grid 100/100 Readiness Verification"
echo ""

# Security scans
echo "🔐 Running security scans..."
bandit -r backend/ -f json -o bandit_report.json
safety check --json > safety_report.json
detect-secrets scan --baseline .secrets.baseline

# Test coverage
echo "🧪 Checking test coverage..."
pytest --cov=apps --cov-report=term --cov-fail-under=90

# Load tests
echo "⚡ Running load tests..."
locust -f tests/load/locustfile.py --headless --users 100 --spawn-rate 10 --run-time 60s

# Health checks
echo "💚 Verifying health endpoints..."
curl -f http://localhost/health || exit 1

# Documentation
echo "📚 Verifying documentation..."
[ -f docs/API.md ] || echo "❌ API documentation missing"
[ -f cli/README.md ] || echo "❌ CLI documentation missing"

echo ""
echo "✅ Verification complete!"
```

---

## Success Metrics

| Metric | Target | Verification |
|--------|--------|--------------|
| Security Scan | 0 HIGH/CRITICAL | `bandit`, `safety`, `npm audit` |
| Test Coverage | ≥90% | `pytest --cov-fail-under=90` |
| Load Test | 100 concurrent users | Locust p95 response time <500ms |
| Documentation | 100% API coverage | OpenAPI spec + CLI docs exist |
| Deployment Time | <5 minutes | Measure average deployment duration |
| Zero-Downtime | No 5xx errors during deploy | Health check monitoring |

---

## Rollback Plan

If any phase fails:
1. **Stop immediately** and document the failure
2. **Revert changes** using git: `git revert <commit>`
3. **File issue** with error details
4. **Re-plan** the failed phase with updated approach

---

## Final Deliverables

1. **Security Report** — All vulnerabilities fixed, scans clean
2. **Test Coverage Report** — HTML report showing 90%+ coverage
3. **OpenAPI Specification** — Published at `/api/docs/`
4. **CLI Documentation** — Complete usage guide
5. **Performance Benchmarks** — Load test results vs. competitors
6. **Helm Chart** — K8s deployment ready
7. **Terraform Modules** — AWS/GCP/Azure provisioning

---

**Status:** Ready for execution by AI agent (Jules or equivalent)
