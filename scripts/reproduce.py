"""Reproduce training and Selection reports in a fresh output root, without opening Test."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

import yaml

from risklab.config import load_data_config, load_partition_config

ROOT = Path(__file__).resolve().parents[1]


def prepare_output(source_config: Path, output: Path) -> Path:
    """Copy the protocol and redirect derived artifacts; never overwrite an existing run."""
    if output.exists() and any(output.iterdir()):
        raise ValueError("output directory must be absent or empty")
    output.mkdir(parents=True, exist_ok=True)
    config_dir = output / "configs"
    shutil.copytree(source_config, config_dir)
    shutil.copytree(source_config.parent / "sql", output / "sql")
    data = load_data_config(source_config).model_dump(mode="json")
    data.update(
        parquet_dir=str(output / "data/parquet"),
        manifest_path=str(output / "data/manifest.json"),
        reports_dir=str(output / "reports"),
    )
    data["duckdb"]["temp_directory"] = str(output / "data/duckdb_tmp")
    (config_dir / "data.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")
    partition = load_partition_config(source_config).model_dump(mode="json")
    partition["output_dir"] = str(output / "data/partitions")
    (config_dir / "partition.yaml").write_text(yaml.safe_dump(partition), encoding="utf-8")
    return config_dir


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--config-dir", type=Path, default=ROOT / "configs")
    parser.add_argument("--reference", type=Path, default=ROOT / "reports/reference/contract-1.1")
    parser.add_argument("--feature-reference", type=Path)
    args = parser.parse_args()
    if not os.environ.get("MLFLOW_TRACKING_URI", "").startswith(("http://", "https://")):
        raise SystemExit("set MLFLOW_TRACKING_URI to a running HTTP tracking server first")
    config_dir = prepare_output(args.config_dir.resolve(), args.out.resolve())
    prefix = [
        sys.executable,
        "-c",
        "from risklab.cli import app; app()",
        "--config-dir",
        str(config_dir),
    ]
    for command in (
        "ingest",
        "partition",
        "validate",
        "validate-history",
        "features",
        "e1",
        "e2",
        "e3",
        "e4",
        "prepare-candidate",
    ):
        subprocess.run([*prefix, command], check=True)
        if command == "e2":
            subprocess.run([*prefix, "e1", "--served"], check=True)
            subprocess.run([*prefix, "policy-analysis"], check=True)
            subprocess.run([*prefix, "diagnostics", "--served"], check=True)
    verification = [*prefix, "verify", "--reference", str(args.reference.resolve())]
    if args.feature_reference:
        verification.extend(["--feature-reference", str(args.feature_reference.resolve())])
    subprocess.run(verification, check=True)
    print(
        f"Reproduced Selection reports in {args.out}. No Test evaluation or registry alias change."
    )


if __name__ == "__main__":
    main()
