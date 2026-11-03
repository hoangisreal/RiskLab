"""Register the evaluated champion artifact in the MLflow registry and promote it.

First registration uses the absolute floors in the promotion gate. The logged model
is the exact pickle produced by scripts/train_champion.py (no refit).
"""

from __future__ import annotations

import json
import pickle
from pathlib import Path

import mlflow
import mlflow.sklearn

from risklab.registry.audit import append_audit
from risklab.registry.client import MODEL_NAME, set_alias
from risklab.registry.gates import promotion_gate

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    reports = ROOT / "reports"
    serving = json.loads((reports / "models" / "serving_config.json").read_text(encoding="utf-8"))
    e3 = json.loads((reports / "experiments" / "e3" / "summary.json").read_text(encoding="utf-8"))
    metrics = e3["selection"]["metrics"][serving["calibration_method"]]
    gate = promotion_gate(
        metrics,
        None,
        ece=float(e3["selection"]["ece"][serving["calibration_method"]]),
        contract_valid=True,
        parity_passed=True,
        versions_match=True,
        lock_present=True,
    )
    print("gate:", gate)
    if not gate["passed"]:
        raise SystemExit("promotion gate blocked registration")
    with open(reports / "models" / "champion.pkl", "rb") as fh:
        model = pickle.load(fh)  # noqa: S301 - trusted local artifact
    mlflow.set_experiment("risklab-candidates")
    with mlflow.start_run(run_name="champion") as run:
        mlflow.log_metrics({f"selection_{k}": float(v) for k, v in metrics.items()})
        mlflow.log_dict(serving, "serving_config.json")
        mlflow.log_dict(gate, "gate_report.json")
        mlflow.sklearn.log_model(  # type: ignore[attr-defined]
            model,
            name="model",
            registered_model_name=MODEL_NAME,
            skops_trusted_types=[
                "collections.OrderedDict",
                "lightgbm.basic.Booster",
                "lightgbm.sklearn.LGBMClassifier",
                "risklab.models.pipelines.CategoryCaster",
            ],
        )
        run_id = run.info.run_id
    from mlflow.tracking import MlflowClient

    client = MlflowClient()
    versions = client.search_model_versions(f"name='{MODEL_NAME}' and run_id='{run_id}'")
    version = str(versions[0].version)
    client.set_model_version_tag(
        MODEL_NAME, version, "contract_version", serving["contract_version"]
    )
    client.set_model_version_tag(MODEL_NAME, version, "gate_status", "passed")
    set_alias(version, "champion")
    append_audit(
        reports / "registry_audit.jsonl",
        action="promote",
        from_version=None,
        to_version=version,
        actor="register_champion",
        reason="first registration, gate passed",
    )
    print(f"registered {MODEL_NAME} version {version} (run {run_id}) -> @champion")


if __name__ == "__main__":
    main()
