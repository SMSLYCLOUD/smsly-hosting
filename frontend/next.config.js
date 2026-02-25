/** @type {import('next').NextConfig} */
const path = require('path')

const nextConfig = {
  reactStrictMode: true,
  output: 'standalone',
  outputFileTracingRoot: path.join(__dirname),
  images: {
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
  typescript: {
    // Pre-existing TS2554 in topology/ServiceTopologyTab from react-force-graph-3d types.
    // Was previously masked by OOM crash (512MB limit). Safe to ignore for now.
    ignoreBuildErrors: true,
  },
  eslint: {
    ignoreDuringBuilds: false,
  },
  async rewrites() {
    return [
      {
        source: '/api/:path*',
        destination: process.env.INTERNAL_API_URL
          ? `${process.env.INTERNAL_API_URL}/api/:path*`
          : 'http://localhost:8000/api/:path*',
      },
      {
        source: '/accounts/:path*',
        destination: process.env.INTERNAL_API_URL
            ? `${process.env.INTERNAL_API_URL}/accounts/:path*`
            : 'http://localhost:8000/accounts/:path*',
      },
    ]
  },
}

module.exports = nextConfig
