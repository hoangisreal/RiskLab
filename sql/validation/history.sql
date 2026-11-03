-- name: bureau_duplicate_ids
SELECT COUNT(*) - COUNT(DISTINCT SK_ID_BUREAU) FROM bureau;
-- name: previous_duplicate_ids
SELECT COUNT(*) - COUNT(DISTINCT SK_ID_PREV) FROM previous_application;
-- name: bureau_balance_duplicate_months
SELECT COUNT(*) - (SELECT COUNT(*) FROM (SELECT DISTINCT SK_ID_BUREAU, MONTHS_BALANCE FROM bureau_balance)) FROM bureau_balance;
-- name: pos_duplicate_months
SELECT COUNT(*) - (SELECT COUNT(*) FROM (SELECT DISTINCT SK_ID_PREV, MONTHS_BALANCE FROM pos_cash_balance)) FROM pos_cash_balance;
-- name: card_duplicate_months
SELECT COUNT(*) - (SELECT COUNT(*) FROM (SELECT DISTINCT SK_ID_PREV, MONTHS_BALANCE FROM credit_card_balance)) FROM credit_card_balance;
-- name: installment_exact_duplicates
SELECT COUNT(*) - (SELECT COUNT(*) FROM (SELECT DISTINCT * FROM installments_payments)) FROM installments_payments;
-- name: installment_partial_payment_rows
SELECT COUNT(*) - (SELECT COUNT(*) FROM (SELECT DISTINCT SK_ID_CURR, SK_ID_PREV, NUM_INSTALMENT_VERSION, NUM_INSTALMENT_NUMBER FROM installments_payments)) FROM installments_payments;
-- name: installment_due_date_conflicts
SELECT COUNT(*) FROM (SELECT SK_ID_CURR, SK_ID_PREV, NUM_INSTALMENT_VERSION, NUM_INSTALMENT_NUMBER FROM installments_payments GROUP BY ALL HAVING COUNT(DISTINCT DAYS_INSTALMENT) > 1);
-- name: installment_due_amount_variations
SELECT COUNT(*) FROM (SELECT SK_ID_CURR, SK_ID_PREV, NUM_INSTALMENT_VERSION, NUM_INSTALMENT_NUMBER FROM installments_payments GROUP BY ALL HAVING MAX(AMT_INSTALMENT) - MIN(AMT_INSTALMENT) > {installment_amount_tolerance});
-- name: bureau_balance_orphan_rows
SELECT COUNT(*) FROM bureau_balance bb ANTI JOIN bureau b USING (SK_ID_BUREAU);
-- name: pos_orphan_rows
SELECT COUNT(*) FROM pos_cash_balance c ANTI JOIN previous_application p USING (SK_ID_PREV);
-- name: card_orphan_rows
SELECT COUNT(*) FROM credit_card_balance c ANTI JOIN previous_application p USING (SK_ID_PREV);
-- name: installment_orphan_rows
SELECT COUNT(*) FROM installments_payments c ANTI JOIN previous_application p USING (SK_ID_PREV);
-- name: loan_owner_conflicts
SELECT COUNT(*) FROM (
  SELECT SK_ID_PREV FROM (
    SELECT SK_ID_PREV, SK_ID_CURR FROM previous_application
    UNION ALL SELECT SK_ID_PREV, SK_ID_CURR FROM installments_payments
    UNION ALL SELECT SK_ID_PREV, SK_ID_CURR FROM pos_cash_balance
    UNION ALL SELECT SK_ID_PREV, SK_ID_CURR FROM credit_card_balance
  ) GROUP BY SK_ID_PREV HAVING COUNT(DISTINCT SK_ID_CURR) > 1
);
