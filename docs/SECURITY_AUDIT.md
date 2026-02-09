# Security Audit Report

## Zero Trust Hardening (2026-01-30)

This document summarizes the security audit performed on smsly-hosting.

### Critical Fixes Applied

#### 1. Settings Hardening

- `SECRET_KEY` - Fail-fast in production (no default)
- `FIELD_ENCRYPTION_KEY` - Fail-fast in production
- `DEBUG=True` - Blocked in production
- `ALLOWED_HOSTS='*'` - Blocked in production

#### 2. Ownership Filtering

All ViewSets now filter by `owner`:

- ServiceViewSet
- DeploymentViewSet
- AddonViewSet
- CronJobViewSet
- VolumeViewSet
- TopologyViewSet
- MetricsViewSet

#### 3. Authentication Enforcement

Added `IsAuthenticated` to:

- TemplateViewSet
- MetricsViewSet
- TopologyViewSet
- RepoAnalysisView
- AIChatView

#### 4. Input Validation

- RepoAnalysisView: SSRF protection (only GitHub/GitLab/Bitbucket)
- AIChatView: 2000 char message limit

#### 5. WebSocket Security

- Token authentication required
- Deployment ownership verification
- Connection rejected if auth fails

#### 6. Prometheus Metrics

- IP-based restriction (internal networks only)

### Commit

`49f320c` - "security: Zero Trust hardening for smsly-hosting"
