/** @type {import('next').NextConfig} */
const path = require('path')

// Resolve the upstream API origin in priority order:
//   1. NEXT_PUBLIC_API_BASE / INTERNAL_API_BASE   (preferred — base URL only, no /api/v1 suffix)
//   2. INTERNAL_API_URL / NEXT_PUBLIC_API_URL     (legacy — these include /api/v1; strip the suffix)
//   3. http://localhost:8000                       (local Django dev default)
// The proxy routes below append `/api/v1/...` to this value.
function resolveApiBase() {
  const raw =
    process.env.NEXT_PUBLIC_API_BASE ||
    process.env.INTERNAL_API_BASE ||
    (process.env.INTERNAL_API_URL || process.env.NEXT_PUBLIC_API_URL || '')
      .replace(/\/api\/v\d+\/?$/, '') ||
    'http://localhost:8000'
  return raw.replace(/\/+$/, '')
}

const API_BASE = resolveApiBase()

// Build the list of explicit per-prefix rewrites. The existing global
// `/api/:path*` catchall from Frontend Agent 5 is preserved as a safety net so
// any future backend route still gets proxied even if it isn't enumerated
// here. These explicit entries are evaluated FIRST by Next.js, so they win
// for the 30+ rust_twin routes the Next.js app is expected to cover.
const API_V1_PREFIXES = [
  '/api/v1/auth',
  '/api/v1/projects',
  '/api/v1/services',
  '/api/v1/deployments',
  '/api/v1/billing',
  '/api/v1/teams',
  '/api/v1/domains',
  '/api/v1/tunnels',
  '/api/v1/transfers',
  '/api/v1/webhooks',
  '/api/v1/sso',
  '/api/v1/backups',
  '/api/v1/marketplace',
  '/api/v1/addons',
  '/api/v1/admin',
  '/api/v1/approvals',
  '/api/v1/replication',
  '/api/v1/notifications',
  '/api/v1/observability',
  '/api/v1/database-replicas',
  '/api/v1/cloud-storage',
]

const API_V1_EXACT_PATHS = [
  '/api/v1/audit-log',
]

const EXACT_REWRITES = [
  { source: '/health',     destination: `${API_BASE}/health` },
  { source: '/health/live', destination: `${API_BASE}/health/live` },
  { source: '/health/ready', destination: `${API_BASE}/health/ready` },
  { source: '/health/deps', destination: `${API_BASE}/health/deps` },
  { source: '/metrics',    destination: `${API_BASE}/metrics` },
  { source: '/openapi.json', destination: `${API_BASE}/openapi.json` },
]

const nextConfig = {
  reactStrictMode: true,
  output: 'standalone',
  outputFileTracingRoot: path.join(__dirname),
  poweredByHeader: false,
  compress: true,
  experimental: {
    optimizePackageImports: ['lucide-react', 'framer-motion', 'date-fns', '@heroicons/react/24/outline'],
  },
  images: {
    formats: ['image/avif', 'image/webp'],
    remotePatterns: [
      {
        protocol: 'https',
        hostname: 'ui-avatars.com',
        // Some providers use `/api` and `/api/` with query params.
        // Allow all paths on this host to avoid 400s from `_next/image`.
        pathname: '/**',
      },
      {
        protocol: 'https',
        hostname: 'avatar.vercel.sh',
        pathname: '/**',
      },
    ],
  },
  async rewrites() {
    const explicitApiV1 = API_V1_PREFIXES.map((prefix) => ({
      source: `${prefix}/:path*`,
      destination: `${API_BASE}${prefix}/:path*`,
    }))

    const explicitApiV1Root = API_V1_PREFIXES.map((prefix) => ({
      source: prefix,
      destination: `${API_BASE}${prefix}`,
    }))

    const explicitApiV1Exact = API_V1_EXACT_PATHS.map((path) => ({
      source: path,
      destination: `${API_BASE}${path}`,
    }))

    return [
      // Explicit per-prefix proxy entries (rust_twin / Django, port 8000 or 8080).
      // Listed first so they take precedence over the generic `/api/:path*` catchall.
      ...explicitApiV1,
      ...explicitApiV1Root,
      ...explicitApiV1Exact,
      // Top-level exact proxies (health, metrics, openapi).
      ...EXACT_REWRITES,
      // Original Agent-5 catchall kept as a safety net for unlisted routes.
      {
        source: '/api/:path*',
        destination: process.env.INTERNAL_API_URL
          ? `${process.env.INTERNAL_API_URL}/api/:path*`
          : `${API_BASE}/api/:path*`,
      },
      {
        source: '/accounts/:provider(github|google)/:path*',
        destination: process.env.INTERNAL_API_URL
            ? `${process.env.INTERNAL_API_URL}/accounts/:provider/:path*`
          : `${API_BASE}/accounts/:provider/:path*`,
      },
    ]
  },
}

module.exports = nextConfig
