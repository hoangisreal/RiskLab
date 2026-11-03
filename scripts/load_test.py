"""Minimal load check against a running API (httpx, no extra dependency).

Reports p50/p95/p99, throughput and error rate for a no-history payload.
Usage: uv run python scripts/load_test.py [--url http://127.0.0.1:8000] [--n 100]
"""

from __future__ import annotations

import argparse
import json
import time

import httpx
import numpy as np

PAYLOAD = {
    "contract_version": "1.0",
    "application": {"AMT_CREDIT": 500000.0, "AMT_INCOME_TOTAL": 200000.0, "AMT_ANNUITY": 25000.0},
    "history": {
        "previous_applications": {"status": "provided", "records": []},
        "installments": {"status": "provided", "records": []},
    },
}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8000")
    ap.add_argument("--n", type=int, default=100)
    args = ap.parse_args()
    lat: list[float] = []
    errors = 0
    t0 = time.perf_counter()
    with httpx.Client(base_url=args.url, timeout=30.0) as client:
        for _ in range(args.n):
            s = time.perf_counter()
            try:
                r = client.post("/predict", json=PAYLOAD)
                if r.status_code != 200:
                    errors += 1
            except Exception:
                errors += 1
            lat.append((time.perf_counter() - s) * 1000)
    wall = time.perf_counter() - t0
    arr = np.array(lat)
    out = {
        "n": args.n,
        "errors": errors,
        "error_rate": errors / max(args.n, 1),
        "p50_ms": float(np.percentile(arr, 50)),
        "p95_ms": float(np.percentile(arr, 95)),
        "p99_ms": float(np.percentile(arr, 99)),
        "throughput_rps": args.n / wall,
    }
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
