/**
 * Static historical ML evidence, transcribed from the canonical documents
 * (docs/RESULTS.md and docs/portfolio/*.md) on 2026-11-17.
 *
 * These numbers belong to the evaluated Home Credit research artifact and
 * its run/lock identities. They are NOT metrics of the synthetic demo model
 * that scores cases in this application, and they must never be presented
 * as such. Update this file only from the canonical documents.
 */

export const LOCKED_EVALUATION = {
  model: "LightGBM (147 inputs)",
  runId: "50d36f3b142a41039ff4d72967242f9a",
  lockId: "17b83143e33bb907",
  testApplicants: 46127,
  threshold: 0.08040201,
  note: "Phân vùng Test được mở một lần cho lock này (toàn project có ba lần mở, mỗi lock một lần, đều có sổ ghi công khai).",
  metrics: [
    { name: "ROC-AUC", value: 0.775432, low: 0.767577, high: 0.782915 },
    { name: "PR-AUC", value: 0.256541, low: 0.24563, high: 0.269374 },
    { name: "Brier score", value: 0.06715, low: 0.066568, high: 0.06769 },
    { name: "Log loss", value: 0.241822, low: 0.239487, high: 0.244109 },
  ],
} as const;

export const E7_COMPARISON = {
  runId: "05a1da3d6f3c4900ab0ebe12a119cdcf",
  note: "12 pipeline chỉ đánh giá trên Selection. XGBoost dẫn đầu với +0.001766 AUC, khoảng tin cậy 95% theo cặp [-0.000834; +0.004528], dưới ngưỡng quy tắc thăng hạng, nên champion LightGBM được giữ nguyên mà không tinh chỉnh thêm.",
  models: [
    { family: "xgboost", auc: 0.774287 },
    { family: "lightgbm (champion)", auc: 0.772521 },
    { family: "catboost", auc: 0.772383 },
    { family: "hist_gradient_boosting", auc: 0.770225 },
    { family: "linear_svm_sigmoid", auc: 0.759739 },
    { family: "logistic_regression", auc: 0.759367 },
    { family: "random_forest", auc: 0.75666 },
    { family: "mlp", auc: 0.754668 },
    { family: "extra_trees", auc: 0.737901 },
    { family: "adaboost", auc: 0.721792 },
    { family: "decision_tree", auc: 0.713672 },
    { family: "gaussian_nb", auc: 0.513622 },
  ],
} as const;

export const E8_TEMPORAL = {
  runId: "5ce5550e61724c478e3e3fb0a003ad3d",
  note: "Benchmark theo trình tự thời gian tách riêng trên Home Credit Model Stability (11 biến dự báo, tuần 0-91). Tín hiệu yếu; không được so các AUC này với 0.775 của model 2018 vì quần thể, nhãn, feature và cách chia dữ liệu đều khác nhau. Nhãn của Calibration, Test cuối và Monitoring vẫn chưa được mở.",
  models: [
    { family: "Logistic Regression", auc: 0.615878, prAuc: 0.058438 },
    { family: "LightGBM", auc: 0.583525, prAuc: 0.048628 },
    { family: "XGBoost CUDA", auc: 0.584143, prAuc: 0.048113 },
  ],
} as const;

export const EVIDENCE_LIMITATIONS = [
  "Đây là kết quả nghiên cứu lịch sử, không phải metrics của model synthetic đang dùng trong workspace này.",
  "Dữ liệu 2018 gồm các khoản vay đã duyệt trong một ảnh chụp tĩnh; khó khăn thanh toán không phải tổn thất vỡ nợ đã đo lường, và cách chia dữ liệu không phải ngoài thời gian (out-of-time).",
  "Metrics theo nhóm con không chứng minh tính công bằng; phần giải thích mô tả đóng góp của model, không phải quan hệ nhân quả.",
  "Selection được tái sử dụng qua E5-E7 mà không hiệu chỉnh đa so sánh (multiplicity); thứ hạng không bao giờ là căn cứ thăng hạng model.",
  "Không kết quả nào tại đây chứng minh hiệu quả cho vay thực tế hay mức sẵn sàng production.",
] as const;
