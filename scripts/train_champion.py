"""Fit the champion exactly as selected (Train -> Calibration -> Selection threshold).

Reads the E2/E3/E4 decisions from reports/experiments/*/summary.json, refits nothing
beyond the protocol (base on Train, calibrator on Calibration), and writes the
evaluated artifact plus serving_config.json. No Test data is touched.
"""

from __future__ import annotations

import json
import pickle
from pathlib import Path

from risklab.config import load_data_config, load_partition_config
from risklab.features.application import FeatureColumns, feature_spec, load_features_config
from risklab.features.historical import load_full_frame
from risklab.features.registry import load_registry
from risklab.models.calibrate import fit_calibrator
from risklab.models.pipelines import load_lgbm_params, make_lgbm_pipeline
from risklab.models.train import fit_pipeline

ROOT = Path(__file__).resolve().parents[1]
CFG = ROOT / "configs"


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> None:
    reports = ROOT / "reports"
    e2 = _read(reports / "experiments" / "e2" / "summary.json")
    e3 = _read(reports / "experiments" / "e3" / "summary.json")
    e4 = _read(reports / "experiments" / "e4" / "summary.json")
    groups = [g for g in e2["served_groups"] if g != "application"]
    method = e3["selection"]["chosen"]
    threshold = float(e4["threshold_choice"]["threshold"])
    registry = load_registry(CFG)
    data_cfg = load_data_config(CFG)
    partition_cfg = load_partition_config(CFG)
    spec = feature_spec(
        load_features_config(CFG), config_dir=CFG, data_cfg=data_cfg, partition_cfg=partition_cfg
    )
    cols = FeatureColumns(
        numeric=list(spec.numeric) + registry.group_features(groups),
        categorical=list(spec.categorical),
        log=list(spec.log),
    )
    X_tr, y_tr = load_full_frame(
        "train",
        "fit",
        groups,
        data_cfg=data_cfg,
        partition_cfg=partition_cfg,
        config_dir=CFG,
        root=ROOT,
        registry=registry,
    )
    X_cal, y_cal = load_full_frame(
        "calibration",
        "calibrate",
        groups,
        data_cfg=data_cfg,
        partition_cfg=partition_cfg,
        config_dir=CFG,
        root=ROOT,
        registry=registry,
    )
    base = fit_pipeline(
        make_lgbm_pipeline(load_lgbm_params(CFG), columns=cols, config_dir=str(CFG)),
        X_tr[cols.all],
        y_tr,
    )
    model = fit_calibrator(base, X_cal[cols.all], y_cal, method)
    out = reports / "models"
    out.mkdir(parents=True, exist_ok=True)
    with open(out / "champion.pkl", "wb") as fh:
        pickle.dump(model, fh)
    serving = {
        "threshold": threshold,
        "calibration_method": method,
        "contract_version": registry.contract_version,
        "feature_groups": groups,
        "feature_columns": cols.all,
        "e2_run_id": e2.get("run_id"),
        "e3_run_id": e3.get("run_id"),
        "e4_run_id": e4.get("run_id"),
    }
    (out / "serving_config.json").write_text(json.dumps(serving, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(serving, indent=2))


if __name__ == "__main__":
    main()
