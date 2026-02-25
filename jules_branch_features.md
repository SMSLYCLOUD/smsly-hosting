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

### 7. Ecosystem Income Dashboard Add-ons (smsly-hosting)

**From branch:** `ecosystem-income-report` (4 commits, 241 files in smsly-hosting, deleted)

This was the biggest branch. It attempted to add:

#### Backend:
- **Blueprints auto-assessment**: Automated scoring of service templates/blueprints
- **Income projection model**: Track and project hosting revenue per service
- **Test fixtures**: Comprehensive test data for addon provisioning, deployments, and billing
- **Install simulation tests**: End-to-end install.sh simulation for CI

#### Frontend:
- **DB Explorer component**: `frontend/src/components/DbExplorer.tsx` — visual database browser for addon databases (Postgres, Redis, MySQL, MongoDB)
- **Middleware improvements**: Enhanced Next.js middleware for auth and routing
- **Dashboard widgets**: Revenue/income widgets on the main dashboard

#### What to actually implement:
Focus on the **DB Explorer** — this is the most valuable piece. Create a component that:
1. Connects to addon databases via the backend API
2. Shows tables/collections in a tree view
3. Allows running read-only queries
4. Displays results in a table format
5. Only works for ACTIVE addons

**New file: `frontend/src/components/addons/DbExplorer.tsx`**
- Fetch addon credentials from `GET /api/v1/addons/{id}/credentials/` (from Prompt 2 in jules_feature_prompts.md)
- Show a SQL/query input (read-only queries only)
- Display results in a responsive table
- Add a "DB Explorer" button on each active addon card in AddonsTab

**New backend endpoint in `backend/apps/deployments/views_addons.py`:**
```python
@action(detail=True, methods=['post'])
def query(self, request, pk=None):
    """Execute a read-only query against the addon database."""
    addon = self.get_object()
    if addon.status != 'ACTIVE':
        return Response({'error': 'Addon not active'}, status=400)

    sql = request.data.get('query', '').strip()
    if not sql:
        return Response({'error': 'Query is required'}, status=400)

    # Safety: only allow SELECT statements
    if not sql.upper().startswith('SELECT'):
        return Response({'error': 'Only SELECT queries are allowed'}, status=400)

    # Execute against the addon's database
    from services.addon_query import execute_readonly_query
    try:
        result = execute_readonly_query(addon, sql, limit=100)
        return Response(result)
    except Exception as e:
        return Response({'error': str(e)}, status=400)
```

**New file: `backend/services/addon_query.py`**
```python
"""Execute read-only queries against addon databases."""
import logging
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


def execute_readonly_query(addon, query: str, limit: int = 100) -> dict:
    """Execute a SELECT query against an addon database. Returns {columns, rows}."""
    parsed = urlparse(addon.connection_url)

    if addon.addon_type == 'POSTGRES':
        return _query_postgres(parsed, query, limit)
    elif addon.addon_type == 'MYSQL':
        return _query_mysql(parsed, query, limit)
    elif addon.addon_type == 'MONGODB':
        return {'error': 'MongoDB queries not yet supported via SQL'}
    elif addon.addon_type == 'REDIS':
        return {'error': 'Redis does not support SQL queries'}
    else:
        return {'error': f'Query not supported for {addon.addon_type}'}


def _query_postgres(parsed, query: str, limit: int) -> dict:
    import psycopg2
    conn = psycopg2.connect(
        host=parsed.hostname,
        port=parsed.port or 5432,
        user=parsed.username,
        password=parsed.password,
        dbname=parsed.path.lstrip('/'),
    )
    conn.set_session(readonly=True, autocommit=True)
    try:
        cur = conn.cursor()
        # Add LIMIT if not present
        if 'LIMIT' not in query.upper():
            query = f'{query} LIMIT {limit}'
        cur.execute(query)
        columns = [desc[0] for desc in cur.description] if cur.description else []
        rows = [list(row) for row in cur.fetchall()]
        return {'columns': columns, 'rows': rows}
    finally:
        conn.close()


def _query_mysql(parsed, query: str, limit: int) -> dict:
    import pymysql
    conn = pymysql.connect(
        host=parsed.hostname,
        port=parsed.port or 3306,
        user=parsed.username,
        password=parsed.password,
        database=parsed.path.lstrip('/'),
    )
    try:
        cur = conn.cursor()
        if 'LIMIT' not in query.upper():
            query = f'{query} LIMIT {limit}'
        cur.execute(query)
        columns = [desc[0] for desc in cur.description] if cur.description else []
        rows = [list(row) for row in cur.fetchall()]
        return {'columns': columns, 'rows': rows}
    finally:
        conn.close()
```

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
