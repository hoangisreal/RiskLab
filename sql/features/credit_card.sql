-- Credit-card monthly aggregates per applicant, keyed on SK_ID_CURR.
-- Input views: applicants, credit_card_balance. MONTHS_BALANCE must be <= -1 for card data.
SELECT
  a.SK_ID_CURR,
  CAST(COALESCE(COUNT(c.SK_ID_PREV), 0) AS DOUBLE) AS cc_record_count,
  CAST(AVG(c.AMT_BALANCE / NULLIF(c.AMT_CREDIT_LIMIT_ACTUAL, 0)) AS DOUBLE) AS cc_mean_utilization,
  CAST(MAX(c.AMT_BALANCE / NULLIF(c.AMT_CREDIT_LIMIT_ACTUAL, 0)) AS DOUBLE) AS cc_max_utilization,
  CAST(AVG(CAST(c.SK_DPD AS DOUBLE)) AS DOUBLE) AS cc_mean_dpd,
  CAST(MAX(c.SK_DPD) AS DOUBLE) AS cc_max_dpd,
  CAST(AVG(c.AMT_BALANCE) AS DOUBLE) AS cc_mean_balance
FROM applicants a
LEFT JOIN credit_card_balance c ON c.SK_ID_CURR = a.SK_ID_CURR AND c.MONTHS_BALANCE <= 0
GROUP BY a.SK_ID_CURR
