-- Floating aggregates have an explicit value order for batch/API reproducibility.
-- Monthly observations -> bureau credit -> applicant. The application snapshot (month 0) is valid.
WITH credit AS (
  SELECT SK_ID_BUREAU, COUNT(*) AS n,
         SUM(CASE WHEN STATUS IN ('1','2','3','4','5') THEN 1.0 ELSE 0.0 END ORDER BY CASE WHEN STATUS IN ('1','2','3','4','5') THEN 1.0 ELSE 0.0 END) AS dpd,
         AVG(CASE WHEN STATUS IS NULL THEN NULL WHEN STATUS IN ('1','2','3','4','5') THEN 1.0 ELSE 0.0 END ORDER BY CASE WHEN STATUS IS NULL THEN NULL WHEN STATUS IN ('1','2','3','4','5') THEN 1.0 ELSE 0.0 END) AS dpd_ratio,
         MAX(TRY_CAST(STATUS AS INTEGER)) AS max_status,
         MAX(MONTHS_BALANCE) - MIN(MONTHS_BALANCE) AS span
  FROM bureau_balance GROUP BY SK_ID_BUREAU
)
SELECT a.SK_ID_CURR,
  CAST(COALESCE(SUM(c.n ORDER BY c.n), 0) AS DOUBLE) AS bb_record_count,
  CAST(SUM(c.dpd ORDER BY c.dpd) AS DOUBLE) AS bb_dpd_months,
  CAST(AVG(c.dpd_ratio ORDER BY c.dpd_ratio) AS DOUBLE) AS bb_dpd_ratio,
  CAST(MAX(c.max_status) AS DOUBLE) AS bb_max_status,
  CAST(AVG(c.span ORDER BY c.span) AS DOUBLE) AS bb_months_span
FROM applicants a LEFT JOIN bureau b ON b.SK_ID_CURR = a.SK_ID_CURR
LEFT JOIN credit c ON c.SK_ID_BUREAU = b.SK_ID_BUREAU GROUP BY a.SK_ID_CURR
