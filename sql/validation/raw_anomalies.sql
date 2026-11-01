-- Draft raw-data checks for docs/data_contract.md (formal validation is Phase 1).
-- Run with: uv run python scripts/raw_anomalies.py
-- Each block is one query returning (metric, value). Views named after the tables exist
-- over data/parquet/<table>.parquet; `applications` is application_train UNION ALL application_test.

-- name: key_uniqueness
SELECT 'application_train.dup_SK_ID_CURR' AS metric,
       count(*) - count(DISTINCT SK_ID_CURR) AS value FROM application_train
UNION ALL SELECT 'application_test.dup_SK_ID_CURR',
       count(*) - count(DISTINCT SK_ID_CURR) FROM application_test
UNION ALL SELECT 'train_test.overlap_SK_ID_CURR', count(*)
       FROM application_train t SEMI JOIN application_test s USING (SK_ID_CURR)
UNION ALL SELECT 'bureau.dup_SK_ID_BUREAU',
       count(*) - count(DISTINCT SK_ID_BUREAU) FROM bureau
UNION ALL SELECT 'previous_application.dup_SK_ID_PREV',
       count(*) - count(DISTINCT SK_ID_PREV) FROM previous_application;

-- name: grain_duplicates
SELECT 'bureau_balance.dup_(SK_ID_BUREAU,MONTHS_BALANCE)' AS metric,
       (SELECT count(*) FROM bureau_balance)
       - (SELECT count(*) FROM (SELECT DISTINCT SK_ID_BUREAU, MONTHS_BALANCE FROM bureau_balance)) AS value
UNION ALL SELECT 'pos_cash_balance.dup_(SK_ID_PREV,MONTHS_BALANCE)',
       (SELECT count(*) FROM pos_cash_balance)
       - (SELECT count(*) FROM (SELECT DISTINCT SK_ID_PREV, MONTHS_BALANCE FROM pos_cash_balance))
UNION ALL SELECT 'credit_card_balance.dup_(SK_ID_PREV,MONTHS_BALANCE)',
       (SELECT count(*) FROM credit_card_balance)
       - (SELECT count(*) FROM (SELECT DISTINCT SK_ID_PREV, MONTHS_BALANCE FROM credit_card_balance))
UNION ALL SELECT 'installments_payments.dup_(SK_ID_PREV,NUM_INSTALMENT_VERSION,NUM_INSTALMENT_NUMBER)',
       (SELECT count(*) FROM installments_payments)
       - (SELECT count(*) FROM (SELECT DISTINCT SK_ID_PREV, NUM_INSTALMENT_VERSION, NUM_INSTALMENT_NUMBER
                                FROM installments_payments));

-- name: fk_coverage
SELECT 'bureau.rows_SK_ID_CURR_not_in_applications' AS metric, count(*) AS value
       FROM bureau b ANTI JOIN applications a USING (SK_ID_CURR)
UNION ALL SELECT 'bureau_balance.rows_SK_ID_BUREAU_not_in_bureau', count(*)
       FROM bureau_balance b ANTI JOIN bureau u USING (SK_ID_BUREAU)
UNION ALL SELECT 'bureau.distinct_SK_ID_BUREAU_without_bureau_balance', count(*)
       FROM (SELECT DISTINCT SK_ID_BUREAU FROM bureau) b ANTI JOIN (SELECT DISTINCT SK_ID_BUREAU FROM bureau_balance) u USING (SK_ID_BUREAU)
UNION ALL SELECT 'previous_application.rows_SK_ID_CURR_not_in_applications', count(*)
       FROM previous_application p ANTI JOIN applications a USING (SK_ID_CURR)
UNION ALL SELECT 'pos_cash_balance.rows_SK_ID_PREV_not_in_previous_application', count(*)
       FROM pos_cash_balance c ANTI JOIN previous_application p USING (SK_ID_PREV)
UNION ALL SELECT 'credit_card_balance.rows_SK_ID_PREV_not_in_previous_application', count(*)
       FROM credit_card_balance c ANTI JOIN previous_application p USING (SK_ID_PREV)
UNION ALL SELECT 'installments_payments.rows_SK_ID_PREV_not_in_previous_application', count(*)
       FROM installments_payments c ANTI JOIN previous_application p USING (SK_ID_PREV)
UNION ALL SELECT 'pos_cash_balance.rows_SK_ID_CURR_not_in_applications', count(*)
       FROM pos_cash_balance c ANTI JOIN applications a USING (SK_ID_CURR)
UNION ALL SELECT 'credit_card_balance.rows_SK_ID_CURR_not_in_applications', count(*)
       FROM credit_card_balance c ANTI JOIN applications a USING (SK_ID_CURR)
UNION ALL SELECT 'installments_payments.rows_SK_ID_CURR_not_in_applications', count(*)
       FROM installments_payments c ANTI JOIN applications a USING (SK_ID_CURR)
UNION ALL SELECT 'pos_cash_balance.rows_SK_ID_CURR_differs_from_previous_application', count(*)
       FROM pos_cash_balance c JOIN previous_application p USING (SK_ID_PREV) WHERE c.SK_ID_CURR <> p.SK_ID_CURR
UNION ALL SELECT 'credit_card_balance.rows_SK_ID_CURR_differs_from_previous_application', count(*)
       FROM credit_card_balance c JOIN previous_application p USING (SK_ID_PREV) WHERE c.SK_ID_CURR <> p.SK_ID_CURR
UNION ALL SELECT 'installments_payments.rows_SK_ID_CURR_differs_from_previous_application', count(*)
       FROM installments_payments c JOIN previous_application p USING (SK_ID_PREV) WHERE c.SK_ID_CURR <> p.SK_ID_CURR;

-- name: applicants_without_history
SELECT 'applications.total' AS metric, count(*) AS value FROM applications
UNION ALL SELECT 'applications.without_bureau', count(*)
       FROM applications a ANTI JOIN (SELECT DISTINCT SK_ID_CURR FROM bureau) b USING (SK_ID_CURR)
UNION ALL SELECT 'applications.without_previous_application', count(*)
       FROM applications a ANTI JOIN (SELECT DISTINCT SK_ID_CURR FROM previous_application) p USING (SK_ID_CURR)
UNION ALL SELECT 'applications.without_bureau_and_previous', count(*)
       FROM applications a
       ANTI JOIN (SELECT DISTINCT SK_ID_CURR FROM bureau) b USING (SK_ID_CURR)
       ANTI JOIN (SELECT DISTINCT SK_ID_CURR FROM previous_application) p USING (SK_ID_CURR);

-- name: target
SELECT 'application_train.rows' AS metric, count(*)::DOUBLE AS value FROM application_train
UNION ALL SELECT 'application_train.target_null', count(*) FILTER (WHERE TARGET IS NULL) FROM application_train
UNION ALL SELECT 'application_train.target_positive', count(*) FILTER (WHERE TARGET = 1) FROM application_train
UNION ALL SELECT 'application_train.target_prevalence', avg(TARGET::DOUBLE) FROM application_train
UNION ALL SELECT 'application_train.target_other_values', count(*) FILTER (WHERE TARGET NOT IN (0, 1)) FROM application_train
UNION ALL SELECT 'application_train.DAYS_EMPLOYED_365243_target_prevalence',
       avg(TARGET::DOUBLE) FILTER (WHERE DAYS_EMPLOYED = 365243) FROM application_train
UNION ALL SELECT 'application_train.DAYS_EMPLOYED_not_365243_target_prevalence',
       avg(TARGET::DOUBLE) FILTER (WHERE DAYS_EMPLOYED <> 365243) FROM application_train;
