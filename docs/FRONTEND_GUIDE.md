# SMSLY Hosting — Frontend Developer Guide

> Internal reference for the Next.js 15 frontend.

---

## Tech Stack

| Layer | Technology |
|-------|------------|
| Framework | Next.js 15 (App Router, TypeScript) |
| Styling | Tailwind CSS v3 (`^3.3.0` per `frontend/package.json`) |
| UI Components | Radix UI primitives + shadcn/ui + custom (28 UI primitives) |
| HTTP Client | Axios (via `src/lib/api.ts`, 2,494 lines, 41 API groups) |
| Auth | HttpOnly cookie-only (no localStorage tokens) |
| Theme | `next-themes` (dark/light) |
| Animation | Framer Motion (LazyMotion, domAnimation) |
| 3D/Topology | Three.js + React Three Fiber + react-force-graph + reactflow |
| Terminal | xterm.js (@xterm/xterm) |
| Code Editor | Monaco Editor (@monaco-editor/react) |

---

## Directory Structure

```
frontend/src/
├── app/                    # App Router pages (49 route directories)
│   ├── layout.tsx          # Root layout (12 providers/components tree)
│   ├── page.tsx            # Landing page (~1,400 lines, marketing)
│   ├── globals.css         # Global styles + Tailwind
│   ├── middleware.ts       # Auth guard (cookie-based route protection)
│   ├── login/              # Auth pages
│   ├── register/
│   ├── auth/               # OAuth callbacks (GitHub, GitLab, Bitbucket)
│   ├── dashboard/          # Main dashboard
│   ├── services/           # Service management
│   ├── deployments/        # Deployment history
│   ├── settings/           # 26-tab settings panel
│   ├── billing/            # Subscription management
│   ├── intelligence/       # AI diagnostics
│   ├── topology/           # Service graph visualization (3D, Solar System, City)
│   ├── tunnels/            # Tunnel management
│   ├── templates/          # One-click deploy templates
│   ├── marketplace/        # Add-on marketplace
│   ├── ecosystem/          # Platform ecosystem
│   ├── servers/            # Multi-server management
│   ├── functions/          # Serverless functions
│   ├── autoscaler/         # Autoscaler dashboard
│   ├── network/            # WireGuard VPN mesh
│   ├── replication/        # Database replication
│   ├── backups/            # Backup management
│   ├── domains/            # Custom domain management
│   ├── monitoring/         # Monitoring dashboard
│   ├── mcp/                # Model Context Protocol
│   ├── projects/           # Project management
│   ├── blueprints/         # Infrastructure blueprints
│   ├── transfers/          # Server transfers
│   ├── cloud/              # Cloud resources
│   ├── admin-dashboard/    # Admin pages
│   ├── pricing/            # Pricing page
│   ├── docs/               # Documentation pages (15+ subpages)
│   ├── grafana/            # Grafana embed
│   ├── logs/               # Log viewer
│   ├── activity/           # Activity feed
│   ├── restore/            # Restore wizard
│   ├── new/                # New service wizard
│   └── ... (49 total)
├── components/
│   ├── ui/                 # 28 Radix-based UI primitives (Button, Dialog, Tabs, etc.)
│   ├── layout/             # DashboardShell, Navbar, Footer, NotificationsDropdown
│   ├── dashboard/          # Dashboard widgets (ServicesGrid, ActivityFeed)
│   ├── deployments/        # Pipeline visualizer, buildpack selector, SafeDeploy panel
│   ├── settings/           # 26 settings tab components
│   ├── ai/                 # AI chat, floating AI assistant, repo analyzer
│   ├── addons/             # Addon management, DB explorer, maintenance
│   ├── metrics/            # Charts, resource graphs, world traffic map
│   ├── logs/               # Log viewer
│   ├── terminal/           # xterm console, update stream
│   ├── topology/           # 8 topology visualization components
│   ├── canvas/             # Service canvas, fleet radar, custom nodes
│   ├── effects/            # Starfield, NatureBackground, SpaceOpsBackground
│   ├── animations/         # Parallax, CloudHero
│   ├── billing/            # Resource price card
│   ├── blueprints/         # Blueprint card
│   ├── cloud/              # Cloud resource card
│   ├── insights/           # Security status tab
│   ├── intelligence/       # Code map view
│   ├── licensing/          # Upgrade prompt, tier badge, powered-by badge
│   ├── observability/      # Grafana embed
│   ├── cron/               # Cron job editor
│   ├── storage/            # Volume browser
│   ├── public/             # Platform notice
│   ├── sections/           # Marketing sections
│   ├── sidebar.tsx         # Main navigation sidebar
│   ├── auth-provider.tsx   # Auth context + periodic revalidation
│   ├── theme-provider.tsx  # Dark/light mode
│   ├── team-switcher.tsx   # Multi-team dropdown
│   ├── user-nav.tsx        # User menu
│   ├── RequirePermission.tsx # RBAC guard component
│   ├── ErrorBoundary.tsx   # Error boundary
│   ├── LazyMount.tsx       # Deferred rendering via requestIdleCallback
│   └── three-compat.tsx    # Three.js compatibility wrapper
├── lib/
│   ├── api.ts              # Axios API client (2,494 lines, 41 API groups)
│   ├── api-base.ts         # Server-side API proxy helper for Next.js route handlers
│   ├── apiError.ts         # API error handling
│   ├── auth.ts             # Logout function
│   ├── auth-cookies.ts     # Cookie helpers (legacy cleanup only)
│   ├── device-fingerprint.ts # Hardware fingerprint for device trust
│   ├── featureFlags.ts     # Feature flag configuration
│   ├── nav-visibility.ts   # Navigation visibility rules
│   ├── paths.ts            # Protected route prefixes (34 prefixes)
│   ├── role-routes.ts      # Route-to-permission mapping
│   ├── spaceStatusMap.ts   # SpaceOps visual status mapping
│   ├── utils.ts            # cn() helper for className merging
│   ├── websocket.ts        # WebSocket hook with auto-reconnect
│   ├── addonConstants.ts   # Addon metadata constants
│   ├── addonRegistry.ts    # 50+ addon registry
│   └── project-constants.ts # Project constants
├── hooks/
│   ├── use-team.ts         # Team context & switching (localStorage + DOM events)
│   ├── useLiveData.ts      # Polling hook (auto-pauses on tab hidden)
│   ├── usePermissions.ts   # RBAC permission checking (has, hasAny, hasAll)
│   └── useGraphData.ts     # Topology graph data (merges user + ecosystem graphs)
├── data/
│   └── app-catalog.ts      # Static app catalog data
├── _impl/                  # Heavy 3D/topology implementations (code-split)
└── types/                  # TypeScript type definitions
```

---

## Authentication Flow

The frontend uses **HttpOnly cookie-only** authentication. No tokens are stored in `localStorage`.

```
Login Form → POST /api/v1/auth/login/
                ↓
         Backend sets HttpOnly cookie:
         __Host-auth_token (production) or auth_token (development)
                ↓
         AuthProvider calls GET /api/v1/auth/user/
         (cookie auto-attached by browser)
                ↓
         User state populated → redirect to /dashboard
```

### Three-Layer Auth Defense

**Layer 1: Next.js Middleware** (`src/middleware.ts`)
- Runs on every request matching 34 protected route prefixes
- Checks for `__Host-auth_token` or `auth_token` HttpOnly cookies
- Also checks `sessionid` Django session cookie
- No cookie → redirect to `/login`. Has cookie on auth page → redirect to `/dashboard`
- Cookie check is **presence-only** (no validation) — `AuthProvider` validates against backend

**Layer 2: AuthProvider** (`src/components/auth-provider.tsx`)
- Client-side React context that fetches `GET /api/v1/auth/user/`
- **Periodic revalidation** every 60 seconds to catch expired sessions
- On 401: calls `POST /api/v1/auth/logout/` (backend clears cookie), redirects to `/login`
- Redirect guard prevents infinite loops (5s cooldown, max 3 redirects per session)
- Exposes `useAuth()` hook: `{ user, loading }`

**Layer 3: Axios Interceptors** (`src/lib/api.ts`)
- Response interceptor: On 401, calls backend logout, clears legacy cookies, redirects to login
- Request interceptor: Attaches `X-Team-ID` header from localStorage
- Remote server proxy: Rewrites requests to `/servers/{id}/proxy/` when remote server selected

### Auth Cookie Strategy

Fully HttpOnly — the frontend never reads or writes tokens in JavaScript. The backend manages the cookie lifecycle via `Set-Cookie` headers. `auth-cookies.ts` exists for legacy cleanup only.

---

## Authorization / RBAC

- **Permission system**: 28 permission codes (`src/hooks/usePermissions.ts`) matching backend `apps.permissions.codes`
- **Route-level guards**: `src/lib/role-routes.ts` maps 18 admin/billing/settings routes to required permissions
- **Component-level guards**: `<RequirePermission code="billing.manage" fallback={<AccessDenied />}>` renders children conditionally
- **usePermissions() hook**: Provides `has()`, `hasAny()`, `hasAll()`, team roles, org roles, isSuperuser, isStaff
- **Team context**: `useTeam()` hook manages active team via localStorage + custom DOM events (`smsly:team-changed`)

---

## API Client (`src/lib/api.ts`)

Single Axios instance with:
- **Base URL**: Dynamic origin detection (`window.location.origin + /api/v1/`)
- **Auth**: HttpOnly cookie auto-attached by browser (no manual token management)
- **401 interceptor**: Auto-clears cookies and redirects to `/login`
- **Remote proxy**: Rewrites requests through `/servers/{id}/proxy/` when remote server selected
- **Failover**: Auto-switches to local after 3 consecutive gateway failures

### API Groups (41 total)

| Group | Domain | Key Methods |
|-------|--------|-------------|
| `servicesApi` | Services | `list`, `create`, `get`, `update`, `deploy`, `restart`, `stop`, `delete`, `uploadDeploy` (50+ methods) |
| `serversApi` | Multi-server | `list`, `get`, `create`, `update`, `provision`, `proxy`, `remoteDeployService` |
| `deployApi` | Multi-deploy | `multiDeploy`, `agentReady`, `agentHeartbeat` |
| `addonsApi` | Addons | `list`, `create`, `delete`, `expose`, `reprovision`, `rotateCredentials`, `runQuery`, `getLogs` |
| `billingApi` | Billing | `getPlans`, `getSubscription`, `subscribe`, `getInvoices`, `getUsage` |
| `aiApi` | AI | `getProviders`, `testPrompt`, `analyzeLogs`, `costEstimate`, `getReport`, `getAnomalies` |
| `previewApi` | Preview envs | `create`, `list`, `destroy` |
| `teamsApi` | Teams | `list`, `create`, `members`, `inviteMember` |
| `tunnelsApi` | Tunnels | `list`, `create`, `delete`, `requests`, `replay`, `share` |
| `autoscalerApi` | Autoscaler | `getStatus`, `getHistory`, `updateConfig`, `trigger` |
| `scalingApi` | Scaling | `getReplicas`, `spawnReplica`, `destroyReplica` |
| `tokensApi` | API tokens | `list`, `create`, `revoke` |
| `backupsApi` | Backups | `importKey`, `getHeader`, `list`, `listKeys` |
| `organizationsApi` | Organizations | `list`, `create`, SSO management |
| `licensingApi` | Licensing | `getStatus`, `activate`, `deactivate` |
| `domainsApi` | Domains | `list`, `create`, `detail` |
| `notificationsApi` | Notifications | CRUD, preferences |
| `alertsApi` | Alerts | `listRules`, `createRule`, `toggleRule`, `testSmtp` |
| `ecosystemApi` | Ecosystem | `bulkUpdateEnvironment`, `cachedScan` |
| `databaseReplicasApi` | DB replicas | `list`, `create`, `update`, `test`, `sync` |
| `registryCredentialsApi` | Registry | `list`, `create`, `delete` |
| `coreApi` | Dashboard | `getDashboardOverview`, `adminGetUsers`, `getNotifications` |
| `systemApi` | System | `health`, `resources`, `config`, `runMaintenance`, `getPlatformUpdate` |
| `templatesApi` | Templates | `list`, `deploy` |
| `projectsApi` | Projects | `list`, `create`, `update`, `delete`, `moveService`, `syncEnvs` |
| `codeAnalysisApi` | Code analysis | `analyze`, `getResult` |
| `cloudResourceApi` | Cloud resources | `list`, `create`, `detail` |
| *...and 14 more* | | |

### Adding New API Methods

```typescript
// In src/lib/api.ts, add to the relevant group:
export const myApi = {
  // ...existing methods...

  newMethod: async (id: string, data: any) => {
    const res = await api.post(`/my-resource/${id}/action/`, data);
    return res.data;
  },
};
```

---

## Key Components

### DeploymentsTab

Status badges with icons and colors:

| Status | Color | Icon | Actions |
|--------|-------|------|---------|
| `QUEUED` | Gray | Clock | Cancel |
| `REVIEW` | Amber pulse | Eye | Approve, Cancel |
| `BUILDING` | Blue | Spinner | Cancel |
| `BUILD_FAILED` | Red | X | — |
| `AWAITING_APPROVAL` | Amber | Check | Approve, Cancel |
| `DEPLOYING` | Blue | Spinner | — |
| `HEALTH_CHECK` | Blue | Spinner | — |
| `HEALTH_CHECK_FAILED` | Red | X | — |
| `ACTIVE` | Green | Check | — |
| `FAILED` | Red | X | — |
| `CANCELLED` | Gray | Ban | — |
| `ROLLING_BACK` | Orange | Spinner | — |
| `ROLLED_BACK` | Orange | Check | — |

### AuthProvider

- Wraps entire app in `layout.tsx` (part of 12-provider tree)
- On mount: fetches `GET /api/v1/auth/user/` (cookie auto-attached)
- **Periodic revalidation** every 60 seconds
- On 401: calls backend logout, redirects to `/login`
- Exposes `useAuth()` hook: `{ user, loading }`
- User object: `{ pk, username, email, first_name, last_name, is_staff, is_superuser, permissions, roles: { teams: [], orgs: [] } }`

### Root Layout Provider Tree

```
ThemeProvider → AuthProvider → LazyMotion → TierProvider → ConfirmProvider
  → SpaceOpsProvider → SpaceOpsBackground + Navbar + Footer
  → FloatingAILoader + ThreeCompat + PoweredByBadge + Toaster
```

### Sidebar

- Collapsible navigation with icons
- Route groups: Dashboard, Services, Deployments, Intelligence, Billing, Settings
- Team switcher at top
- User nav at bottom
- Feature-flag controlled visibility for autoscaler, replication, tunnels, VPN mesh, functions, transfers, grafana

---

## Hooks

### `useAuth()`
```typescript
const { user, loading } = useAuth();
// user: { pk, username, email, first_name, last_name, is_staff, is_superuser, permissions, roles } | null
```

### `useTeam()`
```typescript
const { currentTeam, teams, switchTeam } = useTeam();
// Uses localStorage + custom DOM events (smsly:team-changed)
```

### `useLiveData(endpoint, interval)`
```typescript
const { data, loading, error } = useLiveData('/services/', 5000);
// Polls endpoint every `interval` ms. Auto-pauses when tab hidden.
```

### `usePermissions()`
```typescript
const { has, hasAny, hasAll, teamRole, orgRole, isSuperuser, isStaff } = usePermissions();
// has('billing.manage') → boolean
```

### `useGraphData()`
```typescript
const { nodes, edges } = useGraphData();
// Merges user services + ecosystem graph for topology visualization
```

---

## State Management

No Redux/Zustand/Jotai — the app uses a **minimal, context-based approach**:

### Contexts (4 total)
| Context | Purpose |
|---------|---------|
| `AuthContext` | User data, loading state |
| `TierContext` | License tier/features |
| `SpaceOpsContext` | Visual effect mode (idle/analyzing/deploying) |
| `ConfirmContext` | Global confirm dialog state |

### Cross-Component Communication
- **Custom DOM Events**: `smsly:team-changed`, `smsly:server-changed`, `smsly:topology-refresh`
- **localStorage**: Active server (`smsly_active_server`), active team (`smsly_active_team`)
- **WebSockets**: `useWebSocket()` hook with auto-reconnect (exponential backoff, max 10 attempts)

---

## Styling Conventions

1. **Tailwind v3** — use `@tailwind base; @tailwind components; @tailwind utilities;` in `globals.css` (PostCSS pipeline via `tailwindcss` v3). Do **not** use the v4-only `@import "tailwindcss"` syntax until the project upgrades.
2. **Dark mode** — use `dark:` variants, theme-provider handles toggling (class strategy)
3. **shadcn/ui** — import from `@/components/ui/`
4. **`cn()` helper** — merge classNames: `cn("base", conditional && "active")`
5. **Design tokens** — Indigo primary (#6366f1), Cyan accent (#06b6d4), Emerald (#10b981)
6. **Typography** — Inter (sans), JetBrains Mono (mono)
7. **Animations** — `fadeIn`, `slideUp`, `shimmer`, `float`, `breathe`, `pulseGlow`, `gradient`

---

## Development

```bash
cd frontend
npm install
npm run dev       # http://localhost:3000 (Turbopack HMR via --turbo)
```

Backend API proxy: requests to `/api/` are proxied through Next.js rewrites in `next.config.js` (30+ explicit rewrites + catchall).

### Build

```bash
npm run build     # Production build (standalone output)
npm run start     # Production server
npm run lint      # ESLint
npm run typecheck # TypeScript check (tsc --noEmit)
npm run test:unit # Vitest unit tests
npm test          # Playwright E2E tests
```

---

## Recent Changes

| Date | Change | Files |
|------|--------|-------|
| 2026-06 | HttpOnly cookie-only auth (removed localStorage token storage) | `auth-cookies.ts`, `auth-provider.tsx`, `middleware.ts` |
| 2026-06 | 3D topology visualization with Three.js | `components/topology/`, `_impl/` |
| 2026-06 | Feature flags for autoscaler, replication, tunnels, VPN mesh | `featureFlags.ts` |
| 2026-02-17 | Cancel/Approve deployment buttons | `DeploymentsTab.tsx`, `api.ts` |
| 2026-02-17 | Review status with amber pulsing eye icon | `DeploymentsTab.tsx` |
