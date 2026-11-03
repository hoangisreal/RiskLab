"""Feature SQL on the synthetic env: known aggregates, no-history semantics, parity."""

from __future__ import annotations

import duckdb
import pandas as pd
import pytest

from risklab.config import load_raw_schema
from risklab.data.clean import clean_application, load_cleaning_config
from risklab.data.loaders import PartitionRole, Purpose, load_partition
from risklab.features.build import build_features
from risklab.features.online import build_online_features
from risklab.features.registry import load_registry

pytestmark = pytest.mark.integration


def test_history_features_and_no_history(app_env) -> None:  # type: ignore[no-untyped-def]
    cfg_dir, data_cfg, partition_cfg = app_env.config_dir, app_env.data_cfg, app_env.partition_cfg
    schema = load_raw_schema(cfg_dir, data_cfg=data_cfg)
    cleaning = load_cleaning_config(cfg_dir, schema)
    table = load_partition(
        PartitionRole.train,
        Purpose.fit,
        data_cfg=data_cfg,
        partition_cfg=partition_cfg,
        config_dir=cfg_dir,
    )
    cleaned = clean_application(table, cleaning)
    ids = cleaned[partition_cfg.id_column].to_pylist()
    from pathlib import Path

    root = Path(cfg_dir).parent
    frames = build_features(
        data_cfg=data_cfg,
        root=root,
        applicant_ids=ids,
        application_cleaned=cleaned,
        registry=load_registry(cfg_dir),
    )
    bureau = frames["bureau"]
    assert (bureau["bureau_record_count"] >= 0).all()
    no_hist = bureau[bureau["bureau_record_count"] == 0]
    assert len(no_hist) > 0  # synthetic has applicants without bureau history
    assert no_hist["bureau_debt_sum"].isna().all()
    assert (frames["installments"]["inst_record_count"] >= 0).all()
    # Hand-check one applicant's late ratio against the raw Parquet.
    con = duckdb.connect()
    target_id = int(bureau[bureau["bureau_record_count"] > 0].index[0])
    raw = con.execute(
        "SELECT COUNT(*) c, "
        "AVG(CASE WHEN DAYS_ENTRY_PAYMENT > DAYS_INSTALMENT THEN 1.0 ELSE 0.0 END) r "
        "FROM read_parquet(?) WHERE SK_ID_CURR = ? AND DAYS_ENTRY_PAYMENT <= 0",
        [str(data_cfg.parquet_dir / "installments_payments.parquet"), target_id],
    ).fetchone()
    got = frames["installments"].loc[target_id]
    assert int(got["inst_record_count"]) == int(raw[0])  # type: ignore[index]
    if raw[0]:  # type: ignore[index]
        assert float(got["inst_late_ratio"]) == pytest.approx(float(raw[1]))  # type: ignore[index]


def test_online_batch_parity(app_env) -> None:  # type: ignore[no-untyped-def]
    cfg_dir, data_cfg, partition_cfg = app_env.config_dir, app_env.data_cfg, app_env.partition_cfg
    schema = load_raw_schema(cfg_dir, data_cfg=data_cfg)
    cleaning = load_cleaning_config(cfg_dir, schema)
    table = load_partition(
        PartitionRole.train,
        Purpose.fit,
        data_cfg=data_cfg,
        partition_cfg=partition_cfg,
        config_dir=cfg_dir,
    )
    cleaned = clean_application(table, cleaning)
    from pathlib import Path

    root = Path(cfg_dir).parent
    registry = load_registry(cfg_dir)
    frames = build_features(
        data_cfg=data_cfg,
        root=root,
        applicant_ids=cleaned[partition_cfg.id_column].to_pylist(),
        application_cleaned=cleaned,
        registry=registry,
    )
    batch = pd.concat([frames[g] for g in ["bureau", "previous"]], axis=1)
    # Rebuild the same applicant online from its raw history rows.
    con = duckdb.connect()
    applicant = int(batch.index[5])
    app_row = {
        c: cleaned.column(c).to_pylist()[
            list(cleaned[partition_cfg.id_column].to_pylist()).index(applicant)
        ]
        for c in cleaned.column_names
        if c != "TARGET"
    }

    def rows(table: str, key: str) -> list[dict]:
        df = con.execute(
            "SELECT * FROM read_parquet(?) WHERE SK_ID_CURR = ?",
            [str(data_cfg.parquet_dir / f"{table}.parquet"), applicant],
        ).fetchdf()
        return df.where(pd.notna(df), None).to_dict(orient="records")  # type: ignore[return-value]

    history = {
        "bureau": {"status": "provided", "records": rows("bureau", "SK_ID_CURR")},
        "previous_applications": {
            "status": "provided",
            "records": rows("previous_application", "SK_ID_CURR"),
        },
    }
    online, _ = build_online_features(
        app_row,
        history,
        groups=["bureau", "previous"],
        registry=registry,
        root=root,
        cleaning=cleaning,
        schema=schema,
        applicant_id=applicant,
    )
    for col in ["bureau_record_count", "prev_record_count"]:
        assert float(online.iloc[0][col]) == pytest.approx(float(batch.loc[applicant][col]))
