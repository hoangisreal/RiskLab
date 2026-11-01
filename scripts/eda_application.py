"""Exploratory data analysis of the application model inputs on the Train partition only.

Writes ``reports/eda/application_train.md``. Only ``load_application_frame`` for the Train role is
used, so Selection, Calibration, Test and Monitoring rows are never read.

    uv run python scripts/eda_application.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

from risklab.config import load_data_config, project_root, resolve_config_dir
from risklab.data.loaders import PartitionRole, Purpose, partition_info
from risklab.features.application import (
    feature_columns,
    load_application_frame,
    load_features_config,
)


def _table(frame: pd.DataFrame, floatfmt: str = ".4f") -> str:
    cols = list(frame.columns)
    lines = ["| " + " | ".join(cols) + " |", "|" + "|".join("---" for _ in cols) + "|"]
    for _, row in frame.iterrows():
        cells = [format(v, floatfmt) if isinstance(v, float) else str(v) for v in row]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def main() -> int:
    cfg_dir = resolve_config_dir(None)
    data_cfg = load_data_config(cfg_dir)
    features_cfg = load_features_config(cfg_dir)
    numeric, categorical = feature_columns(features_cfg, config_dir=cfg_dir)
    info = partition_info(PartitionRole.train, config_dir=cfg_dir)
    X, y = load_application_frame(PartitionRole.train, Purpose.fit, config_dir=cfg_dir)

    out = [
        "# EDA: application features (Train partition only)",
        "",
        f"- Partition: `train`, {len(X):,} rows, {int(y.sum()):,} positives "
        f"({y.mean():.4%}); partition hash `{info.partition_hash[:16]}...`",
        f"- Model inputs: {len(numeric)} numeric, {len(categorical)} categorical "
        "(after cleaning; `CODE_GENDER` excluded by policy)",
        "- Selection, Calibration, Test and Monitoring rows are not read by this report.",
        "",
        "## Missingness (top 15 columns)",
        "",
    ]
    nulls = X.isna().mean().sort_values(ascending=False)
    out.append(_table(nulls.head(15).rename("null_rate").rename_axis("feature").reset_index()))
    out += ["", f"Columns with no missing values: {int((nulls == 0).sum())} of {X.shape[1]}.", ""]

    sentinel = "DAYS_EMPLOYED_IS_SENTINEL"
    if sentinel in X:
        flagged = X[sentinel] == 1
        out += [
            "## Cleaning effect: `DAYS_EMPLOYED` sentinel",
            "",
            f"- Rows with the sentinel: {int(flagged.sum()):,} ({flagged.mean():.2%} of Train)",
            f"- Positive rate with sentinel: {y[flagged].mean():.4f}; without: "
            f"{y[~flagged].mean():.4f}",
            "",
        ]

    desc = (
        X[numeric]
        .describe(percentiles=[0.01, 0.5, 0.99])
        .T[["mean", "std", "min", "1%", "50%", "99%", "max"]]
    )
    spread = desc.assign(feature=desc.index).reset_index(drop=True)
    out += [
        "## Numeric columns with the widest range (by max / 99th percentile)",
        "",
    ]
    ratio = (spread["max"] / spread["99%"].replace(0, np.nan)).abs()
    top = spread.assign(max_over_p99=ratio).sort_values("max_over_p99", ascending=False).head(10)
    out.append(_table(top[["feature", "50%", "99%", "max", "max_over_p99"]]))
    out += ["", "## Categorical columns: levels and rarest-level share", ""]
    rows = []
    for col in categorical:
        vc = X[col].value_counts(normalize=True, dropna=True)
        rows.append(
            {
                "feature": col,
                "levels": int(len(vc)),
                "top_level_share": float(vc.iloc[0]),
                "rarest_share": float(vc.iloc[-1]),
            }
        )
    out.append(_table(pd.DataFrame(rows)))

    out += ["", "## Univariate association with TARGET (Train, top 15)", ""]
    from risklab.models.diagnostics import single_feature_screen

    screen = single_feature_screen(X, y, categorical)
    out.append(_table(screen.head(15)[["feature", "auc", "null_rate", "n_levels"]]))
    out += [
        "",
        "Categorical AUCs use in-sample level rates; optimistic for high-cardinality columns.",
        "",
    ]

    target = Path(project_root(cfg_dir)) / data_cfg.reports_dir / "eda" / "application_train.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(out), encoding="utf-8")
    print(f"Wrote {target}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
