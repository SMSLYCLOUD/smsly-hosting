# User Dashboard & Account Management

## Context
Current user experience after login goes straight to services list. Need a proper user dashboard showing account overview, resource usage, cost summary, and quick actions. Also need account management features (profile, API keys, team invites, notification preferences).

## Codebase Location
- Frontend pages: `frontend/src/app/dashboard/`
- Auth: `frontend/src/app/auth/`, `backend/apps/core/`
- Billing: `backend/apps/billing/`
- Services: `backend/apps/deployments/`

## Phase 1: User Dashboard (Frontend)

### 1.1 Dashboard overview page
File: `frontend/src/app/dashboard/page.tsx` [MODIFY — currently exists but needs enhancement]

Replace with a rich overview:

**Top Row — Key Metrics Cards:**
- Total Services (running / total)
- Total Deployments (this month)
- Active Addons count
- Current Monthly Cost estimate

**Middle Row — Activity Feed + Resource Usage:**
- Left (2/3 width): Recent Activity feed
  - Last 10 deployments with status, commit, time
  - Addon events (provisioned, backed up)
  - Billing events (invoice paid, payment failed)
  - Click any item to navigate to detail
- Right (1/3 width): Resource Usage gauges
  - CPU usage across all services (ring chart)
  - Memory usage across all services
  - Storage used / quota
  - Bandwidth used this month

**Bottom Row — Quick Actions + Alerts:**
- Quick Deploy button → `/new`
- Quick Template → `/templates`
- Active Alerts (health check failures, billing due, SSL expiring)

### 1.2 Backend: Dashboard aggregation endpoint
File: `backend/apps/core/views.py` [MODIFY] — add:

```
GET /api/v1/dashboard/overview/
```

Returns:
```json
{
  "services": {"total": 5, "running": 3, "failed": 1, "stopped": 1},
  "deployments_this_month": 23,
  "addons": {"total": 8, "active": 7},
  "cost_estimate": {"monthly_usd": 45.00, "currency": "USD"},
  "resource_usage": {
    "cpu_percent": 67,
    "memory_percent": 54,
    "storage_gb": 12.3,
    "bandwidth_gb": 45.2
  },
  "recent_activity": [...],
  "alerts": [...]
}
```

### 1.3 Account Settings pages
File: `frontend/src/app/settings/page.tsx` [MODIFY or verify exists]

Tabs needed:
- **Profile**: name, email, avatar, timezone, password change
- **API Keys**: generate/revoke API tokens for CI/CD
- **Team**: invite members, set roles (admin/viewer/deployer)
- **Notifications**: email/webhook preferences per event type
- **Security**: 2FA setup, active sessions, login history

### 1.4 API Keys management (Backend)
File: `backend/apps/core/models.py` — add:
```python
class APIKey(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    name = models.CharField(max_length=100)
    key_hash = models.CharField(max_length=128)  # bcrypt hash, never store raw
    prefix = models.CharField(max_length=8)  # first 8 chars shown to user
    last_used = models.DateTimeField(null=True)
    expires_at = models.DateTimeField(null=True)
    created_at = models.DateTimeField(auto_now_add=True)
```

Endpoints:
```
POST   /api/v1/api-keys/          → create (returns raw key ONCE)
GET    /api/v1/api-keys/          → list (shows prefix + name + last_used)
DELETE /api/v1/api-keys/{id}/     → revoke
```

### 1.5 Notification system
File: `backend/apps/notifications/` [NEW app]

Models:
```python
class NotificationPreference(models.Model):
    user = models.ForeignKey(...)
    event_type = models.CharField(choices=[
        'deploy_success', 'deploy_failed', 'health_alert',
        'billing_due', 'ssl_expiring', 'backup_completed',
    ])
    channels = ArrayField(models.CharField(...))  # ['email', 'webhook', 'in_app']

class Notification(models.Model):
    user = models.ForeignKey(...)
    title = models.CharField(max_length=200)
    message = models.TextField()
    event_type = models.CharField(max_length=50)
    read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
```

- In-app notification bell in header
- WebSocket channel for real-time notifications
- Email sending via existing email config

## Validation
1. Dashboard loads with real data from all services
2. Activity feed shows correct events in chronological order
3. Resource usage gauges match actual container metrics
4. API key create → use in CI/CD → verify authentication works
5. Team invite → new user registers → verify correct permissions
6. Notification preferences → trigger event → verify correct channel fires
