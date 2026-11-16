"""Worker heartbeat file: the signal Docker health checks rely on."""

from __future__ import annotations

import json
import time

import pytest

from risklab.business.worker import heartbeat_fresh, write_heartbeat

pytestmark = pytest.mark.unit


def test_heartbeat_roundtrip_is_fresh(tmp_path):
    path = tmp_path / "worker-heartbeat.json"
    write_heartbeat(path, scorer_ready=True)
    assert json.loads(path.read_text())["scorer_ready"] is True
    assert heartbeat_fresh(path)


def test_readiness_requires_scorer_only_when_asked(tmp_path):
    path = tmp_path / "worker-heartbeat.json"
    write_heartbeat(path, scorer_ready=False)
    assert heartbeat_fresh(path)  # liveness: the loop is alive
    assert not heartbeat_fresh(path, require_scorer=True)  # readiness: degraded
    write_heartbeat(path, scorer_ready=True)
    assert heartbeat_fresh(path, require_scorer=True)


def test_stale_and_corrupt_heartbeats_fail(tmp_path):
    path = tmp_path / "worker-heartbeat.json"
    path.write_text(json.dumps({"ts": time.time() - 3600, "scorer_ready": True}))
    assert not heartbeat_fresh(path)
    path.write_text("not-json")
    assert not heartbeat_fresh(path)
    assert not heartbeat_fresh(tmp_path / "missing.json")
