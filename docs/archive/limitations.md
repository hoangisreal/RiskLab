# Demo vs production

> ARCHIVED 2026-11-04 — merged into `docs/RESULTS.md` §8.

| Aspect | RiskLab | Real production |
|---|---|---|
| Data | Static Kaggle snapshot; stratified holdouts | Continuous data; out-of-time validation |
| Labels | Immediate | Delayed 6–12+ months |
| Population | Approved loans only | Reject inference; live experiments |
| Features | Client-sent raw records | Bureau integrations, feature stores |
| Deployment | Alias change + in-process reload | Immutable bundles, shadow/canary |
| Governance | Model card + audit log | Independent validation, fair-lending review |
| Security | Localhost + admin API key | AuthN/AuthZ, encryption, PII law |
| Tracking | SQLite, single writer | PostgreSQL + object storage |
