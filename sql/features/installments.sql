-- Floating aggregates have an explicit value order for batch/API reproducibility.
-- Contract 1.1: combine partial payments of the same installment before comparing
-- amounts; then aggregate installments to loans and loans to the applicant.
WITH paid AS (
  SELECT SK_ID_CURR, SK_ID_PREV, NUM_INSTALMENT_VERSION, NUM_INSTALMENT_NUMBER,
         MAX(DAYS_INSTALMENT) AS due_day,
         MAX(DAYS_ENTRY_PAYMENT) AS last_payment_day,
         MAX(AMT_INSTALMENT) AS amount_due,
         SUM(AMT_PAYMENT ORDER BY AMT_PAYMENT) AS amount_paid
  FROM installments_payments
  GROUP BY SK_ID_CURR, SK_ID_PREV, NUM_INSTALMENT_VERSION, NUM_INSTALMENT_NUMBER
), loan AS (
  SELECT SK_ID_CURR, SK_ID_PREV, COUNT(*) AS n,
         AVG(CASE WHEN last_payment_day > due_day THEN 1.0 ELSE 0.0 END ORDER BY CASE WHEN last_payment_day > due_day THEN 1.0 ELSE 0.0 END) AS late_ratio,
         AVG(GREATEST(last_payment_day - due_day, 0) ORDER BY GREATEST(last_payment_day - due_day, 0)) AS mean_days_late,
         MAX(GREATEST(last_payment_day - due_day, 0)) AS max_days_late,
         AVG(CASE WHEN amount_paid IS NULL OR amount_due IS NULL THEN NULL
                  WHEN amount_paid < amount_due THEN 1.0 ELSE 0.0 END ORDER BY CASE WHEN amount_paid IS NULL OR amount_due IS NULL THEN NULL
                  WHEN amount_paid < amount_due THEN 1.0 ELSE 0.0 END) AS underpaid_ratio,
         AVG(amount_paid / NULLIF(amount_due, 0) ORDER BY amount_paid / NULLIF(amount_due, 0)) AS payment_ratio,
         AVG(CASE WHEN due_day >= -365 THEN
             CASE WHEN last_payment_day > due_day THEN 1.0 ELSE 0.0 END END ORDER BY CASE WHEN due_day >= -365 THEN
             CASE WHEN last_payment_day > due_day THEN 1.0 ELSE 0.0 END END) AS recent_late_ratio
  FROM paid GROUP BY SK_ID_CURR, SK_ID_PREV
)
SELECT a.SK_ID_CURR,
  CAST(COALESCE(SUM(l.n ORDER BY l.n), 0) AS DOUBLE) AS inst_record_count,
  CAST(AVG(l.late_ratio ORDER BY l.late_ratio) AS DOUBLE) AS inst_late_ratio,
  CAST(AVG(l.mean_days_late ORDER BY l.mean_days_late) AS DOUBLE) AS inst_mean_days_late,
  CAST(MAX(l.max_days_late) AS DOUBLE) AS inst_max_days_late,
  CAST(AVG(l.underpaid_ratio ORDER BY l.underpaid_ratio) AS DOUBLE) AS inst_underpaid_ratio,
  CAST(AVG(l.payment_ratio ORDER BY l.payment_ratio) AS DOUBLE) AS inst_payment_ratio_mean,
  CAST(AVG(l.recent_late_ratio ORDER BY l.recent_late_ratio) AS DOUBLE) AS inst_recent_late_ratio
FROM applicants a LEFT JOIN loan l ON l.SK_ID_CURR = a.SK_ID_CURR GROUP BY a.SK_ID_CURR
