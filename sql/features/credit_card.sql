-- Floating aggregates have an explicit value order for batch/API reproducibility.
-- Monthly observations -> loan -> applicant; rates give each observed loan equal weight.
WITH loan AS (
  SELECT SK_ID_CURR, SK_ID_PREV, COUNT(*) AS n,
         AVG(AMT_BALANCE / NULLIF(AMT_CREDIT_LIMIT_ACTUAL, 0) ORDER BY AMT_BALANCE / NULLIF(AMT_CREDIT_LIMIT_ACTUAL, 0)) AS utilization,
         MAX(AMT_BALANCE / NULLIF(AMT_CREDIT_LIMIT_ACTUAL, 0)) AS max_utilization,
         AVG(CAST(SK_DPD AS DOUBLE) ORDER BY CAST(SK_DPD AS DOUBLE)) AS mean_dpd, MAX(SK_DPD) AS max_dpd,
         AVG(AMT_BALANCE ORDER BY AMT_BALANCE) AS mean_balance
  FROM credit_card_balance GROUP BY SK_ID_CURR, SK_ID_PREV
)
SELECT a.SK_ID_CURR,
  CAST(COALESCE(SUM(c.n ORDER BY c.n), 0) AS DOUBLE) AS cc_record_count,
  CAST(AVG(c.utilization ORDER BY c.utilization) AS DOUBLE) AS cc_mean_utilization,
  CAST(MAX(c.max_utilization) AS DOUBLE) AS cc_max_utilization,
  CAST(AVG(c.mean_dpd ORDER BY c.mean_dpd) AS DOUBLE) AS cc_mean_dpd,
  CAST(MAX(c.max_dpd) AS DOUBLE) AS cc_max_dpd,
  CAST(AVG(c.mean_balance ORDER BY c.mean_balance) AS DOUBLE) AS cc_mean_balance
FROM applicants a LEFT JOIN loan c ON c.SK_ID_CURR = a.SK_ID_CURR GROUP BY a.SK_ID_CURR
