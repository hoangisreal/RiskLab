"""Static and behavioral guards that Test rows cannot be read through the loaders.

The AST scan walks every module under ``src/risklab/``. Outside ``data/partition.py`` (which
writes and verifies all five role files) it fails on:

* a string constant containing ``test.parquet``;
* an f-string that builds ``{...role...}.parquet``, which could produce the Test role path;
* any reference to ``read_role_ids``, which reads a role file by arbitrary path.

``data/loaders.py`` is covered by the same rules and keeps an explicit path map for the four
readable roles. This is a guard against accidental reads, not a security boundary.
"""

import ast
from pathlib import Path

import pytest

from risklab.data import loaders
from risklab.data.loaders import (
    ALLOWED_PURPOSE,
    LoaderError,
    PartitionRole,
    Purpose,
    TestAccessError,
    load_partition,
    partition_info,
)

pytestmark = pytest.mark.unit

SRC = Path(__file__).resolve().parents[2] / "src" / "risklab"
PARTITION_MODULE = SRC / "data" / "partition.py"


def violations(path: Path, base: Path = SRC) -> list[str]:
    found: list[str] = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        where = f"{path.relative_to(base)}:{getattr(node, 'lineno', '?')}"
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if "test.parquet" in node.value:
                found.append(f"{where}: literal {node.value!r}")
        elif isinstance(node, ast.JoinedStr):
            for i, part in enumerate(node.values[:-1]):
                nxt = node.values[i + 1]
                if (
                    isinstance(part, ast.FormattedValue)
                    and "role" in ast.unparse(part.value).lower()
                    and isinstance(nxt, ast.Constant)
                    and str(nxt.value).startswith(".parquet")
                ):
                    found.append(f"{where}: f-string builds a role file path")
        elif isinstance(node, ast.Name | ast.Attribute):
            name = node.id if isinstance(node, ast.Name) else node.attr
            if name == "read_role_ids":
                found.append(f"{where}: references read_role_ids")
    return found


def test_only_partition_module_can_build_the_test_role_path() -> None:
    modules = [p for p in SRC.rglob("*.py") if p != PARTITION_MODULE]
    assert SRC / "data" / "loaders.py" in modules
    assert [v for p in modules for v in violations(p)] == []


def test_scan_detects_forbidden_patterns(tmp_path: Path) -> None:
    bad = tmp_path / "bad.py"
    bad.write_text(
        'a = "data/test.parquet"\nb = f"{role}.parquet"\nc = f"{cfg.role_name}.parquet"\n'
        "d = read_role_ids\n"
    )
    assert len(violations(bad, tmp_path)) == 4


def test_test_role_exists_but_has_no_allowed_purpose() -> None:
    assert PartitionRole.test == "test"
    assert PartitionRole.test not in ALLOWED_PURPOSE
    assert set(ALLOWED_PURPOSE.values()) == set(Purpose)
    assert {r for r in PartitionRole} - set(ALLOWED_PURPOSE) == {PartitionRole.test}
    assert not hasattr(loaders, "PARTITION_ROLES")


@pytest.mark.parametrize("purpose", [*Purpose, "anything"])
@pytest.mark.parametrize("role", [PartitionRole.test, "test"])
def test_load_partition_rejects_test_for_every_purpose(role: object, purpose: object) -> None:
    # No configs are passed: the rejection happens before any file or config is touched.
    with pytest.raises(TestAccessError, match="selection lock"):
        load_partition(role, purpose)  # type: ignore[arg-type]
    with pytest.raises(TestAccessError):
        partition_info(role)  # type: ignore[arg-type]


def test_unknown_role_is_a_loader_error() -> None:
    with pytest.raises(LoaderError, match="Unknown partition role"):
        load_partition("holdout", "fit")
