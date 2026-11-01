import random
from pathlib import Path

import pytest
import yaml

from risklab.config import PARTITION_ROLES, ConfigError, PartitionConfig, load_partition_config
from risklab.data import partition as partition_mod
from risklab.data.partition import (
    PartitionError,
    assign_partitions,
    class_counts,
    ids_sha256,
    partition_hash,
    validate_rows,
)
from tests.conftest import CONFIG_DIR

pytestmark = pytest.mark.unit

GOLDEN_HASH = "8c0d40cf4dac46392b58fd08c3d35df097501cc1c8c3c0aa74e45d56a44d37cf"
SHARES = {"train": 0.55, "calibration": 0.10, "selection": 0.10, "test": 0.15, "monitoring": 0.10}


def cfg_with(**shares_update: float | None) -> dict[str, object]:
    shares = {**SHARES, **shares_update}
    shares = {k: v for k, v in shares.items() if v is not None}
    return {
        "seed": 1,
        "shares": shares,
        "source_table": "application_train",
        "id_column": "SK_ID_CURR",
        "stratify_column": "TARGET",
        "output_dir": "data/partitions",
    }


def test_repo_config_loads() -> None:
    cfg = load_partition_config(CONFIG_DIR)
    assert cfg.seed == 20261101
    assert cfg.shares == SHARES
    assert cfg.output_dir.is_absolute()


@pytest.mark.parametrize(
    "update",
    [
        {"train": 0.5},  # sum != 1
        {"monitoring": None},  # missing role
        {"extra": 0.0},  # unexpected role (also breaks the sum rule)
        {"train": 0.65, "monitoring": 0.0},  # non-positive share, sum is still 1
        {"train": 0.56, "monitoring": -0.01},  # negative share
    ],
)
def test_invalid_shares_rejected(update: dict[str, float | None]) -> None:
    with pytest.raises(ValueError, match="shares"):
        PartitionConfig.model_validate(cfg_with(**update))


def test_extra_key_and_bad_file_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="extra"):
        PartitionConfig.model_validate({**cfg_with(), "unknown": 1})
    cfg_dir = tmp_path / "configs"
    cfg_dir.mkdir()
    (cfg_dir / "partition.yaml").write_text(yaml.safe_dump(cfg_with(train=0.5)))
    with pytest.raises(ConfigError, match="shares must sum to 1"):
        load_partition_config(cfg_dir)


def test_class_counts_largest_remainder() -> None:
    # 17 * share = 9.35, 1.7, 1.7, 2.55, 1.7 -> floors 9,1,1,2,1 (sum 14), leftover 3
    # remainders .35, .7, .7, .55, .7 -> calibration, selection, monitoring (role order)
    assert class_counts(17, SHARES) == {
        "train": 9,
        "calibration": 2,
        "selection": 2,
        "test": 2,
        "monitoring": 2,
    }
    for n in (0, 1, 4, 99, 1000, 24825):
        counts = class_counts(n, SHARES)
        assert sum(counts.values()) == n
        assert all(abs(counts[r] - SHARES[r] * n) < 1 for r in PARTITION_ROLES)


def make_rows(n: int, positive_every: int = 10) -> list[tuple[int, int]]:
    return [(100_000 + 3 * i, int(i % positive_every == 0)) for i in range(n)]


def test_assignment_is_independent_of_row_order() -> None:
    rows = make_rows(500)
    shuffled = rows[:]
    random.Random(0).shuffle(shuffled)
    assert assign_partitions(rows, 7, SHARES) == assign_partitions(shuffled, 7, SHARES)


def test_assignment_disjoint_complete_and_exactly_stratified() -> None:
    rows = make_rows(1003)
    roles = assign_partitions(rows, 7, SHARES)
    all_ids = [i for ids in roles.values() for i in ids]
    assert sorted(all_ids) == sorted(i for i, _ in rows)
    target = dict(rows)
    for t in (0, 1):
        n_class = sum(1 for _, x in rows if x == t)
        expected = class_counts(n_class, SHARES)
        for role in PARTITION_ROLES:
            assert sum(1 for i in roles[role] if target[i] == t) == expected[role]


def test_seed_changes_assignment_not_counts() -> None:
    rows = make_rows(1000)
    a, b = assign_partitions(rows, 1, SHARES), assign_partitions(rows, 2, SHARES)
    assert a != b
    assert {r: len(v) for r, v in a.items()} == {r: len(v) for r, v in b.items()}


def test_golden_hash_for_fixed_tiny_input() -> None:
    """Pins the algorithm: BLAKE2b ordering, largest-remainder cut, and hash definitions."""
    rows = [(i, int(i % 4 == 0)) for i in range(1, 41)]
    roles = assign_partitions(rows, 20261101, SHARES)
    assert {r: len(v) for r, v in roles.items()} == {
        "train": 23,
        "calibration": 4,
        "selection": 4,
        "test": 5,
        "monitoring": 4,
    }
    hashes = {r: ids_sha256(roles[r]) for r in PARTITION_ROLES}
    assert partition_hash(20261101, SHARES, hashes, "abc") == GOLDEN_HASH


def test_ids_sha256_is_order_independent_and_newline_terminated() -> None:
    import hashlib

    assert ids_sha256([3, 1, 2]) == hashlib.sha256(b"1\n2\n3\n").hexdigest()


@pytest.mark.parametrize(
    ("rows", "message"),
    [
        ([(1, 0), (1, 1)], "duplicate"),
        ([(None, 0)], "null"),
        ([(1, None)], "null"),
        ([(1, 2)], r"must be in \{0, 1\}"),
        ([(1, -1)], r"must be in \{0, 1\}"),
    ],
)
def test_input_validation(rows: list[tuple[int | None, int | None]], message: str) -> None:
    cfg = PartitionConfig.model_validate(cfg_with())
    with pytest.raises(PartitionError, match=message):
        validate_rows(rows, cfg)


@pytest.mark.parametrize("rows", [[], [(1, 1)] + [(i, 0) for i in range(2, 7)]])
def test_tiny_inputs_are_rejected_before_division(rows: list[tuple[int, int]]) -> None:
    roles = partition_mod.assign_partitions(rows, 1, SHARES)
    with pytest.raises(partition_mod.PartitionError, match=r"no rows|Role '\w+' would get \d+ row"):
        partition_mod._check_role_sizes(rows, roles)
