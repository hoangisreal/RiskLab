# Synthetic business/import contract fixtures

These fixtures are handwritten synthetic examples, not real customer data or a
deployed catalog. The runtime catalog is prepared separately by demo-init.

- `catalog.json`: three example snapshots using raw scoring contract 1.1. IDs
  are opaque lookup keys, not filesystem paths.
- `import-valid.csv`: two READY cases and one INCOMPLETE case that cannot be scored.
- `import-unknown-snapshot.csv`: rejects an unknown snapshot without fetching a URL/path.
- `import-duplicate-reference.csv`: rejects the entire import for duplicate references.
- `import-path-injection.csv`: rejects a path presented as a snapshot ID.
- `import-extra-column.csv`: rejects headers/labels outside the contract.
- `invalid-history.json`: rejects a future realized event; excluded from the
  trusted catalog. Invalid data differs from an unavailable source or confirmed
  absence of history.

Enforced contracts: [business schemas](../../../src/risklab/business/schemas.py)
and [import parser](../../../src/risklab/business/import_csv.py).

Schema/shared-SQL tests check each fixture's semantics. Importer, worker and
PostgreSQL verification uses the [import tests](../../business/test_imports.py)
and [workflow tests](../../business/test_workflow.py). A fixture alone does not
establish acceptance of the application or production environment.
