# Synthetic business/import contract examples

These are **hand-authored synthetic fixtures**, not a deployed catalog or real
applicant data. The business API/importer is not implemented by these files.

- `catalog.json`: three versioned server-owned snapshot examples with raw scoring
  contract 1.1 payloads. IDs are opaque lookup keys, never filesystem paths.
- `import-valid.csv`: two READY examples plus one INCOMPLETE example that must not score.
- `import-unknown-snapshot.csv`: reject unknown snapshot; do not fetch a URL/path.
- `import-duplicate-reference.csv`: reject the whole import, not just the second row.
- `import-path-injection.csv`: reject a path masquerading as a snapshot ID.
- `import-extra-column.csv`: reject unexpected headers/labels.
- `invalid-history.json`: reject the future realized event; deliberately excluded
  from the trusted catalog. Invalid data is not equivalent to missing history.

Canonical specifications: [business API](../../../docs/business_api_contract.md)
and [demo import](../../../docs/demo_import_contract.md).

Tests validate catalog payloads through the existing scoring schema, relational
validation and actual shared SQL feature builder. CSV example checks only verify
fixture intent; they are not acceptance tests for a production importer. B03
must exercise the same examples through the implemented import API/worker and DB.
