# Data contract (DRAFT)

Draft status: counts below were computed once from the real Parquet files. Formal, automated validation is Phase 1 (`validate`). Nothing here is a tested guarantee yet.

Sources of the numbers:

- Row and column counts: `data/manifest.json` (written by `risklab ingest`; run `uv run risklab ingest`).
- Anomaly and key counts: `uv run python scripts/raw_anomalies.py`, which runs `sql/validation/raw_anomalies.sql` plus generated per-column scans over `data/parquet/*.parquet`. Output: `reports/validation/raw_anomalies.json` (gitignored).
- Column names and types: `configs/raw_schema.yaml` (the ingest contract).

All time fields (`DAYS_*`, `MONTHS_BALANCE`) are relative to the application date of the current loan (`SK_ID_CURR`). There is no absolute date. Negative `DAYS_*` means before the application. `MONTHS_BALANCE` is in months before the application.

## Tables

| Table | Grain | Primary key | Foreign keys | Rows | Columns |
|---|---|---|---|---:|---:|
| application_train | one application (current loan) | `SK_ID_CURR` | none | 307,511 | 122 |
| application_test | one application, no `TARGET` | `SK_ID_CURR` | none | 48,744 | 121 |
| bureau | one credit at another institution | `SK_ID_BUREAU` | `SK_ID_CURR` -> applications | 1,716,428 | 17 |
| bureau_balance | one month of one bureau credit | (`SK_ID_BUREAU`, `MONTHS_BALANCE`) | `SK_ID_BUREAU` -> bureau | 27,299,925 | 3 |
| previous_application | one earlier Home Credit application | `SK_ID_PREV` | `SK_ID_CURR` -> applications | 1,670,214 | 37 |
| pos_cash_balance | one month of one POS/cash loan | (`SK_ID_PREV`, `MONTHS_BALANCE`) | `SK_ID_PREV` -> previous_application, `SK_ID_CURR` | 10,001,358 | 8 |
| credit_card_balance | one month of one credit card | (`SK_ID_PREV`, `MONTHS_BALANCE`) | `SK_ID_PREV` -> previous_application, `SK_ID_CURR` | 3,840,312 | 23 |
| installments_payments | one installment payment record | none unique (see below) | `SK_ID_PREV` -> previous_application, `SK_ID_CURR` | 13,605,401 | 8 |

"applications" means `application_train` union `application_test`. Parquet row counts equal CSV record counts for every table, including `wc -l` minus header (no embedded newlines were found in these files).

## Key checks

- `SK_ID_CURR` is unique within train and within test, and the two sets do not overlap (0 duplicates, 0 overlap). `SK_ID_BUREAU` in bureau and `SK_ID_PREV` in previous_application are unique.
- No duplicates on (`SK_ID_BUREAU`, `MONTHS_BALANCE`), (`SK_ID_PREV`, `MONTHS_BALANCE`) for POS and credit card.
- **installments_payments has no unique key.** On (`SK_ID_PREV`, `NUM_INSTALMENT_VERSION`, `NUM_INSTALMENT_NUMBER`) there are 640,905 key groups with more than one row, covering 1,294,388 rows; 653,483 rows exceed one per key. There are 0 exact full-row duplicates. Within every such group `DAYS_INSTALMENT` is identical, and in 640,238 groups `DAYS_ENTRY_PAYMENT` differs, which is consistent with one installment paid in several parts. Aggregations must not assume one row per installment (for example, sum `AMT_PAYMENT` per installment before comparing it with `AMT_INSTALMENT`).

## Foreign key coverage

| Check | Count |
|---|---:|
| bureau rows with `SK_ID_CURR` not in applications | 0 |
| bureau_balance rows with `SK_ID_BUREAU` not in bureau | 3,120,184 |
| bureau credits (distinct `SK_ID_BUREAU`) with no bureau_balance rows | 942,074 |
| previous_application rows with `SK_ID_CURR` not in applications | 0 |
| pos_cash_balance rows with `SK_ID_PREV` not in previous_application | 340,561 |
| credit_card_balance rows with `SK_ID_PREV` not in previous_application | 1,082,816 |
| installments_payments rows with `SK_ID_PREV` not in previous_application | 1,250,826 |
| POS / credit card / installments rows with `SK_ID_CURR` not in applications | 0 / 0 / 0 |
| POS / credit card / installments rows whose `SK_ID_CURR` differs from previous_application (matched rows) | 0 / 0 / 0 |

Ingest keeps every record: orphan rows and duplicate installment keys are present in the Parquet files (Parquet row counts equal CSV record counts). Nothing is filtered in Phase 0.

Consequence for later phases: inner joins from bureau_balance to bureau, and from the three monthly tables to previous_application, would drop these rows silently. Orphan bureau_balance rows cannot be linked to any applicant, because bureau_balance has no `SK_ID_CURR`. Monthly-table orphans can still be keyed on `SK_ID_CURR` (present and consistent in those tables). How to handle each case, and how to report the excluded counts, is a Phase 1 validation decision.

The orphan and duplicate counts above were cross-checked on 2026-11-01 by an independent DuckDB pass over the raw CSVs (not the Parquet files); all counts matched.

Applicants without history (356,255 applications in total):

| Group | Count |
|---|---:|
| without bureau | 50,444 |
| without previous_application | 17,398 |
| without both | 2,678 |

## Time fields and sentinels

- `365243` is a sentinel (about 1000 years), not a real value.
  - `DAYS_EMPLOYED`: 55,374 rows in train and 9,274 in test. `ORGANIZATION_TYPE = 'XNA'` has the same counts (55,374 / 9,274), which suggests the same rows (not verified row by row). TARGET prevalence is 5.40% for the sentinel rows and 8.66% for the others.
  - previous_application: `DAYS_FIRST_DRAWING` 934,444; `DAYS_FIRST_DUE` 40,645; `DAYS_LAST_DUE_1ST_VERSION` 93,864; `DAYS_LAST_DUE` 211,221; `DAYS_TERMINATION` 225,913.
- Positive values (excluding the sentinel) in fields that describe realized events:
  - `bureau.DAYS_ENDDATE_FACT` > 0: 0. `installments_payments.DAYS_ENTRY_PAYMENT` > 0: 0. These are safe as "already happened" fields.
  - `MONTHS_BALANCE` > 0: 0 in all three monthly tables. `MONTHS_BALANCE` = 0 occurs only in bureau_balance (610,965 rows); POS and credit card have max -1.
- Positive values in fields that are planned or updated dates (not realized events):
  - `bureau.DAYS_CREDIT_ENDDATE` > 0: 602,603 rows (planned end of active credits).
  - `bureau.DAYS_CREDIT_UPDATE` > 0: 17 rows (max 372).
  - `previous_application.DAYS_LAST_DUE_1ST_VERSION` > 0 excluding sentinel: 224,392 rows (planned dates, max 2,389).
- Application-level `DAYS_REGISTRATION`, `DAYS_ID_PUBLISH`, `DAYS_LAST_PHONE_CHANGE` have max 0 (no positives).
- Some `DAYS_*` columns are DOUBLE in the raw files (decimal-formatted); declared types are in `raw_schema.yaml`.

## `XNA` category values

`XNA` means "not applicable". Counts of `XNA` (all other VARCHAR columns have none):

| Column | Count |
|---|---:|
| application_train.CODE_GENDER | 4 |
| application_train.ORGANIZATION_TYPE / application_test.ORGANIZATION_TYPE | 55,374 / 9,274 |
| previous_application.NAME_CASH_LOAN_PURPOSE | 677,918 |
| previous_application.NAME_PAYMENT_TYPE | 627,384 |
| previous_application.NAME_GOODS_CATEGORY | 950,809 |
| previous_application.NAME_PORTFOLIO | 372,230 |
| previous_application.NAME_PRODUCT_TYPE | 1,063,666 |
| previous_application.NAME_SELLER_INDUSTRY | 855,720 |
| previous_application.NAME_YIELD_GROUP | 517,215 |
| previous_application.CODE_REJECT_REASON | 5,244 |
| previous_application.NAME_CLIENT_TYPE | 1,941 |
| previous_application.NAME_CONTRACT_TYPE | 346 |
| pos_cash_balance.NAME_CONTRACT_STATUS | 2 |

`CODE_GENDER = 'XNA'` occurs only in train (4 rows).

## TARGET

`application_train`: 307,511 rows, 0 NULL, values only 0/1, 24,825 positives, prevalence 8.073%. Meaning: client had payment difficulties, not default or loss.

## Other raw-data notes

- `EMERGENCYSTATE_MODE` is `Yes`/`No` text and is kept as VARCHAR.
- Empty CSV fields become NULL. Ingest fails on any value that does not match its declared type.
- Parquet files are not guaranteed byte-identical across runs (`preserve_insertion_order=false`). The raw-file SHA256 and row counts in the manifest are the exact references.

## Cleaning rules

Defined in `configs/cleaning.yaml`, applied by `risklab.data.clean.clean_application(table, cfg)`.

| Rule | Effect |
|---|---|
| `DAYS_EMPLOYED = 365243` | set to NULL; integer flag `DAYS_EMPLOYED_IS_SENTINEL` = 1 (0 otherwise, also 0 when the source is NULL) |
| `XNA` in `CODE_GENDER`, `ORGANIZATION_TYPE` | set to NULL (exact match only; `'XNA '` and `'xna'` are untouched) |

Rationale:

- The sentinel and `XNA` mean "not applicable" (for example pensioners have no employment date), not a real value. As NULL they cannot distort ratios or scaling. The flag keeps the signal, because the sentinel group has a different default rate (see above).
- The rules are strictly row-wise: each output value depends only on the same row, with no statistic computed across rows. Cleaning is therefore safe to run before partitioning without leakage. Rows are never dropped, added or reordered, and `SK_ID_CURR` and `TARGET` are passed through untouched. A unit test checks that cleaning the whole table equals cleaning its chunks separately.
- The online (API) path reuses the same function on a single-row table.
- Cleaned data is not yet wired into the loaders and no cleaned Parquet is written.

## Validation

`risklab validate` (`make validate`) checks `application_train` and `application_test` (not the partitions) after verifying the Parquet hashes against `data/manifest.json`. Rules: `configs/application_validation.yaml`; categorical domains: `configs/application_domains.yaml` (derived once from the real train Parquet by `scripts/derive_application_domains.py`, then reviewed). The report is `reports/validation/application.json` (gitignored) and the step manifest is `reports/manifests/validate.json`. The command exits 1 if any `error` check fails.

Finding on the real data: one `application_test` row (`SK_ID_CURR` 224393) has `REGION_RATING_CLIENT_W_CITY = -1`; the domain is {1, 2, 3} everywhere else (train has no such value). The `range:region_rating` check is not loosened. Instead, `known_exceptions` in `configs/application_validation.yaml` accepts this one row explicitly.

Known-exceptions mechanism:

- Each entry has `table`, `check` (an exact rule check name: `categorical_domain` or `range:<rule>`), a non-empty unique `ids` list of `SK_ID_CURR`, and a non-empty `reason`. At most one entry per (table, check). The config is validated by pydantic (unknown keys are rejected).
- An offending row is accepted only if its table, check and id all match. All other offending rows keep the check's severity, so a second bad row in the same check still fails (exit 1).
- Accepted rows appear under `accepted_exceptions` (`ids`, `count`, `reason`) in the report and CLI output. The check `status` is `passed_with_exceptions` only if every offending row was accepted. The summary counts these checks in `accepted_exceptions`.
- An entry that matches no offending row is reported as a `stale_known_exception` warning, so fixed or removed data is noticed.

Accepted row: `SK_ID_CURR` 224393 in `application_test`, check `range:region_rating`. The value -1 is an undocumented code in an unlabeled row used only for drift monitoring. The value is retained unchanged, and the API contract must still reject it.

## Modelling inputs

Policy: `configs/features_application.yaml`; code: `risklab.features.application`.

- Rows: `load_application_frame(role, purpose)` reads a partition only through `load_partition` (never Test), applies `clean_application`, drops the excluded columns and returns X and y indexed by `SK_ID_CURR`.
- Excluded from X: `SK_ID_CURR` (identifier), `TARGET` (label) and `CODE_GENDER`. Excluding `CODE_GENDER` is the default policy while the owner decision on gender as a model input is open (see `docs/PROGRESS.md`). Changing the list changes every downstream result.
- Cleaning before modelling: the rules above. `DAYS_EMPLOYED` sentinel and `XNA` become NULL (NaN in X) and `DAYS_EMPLOYED_IS_SENTINEL` is added as a numeric column.
- Numeric vs categorical comes from the types in `raw_schema.yaml` only: `VARCHAR` is categorical, everything else numeric (plus the cleaning flag columns). Column order follows the schema. Numeric columns are float64 and categorical columns are object, with NaN for NULL, whichever values a given partition happens to contain.
- Log-transformed (Logistic Regression only): numeric columns starting with `AMT_`, except the `AMT_REQ_CREDIT_BUREAU_*` enquiry counts. Negatives are clipped to 0 before `log1p`.
- All statistics across applicants (medians, scaling, category sets) are learned inside the sklearn pipelines in `risklab.models.pipelines`, fitted on Train only.
