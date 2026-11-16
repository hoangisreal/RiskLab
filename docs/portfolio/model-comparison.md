# So sánh model mở rộng — E7

Mục tiêu là so sánh các pipeline thường dùng cho phân loại rủi ro trên dữ liệu
bảng. E7 mở rộng breadth của E5/E6, không phải một vòng chọn model/Test mới.
Các cấu hình được khai báo trước tại `experiments/e7.yaml`; năm baseline lấy
nguyên từ `experiments/e5.yaml`, model mới có budget cố định.

| Họ model | Vai trò | Execution |
|---|---|---|
| Logistic Regression | Baseline tuyến tính | CPU |
| Decision Tree | Cây đơn, diễn giải cấu trúc | CPU |
| Random Forest | Bagging cây | CPU, 4 fit threads |
| Extra Trees | Randomized tree ensemble | CPU, 4 fit threads |
| AdaBoost | Boosting cây nông | CPU |
| Gaussian Naive Bayes | Baseline generative, giả định độc lập có điều kiện | CPU |
| MLP | Neural tabular baseline nhỏ | CPU, early stopping trong Train |
| Linear SVM + sigmoid | Margin classifier với probability calibration riêng | CPU |
| HistGradientBoosting | Histogram gradient boosting | CPU |
| LightGBM | Champion protocol đang phục vụ | CPU |
| XGBoost | Challenger cây boosting | CUDA |
| CatBoost | Challenger cây boosting | CUDA |

Tất cả dùng cùng served set 147 inputs và cùng Train/Selection. LightGBM
giữ native categorical pipeline; các family khác dùng preprocessing LR hiện
có (Train-only imputation/scaling/one-hot). Vì preprocessing và search budget
khác nhau, đây là so sánh pipeline ứng dụng, không chứng minh family tối ưu.
Không có class weighting/resampling hoặc tuning sau khi xem Selection.

SVM fit estimator trên Train rồi đóng băng, fit sigmoid trên Calibration;
không fit calibrator lên Selection. Model khác giữ probability gốc. Nội bộ
MLP/HistGB có early stopping trong Train; không dùng Selection để dừng.
RF/ExtraTrees prediction dùng một thread để giữ thứ tự floating reductions;
không nới tolerance để làm exact reload pass. Training vẫn dùng bốn threads.

```bash
uv run python scripts/portfolio_run.py --study e7 \
  --out reports/workspaces/fresh-e7 \
  --baseline reports/workspaces/fresh-baseline/baseline
```

Primary ROC-AUC, PR-AUC, Brier, log loss; ghi thêm ECE, thời gian fit/batch
score, convergence warnings và artifact hash. So sánh AUC paired bootstrap
1.000 lần; flag mô tả khi gain ≥ 0,005 và CI lower > 0. Selection đã tái sử
dụng, không điều chỉnh multiplicity; không promotion theo bảng xếp hạng này.
Timing là phép đo local cho batch, không phải HTTP latency hoặc production SLO.

## Kết quả

Run đầu `baaf8585b3134e36b924610551159b6f` được dừng để
sửa tính tái lập execution: synthetic RF parallel prediction lệch 2,22e-16
do thứ tự cộng floating point; giữ output cũ và restart bằng config mới,
không thay hyperparameters để chọn theo Selection.

Run hiện hành gồm 12 model (đã bỏ Dummy theo yêu cầu owner); old runs giữ local.
Run hoàn chỉnh `05a1da3d6f3c4900ab0ebe12a119cdcf`, 169.131 Train / 30.752 Calibration / 30.751 Selection.
Config SHA256 `b764f2d96ca050450c7f3dde444ba6a90fcba5d638fb104653c8b4ed901a3e87`; source SHA256 `00837cf0688d6a43c02112d6bb2700bf339e256c19517bde32ad13d2ef02ae4e`.

| Model | ROC-AUC | PR-AUC | Brier | Log loss | Fit (s) |
|---|---:|---:|---:|---:|---:|
| xgboost | 0.774287 | 0.270511 | 0.066475 | 0.240619 | 15.2 |
| lightgbm | 0.772521 | 0.263679 | 0.066776 | 0.241557 | 7.5 |
| catboost | 0.772383 | 0.269580 | 0.066586 | 0.241300 | 11.5 |
| hist_gradient_boosting | 0.770225 | 0.263746 | 0.066803 | 0.242090 | 107.2 |
| linear_svm_sigmoid | 0.759739 | 0.249534 | 0.067535 | 0.245348 | 72.2 |
| logistic_regression | 0.759367 | 0.252196 | 0.067439 | 0.245145 | 12.4 |
| random_forest | 0.756660 | 0.251009 | 0.069112 | 0.251861 | 29.9 |
| mlp | 0.754668 | 0.245513 | 0.067816 | 0.247441 | 16.4 |
| extra_trees | 0.737901 | 0.226715 | 0.070536 | 0.258857 | 34.8 |
| adaboost | 0.721792 | 0.197146 | 0.081209 | 0.309825 | 225.7 |
| decision_tree | 0.713672 | 0.194541 | 0.070266 | 0.258565 | 15.8 |
| gaussian_nb | 0.513622 | 0.082817 | 0.887754 | 30.318458 | 6.1 |

XGBoost đứng đầu bảng này nhưng AUC gain chỉ +0,001766, paired 95% CI
[-0,000834; +0,004528]; không đạt rule. Không model nào được flag superior,
không thay champion hoặc mở rộng tuning sau khi xem Selection.

GaussianNB mất calibration nghiêm trọng trong pipeline này (Brier 0,887754,
log loss 30,318458); kết quả xấu được giữ nguyên, không dùng cho quyết định.
Đây không chứng minh family này luôn kém ở dataset khác. AdaBoost có AUC
0,722 nhưng probability metrics yếu; AUC không đủ để nghiệm thu PD.
Các model mới chưa được tuning ngang mức champion; không diễn giải bảng như
một kết luận tổng quát về thuật toán.

Independent verification trong tiến trình Python riêng đã tải lại cả 12
artifacts theo SHA256, predict Selection và tính trực tiếp bằng sklearn:
sai khác metrics tối đa 0. Model nào cũng pass exact reload probe. Không có
ConvergenceWarning trong run hoàn chỉnh. Output/model/predictions từng hàng
chỉ nằm local tại `reports/workspaces/portfolio-e7-no-prior-20261116/study/`, không
được commit hoặc public.

Bằng chứng tổng hợp có thể đọc bằng máy: [benchmark evidence](benchmark-evidence.json).
