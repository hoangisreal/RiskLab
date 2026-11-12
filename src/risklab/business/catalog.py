"""Trusted, checksum-pinned demo catalog validation; never a browser-uploaded catalog.

Only a release adapter/operator supplies catalog bytes and expected SHA256. Snapshot
payload hashes bind canonical raw JSON, not defaults/coercions added by Pydantic.
Parsing and immutable object writes happen before the persistence transaction.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError

from risklab.api.contract import request_model
from risklab.api.service import ServiceError
from risklab.api.validation import validate_history_records
from risklab.business.errors import AppError
from risklab.business.import_csv import ImportIssue, ImportRow, InvalidImport, parse_import
from risklab.business.storage import LocalStorage
from risklab.features.online import GROUP_EXTRA_TABLES, GROUP_SOURCE, SOURCE_GROUPS
from risklab.features.registry import load_registry

Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
SafeName = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]{1,64}$")]


def canonical_json(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")


class PinnedRelease(BaseModel):
    """Identity only, not evidence that a model was loaded/verified (B03-T03).

    Scoring fields stay optional at import time: imports pin identity even when
    the bundle is unavailable, while the scoring adapter refuses to run until
    threshold/policy/protocol/column identity is fully supplied.
    """

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    model_version: str = Field(min_length=1, max_length=128)
    model_sha256: Sha256
    model_run_id: str = Field(min_length=1, max_length=128)
    lock_id: str = Field(min_length=1, max_length=128)
    feature_contract_version: str = Field(min_length=1, max_length=32)
    feature_groups: list[str] = Field(max_length=8)
    threshold: float | None = Field(default=None, ge=0, le=1)
    policy_version: str | None = Field(default=None, min_length=1, max_length=128)
    protocol_hashes: dict[str, str] = Field(default_factory=dict)
    feature_columns: list[str] = Field(default_factory=list)


class CatalogDocument(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    catalog_version: SafeName
    provenance: str = Field(min_length=1, max_length=2000)
    feature_contract_version: str
    required_groups: list[str] = Field(max_length=8)
    snapshots: dict[SafeName, dict[str, Any]] = Field(min_length=1, max_length=1000)


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


@dataclass(frozen=True)
class PreparedRow:
    source: ImportRow
    input_sha256: str
    # JSON bytes keep prepared values isolated from mutable DTOs supplied by callers.
    availability_json: bytes
    missing_sources: tuple[str, ...]


@dataclass(frozen=True)
class PreparedImport:
    file_sha256: str
    catalog_sha256: str
    catalog_version: str
    release_json: bytes
    rows: tuple[PreparedRow, ...]
    issues: tuple[ImportIssue, ...]
    errors_truncated: bool = False


class DemoCatalog:
    def __init__(self, data: bytes, *, expected_sha256: str, config_dir: Path) -> None:
        if len(data) > 20_000_000 or hashlib.sha256(data).hexdigest() != expected_sha256:
            raise AppError(503, "CATALOG_UNAVAILABLE", "Catalog integrity check failed")
        try:
            document = CatalogDocument.model_validate(
                json.loads(data.decode("utf-8"), object_pairs_hook=_unique_object)
            )
        except (ValueError, ValidationError, RecursionError):
            raise AppError(503, "CATALOG_UNAVAILABLE", "Catalog is not valid") from None
        registry = load_registry(config_dir)
        if (
            document.feature_contract_version != registry.contract_version
            or set(document.required_groups) - set(registry.groups)
            or len(document.required_groups) != len(set(document.required_groups))
        ):
            raise AppError(503, "CATALOG_UNAVAILABLE", "Catalog feature contract is incompatible")
        self._data = bytes(data)
        self._document = document
        self.sha256 = expected_sha256
        self.version = document.catalog_version
        self.config_dir = config_dir

    @property
    def snapshot_count(self) -> int:
        return len(self._document.snapshots)

    @property
    def feature_contract_version(self) -> str:
        return self._document.feature_contract_version

    def store(self, storage: LocalStorage) -> str:
        return storage.put(self._data)

    def validate_release(self, release: PinnedRelease) -> None:
        document = self._document
        if (
            release.feature_contract_version != document.feature_contract_version
            or set(release.feature_groups) != set(document.required_groups)
            or len(release.feature_groups) != len(set(release.feature_groups))
        ):
            raise AppError(503, "MODEL_UNAVAILABLE", "Model and catalog feature contracts differ")

    def prepare(self, data: bytes, release: PinnedRelease, storage: LocalStorage) -> PreparedImport:
        """Validate every referenced payload, then store checksum-verifiable immutable objects."""
        document = self._document
        self.validate_release(release)
        release_json = canonical_json(release.model_dump())
        file_hash = hashlib.sha256(data).hexdigest()
        try:
            sources = parse_import(data, snapshot_ids=document.snapshots)
        except InvalidImport as error:
            return PreparedImport(
                file_hash,
                self.sha256,
                self.version,
                release_json,
                (),
                tuple(error.issues),
                error.truncated,
            )
        issues: list[ImportIssue] = []
        prepared: list[PreparedRow] = []
        payloads: dict[str, bytes] = {}
        for number, source in enumerate(sources, start=2):
            raw = document.snapshots[source.snapshot_id]
            try:
                payload = canonical_json(raw)
                if len(payload) > 5_000_000 or raw.get("request_id") is not None:
                    raise ValueError
                parsed = request_model(self.config_dir).model_validate(raw)
                normalized = parsed.model_dump(exclude_none=True)
                if (
                    normalized["contract_version"] != release.feature_contract_version
                    or normalized["explain"] is not True
                ):
                    raise ValueError
                history = normalized["history"]
                # Validate even unused supplied sources: invalid history is not no-history.
                validate_history_records(history, list(SOURCE_GROUPS.values()))
                _validate_parent_references(history)
                required = {
                    GROUP_SOURCE[group]
                    for group in release.feature_groups
                    if group != "application"
                }
                for group in release.feature_groups:
                    required.update(GROUP_EXTRA_TABLES.get(group, []))
                availability = {
                    name: history.get(name, {}).get("status", "not_provided")
                    for name in SOURCE_GROUPS
                }
                missing = tuple(
                    sorted(name for name in required if availability[name] != "provided")
                )
                payload_hash = hashlib.sha256(payload).hexdigest()
                prepared.append(
                    PreparedRow(source, payload_hash, canonical_json(availability), missing)
                )
                payloads[payload_hash] = payload
            except (ValueError, ValidationError, ServiceError):
                issues.append(ImportIssue(number, "snapshot_id", "INVALID_SNAPSHOT"))
        if not issues:
            # Storage precedes DB references; a failed transaction may leave harmless orphans,
            # never a committed row pointing at an object that was not durably written.
            storage.put(self._data)
            storage.put(data)
            for payload in payloads.values():
                storage.put(payload)
        return PreparedImport(
            file_hash,
            self.sha256,
            self.version,
            release_json,
            tuple(prepared) if not issues else (),
            tuple(issues),
        )


def _validate_parent_references(history: dict[str, Any]) -> None:
    """Raw relational acquisition check also applies to INCOMPLETE snapshots.

    Legacy online feature building checks these links only after rejecting missing
    sources. Keep the same raw FK semantics without fabricating a missing source
    or invoking inference/SQL to classify an otherwise valid incomplete snapshot.
    """
    bureau = {row["bureau_ref"] for row in history.get("bureau", {}).get("records", [])}
    previous = {
        row["prev_ref"] for row in history.get("previous_applications", {}).get("records", [])
    }
    for row in history.get("bureau_balance", {}).get("records", []):
        if row["bureau_ref"] not in bureau:
            raise ValueError("Invalid parent reference")
    for name in ("installments", "pos_cash", "credit_card"):
        for row in history.get(name, {}).get("records", []):
            if row.get("prev_ref") is not None and row["prev_ref"] not in previous:
                raise ValueError("Invalid parent reference")
