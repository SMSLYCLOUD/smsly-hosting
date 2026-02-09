---
description: Exhaustive paranoid security audit of SMSLY platform microservices
---

# Security Audit Workflow

## Purpose

Perform a deep security review of all code, configs, and dependencies.

## Audit Scope

### 1. Secrets & Environment Variables

- [ ] No hardcoded secrets in code
- [ ] `.env` files are in `.gitignore`
- [ ] Production secrets use secure generation
- [ ] API keys have proper rotation policies
- [ ] Database passwords meet complexity requirements (min 16 chars)

### 2. Authentication & Authorization

- [ ] JWT tokens have appropriate expiry (max 24h)
- [ ] Password hashing uses bcrypt/argon2 with cost factor ≥12
- [ ] Session management is secure
- [ ] API endpoints require authentication
- [ ] Role-based access control (RBAC) properly enforced
- [ ] CORS configured restrictively (not `*` in production)

### 3. Input Validation

- [ ] All user inputs sanitized
- [ ] SQL injection protection (parameterized queries)
- [ ] XSS protection (output encoding)
- [ ] CSRF tokens on all state-changing forms
- [ ] File upload restrictions (type, size, path)

### 4. Dependency Security

```bash
# Python dependencies
cd backend
pip install safety
safety check -r requirements.txt

# Node dependencies
cd frontend
npm audit
```

### 5. Docker Security

- [ ] Base images are pinned versions (not `latest`)
- [ ] Non-root user in containers
- [ ] No secrets in Dockerfiles
- [ ] Docker socket access is restricted
- [ ] Read-only filesystems where possible

### 6. Network Security

- [ ] HTTPS enforced in production
- [ ] Internal services not exposed to public
- [ ] Firewall rules configured (ports 80, 443 only)
- [ ] Rate limiting on API endpoints
- [ ] No debug endpoints in production

### 7. Logging & Monitoring

- [ ] Sensitive data not logged (passwords, tokens)
- [ ] Audit logs for auth events
- [ ] Error messages don't leak stack traces
- [ ] Failed login attempts logged

## Commands to Run

```bash
# Check for hardcoded secrets
grep -rn "password\s*=" --include="*.py" backend/
grep -rn "secret" --include="*.py" backend/
grep -rn "api_key" --include="*.ts" --include="*.tsx" frontend/

# Check .gitignore
cat .gitignore | grep -E "\.env|secrets|\.key"

# Check Django settings
grep -n "DEBUG" backend/config/settings/*.py
grep -n "ALLOWED_HOSTS" backend/config/settings/*.py

# Check CORS settings
grep -rn "CORS" backend/config/settings/*.py
```

## Severity Levels

- **P0 CRITICAL**: Fix immediately, blocks deployment
- **P1 HIGH**: Fix before next release
- **P2 MEDIUM**: Fix within sprint
- **P3 LOW**: Track for future improvement
