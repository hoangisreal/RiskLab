"""Run the complete pre-Test protocol in the train image, retaining logs for every step.

Run from the repository root. The first contract-1.1 run must preserve the legacy reports
as described in docs/PROGRESS.md. No command here opens Test or changes a registry alias.
"""

from __future__ import annotations

import argparse
import subprocess
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESOURCE_WRAPPER = (
    "import resource\nfrom risklab.cli import app\ntry:\n app()\nfinally:\n "
    "print({'peak_process_rss_mib': "
    "resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024})"
)
STEPS = {
    "validate": ["validate"],
    "history": ["validate-history"],
    "features": ["features"],
    "e1": ["e1"],
    "e2": ["e2"],
    "e1_served": ["e1", "--served"],
    "policy": ["policy-analysis"],
    "diagnostics": ["diagnostics"],
    "diagnostics_served": ["diagnostics", "--served"],
    "e3": ["e3"],
    "e4": ["e4"],
    "candidate": ["prepare-candidate"],
    "monitoring_controls": [
        "monitoring",
        "calibrate",
        "--candidate",
        "/workspace/reports/models/candidate.json",
    ],
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--steps", nargs="+", choices=list(STEPS), default=list(STEPS))
    args = parser.parse_args()
    logs = ROOT / "reports/operations"
    logs.mkdir(parents=True, exist_ok=True)
    for step in args.steps:
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        path = logs / f"{step}-contract-1.1-{stamp}.log"
        print(f"Running {step}; log: {path}", flush=True)
        with path.open("w") as output:
            subprocess.run(
                [
                    "docker",
                    "compose",
                    "run",
                    "--rm",
                    "--entrypoint",
                    "/app/.venv/bin/python",
                    "train",
                    "-c",
                    RESOURCE_WRAPPER,
                    *STEPS[step],
                ],
                cwd=ROOT,
                stdout=output,
                stderr=subprocess.STDOUT,
                check=True,
            )
    print("Requested steps completed. Test and registry aliases were not touched.")


if __name__ == "__main__":
    main()
