# Stage 1: Build
FROM node:20-alpine AS builder
WORKDIR /app

# Accept build arguments for Next.js public env vars
# In production install_host.sh, this should be set to the relative path /api
# This allows Traefik to proxy the requests to the backend on the same port (8090)
ARG NEXT_PUBLIC_API_URL=/api
ARG NODE_BUILD_MEMORY_MB=8192

ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL

COPY package.json package-lock.json* ./
RUN npm install
COPY . .
RUN NODE_OPTIONS="--max-old-space-size=${NODE_BUILD_MEMORY_MB}" npm run build

# Stage 2: Runner (standalone mode for production + middleware support)
FROM node:20-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production
ENV HOSTNAME=0.0.0.0
ENV PORT=3000

# Security: Create user first
RUN addgroup -g 1001 -S nodejs && \
    adduser -S nextjs -u 1001

# Copy standalone server (includes all necessary node_modules inline)
COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone ./

# Copy static assets (not included in standalone output)
COPY --from=builder --chown=nextjs:nodejs /app/.next/static ./.next/static
COPY --from=builder --chown=nextjs:nodejs /app/public ./public

USER nextjs

EXPOSE 3000

# Wrapper adds resilience against client-abort ECONNRESET/EPIPE errors.
COPY --chown=nextjs:nodejs start.js ./start.js

# Healthcheck for frontend application
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD node -e "require('http').get('http://127.0.0.1:3000/', (r) => {if (r.statusCode !== 200) throw new Error(r.statusCode)})" || exit 1

CMD ["node", "start.js"]
