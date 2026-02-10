# Stage 1: Build
FROM node:20-alpine AS builder
WORKDIR /app

# Accept build arguments for Next.js public env vars
# In production install_host.sh, this should be set to the relative path /api
# This allows Traefik to proxy the requests to the backend on the same port (8090)
ARG NEXT_PUBLIC_API_URL=/api
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL

COPY package.json package-lock.json* ./
RUN npm install
COPY . .
RUN npm run build

# Stage 2: Runner
FROM node:20-alpine AS runner
WORKDIR /app
ENV NODE_ENV production

# Security: Create user first
RUN addgroup -g 1001 -S nodejs && \
    adduser -S nextjs -u 1001

# Copy files with correct ownership directly (faster than chown -R)
COPY --from=builder --chown=nextjs:nodejs /app/package.json ./package.json

# Copy all build output with correct permissions
COPY --from=builder --chown=nextjs:nodejs /app/.next ./.next
COPY --from=builder --chown=nextjs:nodejs /app/public ./public
COPY --from=builder --chown=nextjs:nodejs /app/node_modules ./node_modules

USER nextjs

EXPOSE 3000
CMD ["npm", "start"]
