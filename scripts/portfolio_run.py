"""Reproduce the CPU baseline with isolated artifacts and a private local MLflow server."""

from __future__ import annotations

import argparse
import os
import re
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def aggregate_log_line(line: str) -> str:
    """Remove applicant ID examples emitted by the historical validation CLI."""
    line = re.sub(r" e\.g\. \[[^\]]*\]", "", line)
    return re.sub(r"ids \[[^\]]*\]", "ids [redacted]", line)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument(
        "--baseline", type=Path, help="Run E6 against a completed isolated baseline"
    )
    parser.add_argument("--timeout-hours", type=float, default=12)
    parser.add_argument("--study", choices=["e6", "e7", "e8"], default="e6")
    parser.add_argument("--data-root", type=Path)
    args = parser.parse_args()
    if args.study == "e8" and (not args.data_root or args.baseline):
        parser.error("E8 requires --data-root and does not use --baseline")
    if args.study == "e7" and not args.baseline:
        parser.error("E7 requires --baseline")
    if args.study != "e8" and args.data_root:
        parser.error("--data-root is only supported for E8")
    if not 0 < args.timeout_hours <= 12:
        parser.error("timeout must be greater than zero and at most 12 hours")
    output = args.out.resolve()
    if output.exists():
        parser.error("output must not exist; historical runs are never overwritten")
    output.mkdir(parents=True)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    uri = f"http://127.0.0.1:{port}"
    env = {
        **os.environ,
        "MLFLOW_TRACKING_URI": uri,
        "MLFLOW_DISABLE_AGENT_HINT": "1",
        "OPENBLAS_NUM_THREADS": "1",
        "OMP_NUM_THREADS": "1",
    }
    with (output / "mlflow.log").open("w") as log:
        server = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "mlflow",
                "server",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--workers",
                "1",
                "--backend-store-uri",
                f"sqlite:///{output / 'tracking.db'}",
                "--artifacts-destination",
                str(output / "artifacts"),
            ],
            cwd=ROOT,
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        try:
            ready = False
            for _ in range(120):
                if server.poll() is not None:
                    raise RuntimeError(f"MLflow exited; inspect {output / 'mlflow.log'}")
                try:
                    with urllib.request.urlopen(uri + "/health", timeout=1) as response:
                        ready = response.status == 200
                except (OSError, urllib.error.URLError):
                    pass
                if ready:
                    break
                time.sleep(0.5)
            if not ready:
                raise RuntimeError("MLflow readiness timed out")
            stage = "study" if args.baseline or args.data_root else "baseline"
            print(f"Isolated tracking ready; outputs: {output / stage}", flush=True)
            with (output / "reproduce.log").open("w") as baseline_log:
                command = (
                    [
                        sys.executable,
                        "-m",
                        f"risklab.experiments.{args.study}",
                        "--baseline",
                        str(args.baseline.resolve()),
                        "--out",
                        str(output / "study"),
                    ]
                    if args.baseline
                    else [
                        sys.executable,
                        str(ROOT / "scripts/reproduce.py"),
                        "--out",
                        str(output / "baseline"),
                    ]
                )
                if args.study == "e8":
                    command = [
                        sys.executable,
                        "-m",
                        "risklab.experiments.e8",
                        "--data-root",
                        str(args.data_root.resolve()),
                        "--out",
                        str(output / "study"),
                    ]
                child = subprocess.Popen(
                    command,
                    cwd=ROOT,
                    env=env,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    start_new_session=True,
                )
                try:
                    captured, _ = child.communicate(timeout=args.timeout_hours * 3600)
                except BaseException:
                    # Only the process group created for this run is stopped.
                    os.killpg(child.pid, signal.SIGTERM)
                    try:
                        captured, _ = child.communicate(timeout=15)
                    except subprocess.TimeoutExpired:
                        os.killpg(child.pid, signal.SIGKILL)
                        captured, _ = child.communicate()
                    baseline_log.write(aggregate_log_line(captured))
                    raise
                baseline_log.write(aggregate_log_line(captured))
                code = child.returncode
                if code:
                    raise subprocess.CalledProcessError(code, command)
            print("Isolated run completed; Test was not opened and registry was not changed.")
        finally:
            server.terminate()
            try:
                server.wait(timeout=15)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait()


if __name__ == "__main__":
    main()
