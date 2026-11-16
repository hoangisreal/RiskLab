# RiskLab web

Next.js frontend for the simulated decision-support application. See the
[project README](../../README.md) for the main guide. Analysts propose,
Managers finalize simulated decisions, and Admins manage accounts. The UI does
not access PostgreSQL directly or grant its own permissions.

```bash
npm ci --ignore-scripts
npm run dev
npm run lint
npm run typecheck
npm run format:check
npm run build
npm run test:e2e
```

`RISKLAB_API_ORIGIN` is operator configuration (default http://127.0.0.1:8000),
not browser input or a credential variable. Open http://localhost:3000 so the
Origin matches the backend configuration. E2E requires Docker and runs a
separate synthetic backend/database without production credentials. Never
store secrets/tokens in localStorage or `NEXT_PUBLIC_*` variables.
