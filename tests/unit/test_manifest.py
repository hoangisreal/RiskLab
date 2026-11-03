import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from risklab.manifest import (
    build_manifest,
    config_hash,
    git_info,
    sha256_file,
    write_json_atomic,
    write_manifest,
)

pytestmark = pytest.mark.unit


def test_sha256_known_value(tmp_path: Path) -> None:
    p = tmp_path / "f.bin"
    p.write_bytes(b"abc")
    assert sha256_file(p) == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"


def test_sha256_chunked_matches(tmp_path: Path) -> None:
    p = tmp_path / "f.bin"
    data = b"x" * 10_000
    p.write_bytes(data)
    assert sha256_file(p, chunk_size=7) == hashlib.sha256(data).hexdigest()


def test_config_hash_ignores_key_order_and_detects_change() -> None:
    a = {"x": 1, "y": {"b": 2, "a": [1, 2]}}
    b = {"y": {"a": [1, 2], "b": 2}, "x": 1}
    assert config_hash(a) == config_hash(b)
    assert config_hash(a) != config_hash({**a, "x": 2})


def test_git_info_outside_repo(tmp_path: Path) -> None:
    assert git_info(tmp_path) == {"commit": None, "dirty": None}


def test_git_info_repo_without_commits(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    assert git_info(tmp_path) == {"commit": None, "dirty": None}


def test_git_info_with_commit_and_dirty(tmp_path: Path) -> None:
    def git(*args: str) -> None:
        subprocess.run(
            ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=tmp_path, check=True
        )

    git("init", "-q")
    (tmp_path / "a.txt").write_text("a")
    git("add", "a.txt")
    git("commit", "-q", "-m", "init")
    info = git_info(tmp_path)
    assert isinstance(info["commit"], str) and len(info["commit"]) == 40
    assert info["dirty"] is False
    (tmp_path / "a.txt").write_text("b")
    assert git_info(tmp_path)["dirty"] is True


def test_build_manifest_fields(tmp_path: Path) -> None:
    m = build_manifest(
        "ingest",
        config={"k": 1},
        inputs={"b": "2", "a": "1"},
        outputs={"o": "3"},
        summary={"rows": 5},
        git_cwd=tmp_path,
    )
    assert m["step"] == "ingest"
    assert m["created_at"].endswith("+00:00")
    assert m["config_hash"] == config_hash({"k": 1})
    assert m["git"] == {"commit": None, "dirty": None}
    assert list(m["inputs"]) == ["a", "b"]
    assert set(m["versions"]) == {
        "python",
        "duckdb",
        "pyarrow",
        "numpy",
        "pandas",
        "scikit-learn",
        "lightgbm",
        "mlflow",
    }
    assert all(v for v in m["versions"].values())
    assert m["summary"] == {"rows": 5}


def test_write_json_atomic_sorted_and_no_temp_left(tmp_path: Path) -> None:
    target = tmp_path / "sub" / "m.json"
    write_json_atomic(target, {"b": 1, "a": 2})
    assert list(json.loads(target.read_text())) == ["a", "b"]
    assert [p.name for p in target.parent.iterdir()] == ["m.json"]


def test_write_json_atomic_failure_keeps_original(tmp_path: Path) -> None:
    target = tmp_path / "m.json"
    write_json_atomic(target, {"ok": 1})
    with pytest.raises(TypeError):
        write_json_atomic(target, {1: 1, "a": 2})  # mixed keys cannot be sorted
    assert json.loads(target.read_text()) == {"ok": 1}
    assert [p.name for p in tmp_path.iterdir()] == ["m.json"]


def test_write_manifest_roundtrip(tmp_path: Path) -> None:
    out = tmp_path / "reports" / "manifests" / "ingest.json"
    m = write_manifest(out, "ingest", config={"a": 1}, summary={"n": 1})
    assert json.loads(out.read_text())["config_hash"] == m["config_hash"]


def test_write_json_atomic_uses_fixed_mode(tmp_path: Path) -> None:
    target = write_json_atomic(tmp_path / "m.json", {"a": 1})
    assert target.stat().st_mode & 0o777 == 0o644
