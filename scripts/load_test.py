"""Concurrent public-schema API load evidence with empty and representative Train history.

Run once per worker count configured in load_test.yaml. Generated payloads stay local;
only aggregate latency/error statistics and pinned model identity are logged to MLflow.
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import json
import os
import platform
import time
from collections import Counter
from pathlib import Path
from typing import Any

import httpx
import numpy as np
from pydantic import Field

from risklab.config import _Strict, load_data_config, load_model
from risklab.data.loaders import load_partition
from risklab.features.payload import train_payloads
from risklab.manifest import write_json_atomic
from risklab.tracking import tracked_run

ROOT = Path(__file__).resolve().parents[1]


class LoadConfig(_Strict):
    requests: int = Field(ge=1)
    warmup_requests: int = Field(ge=0)
    concurrency: int = Field(ge=1)
    workers: list[int]
    timeout_seconds: float = Field(gt=0)
    seed: int
    representative_train_applicants: int = Field(ge=1)


def representative_payloads(
    metadata: dict[str, Any], config: LoadConfig, config_dir: Path
) -> dict[str, dict[str, Any]]:
    ids = load_partition("train", "fit", config_dir=config_dir)["SK_ID_CURR"].to_numpy()
    selected = np.random.default_rng(config.seed).choice(
        ids, size=min(config.representative_train_applicants, len(ids)), replace=False
    )
    payloads = [
        payload
        for _, payload in train_payloads(
            applicant_ids=selected.tolist(),
            groups=metadata["feature_groups"],
            config_dir=config_dir,
            root=config_dir.parent,
        )
    ]
    sizes = [
        sum(len(source["records"]) for source in payload["history"].values())
        for payload in payloads
    ]
    nonempty = [i for i in np.argsort(sizes) if sizes[i] > 0]
    if not nonempty:
        raise ValueError("representative Train sample contains no history")
    full = payloads[nonempty[len(nonempty) // 2]]
    empty = copy.deepcopy(full)
    for source in empty["history"].values():
        source["records"] = []
    return {"empty_history": empty, "full_history": full}


async def measure(
    url: str, payload: dict[str, Any], *, config: LoadConfig, expected_version: str
) -> dict[str, Any]:
    latencies: list[float] = []
    statuses: Counter[str] = Counter()
    semaphore = asyncio.Semaphore(config.concurrency)
    limits = httpx.Limits(
        max_connections=config.concurrency, max_keepalive_connections=config.concurrency
    )
    async with httpx.AsyncClient(
        base_url=url, timeout=config.timeout_seconds, limits=limits
    ) as client:
        for _ in range(config.warmup_requests):
            response = await client.post("/predict", json=payload)
            response.raise_for_status()
            if response.json()["model_version"] != expected_version:
                raise ValueError("model changed during warmup")

        async def request() -> None:
            async with semaphore:
                start = time.perf_counter()
                try:
                    response = await client.post("/predict", json=payload)
                    status = str(response.status_code)
                    if status == "200" and response.json()["model_version"] != expected_version:
                        status = "model_version_changed"
                except httpx.HTTPError as exc:
                    status = type(exc).__name__
                latencies.append((time.perf_counter() - start) * 1000)
                statuses[status] += 1

        start = time.perf_counter()
        await asyncio.gather(*(request() for _ in range(config.requests)))
        wall = time.perf_counter() - start
    errors = config.requests - statuses.get("200", 0)
    return {
        "requests": config.requests,
        "concurrency": config.concurrency,
        "warmup_excluded": config.warmup_requests,
        "errors": errors,
        "error_rate": errors / config.requests,
        "statuses": dict(statuses),
        "p50_ms": float(np.percentile(latencies, 50)),
        "p95_ms": float(np.percentile(latencies, 95)),
        "p99_ms": float(np.percentile(latencies, 99)),
        "throughput_rps": config.requests / wall,
        "wall_seconds": wall,
        "payload_bytes": len(json.dumps(payload).encode()),
        "history_records": {
            name: len(source["records"]) for name, source in payload["history"].items()
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--workers", type=int, required=True)
    parser.add_argument("--config-dir", type=Path, default=ROOT / "configs")
    args = parser.parse_args()
    cfg_dir = args.config_dir.resolve()
    cfg = load_model(cfg_dir, "load_test.yaml", LoadConfig)
    if args.workers not in cfg.workers:
        raise ValueError("worker count is outside the declared measurement matrix")
    with httpx.Client(base_url=args.url, timeout=cfg.timeout_seconds) as client:
        response = client.get("/models/current")
        response.raise_for_status()
        metadata = response.json()
    payloads = representative_payloads(metadata, cfg, cfg_dir)
    results = {
        name: asyncio.run(
            measure(args.url, payload, config=cfg, expected_version=metadata["model_version"])
        )
        for name, payload in payloads.items()
    }
    report = {
        "model_version": metadata["model_version"],
        "model_run_id": metadata["run_id"],
        "selection_run_id": metadata["selection_run_id"],
        "lock_id": metadata["lock_id"],
        "workers": args.workers,
        "config": cfg.model_dump(),
        "cases": results,
        "hardware": {
            "platform": platform.platform(),
            "processor": platform.processor(),
            "logical_cpus": os.cpu_count(),
        },
        "latency_definition": "client request round trip after acquiring a concurrency slot",
    }
    with tracked_run(
        "risklab-load-test",
        f"workers-{args.workers}",
        {"model_version": metadata["model_version"], "model_run_id": metadata["run_id"] or ""},
        cfg.model_dump(),
    ) as run_id:
        import mlflow

        report["run_id"] = run_id
        mlflow.log_metrics(
            {
                f"{name}_{metric}": result[metric]
                for name, result in results.items()
                for metric in ("p50_ms", "p95_ms", "p99_ms", "throughput_rps", "error_rate")
            }
        )
        mlflow.log_dict(report, "load_test.json")
    write_json_atomic(
        load_data_config(cfg_dir).reports_dir / "load_test" / f"workers-{args.workers}.json", report
    )
    print(json.dumps(report, indent=2))
    if any(result["errors"] for result in results.values()):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
