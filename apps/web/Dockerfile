# Production Next.js standalone image. The API origin is server-side only.
FROM node:24-bookworm-slim AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --ignore-scripts
COPY . .
# Rewrites are compiled at build time, so the API origin must be a build arg:
# `api:8000` inside Compose, `http://127.0.0.1:8000` for a host-built image.
ARG RISKLAB_API_ORIGIN=http://api:8000
ENV NEXT_TELEMETRY_DISABLED=1 RISKLAB_API_ORIGIN=$RISKLAB_API_ORIGIN
RUN npm run build

FROM node:24-bookworm-slim AS runtime
WORKDIR /app
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1
COPY --from=build /app/.next/standalone ./
COPY --from=build /app/.next/static ./.next/static
USER node
EXPOSE 3000
HEALTHCHECK --interval=15s --timeout=5s --retries=5 \
  CMD ["node", "-e", "fetch('http://127.0.0.1:3000/').then(r=>{if(!r.ok)process.exit(1)}).catch(()=>process.exit(1))"]
CMD ["node", "server.js"]
