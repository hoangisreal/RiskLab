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

Follow [Deployment](docs/deployment.md) for clean-tree locking, registration, gated promotion and API startup. Test evaluation is a separate explicit owner approval step. Credentials/admin keys stay in the environment or ignored local files.

## Evidence and review

Contract 1.1 corrects historical aggregation and floating-point reproducibility, validates the public raw-record schema, and packages immutable model/reference artifacts. Its real-data verification is being completed; [Progress](docs/PROGRESS.md) and the [completion audit](docs/COMPLETION_AUDIT.md) distinguish completed checks from remaining evidence. Historical contract-1.0 Test results do not evaluate the corrected artifact.

- [Model comparisons and decisions](docs/experiments.md)
- [Model card](docs/model_card.md) and [limitations](docs/limitations.md)
- [Leakage controls](docs/leakage.md)
- [API contract](docs/api_contract.md)
- [Monitoring, rollback and load tests](docs/monitoring.md)
- [Reproducibility and independent metric checks](docs/reproducibility.md)
- [Master plan](docs/MASTER_PLAN.md) and [contributor rules](AGENTS.md)

The default corrected aggregate reference lives under `reports/reference/contract-1.1/`; the earlier root references are retained as historical evidence. Reproduction uses an isolated output directory and never opens Test or changes registry aliases. Raw data, predictions and generated reports are never committed.
