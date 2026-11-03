-- Previous-application aggregates per applicant. Input views: applicants, previous_application.
SELECT
  a.SK_ID_CURR,
  CAST(COALESCE(COUNT(p.SK_ID_PREV), 0) AS DOUBLE) AS prev_record_count,
  CAST(AVG(CASE WHEN p.NAME_CONTRACT_STATUS = 'Refused' THEN 1.0 ELSE 0.0 END) AS DOUBLE) AS prev_refusal_rate,
  CAST(AVG(p.AMT_CREDIT / NULLIF(p.AMT_APPLICATION, 0)) AS DOUBLE) AS prev_approved_amount_ratio,
  CAST(AVG(p.AMT_CREDIT) AS DOUBLE) AS prev_mean_credit,
  CAST(AVG(CAST(-p.DAYS_DECISION AS DOUBLE)) AS DOUBLE) AS prev_mean_days_since_decision,
  CAST(AVG(CASE WHEN p.NAME_CONTRACT_TYPE = 'Cash loans' THEN 1.0 ELSE 0.0 END) AS DOUBLE) AS prev_cash_share,
  CAST(AVG(CASE WHEN p.NAME_CONTRACT_STATUS = 'Approved' THEN 1.0 ELSE 0.0 END) AS DOUBLE) AS prev_approval_rate
FROM applicants a
LEFT JOIN previous_application p ON p.SK_ID_CURR = a.SK_ID_CURR
GROUP BY a.SK_ID_CURR
