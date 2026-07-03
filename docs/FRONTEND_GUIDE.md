# SMSLY Hosting — Frontend Developer Guide

> Internal reference for the Next.js 15 frontend.

---

## Tech Stack

| Layer | Technology |
|-------|------------|
| Framework | Next.js 15 (App Router, TypeScript) |
| Styling | Tailwind CSS v3 (`^3.3.0` per `frontend/package.json`) |
| UI Components | shadcn/ui + custom |
| HTTP Client | Axios (via `src/lib/api.ts`) |
| Auth | Cookie + localStorage dual-token |
| Theme | `next-themes` (dark/light) |

---

## Directory Structure

```
frontend/src/
├── app/                    # App Router pages (27 routes)
│   ├── layout.tsx          # Root layout (AuthProvider + ThemeProvider)
│   ├── page.tsx            # Landing page (44K lines, marketing)
│   ├── globals.css         # Global styles + Tailwind
│   ├── login/              # Auth pages
│   ├── register/
│   ├── dashboard/          # Main dashboard
│   ├── services/           # Service management
│   ├── deployments/        # Deployment history
│   ├── settings/           # 8-tab settings panel
│   ├── billing/            # Subscription management
│   ├── intelligence/       # AI diagnostics
│   ├── topology/           # Service graph visualization
│   ├── tunnels/            # Tunnel management
│   ├── templates/          # One-click deploy templates
│   ├── marketplace/        # Add-on marketplace
│   ├── ecosystem/          # Platform ecosystem
│   ├── servers/            # Server management
│   ├── functions/          # Serverless functions
│   ├── reseller/           # Reseller dashboard
│   └── store/              # App store
├── components/
│   ├── ui/                 # shadcn/ui primitives (Button, Dialog, etc.)
│   ├── layout/             # Page layout wrappers
│   ├── dashboard/          # Dashboard widgets
│   ├── deployments/        # DeploymentsTab, status badges
│   ├── settings/           # Settings tabs (8 tabs)
│   ├── ai/                 # AI chat, diagnostics
│   ├── addons/             # Marketplace add-on cards
│   ├── metrics/            # Charts, resource graphs
│   ├── logs/               # Log viewer
│   ├── terminal/           # Web terminal
│   ├── topology/           # Service topology graph
│   ├── tunnels/            # Tunnel management
│   ├── canvas/             # Visual editor
│   ├── cron/               # Cron job editor
│   ├── storage/            # Volume browser
│   ├── effects/            # Visual effects (particles, etc.)
│   ├── animations/         # Scroll/transition animations
│   ├── sidebar.tsx         # Main navigation sidebar
│   ├── auth-provider.tsx   # Auth context + token lifecycle
│   ├── theme-provider.tsx  # Dark/light mode
│   ├── team-switcher.tsx   # Multi-team dropdown
│   └── user-nav.tsx        # User menu
├── lib/
│   ├── api.ts              # Axios API client (550 lines, all endpoints)
│   ├── auth-cookies.ts     # Cookie helpers for SSR auth
│   └── utils.ts            # cn() helper for className merging
├── hooks/
│   ├── use-team.ts         # Team context & switching
│   └── useLiveData.ts      # SSE/polling for real-time updates
├── data/                   # Static data (templates, pricing)
└── middleware.ts           # Auth guard (cookie-based route protection)
```

---

## Authentication Flow

```
Login Form → POST /api/v1/auth/login/
                ↓
         { key: "token-abc" }
                ↓
   localStorage.setItem("auth_token", key)
   setAuthTokenCookie(key)  ← sync to cookie for middleware
                ↓
         AuthProvider re-fetches /auth/user/
                ↓
         User state populated → redirect to /dashboard
```

### Dual-Token Strategy

| Store | Purpose | Reader |
|-------|---------|--------|
| `localStorage` | API requests (Axios interceptor) | Client-side JS |
| `auth_token` cookie | Route protection | Next.js middleware (server-side) |

**Why both?** Next.js middleware runs on the edge (server) and can't read `localStorage`. Cookies let the middleware gate protected routes without a round-trip to the backend.

### Auth Guard (`middleware.ts`)

- Protected routes: `/dashboard`, `/services`, `/settings`, `/billing`, etc. (22 prefixes)
- Auth pages: `/login`, `/register`
- Logic: No cookie → redirect to `/login`. Has cookie on auth page → redirect to `/dashboard`.
- Cookie check is **presence-only** (no validation) — `AuthProvider` validates the token against the backend.

---

## API Client (`src/lib/api.ts`)

Single Axios instance with:
- **Base URL**: Dynamic origin detection (`window.location.origin + /api/v1/`)
- **Auth interceptor**: Reads token from `localStorage`, falls back to cookie
- **401 interceptor**: Auto-clears token and redirects to `/login`

### API Groups

| Group | Methods |
|-------|---------|
| `servicesApi` | `list`, `create`, `get`, `update`, `deploy`, `getDeployments`, `rollback`, `cancelDeployment`, `approveDeployment`, `getEnvVars`, `createEnvVar`, `deleteEnvVar`, `getMetrics`, `getCronJobs`, `getVolumes`, `verifyDomain` |
| `templatesApi` | `list`, `deploy` |
| `deploymentsApi` | `list`, `get`, `getLogs` |
| `teamsApi` | `list`, `create`, `get`, `getMembers`, `invite`, `removeMember` |
| `billingApi` | `getSubscription`, `getInvoices`, `getUsage` |
| `authApi` | `login`, `register`, `logout`, `getUser`, `socialLogin` |
| `settingsApi` | `get`, `update`, `getAlerts`, `updateAlerts`, `getOAuth`, `updateOAuth`, `getInfra`, `updateInfra` |
| `intelligenceApi` | `diagnose`, `chat`, `getHistory` |
| `domainsApi` | `list`, `create`, `verify`, `delete` |
| `addonsApi` | `list`, `provision`, `deprovision`, `getCredentials` |

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
| `LIVE` | Green | Check | — |
| `FAILED` | Red | X | — |
| `CANCELLED` | Gray | Ban | — |

### AuthProvider

- Wraps entire app in `layout.tsx`
- On mount: checks `localStorage` for token → syncs to cookie → validates with backend
- On invalid token: clears both stores, redirects to `/login`
- Exposes `useAuth()` hook: `{ user, loading }`

### Sidebar

- Collapsible navigation with icons
- Route groups: Dashboard, Services, Deployments, Intelligence, Billing, Settings
- Team switcher at top
- User nav at bottom

---

## Hooks

### `useAuth()`
```typescript
const { user, loading } = useAuth();
// user: { pk, username, email, first_name, last_name } | null
```

### `useTeam()`
```typescript
const { currentTeam, teams, switchTeam } = useTeam();
```

### `useLiveData(endpoint, interval)`
```typescript
const { data, loading, error } = useLiveData('/services/', 5000);
// Polls endpoint every `interval` ms
```

---

## Styling Conventions

1. **Tailwind v3** — use `@tailwind base; @tailwind components; @tailwind utilities;` in `globals.css` (PostCSS pipeline via `tailwindcss` v3). Do **not** use the v4-only `@import "tailwindcss"` syntax until the project upgrades.
2. **Dark mode** — use `dark:` variants, theme-provider handles toggling
3. **shadcn/ui** — import from `@/components/ui/`
4. **`cn()` helper** — merge classNames: `cn("base", conditional && "active")`
5. **No inline styles** — all styling via Tailwind classes

---

## Development

```bash
cd frontend
npm install
npm run dev       # http://localhost:3000
```

Backend API proxy: requests to `/api/` are proxied through Nginx in Docker. For local dev, ensure the backend is running on port 8000.

### Build

```bash
npm run build     # Production build (standalone output)
```

---

## Recent Changes

| Date | Change | Files |
|------|--------|-------|
| 2026-02-17 | Cancel/Approve deployment buttons | `DeploymentsTab.tsx`, `api.ts` |
| 2026-02-17 | Review status with amber pulsing eye icon | `DeploymentsTab.tsx` |
| 2026-02-18 | Rate limit fix deployed (backend) — no frontend changes needed | — |
