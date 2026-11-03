-- Floating aggregates have an explicit value order for batch/API reproducibility.
-- Monthly observations -> loan -> applicant. Missing history remains NULL except count.
WITH loan AS (
  SELECT SK_ID_CURR, SK_ID_PREV, COUNT(*) AS n,
         AVG(CAST(SK_DPD AS DOUBLE) ORDER BY CAST(SK_DPD AS DOUBLE)) AS mean_dpd, MAX(SK_DPD) AS max_dpd,
         AVG(CASE WHEN SK_DPD_DEF IS NULL THEN NULL WHEN SK_DPD_DEF > 0 THEN 1.0 ELSE 0.0 END ORDER BY CASE WHEN SK_DPD_DEF IS NULL THEN NULL WHEN SK_DPD_DEF > 0 THEN 1.0 ELSE 0.0 END) AS dpd_ratio,
         AVG(CASE WHEN NAME_CONTRACT_STATUS IS NULL THEN NULL
                  WHEN NAME_CONTRACT_STATUS = 'Active' THEN 1.0 ELSE 0.0 END ORDER BY CASE WHEN NAME_CONTRACT_STATUS IS NULL THEN NULL
                  WHEN NAME_CONTRACT_STATUS = 'Active' THEN 1.0 ELSE 0.0 END) AS active_share
  FROM pos_cash_balance GROUP BY SK_ID_CURR, SK_ID_PREV
)
SELECT a.SK_ID_CURR,
  CAST(COALESCE(SUM(p.n ORDER BY p.n), 0) AS DOUBLE) AS pos_record_count,
  CAST(AVG(p.mean_dpd ORDER BY p.mean_dpd) AS DOUBLE) AS pos_mean_dpd,
  CAST(MAX(p.max_dpd) AS DOUBLE) AS pos_max_dpd,
  CAST(AVG(p.dpd_ratio ORDER BY p.dpd_ratio) AS DOUBLE) AS pos_dpd_def_ratio,
  CAST(AVG(p.active_share ORDER BY p.active_share) AS DOUBLE) AS pos_active_share
FROM applicants a LEFT JOIN loan p ON p.SK_ID_CURR = a.SK_ID_CURR GROUP BY a.SK_ID_CURR
