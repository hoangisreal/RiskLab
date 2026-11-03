"""Audited bad deployment -> detection -> rollback, with a shared-data-drift counterexample.

Requires a running, single-worker API serving the verified candidate and an admin key in
its environment. Alias changes always go through the documented risklab registry CLI.
The script never opens Test. A failed exercise still attempts to restore the starting version.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import urllib.request
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from risklab.config import load_data_config, load_model
from risklab.manifest import write_json_atomic
from risklab.models.demo_candidate import RollbackConfig
from risklab.tracking import require_tracking_uri, tracked_run

ROOT = Path(__file__).resolve().parents[1]


def container_path(path: Path) -> str:
    return "/workspace/" + str(path.resolve().relative_to(ROOT))


def run_cli(*arguments: str, expect_blocked: bool = False) -> str:
    result = subprocess.run(
        ["docker", "compose", "run", "--rm", "train", *arguments],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )
    if expect_blocked:
        if (
            result.returncode == 0
            or "promotion gate blocked candidate" not in result.stdout + result.stderr
        ):
            raise RuntimeError("diagnostic deployment was not blocked by the expected metric gate")
    elif result.returncode:
        raise RuntimeError(result.stdout + result.stderr)
    return result.stdout


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--candidate", type=Path, default=ROOT / "reports/models/candidate.json")
    parser.add_argument("--bad-candidate", type=Path)
    args = parser.parse_args()
    load_dotenv(ROOT / ".env", override=False)
    require_tracking_uri()
    key = os.environ.get("RISKLAB_ADMIN_API_KEY", "")
    if not key:
        raise ValueError("RISKLAB_ADMIN_API_KEY must match the running API environment")
    config_dir = ROOT / "configs"
    config = load_model(config_dir, "experiments/rollback.yaml", RollbackConfig)

    def api(
        path: str, payload: dict[str, Any] | None = None, *, admin: bool = False
    ) -> dict[str, Any]:
        headers = {"Content-Type": "application/json"}
        if admin:
            headers["x-admin-key"] = key
        body = json.dumps(payload).encode() if payload is not None else None
        request = urllib.request.Request(args.url + path, data=body, headers=headers)
        with urllib.request.urlopen(request, timeout=300) as response:
            return json.load(response)

    starting = api("/models/current")
    verified = json.loads(args.candidate.read_text())
    if (
        starting["model_sha256"] != verified["model_sha256"]
        or starting["purpose"] != "selected_model"
    ):
        raise ValueError("the API must start on the verified selected candidate")
    good_version = starting["model_version"]
    bad_path = args.bad_candidate
    if bad_path is None:
        output = run_cli("prepare-bad-candidate", "--candidate", container_path(args.candidate))
        matched = re.search(r"Diagnostic candidate: (/workspace/.+)", output)
        if not matched:
            raise RuntimeError("diagnostic candidate command did not return a path")
        bad_path = ROOT / Path(matched.group(1)).relative_to("/workspace")
    output = run_cli("lock-selection", "--candidate", container_path(bad_path))
    lock = re.search(r"Selection locked: ([0-9a-f]{16})", output)
    if not lock:
        raise RuntimeError("diagnostic lock was not created")
    output = run_cli("registry", "register", "--lock", lock.group(1))
    matched = re.search(r"Registered version (\d+) \(run ([0-9a-f]+)\)", output)
    if not matched:
        raise RuntimeError("diagnostic registration did not return a version/run")
    bad_version, bad_run_id = matched.groups()
    run_cli("registry", "promote", "--version", bad_version, expect_blocked=True)
    control_id = f"rollback-control-{config.batch_seed}"
    drift_id = f"rollback-drift-{config.batch_seed}"
    run_cli(
        "monitoring",
        "create",
        "--batch-id",
        control_id,
        "--scenario",
        config.monitoring_scenario,
        "--seed",
        str(config.batch_seed),
    )
    run_cli(
        "monitoring",
        "create",
        "--batch-id",
        drift_id,
        "--scenario",
        config.data_drift_scenario,
        "--seed",
        str(config.batch_seed),
    )
    before = api("/monitoring/evaluate", {"batch_id": control_id})
    try:
        run_cli(
            "registry",
            "promote",
            "--version",
            bad_version,
            "--force",
            "--reason",
            config.force_reason,
        )
        api("/admin/models/reload", {}, admin=True)
        after = api("/models/current")
        if after["model_version"] != bad_version:
            raise RuntimeError("API did not load the forced diagnostic version")
        regression = api("/monitoring/evaluate", {"batch_id": control_id})
        if not regression["performance_alerts"]:
            raise RuntimeError("monitoring missed the deliberately corrupted-label deployment")
    finally:
        run_cli(
            "registry",
            "rollback",
            "--version",
            good_version,
            "--reason",
            "Restore the verified version after the controlled rollback exercise.",
        )
        api("/admin/models/reload", {}, admin=True)
    restored = api("/models/current")
    if restored["model_version"] != good_version:
        raise RuntimeError("API version did not return to the starting version")
    drift_good = api("/monitoring/evaluate", {"batch_id": drift_id, "model_version": good_version})
    drift_bad = api("/monitoring/evaluate", {"batch_id": drift_id, "model_version": bad_version})
    report = {
        "good_version": good_version,
        "good_run_id": starting["run_id"],
        "bad_version": bad_version,
        "bad_run_id": bad_run_id,
        "bad_lock_id": lock.group(1),
        "gate_blocked": True,
        "restored_version": restored["model_version"],
        "control_before": before,
        "control_bad_deployment": regression,
        "shared_drift_good": drift_good,
        "shared_drift_bad": drift_bad,
        "both_degrade_under_shared_drift": bool(
            drift_good["performance_alerts"] and drift_bad["performance_alerts"]
        ),
        "test_opened": False,
    }
    with tracked_run(
        config.experiment_name,
        "deployment-detection-rollback",
        {"good_version": good_version, "bad_version": bad_version},
        config.model_dump(),
    ) as run_id:
        import mlflow

        report["run_id"] = run_id
        mlflow.log_dict(report, "rollback_demo.json")
    write_json_atomic(
        load_data_config(config_dir).reports_dir / "rollback-demo/result.json", report
    )
    print(
        json.dumps(
            {
                name: report[name]
                for name in (
                    "run_id",
                    "good_version",
                    "bad_version",
                    "gate_blocked",
                    "restored_version",
                    "both_degrade_under_shared_drift",
                    "test_opened",
                )
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
