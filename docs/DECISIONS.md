# RiskLab — Decisions Log (canonical)

Single source for ML and product decisions. [PROJECT.md](PROJECT.md) holds the
recorded ML state; [product/prd.md](product/prd.md) defines the target MVP.
Product decisions marked as specifications are not implemented capabilities.
Changing a decision requires a new entry here and re-running whatever the
decision invalidates. Experiment rules are set in config **before** running.

## Locked values

| Item | Value | Decision |
|---|---|---|
| Partition seed / shares | `20261101` / 55-10-10-15-10 (Train-Cal-Sel-Test-Mon) | D9 |
| Partition hash | `b26bba642ca01b6240cd5f38f21cd0e546b5f975f3ce99cf847eb8f3f35a9101` | D9 |
| Served sources (contract 1.1) | application+ratios, bureau, previous, installments | D2, E2 run `4e467506bab74355b13bca284dacbb87` |
| Model family | LightGBM | E1-served comparison `2b1c9ef2f9f94984bb0900c54ec96887` |
| Calibration | none | E3 run `9a84a8cc279846e88b110f06ee3ea69c` |
| Threshold | `0.08040201` | E4 run `92aa4caf17fd434f95ad13dd80de5198`, D14 |
| Artifact SHA256 | `f5e4d127174bde337f03ed14d47ba050efe6f45ba4c599d0e6e558a256b0d676` | E3 run `e1f58f0e2ad14e74980b7ac5a855191b` |
| Final lock / Test run | `17b83143e33bb907` / `50d36f3b142a41039ff4d72967242f9a` | D20 |
| Test openings | 3 total (legacy `e98453beb10d833e`; contract-1.1 `402b9e050159762d` and `17b83143e33bb907`, same artifact) | D5, D18, D20 |
| Monitoring alert scales | smallest of [1, 1.25, 1.5, 2] meeting 5% calibration target; frozen = 1.0 | D15 |
| Missing-rate alert threshold | 0.20 (`configs/monitoring.yaml`) | 2026-11-03 correction |

## Decisions

| ID | Date | Decision | Consequence / where enforced |
|---|---|---|---|
| D1 | 2026-11-01 | MLflow UI + Markdown reports Phases 1–4; Streamlit optional Phase 5 | No dashboard code |
| D2 | 2026-11-01 | E2 decides which historical tables the API serves; correctness/simplicity over small gains | `configs/features.yaml` served set |
| D3 | 2026-11-01 | No fixed hardware/schedule; memory-bounded DuckDB + Parquet, never load raw tables into pandas | `data/duckdb_utils.py`, configured `memory_limit` |
| D4 | 2026-11-01 | English for docs, code, comments, commits | Everywhere |
| D5 | 2026-11-01 | Test only for final evaluation of a locked selection | `data/loaders.py`, `models/final_eval.py` |
| D6 | 2026-11-01 | Served artifact == evaluated artifact; no refit after selection | `models/candidate.py`, registry bundle |
| D7 | 2026-11-01 | Published AUC benchmarks are context only, not acceptance/leakage evidence | Reporting |
| D8 | 2026-11-01 | Ingest casts from all-VARCHAR with regex guard (DuckDB typed read rounds `2.5`→`3` silently) | `data/ingest.py` |
| D9 | 2026-11-01 | Partition seed/shares fixed; assignment orders IDs by BLAKE2b(`seed:id`) per class, no RNG stream. Changing requires `--overwrite` and invalidates all downstream results | `data/partition.py`, `configs/partition.yaml` |
| D10 | 2026-11-01 | Validation is SQL-driven over Parquet in DuckDB (not Pandera); rules in config; known exceptions listed per table/check/id | `data/validate*.py`, `sql/validation/` |
| D11 | 2026-11-01 | Cleaning is declarative and strictly row-wise (sentinel→NULL+flag, `XNA`→NULL), safe pre-partition and reusable by API | `data/clean.py`, `configs/cleaning.yaml` |
| D12 | 2026-11-01 | E1/diagnostics declared before running: primary ROC-AUC, `min_gain` 0.005, 1,000 paired bootstraps, 95% CI; LightGBM only if gain ≥ min and CI lower bound > 0, else LR. Permutation band 0.02, screening threshold 0.20 | `configs/experiments/e1.yaml`, `diagnostics.yaml` |
| D13 | 2026-11-03 | Feature contract 1.1 corrects installment partial-payment aggregation, two-level loan aggregates, NULL no-history rates; application ratios join the engineered baseline. Data-contract corrections, not Test-driven. Contract-1.0 artifacts/Test evidence immutable | `sql/features/`, `docs/data_contract.md` §Historical contract 1.1 |
| D14 | 2026-11-03 | E1 repeats on E2's served set; its fitted artifact freezes for E3 (E3 no longer refits). Per-loan proxy costs: LGD 0.50, margin 0.05 × `AMT_CREDIT`; E4 picks global threshold on Selection under this policy, reports constant-cost comparison + fixed grid. Illustrative assumptions, not measured losses | `experiments/e234.py`, `configs/cost.yaml` |
| D15 | 2026-11-03 | Policy analysis: descriptive fixed-LightGBM gender/age ablation, 1,000 paired resamples; gender stays excluded, age stays in. Served diagnostics flag importance share > 0.50 or \|Train-CV − Selection AUC\| > 0.05. Monitoring: 100 calibration + 100 held-out controls, disjoint seeds; smallest predeclared alert scale meeting 5% target freezes per model/profile/config before held-out measurement; performance alerts use 99% bootstrap CIs (500 resamples) | `configs/experiments/policy.yaml`, `configs/monitoring.yaml` |
| D16 | 2026-11-03 | First contract-1.1 candidate rejected: unordered floating aggregates changed 9 Selection predictions across processes. SUM/AVG inputs get explicit value order in shared SQL; no rounding, no relaxed tolerance. Superseded experiments retained locally, decision rules unchanged on rerun | `sql/features/*.sql` |
| D17 | 2026-11-03 | Frozen-model behavioral probe declared before running: EXT_SOURCE 0.2→0.8 on seeded Train sample should lower mean risk. Descriptive only; no refit/feature decision follows | Diagnostics |
| D18 | 2026-11-04 | Owner approved one disclosed Test reuse for lock `402b9e050159762d` (reply "ok"); approval attached to run `a9fc07a3cbaf43ab88d450ed6a3eca58`. No decision follows Test. Phase 5 remains out of scope | `models/final_eval.py` ledger |
| D19 | 2026-11-04 | Mild-shift sensitivity probe, declared before running: extend alert-scale candidates below 1 to [0.5, 0.75, 1.0, 1.25, 1.5, 2.0]; calibration picks the smallest scale meeting the 5% target on 100 calibration controls, freezes it, then measures 100 held-out controls and the mild-shift scenario. Outcome: scale 0.5 qualified (calibration false alarms 2%, held-out 0%, run `f0dafdc48e374218a9267492c291d786`); mild shift is now detected on `DAYS_BIRTH` (run `cfbe4b583c9b4ee19fb790f2ac657c66`) with no control alert (run `119db7b7ff5940a7829f64679e94122f`). **Not adopted into the registry:** `monitoring.yaml` is inside the bundle protocol hashes, so shipping sub-1 candidates would break registry-pinned monitoring for v6 and adopting them requires a full protocol rerun + new lock/registration, which would also detach the served version from the completed Test evaluation of lock `402b9e050159762d`. The shipped config therefore keeps [1.0, 1.25, 1.5, 2.0] and scale 1.0 (run `03b14e0ed97c43a48aaaa067ca7eba19`) remains in force; code support for sub-1 scales is retained for a future adoption cycle. Adoption is an explicit owner decision | `monitoring/policy.py`, `monitoring/service.py` |

| D20 | 2026-11-08 | Comment-only cleanup of protocol-hashed configs and sources (removal of internal working-plan references) invalidated lock `402b9e050159762d` by hash. The owner approved a full re-lock: the Selection protocol was re-run unchanged, reproduced every metric exactly, and produced the identical artifact SHA256 `f5e4d127174bde337f03ed14d47ba050efe6f45ba4c599d0e6e558a256b0d676`. New lock `17b83143e33bb907` was registered as version 7 and evaluated on Test with explicit reuse approval (run `50d36f3b142a41039ff4d72967242f9a`, third opening total, identical metrics). | `reports/locks/17b83143e33bb907.json`, registry v7 |

| D21 | 2026-11-08 | E5 challenger study, declared before running: XGBoost, CatBoost and HistGradientBoosting with fixed hyperparameters from `experiments/e5.yaml` are refitted on Train and scored on Selection against the frozen LightGBM champion protocol (champion refit with its E1-served CV outcome, 283 rounds; LR refit at C=0.01). Primary metric ROC-AUC, 1,000 paired bootstraps; a challenger is flagged superior only if gain ≥ 0.005 and the interval lower bound is above 0. The study is descriptive and outside the serving protocol surface (config in `experiments/`, not `configs/`), so it cannot invalidate lock `17b83143e33bb907`; the served artifact does not change whatever the outcome. Outcome: no challenger flagged (XGBoost +0.001915 [−0.000767, +0.004753], CatBoost +0.000740, HistGradientBoosting −0.002296 vs champion 0.772521; comparison run `b6b7c0e8439c4c26967b3e3d5559747e`), so LightGBM remains the served family. A flagged win only justifies proposing a new selection cycle. | `experiments/e5.yaml`, `src/risklab/experiments/e5.py` |

## MVP decisions (2026-11-10; specifications, not shipped features)

Các quyết định sau bổ sung phạm vi sản phẩm, không thay đổi artifact/metrics ML
đã đóng băng. Lịch sử D1–D21 được giữ nguyên.

| ID | Quyết định | Hệ quả / nguồn chuẩn |
|---|---|---|
| D22 | Analyst chỉ review/ghi chú/đề xuất; Manager duy nhất chốt quyết định mô phỏng; Admin quản lý tài khoản/role. Áp dụng server-side RBAC, object access, separation of duties và audit | [RBAC](product/rbac.md), [workflow](product/workflow.md). Không có Analyst self-approval hoặc Admin business superuser |
| D23 | Một VPS Linux, Next.js + FastAPI + PostgreSQL + Python worker, Docker Compose, Caddy HTTPS và GitHub Actions. Storage/config abstraction nhỏ để chuyển managed PostgreSQL/S3 sau | [Architecture](architecture/overview.md), [deployment](operations/deployment.md). Không Kubernetes/Terraform/microservices; thay lựa chọn Vite trong thảo luận trước |
| D24 | Docs hướng dẫn mới bằng tiếng Việt; code/identifier/docstring/comment kỹ thuật/agent instructions bằng tiếng Anh. Portfolio README và evidence lịch sử giữ tiếng Anh | Thay D4 cho docs mới; [documentation guide](README.md), [AGENTS.md](../AGENTS.md). Không rewrite bằng chứng ML để dịch |
| D25 | Thu scope về MVP: local accounts/server sessions, PostgreSQL-backed worker jobs, basic workload/model reports; chưa OIDC/Redis/Celery/Advanced UI | [PRD](product/prd.md), [acceptance](product/acceptance.md). Web MVP thay phạm vi UI hạn chế của D1/D18, không mở rộng quyền mở Test |
| D26 | Public demo mặc định synthetic-only; anonymized data cần kiểm tra quyền phân phối/tái nhận dạng. Backup ngoài VPS, secrets, health/logs và restore drill là yêu cầu nghiệm thu | [Deployment](operations/deployment.md), [backup/restore](operations/backup-restore.md). Không publish raw Kaggle/row reports; không tuyên bố deployment hoặc restore đã pass |

## B00 contract decision (2026-11-11)

| ID | Quyết định | Hệ quả / nguồn chuẩn |
|---|---|---|
| D27 | MVP CSV chọn snapshot từ versioned trusted synthetic catalog, không upload raw history/feature matrix hoặc model/path/URL tùy ý. Business namespace `/api/v1`, import version `demo-import/1`, ML feature contract 1.1 độc lập nhau | [Business API specification](business_api_contract.md), [import specification](demo_import_contract.md). B00 kiểm tra raw-schema/shared-SQL fixtures; production import/auth/review endpoints vẫn chưa triển khai. Bounds demo là design caps, phải kiểm tra tài nguyên/biên trong B03/B06 |

Cho phép train lại khi refactor cần thiết, nhưng phải tạo version/evidence riêng.
Không sửa lock/reference cũ, bỏ integrity checks hoặc dùng metrics cũ như kết quả
của pipeline mới. Quyền train lại **không** bao gồm quyền mở lại Test.

## Standing policies

- **Gender/age:** `CODE_GENDER` excluded, age retained (policy run
  `ca1631574f494d1c85c0fbb0e4c97a2f`). Including gender requires explicit
  owner approval. See `docs/RESULTS.md` §3.
- **Test reuse:** any new Test opening needs explicit owner authorization +
  recorded acknowledgment **before** the read; the durable journal entry is
  written pre-read. Completed locks return stored results.
- **Aliases:** registry alias changes only via the documented CLI
  (`risklab registry promote/rollback`), always audit-logged.
- **Destructive actions need an explicit decision:** deleting data or
  `mlflow-data/`, rewriting git history, changing partition seed/shares,
  moving aliases outside the CLI, or opening Test.

## Superseded / historical

- Contract-1.0 runs and its single Test opening (lock `e98453beb10d833e`,
  model run `cf726679c64d467f8ed86c9837fa57d6`) — preserved, never relabeled
  as contract 1.1, never used to choose contract-1.1 anything.
- Rejected unordered contract-1.1 experiments (D16) — retained locally under
  `reports/legacy/`, not an accepted baseline.
- Early design drafts proposed Pandera and Locust; D10 and the httpx load
  script supersede those choices.


## Review ứng dụng và vận hành — 2026-11-13

| ID | Quyết định | Lý do / giới hạn |
|---|---|---|
| D28 | Business worker phải kiểm tra protocol, runtime versions và ordered columns/groups; legacy registry tải evaluated artifact đúng hash | Tránh provenance đúng trên metadata nhưng bytes hoặc feature/runtime thực tế khác. Không sửa lock/reference lịch sử; release thiếu metadata bị từ chối, không tự gán lại evidence |
| D29 | Mở lại B06-T03; deploy script chỉ preflight, `--execute` dừng cho đến khi có quy trình release được nghiệm thu | SSH chain cũ không áp manifest và có thể che lỗi migration bằng health của release cũ. VPS/deploy vẫn cần authorization và evidence B07 |
| D30 | Giữ một bản đồ tài liệu và một nguồn trạng thái; lịch sử evidence có nhãn riêng | [README tài liệu](README.md), [PROGRESS](PROGRESS.md); không compact code, tạo persistent memory hay chép lại contracts |

## UI workspace — yêu cầu owner 2026-11-13

| ID | Quyết định | Lý do / giới hạn |
|---|---|---|
| D31 | UI Enterprise SaaS light mode, desktop-first: Tailwind v4, shadcn/ui, TanStack Table v8, Recharts | Owner bổ sung yêu cầu tám màn; ưu tiên Application Detail và Analyst → Manager. Không thay RBAC hoặc tự mở rộng Advanced dashboard; charts là counts thật, registry chỉ đọc, Settings không chỉnh secrets/release |

## Release preflight — 2026-11-13

| ID | Quyết định | Lý do / giới hạn |
|---|---|---|
| D32 | Deployment manifest `risklab-release/2` pin thêm `release.json` và `comparison.json`; preflight render đúng image/catalog từ manifest, tách `--example` khỏi release thật | Model hash riêng không bind threshold/policy/runtime metadata. V1 không được tự coi đủ guards. Bundle/backup input thiếu được ghi NOT CHECKED; Compose config pass không là deploy/rollback/restore evidence. Giữ D29 và không thay scoring contract/selection lock |


## Review độc lập và gọn repo — 2026-11-13

| ID | Quyết định | Lý do / giới hạn |
|---|---|---|
| D33 | Bỏ controller Wonder/Spark/Sol, scripts/tests/docs riêng khỏi repo theo owner; gọi Muse CLI trực tiếp khi cần review | Controller phục vụ phát triển và không là dependency của RiskLab. Review độc lập dùng `muse-wonder-1.0`, requested effort `xhigh`; raw review logs giữ local, không đưa vào source |
| D34 | Gộp tám batch thành backlog còn mở; compact agent/human guides và trạng thái hiện hành; giữ contracts cùng ML evidence | Người đọc bắt đầu ở docs/README. Không minify code, xóa runtime/tests fixtures, đổi protocol-hashed ML sources hoặc sửa lock/reference. Presentation và CatBoost scratch local được ignore |
