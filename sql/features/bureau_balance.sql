-- Bureau-balance DPD aggregates, two levels: bureau_balance -> bureau -> applicant.
-- Input views: applicants(SK_ID_CURR), bureau, bureau_balance.
-- MONTHS_BALANCE = 0 (snapshot at application) is allowed; rows with MONTHS_BALANCE > 0
-- are excluded as temporal violations (none exist in validated data).
SELECT
  a.SK_ID_CURR,
  CAST(COALESCE(COUNT(bb.SK_ID_BUREAU), 0) AS DOUBLE) AS bb_record_count,
  CAST(SUM(CASE WHEN bb.STATUS IN ('1', '2', '3', '4', '5') THEN 1.0 ELSE 0.0 END) AS DOUBLE) AS bb_dpd_months,
  CAST(AVG(CASE WHEN bb.STATUS IN ('1', '2', '3', '4', '5') THEN 1.0 ELSE 0.0 END) AS DOUBLE) AS bb_dpd_ratio,
  CAST(MAX(TRY_CAST(bb.STATUS AS INTEGER)) AS DOUBLE) AS bb_max_status,
  CAST(MAX(bb.MONTHS_BALANCE) - MIN(bb.MONTHS_BALANCE) AS DOUBLE) AS bb_months_span
FROM applicants a
LEFT JOIN bureau b ON b.SK_ID_CURR = a.SK_ID_CURR
LEFT JOIN bureau_balance bb ON bb.SK_ID_BUREAU = b.SK_ID_BUREAU AND bb.MONTHS_BALANCE <= 0
GROUP BY a.SK_ID_CURR
