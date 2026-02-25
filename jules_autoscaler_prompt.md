# Jules Prompt: SMSLY VPS Autoscaler Web UI

## Objective

Add a **VPS-level cross-service autoscaler dashboard** to the `smsly-hosting` platform. This is NOT per-service scaling — it's a single dashboard that shows all SMSLY services running on one VPS and how the autoscaler dynamically redistributes resources (workers, memory) across them based on real-time demand.

The autoscaler script (`smsly-autoscaler.py`) already runs as a systemd service on the VPS. Your job is to:
1. Add a lightweight HTTP API endpoint to the autoscaler so the frontend can query it
2. Create a Django backend proxy in smsly-hosting to relay autoscaler data
3. Build a premium frontend dashboard page at `/autoscaler`

---

## Part 1: Autoscaler HTTP API (modify `smsly-autoscaler.py`)

The autoscaler currently runs a loop every 30s. Add a **lightweight Flask/http.server endpoint** on port `9876` (configurable via `AUTOSCALER_API_PORT` env var) that runs in a background thread alongside the main loop.

### Endpoints to add:

#### `GET /api/status`
Returns the latest autoscaler state as JSON:
```json
{
  "status": "running",
  "uptime_seconds": 3600,
  "check_interval": 30,
  "last_check_at": "2026-02-25T13:00:00Z",
  "budget": {
    "total_system_mb": 10240,
    "infra_reserve_mb": 2048,
    "app_budget_mb": 8192,
    "used_mb": 4200,
    "free_mb": 3992
  },
  "services": {
    "smsly-helper-web": {
      "type": "gunicorn",
      "app": "smsly-helper",
      "priority": 3,
      "status": "running",
      "demand_score": 0.45,
      "cpu_percent": 23.5,
      "memory_mb": 340,
      "memory_limit_mb": 512,
      "memory_percent": 66.4,
      "net_rx_mb": 1.2,
      "net_tx_mb": 3.4,
      "pids": 5,
      "current_workers": 4,
      "min_workers": 2,
      "max_workers": 8,
      "last_action": "none",
      "last_action_at": "2026-02-25T12:59:30Z"
    }
  },
  "recent_decisions": [
    {
      "timestamp": "2026-02-25T12:59:30Z",
      "container": "smsly-helper-web",
      "action": "scale_up",
      "workers_before": 2,
      "workers_after": 4,
      "memory_before_mb": 384,
      "memory_after_mb": 608,
      "reason": "demand=0.65, cpu=78.2%, mem=82.1%"
    }
  ]
}
```

#### `GET /api/history?minutes=60`
Returns time-series data for charting (the autoscaler should keep a rolling in-memory buffer of the last 120 data points = 1 hour at 30s intervals):
```json
{
  "timestamps": ["2026-02-25T12:00:00Z", "2026-02-25T12:00:30Z", ...],
  "services": {
    "smsly-helper-web": {
      "cpu": [12.3, 15.1, ...],
      "memory_mb": [280, 310, ...],
      "demand_score": [0.3, 0.35, ...],
      "workers": [2, 2, ...]
    }
  },
  "budget": {
    "used_mb": [3800, 4100, ...],
    "free_mb": [4392, 4092, ...]
  }
}
```

#### `POST /api/config`
Update autoscaler configuration at runtime (no restart required):
```json
{
  "total_system_mb": 10240,
  "infra_reserve_mb": 2048,
  "check_interval": 30,
  "services": {
    "smsly-helper-web": {
      "priority": 3,
      "min_workers": 2,
      "max_workers": 8
    }
  }
}
```

#### `POST /api/trigger`
Force an immediate autoscaler check cycle (returns current stats after the check).

### Implementation details for the API:

- Use Python's built-in `http.server` (NOT Flask — avoid new dependencies) running in a `threading.Thread(daemon=True)`
- Store latest stats, decisions, and history in module-level variables protected by `threading.Lock()`
- Keep a `collections.deque(maxlen=120)` for history (rolling 1 hour buffer)
- Bind to `0.0.0.0:9876` so the smsly-hosting backend can reach it
- The existing `SERVICE_GROUPS` dict at module level must remain mutable so `/api/config` can update it

### Changes to `smsly-autoscaler.py`:

Location: `c:\Users\osaretin\Downloads\smslycloud-master\smsly-autoscaler.py`

The file is 485 lines. The current structure is:
- Lines 1-44: Imports and logging setup
- Lines 46-66: Configuration constants (`TOTAL_SYSTEM_MB`, `APP_BUDGET_MB`, `CHECK_INTERVAL`, etc.)
- Lines 68-83: `SERVICE_GROUPS` dict mapping container names to configs
- Lines 86-106: `ContainerStats` and `ScalingDecision` dataclasses
- Lines 113-192: `get_docker_stats()` and `_parse_size_mb()` functions
- Lines 199-331: `calculate_demand_scores()` and `make_scaling_decisions()` functions
- Lines 338-429: `apply_decisions()`, `_scale_gunicorn()`, `_scale_celery()` functions
- Lines 436-484: `run_once()`, `main()` loop

Add:
1. After imports: `import collections`, `from http.server import HTTPServer, BaseHTTPRequestHandler`, `from datetime import datetime, timezone`
2. After `SERVICE_GROUPS`: Module-level state variables with a `threading.Lock()`
3. After scaling actions section: A `class AutoscalerAPIHandler(BaseHTTPRequestHandler)` implementing the 4 endpoints
4. In `run_once()`: After each cycle, store stats + decisions into the history deque
5. In `main()`: Start the API server thread before the main loop

---

## Part 2: Django Backend Proxy (in `smsly-hosting/backend`)

### Why a proxy?
The autoscaler runs on the VPS host (not in Docker), so the frontend can't reach it directly. The smsly-hosting backend proxies requests to the autoscaler's HTTP API.

### New Django app: `apps/autoscaler`

Create `smsly-hosting/backend/apps/autoscaler/` with:

#### `__init__.py` (empty)

#### `urls.py`
```python
from django.urls import path
from . import views

urlpatterns = [
    path('status/', views.autoscaler_status),
    path('history/', views.autoscaler_history),
    path('config/', views.autoscaler_config),
    path('trigger/', views.autoscaler_trigger),
]
```

#### `views.py`
```python
import requests
from django.conf import settings
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response

AUTOSCALER_URL = getattr(settings, 'AUTOSCALER_API_URL', 'http://localhost:9876')

@api_view(['GET'])
@permission_classes([IsAdminUser])
def autoscaler_status(request):
    """Proxy to autoscaler /api/status"""
    try:
        r = requests.get(f'{AUTOSCALER_URL}/api/status', timeout=5)
        return Response(r.json(), status=r.status_code)
    except requests.RequestException as e:
        return Response({'error': str(e), 'autoscaler_reachable': False}, status=503)

@api_view(['GET'])
@permission_classes([IsAdminUser])
def autoscaler_history(request):
    """Proxy to autoscaler /api/history"""
    minutes = request.query_params.get('minutes', '60')
    try:
        r = requests.get(f'{AUTOSCALER_URL}/api/history', params={'minutes': minutes}, timeout=5)
        return Response(r.json(), status=r.status_code)
    except requests.RequestException as e:
        return Response({'error': str(e)}, status=503)

@api_view(['POST'])
@permission_classes([IsAdminUser])
def autoscaler_config(request):
    """Proxy config update to autoscaler"""
    try:
        r = requests.post(f'{AUTOSCALER_URL}/api/config', json=request.data, timeout=5)
        return Response(r.json(), status=r.status_code)
    except requests.RequestException as e:
        return Response({'error': str(e)}, status=503)

@api_view(['POST'])
@permission_classes([IsAdminUser])
def autoscaler_trigger(request):
    """Trigger an immediate autoscaler check"""
    try:
        r = requests.post(f'{AUTOSCALER_URL}/api/trigger', timeout=15)
        return Response(r.json(), status=r.status_code)
    except requests.RequestException as e:
        return Response({'error': str(e)}, status=503)
```

### Register the URLs

In the main `urls.py` (find it by searching for `urlpatterns` in `smsly-hosting/backend/`), add:
```python
path('api/v1/autoscaler/', include('apps.autoscaler.urls')),
```

### Register the app

In `settings.py`, add `'apps.autoscaler'` to `INSTALLED_APPS`.

Add to settings:
```python
AUTOSCALER_API_URL = os.environ.get('AUTOSCALER_API_URL', 'http://localhost:9876')
```

---

## Part 3: Frontend Dashboard (in `smsly-hosting/frontend`)

### Tech Stack (MUST match existing patterns exactly)

The existing frontend uses:
- **Next.js 14 App Router** (`'use client'` directives)
- **shadcn/ui** components: `Card`, `CardContent`, `CardHeader`, `CardTitle`, `CardDescription`, `Button`, `Input`, `Label`, `Slider`, `Switch` (from `@/components/ui/`)
- **recharts**: `LineChart`, `Line`, `AreaChart`, `Area`, `XAxis`, `YAxis`, `CartesianGrid`, `Tooltip`, `ResponsiveContainer`
- **framer-motion**: `motion`, `AnimatePresence` for animations
- **lucide-react** for icons
- **`@/components/layout/DashboardShell`** for page wrapper
- **`@/components/ui/use-toast`** for toast notifications
- **API pattern**: Axios instance at `@/lib/api.ts` with `Token` auth from localStorage

### API Client additions to `frontend/src/lib/api.ts`

Add a new `autoscalerApi` object at the end of the file (BEFORE the final `export default api;` if one exists, or just add as a new export):

```typescript
// ==========================================
// Autoscaler (VPS-level cross-service)
// ==========================================

export interface AutoscalerService {
  type: 'gunicorn' | 'celery' | 'daphne';
  app: string;
  priority: number;
  status: string;
  demand_score: number;
  cpu_percent: number;
  memory_mb: number;
  memory_limit_mb: number;
  memory_percent: number;
  net_rx_mb: number;
  net_tx_mb: number;
  pids: number;
  current_workers: number;
  min_workers: number;
  max_workers: number;
  last_action: string;
  last_action_at: string;
}

export interface AutoscalerBudget {
  total_system_mb: number;
  infra_reserve_mb: number;
  app_budget_mb: number;
  used_mb: number;
  free_mb: number;
}

export interface AutoscalerStatus {
  status: string;
  uptime_seconds: number;
  check_interval: number;
  last_check_at: string;
  budget: AutoscalerBudget;
  services: Record<string, AutoscalerService>;
  recent_decisions: {
    timestamp: string;
    container: string;
    action: string;
    workers_before: number;
    workers_after: number;
    memory_before_mb: number;
    memory_after_mb: number;
    reason: string;
  }[];
}

export interface AutoscalerHistory {
  timestamps: string[];
  services: Record<string, {
    cpu: number[];
    memory_mb: number[];
    demand_score: number[];
    workers: number[];
  }>;
  budget: {
    used_mb: number[];
    free_mb: number[];
  };
}

export const autoscalerApi = {
  getStatus: async (): Promise<AutoscalerStatus> => {
    const { data } = await api.get('/api/v1/autoscaler/status/');
    return data;
  },
  getHistory: async (minutes: number = 60): Promise<AutoscalerHistory> => {
    const { data } = await api.get('/api/v1/autoscaler/history/', { params: { minutes } });
    return data;
  },
  updateConfig: async (config: any): Promise<any> => {
    const { data } = await api.post('/api/v1/autoscaler/config/', config);
    return data;
  },
  trigger: async (): Promise<AutoscalerStatus> => {
    const { data } = await api.post('/api/v1/autoscaler/trigger/');
    return data;
  },
};
```

### New Page: `frontend/src/app/autoscaler/page.tsx`

Create a new page at this path. This is the main autoscaler dashboard.

**Design requirements (CRITICAL — this must look premium):**

The page must have these sections:

#### 1. Header
- Title: "VPS Autoscaler" with a `Scaling` icon from lucide-react
- Subtitle: "Cross-service resource manager — balancing {N} services on {TOTAL_MB/1024}GB VPS"
- Status badge (green "RUNNING" / red "OFFLINE" with pulse animation)
- "Force Check" button (calls `/api/trigger`)
- Auto-refresh toggle (polls every 10s when enabled)

#### 2. Memory Budget Ring (prominent, center top)
A large circular gauge showing:
- Total VPS memory budget (e.g. 10GB)
- Used memory across all services (filled portion)
- Free memory (remaining portion)
- Color transitions: green (<60%), amber (60-80%), red (>80%)
- Inside the ring: "4.2GB / 8GB used" text
- Below: "Infra reserved: 2GB • OS headroom: 3.8GB"

Use SVG for this gauge, similar to the existing `GaugeRing` component in `MetricsTab.tsx`:
```tsx
function GaugeRing({ value, color, size = 56 }: { value: number; color: string; size?: number }) {
    const radius = (size - 6) / 2;
    const circumference = 2 * Math.PI * radius;
    const offset = circumference - (value / 100) * circumference;
    return (
        <svg width={size} height={size} className="transform -rotate-90">
            <circle cx={size/2} cy={size/2} r={radius} fill="none" stroke="currentColor" strokeWidth="4" className="text-muted/20" />
            <circle cx={size/2} cy={size/2} r={radius} fill="none" stroke={color} strokeWidth="4"
                strokeDasharray={circumference} strokeDashoffset={offset} strokeLinecap="round"
                className="transition-all duration-700"
            />
        </svg>
    );
}
```

Scale this up to `size={200}` for the main budget gauge, with the text values rendered inside using absolute positioning.

#### 3. Service Cards Grid
A responsive grid (1-2-3 columns depending on viewport) of cards, one per service from `services` in the API response.

Each card must show:
- **Header**: Service name (icon based on type: `Server` for gunicorn, `Layers` for celery, `Radio` for daphne) + app name badge
- **Demand bar**: Horizontal bar showing demand score 0-1, color-coded (green <0.3, amber 0.3-0.6, red >0.6)
- **Stats row**: CPU %, Memory (used/limit), Network I/O, PIDs — use small `GaugeRing` components for CPU and Memory
- **Workers row**: Current workers / max workers with a visual indicator (dots or blocks, filled = active, empty = available)
- **Priority badge**: P1/P2/P3 with color
- **Last action**: "Scaled up 2→4 workers 5m ago" or "No changes" with timestamp
- **Quick actions**: "Edit" button to adjust min/max workers + priority inline

The cards should group visually by `app` name. Use a subtle left border color per app:
- smsly-helper: blue
- lina-deluxe: purple
- buyforfront: emerald
- marketer: amber

#### 4. Time-Series Charts (using recharts)
Two charts showing the last 1 hour of data (from `/api/history`):

**Chart A: Memory Usage Over Time**
- Stacked area chart with one area per service
- Y-axis: MB
- X-axis: timestamps
- Dashed horizontal line at budget limit
- Legend with service names

**Chart B: Demand Scores Over Time**
- Line chart with one line per service
- Y-axis: 0-1 demand score
- Background shaded regions: green (0-0.3), amber (0.3-0.6), red (0.6-1.0)
- Legend with service names

**Duration picker**: 15m, 30m, 1h buttons (filter data client-side from the 1h buffer)

#### 5. Recent Decisions Timeline
A scrollable vertical timeline of recent scaling decisions from `recent_decisions`:
- Timestamp on the left
- Decision card: "[container] scale_up: workers 2→4, memory 384MB→608MB"
- Color-coded by action type: green for scale_up, amber for scale_down, blue for adjust_memory
- Reason text in muted smaller font
- Maximum 20 most recent entries

#### 6. Configuration Panel (collapsible)
A collapsible card at the bottom with:
- **Global settings**: Total memory budget (slider: 4GB-16GB), Infra reserve (slider: 1GB-4GB), Check interval (slider: 10s-120s)
- **Per-service overrides**: Expandable accordion for each service showing min_workers, max_workers, priority sliders
- "Save Configuration" button that calls `POST /api/config`
- "Reset to Defaults" button

### Styling requirements (match existing smsly-hosting patterns exactly):

```typescript
// Use these exact patterns from the existing codebase:
// - DashboardShell wrapper
// - Card from shadcn/ui (not custom divs)
// - motion.div from framer-motion for enter animations
// - Gradient buttons: className="bg-gradient-to-r from-blue-500 to-cyan-600 text-white"
// - Muted text: className="text-muted-foreground"
// - Card style: className="bg-card border border-border rounded-xl"
// - Status pills: className="px-2 py-0.5 rounded text-xs font-bold"
// - animate-in fade-in slide-in-from-bottom-4 for page entry
```

### Add navigation link

Find the sidebar/navigation component (likely in `@/components/layout/DashboardShell.tsx` or a separate nav file). Add a new link:
```tsx
{ href: '/autoscaler', icon: Scaling, label: 'Autoscaler' }
```
Place it in the "Infrastructure" or "Servers" section of the nav, after the Servers link.

---

## File Summary

### Files to MODIFY:
1. `smsly-autoscaler.py` — Add HTTP API server thread + history buffer + 4 endpoints
2. `smsly-hosting/frontend/src/lib/api.ts` — Add `autoscalerApi` + TypeScript interfaces
3. `smsly-hosting/backend/config/urls.py` (or wherever the root urlconf is) — Add autoscaler URL include
4. `smsly-hosting/backend/config/settings/*.py` — Add `apps.autoscaler` to INSTALLED_APPS, `AUTOSCALER_API_URL` setting
5. Navigation component — Add Autoscaler link

### Files to CREATE:
1. `smsly-hosting/backend/apps/autoscaler/__init__.py`
2. `smsly-hosting/backend/apps/autoscaler/urls.py`
3. `smsly-hosting/backend/apps/autoscaler/views.py`
4. `smsly-hosting/frontend/src/app/autoscaler/page.tsx`

---

## Existing Code Reference (CRITICAL — read these files)

Before making ANY changes, read these files to understand the exact patterns used:

1. **`smsly-autoscaler.py`** — The autoscaler script you'll modify (485 lines, structure described above)
2. **`smsly-hosting/frontend/src/components/settings/ResourcesTab.tsx`** — Shows how CPU/memory presets are styled (204 lines)
3. **`smsly-hosting/frontend/src/components/settings/ScalingTab.tsx`** — Shows slider + switch + Card patterns (147 lines)
4. **`smsly-hosting/frontend/src/components/metrics/MetricsTab.tsx`** — Shows recharts AreaChart + GaugeRing + duration picker patterns (327 lines)
5. **`smsly-hosting/frontend/src/app/servers/page.tsx`** — Shows full page pattern with DashboardShell, framer-motion, status badges, polling (666 lines)
6. **`smsly-hosting/frontend/src/lib/api.ts`** — Shows axios instance, Token auth, existing API structure (948 lines)
7. **`smsly-hosting/backend/services/orchestrator.py`** — Shows Django backend patterns (226 lines)

---

## Critical Constraints

1. **NO new npm dependencies** — use only what's already installed (recharts, framer-motion, lucide-react, shadcn/ui)
2. **NO new Python dependencies for the autoscaler** — use only stdlib (`http.server`, `json`, `threading`, `collections`)
3. The `requests` library is already available in the Django backend (it's in requirements.txt)
4. The autoscaler API must be **read-only by default** — the `POST /api/config` endpoint should only accept changes from authenticated requests (check a simple bearer token from `AUTOSCALER_API_TOKEN` env var)
5. All timestamps must be ISO 8601 UTC
6. The frontend MUST be responsive (mobile-friendly)
7. The frontend MUST use dark mode compatible colors (the app uses a dark theme)
8. Use `'use client'` directive at the top of the page component
9. The page must handle the autoscaler being offline gracefully (show a clear "Autoscaler Offline" state with a retry button, not a crash)

## Design Philosophy

This is **THE** operations dashboard for the VPS. It should feel like a premium cloud console (think Vercel, Railway, or Render's dashboards). The memory budget ring is the hero element — it should immediately communicate "how much headroom do I have?" The service cards should make it obvious which services are busy and which are idle. The charts should show trends over time so the operator can identify patterns.

---

## Part 4: Intelligence System Gaps — Make It the Autonomous DevOps Brain

### ⚠️ CRITICAL: All 5 AI Models Must Power Every Module

The system supports **5 AI providers**: OpenAI (GPT-4o), Grok (xAI), Gemini (Google), Claude (Anthropic), and Mock (fallback). The function `ask_with_fallback()` in `providers.py` already handles multi-model orchestration:

- **0 keys configured** → Mock fallback (static responses)
- **1 key configured** → Solo mode (single provider)
- **2+ keys configured** → **Senate Committee** (all providers answer in parallel → cross-review each other → chair synthesizes consensus)

**Every module below MUST use `ask_with_fallback()` for ALL AI calls.** This means when the analyzer diagnoses logs, when the remediator decides on fixes, when cost.py generates recommendations, and when tasks generate reports — they ALL go through the multi-model pipeline. If 4 providers are configured, ALL 4 deliberate on every decision. This is the core value prop: multiple AI models collaborating on DevOps decisions, not just one.

The intelligence system at `smsly-hosting/backend/apps/intelligence/` is meant to be the **autonomous DevOps brain** that handles all human work: diagnosing failures, auto-remediating problems, analyzing logs with real AI, estimating costs, scanning codebases, and providing proactive recommendations. The provider infrastructure (`providers.py`) is solid — 4 real AI providers + 1 mock with Senate Committee consensus mode — but it's severely underutilized. Most modules use hardcoded responses instead of actually calling the AI.

### Current State (what exists)

| File | Lines | Status |
|------|-------|--------|
| `providers.py` | 784 | ✅ Solid — OpenAI, Grok, Gemini, Claude providers + Senate Committee consensus + balance checking |
| `models.py` | 43 | ✅ OK — Singleton `AIProviderSettings` with encrypted API keys |
| `views.py` | 130 | ⚠️ Minimal — Only 3 endpoints (`GET providers/`, `POST providers/update/`, `POST test/`) |
| `urls.py` | 10 | ⚠️ Minimal — Only 3 routes |
| `analyzer.py` | 81 | ❌ Stub — 3 hardcoded regex patterns, `generate_diagnosis()` fakes LLM with static strings |
| `remediator.py` | 150 | ❌ Partial — Only handles 3 issue types (OOM, DB timeout, crash loop), no AI-driven analysis |
| `scanner.py` | 445 | ✅ OK — Comprehensive repo scanner for stack/config/env detection |
| `cost.py` | 43 | ❌ Stub — Static pricing table only (AWS/GCP/Azure/Railway), no real cloud API integration |
| `tasks.py` | 85 | ❌ Partial — Anomaly task only checks last deployment's build logs |

### Gap 1: `analyzer.py` — Wire to Real AI Providers

**Current:** `LogAnalyzer.generate_diagnosis()` returns hardcoded strings like "AI Diagnosis: Check line 42" instead of actually calling the AI providers.

**Fix:** Import `ask_with_fallback` from `providers.py` and use it when regex patterns don't match. The AI should analyze logs contextually.

```python
# In analyzer.py, modify generate_diagnosis():
from .providers import ask_with_fallback

def generate_diagnosis(self, logs: str) -> str:
    issues = self.analyze_logs(logs)
    if issues:
        # Still use regex results for known patterns (fast)
        return self._format_known_issues(issues)

    # For unknown issues, use real AI analysis
    if len(logs) > 200:  # Only call AI if there's substantial log content
        try:
            prompt = (
                f"Analyze these deployment logs and diagnose the issue. "
                f"Be concise (max 3 sentences):\n\n{logs[-5000:]}"
            )
            response, provider = ask_with_fallback(prompt)
            return f"[{provider}] {response}"
        except Exception as e:
            logger.warning("AI diagnosis failed: %s", e)

    return "No obvious issues detected."
```

### Gap 2: `analyzer.py` — Expand Pattern Library

**Current:** Only 3 pattern types: `OOM_KILLED`, `DB_CONNECTION_TIMEOUT`, `CRASH_LOOP`.

**Add these pattern types** (at minimum):

```python
PATTERNS = {
    # ... existing 3 ...
    'SSL_CERT_EXPIRED': [
        r"SSL_ERROR_EXPIRED_CERT_ALERT",
        r"certificate has expired",
        r"SSL certificate problem: certificate has expired",
    ],
    'DISK_FULL': [
        r"No space left on device",
        r"ENOSPC",
        r"disk quota exceeded",
    ],
    'PORT_CONFLICT': [
        r"EADDRINUSE",
        r"address already in use",
        r"port is already allocated",
    ],
    'DNS_FAILURE': [
        r"EAI_NONAME",
        r"Name or service not known",
        r"NXDOMAIN",
        r"getaddrinfo failed",
    ],
    'DEPENDENCY_MISSING': [
        r"ModuleNotFoundError",
        r"ImportError",
        r"Cannot find module",
        r"Module not found",
    ],
    'BUILD_FAILURE': [
        r"ERROR: failed to solve",
        r"npm ERR!",
        r"pip install.*failed",
        r"cargo build.*error",
        r"SyntaxError",
    ],
    'PERMISSION_DENIED': [
        r"Permission denied",
        r"EACCES",
        r"Operation not permitted",
    ],
    'TIMEOUT': [
        r"TimeoutError",
        r"context deadline exceeded",
        r"request timeout",
        r"ETIMEDOUT",
    ],
    'RATE_LIMITED': [
        r"429 Too Many Requests",
        r"rate limit exceeded",
        r"RateLimitError",
    ],
    'HEALTH_CHECK_FAIL': [
        r"health check failed",
        r"unhealthy",
        r"readiness probe failed",
    ],
}
```

### Gap 3: `remediator.py` — Add Remediation Actions for New Patterns

**Current:** Only handles `SCALE_UP` (OOM), `SCALE_UP_POOL` (DB), `ROLLBACK` (crash loop).

**Add recommendations for each new pattern type:**

```python
RECOMMENDATIONS = {
    # ... existing 3 ...
    'SSL_CERT_EXPIRED': {
        'action': 'NOTIFY_ADMIN',
        'resource': 'SSL',
        'message': 'SSL certificate has expired. Trigger certificate renewal.'
    },
    'DISK_FULL': {
        'action': 'CLEANUP',
        'resource': 'DISK',
        'message': 'Disk full. Pruning old Docker images and logs.'
    },
    'PORT_CONFLICT': {
        'action': 'RESTART',
        'resource': 'CONTAINER',
        'message': 'Port conflict detected. Restarting container with port reassignment.'
    },
    'DNS_FAILURE': {
        'action': 'NOTIFY_ADMIN',
        'resource': 'DNS',
        'message': 'DNS resolution failed. Check domain configuration.'
    },
    'DEPENDENCY_MISSING': {
        'action': 'REBUILD',
        'resource': 'BUILD',
        'message': 'Missing dependency detected. Triggering fresh build.'
    },
    'BUILD_FAILURE': {
        'action': 'NOTIFY_AND_DIAGNOSE',
        'resource': 'BUILD',
        'message': 'Build failed. Running AI diagnosis on build logs.'
    },
    'TIMEOUT': {
        'action': 'SCALE_UP',
        'resource': 'REPLICAS',
        'amount': 1,
        'message': 'Request timeouts detected. Adding replica.'
    },
    'HEALTH_CHECK_FAIL': {
        'action': 'RESTART_OR_ROLLBACK',
        'resource': 'CONTAINER',
        'message': 'Health check failing. Attempting restart, then rollback if persistent.'
    },
}
```

Also implement the actual remediation logic for these actions in `apply_fix()`:
- `CLEANUP`: Run `docker system prune -f` via subprocess
- `REBUILD`: Trigger a fresh deployment with `--no-cache`
- `NOTIFY_AND_DIAGNOSE`: Call `ask_with_fallback()` with the build logs and store the AI diagnosis on the Deployment model
- `RESTART_OR_ROLLBACK`: Try restart first, if still unhealthy after 2 minutes, rollback
- `NOTIFY_ADMIN`: Create an AuditLog entry with severity=CRITICAL (no auto-fix, needs human)

### Gap 4: `cost.py` — Add AI-Powered Cost Analysis

**Current:** Only a static pricing lookup table. No actual analysis.

**Add:**

```python
from .providers import ask_with_fallback

class CostAdvisor:
    # ... existing PRICING dict ...

    def ai_cost_analysis(self, service_config: dict) -> str:
        """Use AI to provide detailed cost optimization recommendations."""
        prompt = (
            f"Given this service configuration:\n"
            f"- CPU: {service_config.get('cpu_cores', 1)} cores\n"
            f"- Memory: {service_config.get('memory_mb', 512)}MB\n"
            f"- Stack: {service_config.get('stack', 'unknown')}\n"
            f"- Current provider: {service_config.get('provider', 'unknown')}\n\n"
            f"Provide 3 specific cost optimization recommendations. "
            f"Compare AWS vs GCP vs Railway pricing. Be concise."
        )
        try:
            response, provider = ask_with_fallback(prompt)
            return response
        except Exception:
            return self._fallback_advice(service_config)

    def _fallback_advice(self, config: dict) -> str:
        estimates = self.estimate_monthly_cost(
            config.get('cpu_cores', 1),
            config.get('memory_mb', 512) / 1024
        )
        cheapest = min(estimates, key=estimates.get)
        return f"Cheapest option: {cheapest} at ${estimates[cheapest]}/mo"
```

### Gap 5: `tasks.py` — Expand Anomaly Detection Scope

**Current:** Only scans build logs from the latest deployment. Ignores runtime metrics, container health, and resource trends.

**Fix:** Add these task capabilities:

```python
@shared_task
def proactive_health_scan_task():
    """
    Proactive health scan — runs every 5 minutes.
    Checks ALL services for:
    1. Services that have been unhealthy for >5 minutes
    2. Services with memory usage >85% of limit
    3. Services with no successful deployment in >24 hours
    4. Services with repeated restart patterns
    """
    # ... implementation

@shared_task
def ai_deployment_review_task(deployment_id: str):
    """
    Post-deployment AI review — triggered after every deployment.
    Analyzes build logs + runtime behavior in first 2 minutes.
    If issues detected, provides AI-powered diagnosis and
    optionally triggers auto-rollback.
    """
    # ... implementation

@shared_task
def daily_intelligence_report_task():
    """
    Daily intelligence report — runs once per day.
    Generates a summary of:
    - Total deployments (success/fail ratio)
    - Resource utilization trends
    - Cost projections
    - Proactive recommendations
    Stores report in DB and can be viewed in the Intelligence UI.
    """
    # ... implementation
```

### Gap 6: `views.py` — Add Missing API Endpoints

**Current:** Only 3 endpoints (provider status, update, test prompt).

**Add these endpoints:**

```python
# In views.py:

@api_view(["POST"])
@permission_classes([IsAuthenticated])
def ai_analyze_logs(request):
    """
    POST /api/v1/ai/analyze/
    Body: { "logs": "...", "context": "deployment|runtime|build" }
    Returns: { "diagnosis": "...", "issues": [...], "recommendations": [...], "provider": "..." }
    """
    logs = request.data.get("logs", "")
    context = request.data.get("context", "deployment")
    analyzer = LogAnalyzer()
    # ... use analyzer + ask_with_fallback for AI diagnosis

@api_view(["POST"])
@permission_classes([IsAuthenticated])
def ai_cost_estimate(request):
    """
    POST /api/v1/ai/cost-estimate/
    Body: { "cpu_cores": 2, "memory_mb": 1024, "stack": "django", "provider": "aws" }
    Returns: { "estimates": {...}, "ai_recommendations": "..." }
    """
    advisor = CostAdvisor()
    # ... return estimates + AI analysis

@api_view(["GET"])
@permission_classes([IsAuthenticated])
def ai_intelligence_report(request):
    """
    GET /api/v1/ai/report/
    Returns the latest daily intelligence report.
    """
    # Return from DB or generate on-demand

@api_view(["GET"])
@permission_classes([IsAuthenticated])
def ai_anomaly_history(request):
    """
    GET /api/v1/ai/anomalies/
    Returns history of detected anomalies and remediation actions.
    """
    # Query AuditLog for AI_REMEDIATOR actions
```

**Add corresponding URL routes in `urls.py`:**
```python
urlpatterns = [
    # ... existing 3 ...
    path('analyze/', ai_analyze_logs, name='ai-analyze-logs'),
    path('cost-estimate/', ai_cost_estimate, name='ai-cost-estimate'),
    path('report/', ai_intelligence_report, name='ai-intelligence-report'),
    path('anomalies/', ai_anomaly_history, name='ai-anomaly-history'),
]
```

### Gap 7: Frontend Intelligence Page — Add Missing Sections

**Current:** `frontend/src/app/intelligence/page.tsx` (383 lines) shows provider status, AI chat, and deployment insights. Missing critical sections.

**Add these sections to the Intelligence page:**

#### A. Anomaly Detection Dashboard
- Live feed of detected anomalies with severity badges (CRITICAL/WARNING/INFO)
- Auto-remediation history: what the AI fixed, when, and result
- Toggle for enabling/disabling auto-remediation per issue type

#### B. AI Provider Configuration Panel (inline)
The page links to `/settings/ai` which may not exist. Instead, add an inline collapsible configuration panel directly on the Intelligence page:
- API key input fields for each provider (masked, with reveal toggle)
- Model selector dropdowns
- "Test Connection" button per provider
- Balance/credits display
- Save button calling `aiApi.updateProviders()`

#### C. Cost Intelligence Section
- Cloud cost comparison table (AWS vs GCP vs Azure vs Railway)
- Per-service cost breakdown
- "Get AI Recommendation" button that calls the new `/ai/cost-estimate/` endpoint
- Monthly cost trend chart

#### D. Intelligence Report Section
- Daily report card showing deployment success rate, issues detected, auto-fixes applied
- 7-day trend sparklines
- "Generate Report" button for on-demand AI analysis

### Gap 8: Add `api.ts` Client Methods for New Endpoints

Add to the existing `aiApi` object in `frontend/src/lib/api.ts`:

```typescript
export const aiApi = {
  // ... existing methods ...

  /** Analyze logs with AI */
  analyzeLogs: async (logs: string, context: string = 'deployment'): Promise<{
    diagnosis: string;
    issues: { type: string; confidence: number; pattern: string }[];
    recommendations: string[];
    provider: string;
  }> => {
    const res = await api.post('/ai/analyze/', { logs, context });
    return res.data;
  },

  /** Get cost estimates with AI recommendations */
  costEstimate: async (config: {
    cpu_cores: number;
    memory_mb: number;
    stack?: string;
    provider?: string;
  }): Promise<{
    estimates: Record<string, number>;
    ai_recommendations: string;
  }> => {
    const res = await api.post('/ai/cost-estimate/', config);
    return res.data;
  },

  /** Get latest intelligence report */
  getReport: async (): Promise<any> => {
    const res = await api.get('/ai/report/');
    return res.data;
  },

  /** Get anomaly detection history */
  getAnomalies: async (): Promise<{
    anomalies: {
      id: string;
      service_name: string;
      issue_type: string;
      severity: string;
      detected_at: string;
      auto_fixed: boolean;
      fix_result: string;
    }[];
  }> => {
    const res = await api.get('/ai/anomalies/');
    return res.data;
  },
};
```

---

## Part 4 File Summary

### Files to MODIFY:
1. `backend/apps/intelligence/analyzer.py` — Wire `generate_diagnosis()` to real AI, expand pattern library to 12+ patterns
2. `backend/apps/intelligence/remediator.py` — Add remediation actions for all new patterns, implement CLEANUP/REBUILD/NOTIFY logic
3. `backend/apps/intelligence/cost.py` — Add `ai_cost_analysis()` method using real AI providers
4. `backend/apps/intelligence/tasks.py` — Add `proactive_health_scan_task`, `ai_deployment_review_task`, `daily_intelligence_report_task`
5. `backend/apps/intelligence/views.py` — Add 4 new endpoints: analyze, cost-estimate, report, anomalies
6. `backend/apps/intelligence/urls.py` — Add 4 new URL routes
7. `frontend/src/lib/api.ts` — Add 4 new methods to `aiApi`
8. `frontend/src/app/intelligence/page.tsx` — Add Anomaly Dashboard, Provider Config Panel, Cost Intelligence, Report sections

### Existing Code to READ (CRITICAL):
1. **`backend/apps/intelligence/providers.py`** (784 lines) — The core AI provider system. Has `ask_with_fallback()`, `ask_collaborative()`, `get_configured_providers()`, `_sync_db_to_env()`. This is the foundation — all gaps boil down to "use this module more"
2. **`backend/apps/intelligence/scanner.py`** (445 lines) — Reference for how `RepoScanner` builds AI context. Pattern to follow for other analysis tasks
3. **`backend/apps/intelligence/models.py`** (43 lines) — `AIProviderSettings` singleton model with encrypted fields
4. **`backend/apps/deployments/models.py`** — Has `Service` and `Deployment` models used by the remediator
5. **`backend/apps/deployments/models_audit.py`** — Has `AuditLog` model for immutable audit trail
6. **`frontend/src/app/intelligence/page.tsx`** (383 lines) — The page you'll extend with new sections

### Key Principle
The `providers.py` file is the powerhouse — it already handles multi-provider consensus, parallel execution, fallback chains, and balance checking. The gaps are all about **connecting** the other modules to actually USE it. Every place that currently returns hardcoded strings should instead call `ask_with_fallback()`. Every detection task should log results to `AuditLog`. Every UI section should have a corresponding backend endpoint.
