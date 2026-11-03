-- Installment behaviour per applicant, keyed on SK_ID_CURR (present and consistent in
-- installments_payments, so orphan SK_ID_PREV rows are still attributed to the applicant).
-- Input views: applicants, installments_payments.
-- Paid installments only: DAYS_ENTRY_PAYMENT must be <= 0 (realized event). Late means the
-- payment day is after the due day: DAYS_ENTRY_PAYMENT > DAYS_INSTALMENT (both <= 0, so a
-- larger, less negative value is later).
SELECT
  a.SK_ID_CURR,
  CAST(COALESCE(COUNT(i.SK_ID_PREV), 0) AS DOUBLE) AS inst_record_count,
  CAST(AVG(CASE WHEN i.DAYS_ENTRY_PAYMENT > i.DAYS_INSTALMENT THEN 1.0 ELSE 0.0 END) AS DOUBLE) AS inst_late_ratio,
  CAST(AVG(i.DAYS_ENTRY_PAYMENT - i.DAYS_INSTALMENT) AS DOUBLE) AS inst_mean_days_late,
  CAST(MAX(i.DAYS_ENTRY_PAYMENT - i.DAYS_INSTALMENT) AS DOUBLE) AS inst_max_days_late,
  CAST(AVG(CASE WHEN i.AMT_PAYMENT < i.AMT_INSTALMENT THEN 1.0 ELSE 0.0 END) AS DOUBLE) AS inst_underpaid_ratio,
  CAST(AVG(i.AMT_PAYMENT / NULLIF(i.AMT_INSTALMENT, 0)) AS DOUBLE) AS inst_payment_ratio_mean,
  CAST(AVG(CASE WHEN i.DAYS_INSTALMENT >= -365 AND i.DAYS_ENTRY_PAYMENT > i.DAYS_INSTALMENT THEN 1.0
            WHEN i.DAYS_INSTALMENT >= -365 THEN 0.0 END) AS DOUBLE) AS inst_recent_late_ratio
FROM applicants a
LEFT JOIN installments_payments i
  ON i.SK_ID_CURR = a.SK_ID_CURR AND i.DAYS_ENTRY_PAYMENT <= 0
GROUP BY a.SK_ID_CURR
