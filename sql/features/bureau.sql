-- Floating aggregates have an explicit value order for batch/API reproducibility.
-- Bureau aggregates per applicant. Input views: applicants(SK_ID_CURR), bureau.
-- Only the applicant's own credits are aggregated; no label is referenced.
SELECT
  a.SK_ID_CURR,
  CAST(COALESCE(COUNT(b.SK_ID_BUREAU), 0) AS DOUBLE) AS bureau_record_count,
  CAST(AVG(CASE WHEN b.CREDIT_ACTIVE IS NULL THEN NULL WHEN b.CREDIT_ACTIVE = 'Active' THEN 1.0 ELSE 0.0 END ORDER BY CASE WHEN b.CREDIT_ACTIVE IS NULL THEN NULL WHEN b.CREDIT_ACTIVE = 'Active' THEN 1.0 ELSE 0.0 END) AS DOUBLE) AS bureau_active_ratio,
  CAST(SUM(b.AMT_CREDIT_SUM_DEBT ORDER BY b.AMT_CREDIT_SUM_DEBT) AS DOUBLE) AS bureau_debt_sum,
  CAST(SUM(b.AMT_CREDIT_SUM_LIMIT ORDER BY b.AMT_CREDIT_SUM_LIMIT) AS DOUBLE) AS bureau_limit_sum,
  CAST(SUM(b.AMT_CREDIT_SUM_OVERDUE ORDER BY b.AMT_CREDIT_SUM_OVERDUE) AS DOUBLE) AS bureau_overdue_sum,
  CAST(SUM(b.AMT_CREDIT_SUM_DEBT ORDER BY b.AMT_CREDIT_SUM_DEBT) / NULLIF(SUM(b.AMT_CREDIT_SUM_LIMIT ORDER BY b.AMT_CREDIT_SUM_LIMIT), 0) AS DOUBLE) AS bureau_debt_limit_ratio,
  CAST(MAX(b.CREDIT_DAY_OVERDUE) AS DOUBLE) AS bureau_max_overdue_days,
  CAST(AVG(CAST(-b.DAYS_CREDIT AS DOUBLE) ORDER BY CAST(-b.DAYS_CREDIT AS DOUBLE)) AS DOUBLE) AS bureau_mean_days_credit
FROM applicants a
LEFT JOIN bureau b ON b.SK_ID_CURR = a.SK_ID_CURR
GROUP BY a.SK_ID_CURR
