"""Exercise the real bounded parser, not just fixture shape assertions."""

import csv
import io
from itertools import product
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

import pytest

from risklab.business.import_csv import HEADER, InvalidImport, import_fingerprint, parse_import
from tests.conftest import REPO_ROOT

pytestmark = pytest.mark.unit
FIXTURES = REPO_ROOT / "tests/fixtures/business_v1"
SNAPSHOTS = {"demo-history", "demo-no-history", "demo-missing-history"}
CSV_HEADER = ",".join(HEADER) + "\n"


@pytest.mark.parametrize(
    "filename,code",
    [
        ("import-unknown-snapshot.csv", "UNKNOWN_SNAPSHOT"),
        ("import-duplicate-reference.csv", "DUPLICATE_REFERENCE"),
        ("import-path-injection.csv", "INVALID_REFERENCE"),
        ("import-extra-column.csv", "INVALID_HEADER"),
    ],
)
def test_invalid_fixtures_rejected_without_partial_return(filename, code):
    with pytest.raises(InvalidImport) as error:
        parse_import((FIXTURES / filename).read_bytes(), snapshot_ids=SNAPSHOTS)
    assert code in {issue.code for issue in error.value.issues}
    assert all(issue.field in {*HEADER, None} for issue in error.value.issues)


@pytest.mark.parametrize("encoding,newline", [("utf-8", "\n"), ("utf-8-sig", "\r\n")])
def test_valid_import_preserves_missing_history_as_lookup_not_a_default(encoding, newline):
    text = (FIXTURES / "import-valid.csv").read_text().replace("\n", newline)
    result = parse_import(text.encode(encoding), snapshot_ids=SNAPSHOTS)
    assert len(result) == 3
    assert result[2].snapshot_id == "demo-missing-history"
    assert result[2].supersedes_application_id is None


@pytest.mark.parametrize(
    "data,code",
    [
        (b"", "INVALID_HEADER"),
        (CSV_HEADER.encode(), "ROW_LIMIT_EXCEEDED"),
        ((CSV_HEADER + "\n").encode(), "INVALID_CSV"),
        ((CSV_HEADER + '"unclosed,demo-history,').encode(), "INVALID_CSV"),
        ((CSV_HEADER + "A,demo-history,,extra\n").encode(), "INVALID_CSV"),
        ((CSV_HEADER + "A,demo-history\n").encode(), "INVALID_CSV"),
        ((CSV_HEADER + " A,demo-history,\n").encode(), "INVALID_REFERENCE"),
        ((CSV_HEADER + "=FORMULA,demo-history,\n").encode(), "INVALID_REFERENCE"),
        ((CSV_HEADER + "A,https://attacker.invalid/payload,\n").encode(), "INVALID_REFERENCE"),
        ((CSV_HEADER + "A,demo-history,not-a-uuid\n").encode(), "INVALID_REPLACEMENT"),
        ((CSV_HEADER + "A,demo-history,\0\n").encode(), "INVALID_CSV"),
        (b"\xff", "INVALID_CSV"),
        (b"a" * 1_048_577, "PAYLOAD_TOO_LARGE"),
    ],
)
def test_untrusted_bytes_and_cells_have_safe_errors(data, code):
    with pytest.raises(InvalidImport) as error:
        parse_import(data, snapshot_ids=SNAPSHOTS)
    assert code in {issue.code for issue in error.value.issues}
    assert str(error.value) == "Import validation failed"
    assert "attacker.invalid" not in repr(error.value.issues)


def test_row_limit_and_canonical_replacement_identity():
    identifier = str(uuid4())
    hundred = CSV_HEADER + "".join(f"REF-{i},demo-history,{identifier}\n" for i in range(100))
    rows = parse_import(hundred.encode(), snapshot_ids=SNAPSHOTS)
    assert len(rows) == 100
    assert rows[0].supersedes_application_id == UUID(identifier)
    with pytest.raises(InvalidImport) as error:
        parse_import((hundred + "EXTRA,demo-history,\n").encode(), snapshot_ids=SNAPSHOTS)
    assert error.value.issues[-1].code == "ROW_LIMIT_EXCEEDED"


def test_error_report_truncation_and_fingerprint_changes():
    invalid = CSV_HEADER + "".join("unsafe reference,unknown,invalid\n" for _ in range(100))
    with pytest.raises(InvalidImport) as error:
        parse_import(invalid.encode(), snapshot_ids=SNAPSHOTS)
    assert len(error.value.issues) == 100
    assert error.value.truncated
    data = (FIXTURES / "import-valid.csv").read_bytes()
    assert import_fingerprint(data, catalog_version="v1") == import_fingerprint(
        data, catalog_version="v1"
    )
    assert import_fingerprint(data, catalog_version="v1") != import_fingerprint(
        data, catalog_version="v2"
    )
    assert import_fingerprint(data, catalog_version="v1") != import_fingerprint(
        data + b"\n", catalog_version="v1"
    )


@pytest.mark.parametrize(
    "count,quoting,newline,bom",
    list(product((1, 7, 100), (csv.QUOTE_MINIMAL, csv.QUOTE_ALL), ("\n", "\r\n"), (False, True))),
)
def test_csv_writer_roundtrip_preserves_valid_intent(count, quoting, newline, bom):
    # Independent serializer oracle, across boundary sizes/quoting/line endings/encoding.
    original = [
        [
            f"REF-{i}-".ljust(64, "A"),
            "demo-history",
            str(uuid5(NAMESPACE_URL, str(i))) if i % 3 else "",
        ]
        for i in range(count)
    ]
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, quoting=quoting, lineterminator=newline)
    writer.writerow(HEADER)
    writer.writerows(original)
    data = buffer.getvalue().encode("utf-8-sig" if bom else "utf-8")
    result = parse_import(data, snapshot_ids=SNAPSHOTS)
    restored = [
        [
            row.external_reference,
            row.snapshot_id,
            str(row.supersedes_application_id) if row.supersedes_application_id else "",
        ]
        for row in result
    ]
    assert restored == original
