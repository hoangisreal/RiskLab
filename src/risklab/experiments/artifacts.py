"""Aggregate plots and provenance manifests for tracked Selection analyses."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import matplotlib
import numpy as np
import pandas as pd

from risklab.config import DataConfig
from risklab.manifest import sha256_file, write_manifest

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402


def calibration_plot(
    y: np.ndarray, scores: dict[str, np.ndarray], path: Path, *, bins: int
) -> Path:
    """Equal-frequency reliability curves; binning is descriptive on Selection."""
    fig, ax = plt.subplots(figsize=(6, 5))
    for method, p in scores.items():
        groups = np.array_split(np.argsort(p, kind="stable"), min(bins, len(p)))
        ax.plot([p[g].mean() for g in groups], [y[g].mean() for g in groups], "o-", label=method)
    ax.plot([0, 1], [0, 1], "k:", linewidth=1)
    ax.set(
        xlabel="Mean predicted probability", ylabel="Observed rate", title="Selection reliability"
    )
    ax.legend()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(path, dpi=140)
    plt.close(fig)
    return path


def threshold_plot(curve: pd.DataFrame, path: Path) -> Path:
    fig, axes = plt.subplots(1, 2, figsize=(10, 4))
    axes[0].plot(curve["threshold"], curve["expected_cost_per_applicant"])
    axes[0].set(
        xlabel="Reject threshold", ylabel="Proxy cost per applicant", title="Selection cost"
    )
    for metric in ("precision", "recall", "approval_rate"):
        axes[1].plot(curve["threshold"], curve[metric], label=metric)
    axes[1].set(xlabel="Reject threshold", ylabel="Rate", ylim=(0, 1))
    axes[1].legend()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(path, dpi=140)
    plt.close(fig)
    return path


def analysis_manifest(
    step: str, summary: dict[str, Any], *, data_cfg: DataConfig, root: Path
) -> Path:
    """Hash concrete local outputs and log a shared manifest to the originating run."""
    paths: dict[str, str] = dict(summary.get("artifacts", {}))
    paths.update(summary.get("model_paths", {}))
    for key in ("model_path", "predictions_path"):
        if summary.get(key):
            paths[key] = summary[key]
    run_id = summary.get("run_id") or summary.get("run_ids", {}).get("comparison")
    path = data_cfg.reports_dir / "manifests" / f"{step}.json"
    write_manifest(
        path,
        step,
        config=summary.get("resolved_config", {}),
        inputs={
            "data_manifest": sha256_file(data_cfg.manifest_path),
            "partition": summary["partition_hash"],
        },
        outputs={name: sha256_file(value) for name, value in paths.items()},
        summary={"run_id": run_id, "contract_version": summary.get("contract_version")},
        git_cwd=root,
    )
    if run_id:
        import resource

        import mlflow

        with mlflow.start_run(run_id=run_id):
            mlflow.log_artifact(str(path), "manifests")
            mlflow.log_metric(
                "peak_process_rss_mib", resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
            )
            for name in ("ingest", "partition", "validate", "validate-history", "features"):
                source = data_cfg.reports_dir / "manifests" / f"{name}.json"
                if source.is_file():
                    mlflow.log_artifact(str(source), "input_manifests")
    return path
