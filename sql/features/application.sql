-- Application row-wise ratios. Input view: application_src (cleaned, one row per SK_ID_CURR).
-- Every expression is row-wise; no cross-applicant statistic. Sentinel DAYS_EMPLOYED=365243
-- is already NULL after cleaning, so the employment ratio is NULL for those rows.
SELECT
  SK_ID_CURR,
  CAST(AMT_CREDIT / NULLIF(AMT_INCOME_TOTAL, 0) AS DOUBLE) AS app_credit_income_ratio,
  CAST(AMT_ANNUITY / NULLIF(AMT_INCOME_TOTAL, 0) AS DOUBLE) AS app_annuity_income_ratio,
  CAST(AMT_CREDIT / NULLIF(AMT_GOODS_PRICE, 0) AS DOUBLE) AS app_credit_goods_ratio,
  CAST((-DAYS_EMPLOYED) / NULLIF(CAST(-DAYS_BIRTH AS DOUBLE), 0) AS DOUBLE) AS app_employment_age_ratio,
  CAST(AMT_INCOME_TOTAL / NULLIF(CNT_FAM_MEMBERS, 0) AS DOUBLE) AS app_income_per_member
FROM application_src
