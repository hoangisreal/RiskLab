import json
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

import duckdb
import pytest
import yaml
from typer.testing import CliRunner

from risklab.cli import app
from risklab.config import DataConfig, RawSchema, load_data_config
from risklab.data.ingest import quote_ident
from risklab.data.validate import (
    ApplicationDomains,
    ValidateError,
    ValidationRules,
    run_validate,
)
from risklab.manifest import sha256_file
from tests.conftest import CONFIG_DIR, write_config_dir
from tests.fixtures.synthetic import generate

pytestmark = pytest.mark.integration

TRAIN, TEST = "application_train", "application_test"


def parquet(cfg: DataConfig, table: str) -> Path:
    return cfg.parquet_dir / f"{table}.parquet"


def scalar(cfg: DataConfig, sql: str, table: str = TRAIN) -> Any:
    con = duckdb.connect()
    try:
        return con.execute(sql.format(t=f"read_parquet('{parquet(cfg, table)}')")).fetchone()[0]  # type: ignore[index]
    finally:
        con.close()


@pytest.fixture
def env(
    tmp_path: Path, make_config: Callable[..., DataConfig], raw_schema: RawSchema
) -> tuple[Path, DataConfig]:
    cfg = make_config()
    generate(
        cfg.raw_dir, raw_schema, {t: f for f, t in cfg.tables.items()}, n_applicants=1500, seed=5
    )
    cfg_dir = write_config_dir(tmp_path, cfg, raw_schema)
    shutil.copy(CONFIG_DIR / "application_validation.yaml", cfg_dir)
    result = CliRunner().invoke(app, ["--config-dir", str(cfg_dir), "ingest"])
    assert result.exit_code == 0, result.output
    cfg = load_data_config(cfg_dir)
    # The synthetic generator fills generic categoricals with A/B/C and uses its own labels, so
    # the test domains are the reviewed real domains plus every value the clean synthetic data
    # contains. Defect tests inject values outside both.
    domains = yaml.safe_load((CONFIG_DIR / "application_domains.yaml").read_text())["domains"]
    con = duckdb.connect()
    for col, values in domains.items():
        q = quote_ident(col)
        seen = con.execute(
            f"SELECT DISTINCT {q} FROM read_parquet(?) WHERE {q} IS NOT NULL",
            [str(parquet(cfg, TRAIN))],
        ).fetchall()
        domains[col] = sorted(set(values) | {r[0] for r in seen})
    con.close()
    (cfg_dir / "application_domains.yaml").write_text(yaml.safe_dump({"domains": domains}))
    return cfg_dir, cfg


def validate_cli(cfg_dir: Path) -> Any:
    return CliRunner().invoke(app, ["--config-dir", str(cfg_dir), "validate"])


def report(cfg: DataConfig) -> dict[str, Any]:
    return json.loads((cfg.reports_dir / "validation" / "application.json").read_text())


def failing(doc: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    return {
        (c["name"], c["table"]): c
        for c in doc["checks"]
        if c["severity"] == "error" and c["offending_rows"] > 0
    }


def rewrite(cfg: DataConfig, table: str, replace: str, *, update_manifest: bool = True) -> None:
    """Rewrite a Parquet with ``SELECT * REPLACE (...)`` and record its new hash."""
    path = parquet(cfg, table)
    tmp = path.with_suffix(".tmp")
    con = duckdb.connect()
    con.execute(
        f"COPY (SELECT * REPLACE ({replace}) FROM read_parquet(?)) TO ? (FORMAT PARQUET)",
        [str(path), str(tmp)],
    )
    con.close()
    tmp.replace(path)
    if update_manifest:
        manifest = json.loads(cfg.manifest_path.read_text())
        manifest["outputs"][f"{table}.parquet"] = sha256_file(path)
        cfg.manifest_path.write_text(json.dumps(manifest))


def first_id(cfg: DataConfig, table: str, agg: str = "min") -> int:
    return int(scalar(cfg, f"SELECT {agg}(SK_ID_CURR) FROM {{t}}", table))


def set_one(col: str, value: str, row_id: int) -> str:
    q = quote_ident(col)
    return f"CASE WHEN SK_ID_CURR = {row_id} THEN {value} ELSE {q} END AS {q}"


def test_passes_on_synthetic_and_writes_outputs(env: tuple[Path, DataConfig]) -> None:
    cfg_dir, cfg = env
    result = validate_cli(cfg_dir)
    assert result.exit_code == 0, result.output
    doc = report(cfg)
    assert doc["status"] == "passed"
    assert failing(doc) == {}
    ingest = json.loads(cfg.manifest_path.read_text())
    assert doc["sources"][TRAIN]["parquet_sha256"] == ingest["outputs"]["application_train.parquet"]
    assert doc["sources"][TEST]["csv_sha256"] == ingest["inputs"]["application_test.csv"]
    assert len(doc["config_hash"]) == 64
    manifest = json.loads((cfg.reports_dir / "manifests" / "validate.json").read_text())
    assert manifest["step"] == "validate"
    assert manifest["outputs"]["application.json"] == sha256_file(
        cfg.reports_dir / "validation" / "application.json"
    )
    assert manifest["summary"]["status"] == "passed"
    assert "Validation passed" in result.output


def test_info_counts_match_direct_queries(env: tuple[Path, DataConfig]) -> None:
    cfg_dir, cfg = env
    assert validate_cli(cfg_dir).exit_code == 0
    checks = {(c["name"], c["table"]): c for c in report(cfg)["checks"]}
    for table in (TRAIN, TEST):
        expected = scalar(cfg, "SELECT count(*) FROM {t} WHERE DAYS_EMPLOYED = 365243", table)
        assert expected > 0
        assert checks[("sentinel_count:DAYS_EMPLOYED", table)]["offending_rows"] == expected
        xna = checks[("xna_count", table)]
        org = scalar(cfg, "SELECT count(*) FROM {t} WHERE ORGANIZATION_TYPE = 'XNA'", table)
        assert xna["details"]["by_column"]["ORGANIZATION_TYPE"] == org
    gender = scalar(cfg, "SELECT count(*) FROM {t} WHERE CODE_GENDER = 'XNA'")
    assert gender >= 1
    assert checks[("xna_count", TRAIN)]["details"]["by_column"]["CODE_GENDER"] == gender
    nulls = checks[("null_rates", TRAIN)]["details"]["top"]
    assert 0 < len(nulls) <= 10
    rates = [t["null_rate"] for t in nulls]
    assert rates == sorted(rates, reverse=True)
    assert all(c["severity"] == "info" for c in checks.values() if c["name"] == "null_rates")


def build_cases() -> list[Any]:
    def one(col: str, value: str, table: str = TRAIN) -> Callable[[DataConfig], str]:
        return lambda cfg: set_one(col, value, first_id(cfg, table))

    return [
        pytest.param(
            lambda cfg: set_one(
                "SK_ID_CURR", str(first_id(cfg, TRAIN)), first_id(cfg, TRAIN, "max")
            ),
            TRAIN,
            "id_duplicate",
            id="duplicate-id",
        ),
        pytest.param(
            lambda cfg: set_one("SK_ID_CURR", "NULL", first_id(cfg, TRAIN)),
            TRAIN,
            "id_null",
            id="null-id",
        ),
        pytest.param(one("TARGET", "2"), TRAIN, "target_domain", id="target-2"),
        pytest.param(one("TARGET", "NULL"), TRAIN, "target_null", id="target-null"),
        pytest.param(one("CODE_GENDER", "'Q'"), TRAIN, "categorical_domain", id="bad-category"),
        pytest.param(
            one("CODE_GENDER", "'Q'", TEST), TEST, "categorical_domain", id="bad-category-test"
        ),
        pytest.param(one("FLAG_DOCUMENT_3", "2"), TRAIN, "range:flag_binary", id="flag-2"),
        pytest.param(
            one("EXT_SOURCE_1", "1.5"), TRAIN, "range:ext_source_unit_interval", id="ext-1.5"
        ),
        pytest.param(one("AMT_INCOME_TOTAL", "0"), TRAIN, "range:amount_positive", id="income-0"),
        pytest.param(
            one("AMT_ANNUITY", "-1"), TRAIN, "range:annuity_non_negative", id="annuity-neg"
        ),
        pytest.param(one("DAYS_BIRTH", "5"), TRAIN, "range:days_birth_negative", id="birth-5"),
        pytest.param(one("DAYS_EMPLOYED", "7"), TRAIN, "range:days_employed", id="employed-7"),
        pytest.param(
            one("CNT_CHILDREN", "-1"), TRAIN, "range:children_non_negative", id="children-neg"
        ),
        pytest.param(
            one("REGION_RATING_CLIENT", "0", TEST), TEST, "range:region_rating", id="rating-0-test"
        ),
    ]


@pytest.mark.parametrize(("replace", "table", "check"), build_cases())
def test_defect_fails_exactly_the_matching_check(
    env: tuple[Path, DataConfig],
    replace: Callable[[DataConfig], str],
    table: str,
    check: str,
) -> None:
    cfg_dir, cfg = env
    rewrite(cfg, table, replace(cfg))
    result = validate_cli(cfg_dir)
    assert result.exit_code == 1, result.output
    doc = report(cfg)
    assert doc["status"] == "failed"
    found = failing(doc)
    assert set(found) == {(check, table)}, result.output
    assert found[(check, table)]["offending_rows"] >= 1
    assert "[FAIL]" in result.output


def test_duplicate_id_example_is_the_duplicated_id(env: tuple[Path, DataConfig]) -> None:
    cfg_dir, cfg = env
    low = first_id(cfg, TRAIN)
    rewrite(cfg, TRAIN, set_one("SK_ID_CURR", str(low), first_id(cfg, TRAIN, "max")))
    assert validate_cli(cfg_dir).exit_code == 1
    check = failing(report(cfg))[("id_duplicate", TRAIN)]
    assert check["offending_rows"] == 2
    assert check["examples"] == [low]


def test_train_test_overlap(env: tuple[Path, DataConfig]) -> None:
    cfg_dir, cfg = env
    train_id = first_id(cfg, TRAIN)
    rewrite(cfg, TEST, set_one("SK_ID_CURR", str(train_id), first_id(cfg, TEST)))
    assert validate_cli(cfg_dir).exit_code == 1
    found = failing(report(cfg))
    assert set(found) == {("id_overlap_train_test", TEST)}
    assert found[("id_overlap_train_test", TEST)]["examples"] == [train_id]


def test_target_column_present_in_test(env: tuple[Path, DataConfig]) -> None:
    cfg_dir, cfg = env
    path = parquet(cfg, TEST)
    tmp = path.with_suffix(".tmp")
    con = duckdb.connect()
    con.execute(
        "COPY (SELECT *, 0 AS TARGET FROM read_parquet(?)) TO ? (FORMAT PARQUET)",
        [str(path), str(tmp)],
    )
    con.close()
    tmp.replace(path)
    manifest = json.loads(cfg.manifest_path.read_text())
    manifest["outputs"]["application_test.parquet"] = sha256_file(path)
    cfg.manifest_path.write_text(json.dumps(manifest))
    assert validate_cli(cfg_dir).exit_code == 1
    assert set(failing(report(cfg))) == {("target_absent_in_test", TEST)}


def test_row_count_differs_from_manifest(env: tuple[Path, DataConfig]) -> None:
    cfg_dir, cfg = env
    manifest = json.loads(cfg.manifest_path.read_text())
    manifest["summary"]["tables"][TRAIN]["parquet_rows"] += 3
    cfg.manifest_path.write_text(json.dumps(manifest))
    assert validate_cli(cfg_dir).exit_code == 1
    found = failing(report(cfg))
    assert set(found) == {("row_count_matches_manifest", TRAIN)}
    assert found[("row_count_matches_manifest", TRAIN)]["offending_rows"] == 3


def test_stale_parquet_is_rejected_before_validation(env: tuple[Path, DataConfig]) -> None:
    cfg_dir, cfg = env
    with parquet(cfg, TRAIN).open("ab") as fh:
        fh.write(b"\0")
    result = validate_cli(cfg_dir)
    assert result.exit_code == 1
    assert "Error:" in result.output
    assert "stale or modified" in result.output
    assert not (cfg.reports_dir / "validation" / "application.json").exists()


def test_missing_manifest_is_an_error(env: tuple[Path, DataConfig]) -> None:
    cfg_dir, cfg = env
    cfg.manifest_path.unlink()
    result = validate_cli(cfg_dir)
    assert result.exit_code == 1
    assert "Ingest manifest not found" in result.output


def test_domains_must_cover_all_varchar_columns(
    env: tuple[Path, DataConfig], raw_schema: RawSchema
) -> None:
    cfg_dir, cfg = env
    rules = ValidationRules.model_validate(
        yaml.safe_load((cfg_dir / "application_validation.yaml").read_text())
    )
    domains = ApplicationDomains.model_validate({"domains": {"CODE_GENDER": ["F"]}})
    with pytest.raises(Exception, match="must cover exactly"):
        run_validate(cfg, raw_schema, rules, domains, root=cfg_dir.parent)


def test_unreadable_manifest_raises(env: tuple[Path, DataConfig], raw_schema: RawSchema) -> None:
    cfg_dir, cfg = env
    cfg.manifest_path.write_text("{not json")
    rules = ValidationRules.model_validate(
        yaml.safe_load((cfg_dir / "application_validation.yaml").read_text())
    )
    domains = ApplicationDomains.model_validate(
        yaml.safe_load((cfg_dir / "application_domains.yaml").read_text())
    )
    with pytest.raises(ValidateError, match="unreadable"):
        run_validate(cfg, raw_schema, rules, domains, root=cfg_dir.parent)


RATING = "REGION_RATING_CLIENT_W_CITY"


def set_exceptions(cfg_dir: Path, *entries: tuple[str, list[int]]) -> None:
    path = cfg_dir / "application_validation.yaml"
    doc = yaml.safe_load(path.read_text())
    doc["known_exceptions"] = [
        {"table": TEST, "check": check, "ids": ids, "reason": "test reason"}
        for check, ids in entries
    ]
    path.write_text(yaml.safe_dump(doc))


def find(doc: dict[str, Any], name: str, table: str = TEST) -> dict[str, Any]:
    return next(c for c in doc["checks"] if c["name"] == name and c["table"] == table)


def test_listed_id_of_matching_check_is_accepted(env: tuple[Path, DataConfig]) -> None:
    cfg_dir, cfg = env
    row = first_id(cfg, TEST)
    rewrite(cfg, TEST, set_one(RATING, "-1", row))
    set_exceptions(cfg_dir, ("range:region_rating", [row]))
    result = validate_cli(cfg_dir)
    assert result.exit_code == 0, result.output
    doc = report(cfg)
    assert doc["status"] == "passed"
    assert failing(doc) == {}
    check = find(doc, "range:region_rating")
    assert check["status"] == "passed_with_exceptions"
    assert check["offending_rows"] == 0
    assert check["accepted_exceptions"] == {"ids": [row], "count": 1, "reason": "test reason"}
    assert doc["summary"]["accepted_exceptions"] == 1
    assert not [c for c in doc["checks"] if c["name"] == "stale_known_exception"]
    assert "accepted exception" in result.output


def test_same_violation_on_unlisted_id_fails(env: tuple[Path, DataConfig]) -> None:
    cfg_dir, cfg = env
    row = first_id(cfg, TEST)
    other = first_id(cfg, TEST, "max")
    rewrite(cfg, TEST, set_one(RATING, "-1", row))
    set_exceptions(cfg_dir, ("range:region_rating", [other]))
    assert validate_cli(cfg_dir).exit_code == 1
    doc = report(cfg)
    check = failing(doc)[("range:region_rating", TEST)]
    assert (check["offending_rows"], check["examples"]) == (1, [row])
    assert "accepted_exceptions" not in check
    assert find(doc, "stale_known_exception")["examples"] == [other]


def test_listed_id_with_different_check_still_fails(env: tuple[Path, DataConfig]) -> None:
    cfg_dir, cfg = env
    row = first_id(cfg, TEST)
    rewrite(cfg, TEST, set_one(RATING, "-1", row))
    set_exceptions(cfg_dir, ("range:flag_binary", [row]))
    assert validate_cli(cfg_dir).exit_code == 1
    doc = report(cfg)
    assert set(failing(doc)) == {("range:region_rating", TEST)}
    assert find(doc, "stale_known_exception")["details"]["check"] == "range:flag_binary"


def test_exception_does_not_cross_tables(env: tuple[Path, DataConfig]) -> None:
    cfg_dir, cfg = env
    row = first_id(cfg, TRAIN)
    rewrite(cfg, TRAIN, set_one(RATING, "-1", row))
    set_exceptions(cfg_dir, ("range:region_rating", [row]))  # listed for the test table only
    assert validate_cli(cfg_dir).exit_code == 1
    assert set(failing(report(cfg))) == {("range:region_rating", TRAIN)}


def test_stale_exception_warns_but_passes(env: tuple[Path, DataConfig]) -> None:
    cfg_dir, cfg = env
    row = first_id(cfg, TEST)
    set_exceptions(cfg_dir, ("range:region_rating", [row]))
    result = validate_cli(cfg_dir)
    assert result.exit_code == 0, result.output
    doc = report(cfg)
    assert doc["status"] == "passed"
    stale = find(doc, "stale_known_exception")
    assert stale["severity"] == "warning"
    assert stale["examples"] == [row]
    assert doc["summary"]["warnings"] == 1
    assert "accepted_exceptions" not in find(doc, "range:region_rating")


def test_second_offending_row_beside_accepted_one_fails(env: tuple[Path, DataConfig]) -> None:
    cfg_dir, cfg = env
    low, high = first_id(cfg, TEST), first_id(cfg, TEST, "max")
    rewrite(
        cfg,
        TEST,
        f"CASE WHEN SK_ID_CURR IN ({low}, {high}) THEN -1 ELSE {RATING} END AS {RATING}",
    )
    set_exceptions(cfg_dir, ("range:region_rating", [low]))
    assert validate_cli(cfg_dir).exit_code == 1
    check = failing(report(cfg))[("range:region_rating", TEST)]
    assert (check["offending_rows"], check["examples"]) == (1, [high])
    assert check["accepted_exceptions"]["ids"] == [low]
    assert check["status"] == "failed"
