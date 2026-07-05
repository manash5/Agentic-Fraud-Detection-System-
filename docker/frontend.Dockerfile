# =============================================================================
# frontend.Dockerfile — Next.js 16 app, production server (multi-stage)
#
# NOTE ON SERVE STRATEGY: this app is NOT a static site. It uses server-side
# rewrites (the `/api/*` -> backend proxy in next.config.ts) and on-demand
# server-rendered dynamic routes (e.g. /admin/transactions/[id]). A static
# nginx export cannot run either, so the production pattern here is the Next.js
# Node server (`next start`) — built with `output: "standalone"` so the runtime
# stage is still small (just the standalone bundle, no full node_modules).
#
# Build context is the repo ROOT (compose `context: ..`), so paths are
# `frontend/...`.
# =============================================================================

# ---- deps: install node_modules (cached until package files change) ---------
FROM node:20-alpine AS deps
WORKDIR /app
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

# ---- builder: compile the standalone production bundle ----------------------
FROM node:20-alpine AS builder
WORKDIR /app
# Baked into the built rewrite target (rewrites() is evaluated at build time).
# Defaults to the compose service name; override with --build-arg if renamed.
ARG BACKEND_INTERNAL_URL=http://backend:8000
ENV BACKEND_INTERNAL_URL=${BACKEND_INTERNAL_URL}
ENV NEXT_TELEMETRY_DISABLED=1
COPY --from=deps /app/node_modules ./node_modules
COPY frontend/ ./
RUN npm run build

# ---- runner: minimal image that runs `node server.js` -----------------------
FROM node:20-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    HOSTNAME=0.0.0.0

# Run as an unprivileged user.
RUN addgroup -g 1001 -S nodejs && adduser -u 1001 -S nextjs -G nodejs

# The standalone output bundles a trimmed node_modules + server.js. Static
# assets and public/ are copied alongside it (standalone doesn't include them).
COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /app/.next/static ./.next/static
COPY --from=builder --chown=nextjs:nodejs /app/public ./public

USER nextjs
EXPOSE 3000

# HOSTNAME=0.0.0.0 (set above) makes the server reachable from other containers
# and the host — binding localhost would make it unreachable.
CMD ["node", "server.js"]
