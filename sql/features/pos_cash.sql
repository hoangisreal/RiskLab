-- POS/cash monthly aggregates per applicant, keyed on SK_ID_CURR.
-- Input views: applicants, pos_cash_balance. MONTHS_BALANCE must be <= -1 for POS data.
SELECT
  a.SK_ID_CURR,
  CAST(COALESCE(COUNT(p.SK_ID_PREV), 0) AS DOUBLE) AS pos_record_count,
  CAST(AVG(CAST(p.SK_DPD AS DOUBLE)) AS DOUBLE) AS pos_mean_dpd,
  CAST(MAX(p.SK_DPD) AS DOUBLE) AS pos_max_dpd,
  CAST(AVG(CASE WHEN p.SK_DPD_DEF > 0 THEN 1.0 ELSE 0.0 END) AS DOUBLE) AS pos_dpd_def_ratio,
  CAST(AVG(CASE WHEN p.NAME_CONTRACT_STATUS = 'Active' THEN 1.0 ELSE 0.0 END) AS DOUBLE) AS pos_active_share
FROM applicants a
LEFT JOIN pos_cash_balance p ON p.SK_ID_CURR = a.SK_ID_CURR AND p.MONTHS_BALANCE <= 0
GROUP BY a.SK_ID_CURR
