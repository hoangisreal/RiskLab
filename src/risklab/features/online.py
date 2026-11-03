"""Online feature computation: same cleaning code and same SQL as the batch path.

A request carries the raw application fields plus, per history source, either records
or an explicit ``not_provided`` status. Tables are built in an in-memory DuckDB and
the registry SQL files run unchanged, so batch/online parity holds by construction.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd
import pyarrow as pa

from risklab.config import RawSchema
from risklab.data.clean import CleaningConfig, clean_application
from risklab.features.build import build_group_frames
from risklab.features.registry import FeatureRegistry

# History source key -> (DuckDB view/table, registry group)
SOURCE_GROUPS: dict[str, str] = {
    "bureau": "bureau",
    "bureau_balance": "bureau_balance",
    "previous_applications": "previous",
    "installments": "installments",
    "pos_cash": "pos_cash",
    "credit_card": "credit_card",
}
GROUP_SOURCE: dict[str, str] = {v: k for k, v in SOURCE_GROUPS.items()}
# Groups needing extra tables beyond their key source (two-level bureau_balance).
GROUP_EXTRA_TABLES: dict[str, list[str]] = {"bureau_balance": ["bureau", "bureau_balance"]}


class OnlineFeatureError(Exception):
    """Raised for contract violations (missing sources, dangling refs, limits)."""


_ARROW_TYPES = {
    "BIGINT": pa.int64(),
    "INTEGER": pa.int32(),
    "SMALLINT": pa.int16(),
    "TINYINT": pa.int8(),
    "DOUBLE": pa.float64(),
    "FLOAT": pa.float32(),
    "BOOLEAN": pa.bool_(),
    "VARCHAR": pa.string(),
    "DATE": pa.string(),
}


def _records_to_table(records: Sequence[Mapping[str, Any]], specs: list[Any]) -> pa.Table:
    """Arrow table with the raw-schema types (never inferred from request values)."""
    cols: dict[str, list[Any]] = {}
    fields = []
    for spec in specs:
        vals: list[Any] = []
        for r in records:
            v = r.get(spec.name)
            if isinstance(v, float) and v != v:  # NaN from pandas round-trips means NULL
                v = None
            if v is not None and spec.type == "VARCHAR" and not isinstance(v, str):
                v = str(v)
            if v is not None and spec.type in ("DOUBLE", "FLOAT"):
                try:
                    v = float(v)
                except (TypeError, ValueError):
                    v = None
            elif v is not None and spec.type in ("BIGINT", "INTEGER", "SMALLINT", "TINYINT"):
                try:
                    v = int(v)
                except (TypeError, ValueError):
                    v = None
            vals.append(v)
        cols[spec.name] = vals
        fields.append(pa.field(spec.name, _ARROW_TYPES.get(spec.type, pa.string())))
    return pa.table(cols, schema=pa.schema(fields))


def build_online_features(
    application: Mapping[str, Any],
    history: Mapping[str, Any],
    *,
    groups: Sequence[str],
    registry: FeatureRegistry,
    root: Path,
    cleaning: CleaningConfig,
    schema: RawSchema,
    applicant_id: int = 0,
) -> tuple[pd.DataFrame, list[str]]:
    """One-row feature frame for the requested groups plus warnings.

    ``history`` maps a source key to ``{"status": "provided", "records": [...]}`` or
    ``{"status": "not_provided"}``. A required group whose source is not provided
    raises ``OnlineFeatureError`` (422 semantics); unused sources are warnings.
    """
    wanted = list(groups)
    warnings: list[str] = []
    required_sources: set[str] = set()
    for g in wanted:
        if g == "application":
            continue
        src = GROUP_SOURCE.get(g)
        if src is None:
            raise OnlineFeatureError(f"unknown feature group {g!r}")
        required_sources.add(src)
        for extra in GROUP_EXTRA_TABLES.get(g, []):
            # bureau_balance needs the bureau table too (source key "bureau").
            required_sources.add("bureau" if extra == "bureau" else extra)

    provided: dict[str, list[Mapping[str, Any]]] = {}
    for src, payload in (history or {}).items():
        status = (payload or {}).get("status", "not_provided")
        if status == "provided":
            provided[src] = list((payload or {}).get("records") or [])
        elif status != "not_provided":
            raise OnlineFeatureError(f"source {src!r}: unknown status {status!r}")

    missing = sorted(s for s in required_sources if s not in provided)
    if missing:
        raise OnlineFeatureError(f"missing required history source(s): {missing}")
    for src in provided:
        if src not in required_sources and src in SOURCE_GROUPS:
            warnings.append(f"source {src!r} provided but not used by the served model; ignored")

    # Dangling child references within the request.
    bureau_refs = {
        str(r.get("bureau_ref"))
        for r in provided.get("bureau", [])
        if r.get("bureau_ref") is not None
    }
    for rec in provided.get("bureau_balance", []):
        ref = rec.get("bureau_ref")
        if ref is not None and str(ref) not in bureau_refs:
            raise OnlineFeatureError("bureau_balance record references unknown bureau_ref")
    prev_refs = {
        str(r.get("prev_ref"))
        for r in provided.get("previous_applications", [])
        if r.get("prev_ref") is not None
    }
    for src in ("installments", "pos_cash", "credit_card"):
        for rec in provided.get(src, []):
            ref = rec.get("prev_ref")
            if ref is not None and str(ref) not in prev_refs:
                raise OnlineFeatureError(f"{src} record references unknown prev_ref")

    # Application row: fill missing schema columns with None, clean, then register.
    app_cols = [c.name for c in schema.columns("application_train") if c.name != "TARGET"]
    app_row = {c: application.get(c) for c in app_cols}
    app_row["SK_ID_CURR"] = applicant_id
    app_table = pa.table({c: [app_row[c]] for c in app_cols})
    cleaned = clean_application(app_table, cleaning)

    con = duckdb.connect(":memory:")
    try:
        con.register("application_src", cleaned)
        con.register(
            "applicants", pa.table({"SK_ID_CURR": pa.array([applicant_id], type=pa.int64())})
        )
        table_for_source = {
            "bureau": "bureau",
            "bureau_balance": "bureau_balance",
            "previous_applications": "previous_application",
            "installments": "installments_payments",
            "pos_cash": "pos_cash_balance",
            "credit_card": "credit_card_balance",
        }
        for src, table in table_for_source.items():
            specs = schema.columns(table)
            cols = [c.name for c in specs]
            recs = provided.get(src, [])
            # Map request refs onto real keys so the shared SQL joins work.
            norm: list[dict[str, Any]] = []
            for i, r in enumerate(recs):
                row = {c: r.get(c) for c in cols}
                if table == "bureau":
                    row["SK_ID_CURR"] = applicant_id
                    row["SK_ID_BUREAU"] = 1_000_000 + i
                elif table == "bureau_balance":
                    ref = r.get("bureau_ref")
                    bureau_list = provided.get("bureau", [])
                    idx = next(
                        (
                            j
                            for j, b in enumerate(bureau_list)
                            if str(b.get("bureau_ref")) == str(ref)
                        ),
                        None,
                    )
                    row["SK_ID_BUREAU"] = (1_000_000 + idx) if idx is not None else -1
                elif table == "previous_application":
                    row["SK_ID_CURR"] = applicant_id
                    row["SK_ID_PREV"] = 2_000_000 + i
                else:
                    prev_list = provided.get("previous_applications", [])
                    ref = r.get("prev_ref")
                    idx = next(
                        (j for j, b in enumerate(prev_list) if str(b.get("prev_ref")) == str(ref)),
                        None,
                    )
                    row["SK_ID_PREV"] = (2_000_000 + idx) if idx is not None else -1
                    row["SK_ID_CURR"] = applicant_id
                norm.append(row)
            con.register(table, _records_to_table(norm, specs))
        frames = build_group_frames(con, [g for g in wanted], registry, root)
    finally:
        con.close()
    combined = (
        pd.concat([frames[g] for g in wanted], axis=1)
        if wanted
        else pd.DataFrame(index=[applicant_id])
    )
    return combined, warnings


__all__ = ["GROUP_SOURCE", "SOURCE_GROUPS", "OnlineFeatureError", "build_online_features"]
