# RiskLab

Credit risk prediction and model monitoring on the Home Credit Default Risk dataset.
This is a production-oriented demo, not a production credit-decisioning system.

## Quick start

```bash
make setup                      # create the environment from uv.lock
# Accept the competition rules on Kaggle, then:
kaggle competitions download -c home-credit-default-risk -p data/raw
unzip data/raw/home-credit-default-risk.zip -d data/raw
make ingest                     # CSV -> Parquet + data manifest (strict, explicit schema)
make partition                  # stratified Train/Calibration/Selection/Test/Monitoring ids
make validate                   # data-contract checks on the application tables
make synthetic                  # optional: synthetic CSVs in data/synthetic for tests/demos
make test                       # lint, type check, non-data tests

# Experiments (Phase 1)
make mlflow &                   # tracking server on 127.0.0.1:5000
export MLFLOW_TRACKING_URI=http://127.0.0.1:5000
make diagnostics                # leakage diagnostics
make e1                         # Logistic Regression vs LightGBM, results in MLflow
```

Kaggle credentials stay in `~/.kaggle/` or environment variables. See `.env.example`.

## Docs

- [Master plan](docs/MASTER_PLAN.md)
- [Progress](docs/PROGRESS.md)
- [Agent and contributor rules](AGENTS.md)
