# Deployment

```bash
make setup
kaggle competitions download -c home-credit-default-risk -p data/raw
unzip data/raw/home-credit-default-risk.zip -d data/raw
make ingest && make partition && make validate
make mlflow &  # tracking server on 127.0.0.1:5000
export MLFLOW_TRACKING_URI=http://127.0.0.1:5000
uv run risklab features
uv run risklab e2 && uv run risklab e3 && uv run risklab e4
# Train + calibrate the champion, save the evaluated artifact, then:
uv run risklab lock-selection --model-run-id <run> --model-path <artifact.pkl>
uv run risklab final-eval --lock <lock_id>
docker compose up --build  # mlflow + api on 127.0.0.1
curl http://127.0.0.1:8000/health
```

Candidate models intended for registration should be trained in the `train` container (`docker compose run --rm train ...` (the image entrypoint already supplies `risklab`)) so library versions match the API image; the load-time version/contract check rejects mismatches. The MLflow port is bound to localhost only (the server has no authentication). Backup: stop the server and copy `mlflow-data/`.

Verified 2026-11-03: `docker compose up -d --build` brings up MLflow (healthy) and the API; the API resolves `@champion` at startup, `/health` reports `ready: true` with model version 1 (lock e98453beb10d833e), `/predict` returns a probability for a no-history payload and 422 when a required source is `not_provided`.
