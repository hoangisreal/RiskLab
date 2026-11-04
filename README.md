# RiskLab

Credit risk prediction and model monitoring on the Home Credit Default Risk dataset. This is a production-oriented demo, not a production credit-decisioning system.

RiskLab keeps Train, Calibration, Selection, Test and Monitoring roles separate. It compares Logistic Regression and LightGBM, selects historical sources, freezes calibration/threshold decisions, serves that artifact with a versioned API, and demonstrates drift detection and audited rollback.

## Quick start

```bash
make setup
make test                       # synthetic data only; no Kaggle credentials required
# After accepting Kaggle competition rules and placing CSVs in data/raw:
make ingest
make partition
mkdir -p reports features data/duckdb_tmp data/monitoring
docker compose build train api
docker compose up -d mlflow
python scripts/run_selection.py # through verified candidate and Monitoring controls
```

Follow [Operations](docs/OPERATIONS.md) for clean-tree locking, registration, gated promotion and API startup. Test evaluation is a separate explicit owner approval step. Credentials/admin keys stay in the environment or ignored local files.

## Documentation

For coding agents, read in this order:

1. [AGENTS.md](AGENTS.md) — non-negotiable rules and commands.
2. [Project context](docs/PROJECT.md) — architecture, module/config/SQL map, change routing, gotchas.
3. [Decisions](docs/DECISIONS.md) — frozen decisions D1–D18 and locked values.
4. [Progress](docs/PROGRESS.md) — current status and open items.
5. [Results](docs/RESULTS.md) — accepted experiments, Test evidence, limitations.
6. [Operations](docs/OPERATIONS.md) — deploy, API summary, monitoring, reproduction.
7. Detail contracts: [data contract](docs/data_contract.md), [API contract](docs/api_contract.md), [model card](docs/model_card.md).

Historical plan, audit and pre-merge documents are preserved in [docs/archive](docs/archive/).

## Evidence

Contract 1.1 corrects historical aggregation and floating-point reproducibility, validates the public raw-record schema, and packages immutable model/reference artifacts. Its real-data Selection, exact reproduction, public API parity, monitoring and rollback checks pass, including the owner-authorized final Test evaluation and its disclosed reuse (run `a9fc07a3cbaf43ab88d450ed6a3eca58`, lock `402b9e050159762d`). Historical contract-1.0 Test results do not evaluate the corrected artifact.

The default corrected aggregate reference lives under `reports/reference/contract-1.1/`; the earlier root references are retained as historical evidence. Reproduction uses an isolated output directory and never opens Test or changes registry aliases. Raw data, predictions and generated reports are never committed.
