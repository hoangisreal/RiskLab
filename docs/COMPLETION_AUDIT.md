# Completion audit

Scope: mandatory Phases 0–4 and the project Definition of Done in MASTER_PLAN.md. Phase 5 stays optional. This table records evidence. Historical Test results do not inform new choices; a new opening requires explicit owner approval after a concrete clean-tree candidate/lock exists.

| Requirement | Current evidence | Remaining proof |
|---|---|---|
| Fresh setup, synthetic CI, provenance and bounded memory | Locked environment; digest-pinned images build; 369 synthetic tests pass; official action SHAs pinned | Recheck fresh checkout/current GitHub CI |
| Partition roles and Train-only transforms | Loader/fit/hash/mutation guards; Test rejected outside locked evaluation | Maintain unchanged ledger through remaining operations |
| Historical validity and exact feature semantics | Every SQL fixture, partial payments, NULL no-history, shared cutoffs/sentinels, exact order/thread test; real validator passes with recorded exclusions | Independent full feature reproduction running |
| E1/E2/E3/E4, policy and diagnostics | All accepted real runs recorded in experiments.md; E2 includes leave-one-out; repeated served E1 feeds frozen E3; policy keeps defaults | Independent same-image Selection reproduction running |
| Calibration and per-loan costs | Frozen base, all calibration comparisons, reliability plot, configured LGD/margin grid, cost curve, threshold endpoints | No new model decisions remain |
| Lock, one-use evaluation, Test uncertainty/subgroups | Content-bound clean-tree locks, pre-read journal/exclusive guard and synthetic final evaluation | Prepare concrete lock; new real Test CIs/subgroups need explicit reuse approval |
| Served artifact equals selected artifact | Synthetic HTTP registry chain and public-schema golden predictions; real candidate strict metric reproduction and 500-applicant parity | Real registration, compose startup, pinned golden predictions |
| Request contract, limits, explainability and logs | Generated field schema, relational checks, declared/chunked body cap, record limits, original-feature contributions, unseen-category warnings and payload-free logs tested | Current real image/load verification |
| Immutable monitoring reference | Train-fitted Pipeline profile, Selection score/performance reference, pinned exact-byte and identity validation | Registered real bundle loading |
| Monitoring scenarios and Kaggle unlabeled input | CLI/API share one engine; seeded specs, raw-ratio recomputation, synthetic unlabeled/no-invented-performance proof | Run/report real seeded faults and detection limits |
| Full-engine false-alarm calibration | Run 03b14e0ed97c43a48aaaa067ca7eba19: 1/100 calibration, 0/100 held-out; frozen scale 1 | Report real subgroup weaknesses with CIs |
| Gate, forced bad deployment, detection and rollback | Hardened CLI and synthetic real HTTP loading/rollback; one-command corrupted-label exercise implemented | Execute real exercise and both-version shared-drift case |
| Concurrent empty/full-history load | Public-payload httpx runner, declared 1/4-worker matrix, aggregate tracking | Run matrix and record measured latency/errors |
| Independent metrics and reproducibility | Plain sklearn script supports E1/E2/E3/policy; strict aggregate/feature comparator; fresh isolated runner active | Complete real fresh comparison; recompute all accepted metrics |
| Model card and user-facing documentation | Current Selection decisions, source IDs, cost rationale, API/deployment/reproduction/monitoring limits documented | Add final operational evidence and disclose approval-dependent Test status |

No original data, partition membership, legacy model or Test report/journal has been deleted or rewritten. The earlier correction turn was verified progress, not completion. Contract-1.0 and the rejected unordered contract-1.1 reports are retained separately. Alias changes must use the documented CLI. Scientific decisions are configured and recorded before running them.
