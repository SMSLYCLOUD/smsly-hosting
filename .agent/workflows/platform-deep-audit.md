---
description: Exhaustive platform deep audit and fix for SMSLYCLOUD ecosystem
---

# Platform Deep Audit Workflow

## Purpose

Perform critical deep review of the entire platform to identify and fix all issues preventing 100% production readiness.

## Phase 1: Code Scanning

### Backend Review

1. **View all Django apps:**

   ```bash
   ls backend/apps/
   ```

2. **Check for TODO/FIXME/HACK:**

   ```bash
   grep -rn "TODO\|FIXME\|HACK\|XXX" backend/ --include="*.py"
   ```

3. **Find unused imports:**

   ```bash
   cd backend && flake8 . --select=F401
   ```

4. **Check for print statements:**

   ```bash
   grep -rn "^[^#]*print(" backend/ --include="*.py"
   ```

### Frontend Review

1. **Check for console.log:**

   ```bash
   grep -rn "console\.\(log\|debug\|warn\)" frontend/src/ --include="*.ts" --include="*.tsx"
   ```

2. **Find any `any` types:**

   ```bash
   grep -rn ": any" frontend/src/ --include="*.ts" --include="*.tsx"
   ```

3. **Check for disabled ESLint rules:**

   ```bash
   grep -rn "eslint-disable" frontend/src/
   ```

## Phase 2: Configuration Audit

### Environment Variables

1. **List all required env vars:**

   ```bash
   grep -rhn "os\.environ\|os\.getenv\|env\(" backend/ --include="*.py" | sort -u
   ```

2. **Compare with .env.example:**

   ```bash
   diff <(grep -oP "^[A-Z_]+(?==)" .env.example | sort) <(grep -oP "^[A-Z_]+(?==)" .env | sort)
   ```

### Docker Configuration

1. **Check Dockerfile best practices:**
   - [ ] Multi-stage builds used
   - [ ] Non-root user
   - [ ] .dockerignore exists
   - [ ] No COPY . . before pip install

2. **Verify docker-compose services:**

   ```bash
   docker compose -f docker-compose.prod.yml config --services
   ```

## Phase 3: API Contract Verification

### Endpoint Testing

```bash
# Test each critical endpoint
curl -sf http://localhost:8000/api/health/ && echo "✅ Health"
curl -sf http://localhost:8000/api/v1/schema/ && echo "✅ Schema"
curl -sf -X POST http://localhost:8000/api/v1/auth/login/ -H "Content-Type: application/json" -d '{}' | grep -q "error\|required" && echo "✅ Login validation works"
```

### Response Format Check

- [ ] All responses are JSON
- [ ] Error responses have consistent format
- [ ] Pagination uses standard format
- [ ] HTTP status codes are correct

## Phase 4: UI/UX Verification

### Critical Pages

- [ ] Login page loads, button works
- [ ] Register page loads, form submits
- [ ] Dashboard shows data
- [ ] Project creation works
- [ ] Service deployment works

### Responsive Design

- [ ] Mobile viewport works
- [ ] Tablet viewport works
- [ ] Desktop viewport works

## Phase 5: Fix Implementation

For each issue found:

1. Document the issue
2. Determine severity (P0/P1/P2/P3)
3. Create fix
4. Test fix locally
5. Commit with clear message

## Issue Template

```markdown
## Issue: [Title]

**Severity:** P0/P1/P2/P3
**Location:** [file:line]
**Description:** What's wrong
**Impact:** Why it matters
**Fix:** How to resolve
```

## Completion Criteria

- [ ] All P0 issues resolved
- [ ] All P1 issues resolved or documented
- [ ] Build passes
- [ ] Tests pass
- [ ] Manual smoke test passes
