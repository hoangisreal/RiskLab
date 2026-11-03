# Recorded aggregate baseline

The JSON files directly in this directory preserve contract 1.0. The corrected default is the separately declared `contract-1.1/` baseline. Do not compare a contract-1.1 run to historical values and silently overwrite those historical references. The initial unordered contract-1.1 experiment was rejected before locking; it is not an accepted reference.

These JSON references were exported from the existing ingest/partition/validation manifests and E1–E4 Selection summaries. Metric files record the original MLflow run ids. They contain no applicant rows, predictions, model artifacts or raw data. They do not contain Test results.

`risklab verify` compares explicit expected fields against a fresh reports root. Raw hashes, partition/id hashes and stable counts are exact; metric tolerance comes from `configs/verification.yaml`. Generated Parquet byte hashes and environment-specific paths are excluded. Full feature-value verification additionally requires a local, uncommitted feature baseline via `--feature-reference`.

Adding a supplemental report field does not silently replace historical metric values. Investigate a mismatch before exporting a new baseline; record approved protocol changes in docs/PROGRESS.md.
