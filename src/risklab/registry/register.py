"""Register a locked artifact with verified metadata; promotion is a separate gated CLI step."""

from __future__ import annotations

import pickle
from pathlib import Path

import mlflow
import mlflow.sklearn

from risklab.config import load_data_config, load_partition_config
from risklab.features.historical import load_full_frame
from risklab.manifest import sha256_file
from risklab.models.select import load_lock, verify_lock
from risklab.registry.bundle import ServingConfig, validate_serving_config
from risklab.registry.client import MODEL_NAME, _client
from risklab.tracking import require_tracking_uri


def _model_card_text(root: Path, serving: ServingConfig) -> str:
    if serving.purpose == "rollback_demo_only":
        text = (
            "# Diagnostic rollback artifact\n\n"
            "Intentionally trained on permuted Train labels for the controlled rollback exercise. "
            "Unsuitable for credit decisions. No Test evaluation is claimed. The threshold is "
            "copied from the verified selected model, not optimized for this artifact.\n\n"
            f"Selection metrics, run `{serving.selection_run_id}`:\n\n"
        )
        text += "\n".join(f"- {name}: {value}" for name, value in serving.selection_metrics.items())
        text += "\n"
    else:
        card = root / "docs/model_card.md"
        text = card.read_text() if card.is_file() else "# RiskLab model card\n"
    return text + (
        f"\n\nPinned bundle: lock `{serving.lock_id}`, Selection run `{serving.selection_run_id}`, "
        f"artifact `{serving.model_sha256}`, purpose `{serving.purpose}`.\n"
    )


def register(lock_id: str, *, root: Path, config_dir: Path) -> tuple[str, str]:
    cfg = load_data_config(config_dir)
    lock = load_lock(cfg.reports_dir / "locks", lock_id)
    verify_lock(lock, root=root, data_cfg=cfg, partition_cfg=load_partition_config(config_dir))
    candidate = lock["config"]["candidate"]
    fields = {
        k: candidate[k]
        for k in (
            "feature_columns",
            "selection_metrics",
            "selection_ece",
            "selection_run_id",
            "parity",
            "reference_profile_sha256",
        )
    }
    serving = ServingConfig.model_validate(
        {
            **fields,
            "purpose": candidate.get("purpose", "selected_model"),
            "cost": lock["config"]["cost"],
            **{
                k: lock[k]
                for k in (
                    "threshold",
                    "calibration_method",
                    "contract_version",
                    "feature_groups",
                    "lock_id",
                    "model_sha256",
                    "partition_hash",
                    "data_manifest_hash",
                    "git_commit",
                    "library_versions",
                    "protocol_hashes",
                )
            },
        }
    )
    with open(lock["model_path"], "rb") as fh:
        model = pickle.load(fh)
    validate_serving_config(serving, expected_contract=serving.contract_version, model=model)
    X, _ = load_full_frame(
        "selection", "select", serving.feature_groups, config_dir=config_dir, root=root
    )
    X = X[serving.feature_columns]
    mlflow.set_tracking_uri(require_tracking_uri())
    mlflow.set_experiment("risklab-candidates")
    with mlflow.start_run(run_name="locked-candidate") as run:
        mlflow.set_tags(
            {
                "lock_id": lock_id,
                "partition_hash": lock["partition_hash"],
                "git_commit": lock["git_commit"],
            }
        )
        mlflow.log_metrics({f"selection_{k}": v for k, v in serving.selection_metrics.items()})
        mlflow.log_dict(serving.model_dump(), "serving_config.json")
        profile_path = Path(candidate["reference_profile_path"])
        if sha256_file(profile_path) != serving.reference_profile_sha256:
            raise ValueError("reference profile changed after the candidate was locked")
        mlflow.log_artifact(str(profile_path))
        mlflow.log_dict(lock, "selection_lock.json")
        mlflow.log_text(_model_card_text(root, serving), "model_card.md")
        mlflow.log_artifact(lock["model_path"], "evaluated_artifact")
        mlflow.sklearn.log_model(
            model,
            name="model",
            registered_model_name=MODEL_NAME,
            signature=mlflow.models.infer_signature(X.head(1), model.predict_proba(X.head(1))),
            pyfunc_predict_fn="predict_proba",
            pip_requirements=[
                f"{name}=={version}"
                for name, version in serving.library_versions.items()
                if name != "python" and version is not None
            ],
            skops_trusted_types=[
                "collections.OrderedDict",
                "lightgbm.basic.Booster",
                "lightgbm.sklearn.LGBMClassifier",
                "risklab.models.pipelines.CategoryCaster",
            ],
        )
        run_id = run.info.run_id
    client = _client()
    versions = client.search_model_versions(f"name='{MODEL_NAME}' and run_id='{run_id}'")
    version = str(versions[0].version)
    for key in ("contract_version", "lock_id", "model_sha256"):
        client.set_model_version_tag(MODEL_NAME, version, key, getattr(serving, key))
    client.set_model_version_tag(MODEL_NAME, version, "gate_status", "pending")
    return version, run_id
