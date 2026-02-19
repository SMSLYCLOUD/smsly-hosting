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
    ignoreBuildErrors: false,
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
