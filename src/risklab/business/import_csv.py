"""Bounded demo-import/1 parser. Pure validation: never fetch snapshots, URLs or paths.

The application service must validate the entire intent (including replacement
permissions and snapshot availability) before committing any application rows.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from collections.abc import Collection
from dataclasses import dataclass
from uuid import UUID

IMPORT_CONTRACT = "demo-import/1"
HEADER = ["external_reference", "snapshot_id", "supersedes_application_id"]
MAX_FILE_BYTES = 1_048_576
MAX_ROWS = 100
MAX_ERRORS = 100
REFERENCE = re.compile(r"[A-Za-z0-9_-]{1,64}")


@dataclass(frozen=True)
class ImportIssue:
    row: int | None
    field: str | None
    code: str


@dataclass(frozen=True)
class ImportRow:
    external_reference: str
    snapshot_id: str
    supersedes_application_id: UUID | None


class InvalidImport(ValueError):
    def __init__(self, issues: list[ImportIssue], *, truncated: bool = False) -> None:
        super().__init__("Import validation failed")
        self.issues = issues[:MAX_ERRORS]
        self.truncated = truncated or len(issues) > MAX_ERRORS


def parse_import(data: bytes, *, snapshot_ids: Collection[str]) -> list[ImportRow]:
    """Reject the whole file on any error; row numbers are CSV records (header = 1)."""
    if len(data) > MAX_FILE_BYTES:
        raise InvalidImport([ImportIssue(None, None, "PAYLOAD_TOO_LARGE")])
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise InvalidImport([ImportIssue(None, None, "INVALID_CSV")]) from None
    if "\0" in text:
        raise InvalidImport([ImportIssue(None, None, "INVALID_CSV")])
    reader = csv.reader(io.StringIO(text, newline=""), strict=True)
    errors: list[ImportIssue] = []
    rows: list[ImportRow] = []
    seen: set[str] = set()
    try:
        if next(reader, None) != HEADER:
            raise InvalidImport([ImportIssue(1, None, "INVALID_HEADER")])
        count = 0
        for number, cells in enumerate(reader, start=2):
            count += 1
            if count > MAX_ROWS:
                raise InvalidImport([*errors, ImportIssue(number, None, "ROW_LIMIT_EXCEEDED")])
            if len(cells) != len(HEADER):
                errors.append(ImportIssue(number, None, "INVALID_CSV"))
                continue
            reference, snapshot, replacement = cells
            before = len(errors)
            for field, value in (("external_reference", reference), ("snapshot_id", snapshot)):
                if REFERENCE.fullmatch(value) is None:
                    errors.append(ImportIssue(number, field, "INVALID_REFERENCE"))
            if reference in seen:
                errors.append(ImportIssue(number, "external_reference", "DUPLICATE_REFERENCE"))
            seen.add(reference)
            # Lookup keys only. Do not turn an unknown ID into a file/URL fallback.
            if REFERENCE.fullmatch(snapshot) and snapshot not in snapshot_ids:
                errors.append(ImportIssue(number, "snapshot_id", "UNKNOWN_SNAPSHOT"))
            replacement_id = None
            if replacement:
                try:
                    replacement_id = UUID(replacement)
                    if str(replacement_id) != replacement:
                        raise ValueError
                except ValueError:
                    errors.append(
                        ImportIssue(number, "supersedes_application_id", "INVALID_REPLACEMENT")
                    )
            if len(errors) == before:
                rows.append(ImportRow(reference, snapshot, replacement_id))
        if count == 0:
            errors.append(ImportIssue(None, None, "ROW_LIMIT_EXCEEDED"))
    except csv.Error:
        errors.append(ImportIssue(None, None, "INVALID_CSV"))
    if errors:
        raise InvalidImport(errors)
    return rows


def import_fingerprint(data: bytes, *, catalog_version: str) -> str:
    """Hash semantic envelope + exact file bytes; ignore multipart boundaries/filenames."""
    payload = {
        "import_contract_version": IMPORT_CONTRACT,
        "catalog_version": catalog_version,
        "file_sha256": hashlib.sha256(data).hexdigest(),
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
