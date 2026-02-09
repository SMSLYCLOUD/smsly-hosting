---
description: Automated Production Readiness & Integration Verifier for SMSLY Platform
---

# Platform Audit Workflow

## Purpose

Exhaustive verification that all platform components are properly integrated and production-ready.

## Pre-Flight Checks

### 1. Code Quality

```bash
# Backend linting
cd backend
flake8 . --count --statistics

# Frontend linting
cd frontend
npm run lint
```

### 2. Type Safety

```bash
# Python type checking
cd backend
pip install mypy
mypy . --ignore-missing-imports

# TypeScript checking
cd frontend
npx tsc --noEmit
```

### 3. Build Verification

```bash
# Backend collectstatic
cd backend
python manage.py collectstatic --dry-run --no-input

# Frontend production build
cd frontend
npm run build
```

### 4. Database Integrity

```bash
# Check for missing migrations
python manage.py makemigrations --check --dry-run

# Verify all migrations applied
python manage.py showmigrations | grep -E "^\s*\[ \]" && echo "WARN: Unapplied migrations!"
```

### 5. Dependency Audit

```bash
# Python security
pip install pip-audit
pip-audit

# Node security
npm audit --audit-level=moderate
```

## Integration Checks

### API Endpoints

- [ ] `/api/health/` returns 200
- [ ] `/api/v1/auth/login/` accepts POST
- [ ] `/api/v1/projects/` requires auth
- [ ] WebSocket connections work
- [ ] Static files served correctly

### Frontend Routes

- [ ] `/` loads landing page
- [ ] `/login` shows login form
- [ ] `/register` shows registration
- [ ] `/dashboard` requires auth
- [ ] 404 page works

### Background Tasks

- [ ] Celery worker running
- [ ] Celery beat scheduler running
- [ ] Redis connection healthy

## Performance Checks

```bash
# Check Django query performance
python manage.py shell -c "
from django.db import connection
from django.db import reset_queries
import django
django.setup()
# Run test queries
reset_queries()
# Check connection.queries for N+1
"
```

## Checklist Summary

| Category | Status | Notes |
|----------|--------|-------|
| Code Quality | ⬜ | |
| Type Safety | ⬜ | |
| Build Success | ⬜ | |
| Migrations Clean | ⬜ | |
| Dependencies Secure | ⬜ | |
| API Endpoints | ⬜ | |
| Frontend Routes | ⬜ | |
| Background Tasks | ⬜ | |

## Exit Criteria

All boxes must be ✅ before deploying to production.
