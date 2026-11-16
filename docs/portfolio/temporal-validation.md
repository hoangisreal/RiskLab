# Mở rộng đánh giá theo thời gian

Home Credit 2018 thiếu ngày application tuyệt đối. Stratified holdout không
chứng minh model chạy tốt trên khách hàng tương lai. Đổi seed hoặc lấy ID
làm thứ tự thời gian không khắc phục được giới hạn này.

## Dataset được chọn để nghiên cứu tiếp

[Home Credit — Credit Risk Model Stability](https://www.kaggle.com/c/home-credit-credit-risk-model-stability/data)
có `date_decision`, `WEEK_NUM`, `case_id`, nhãn `target` trong training data.
Nguồn chính thức mô tả target được xác định sau một khoảng thời gian theo
default trên khoản vay. Đây là bộ dữ liệu/nhãn khác contract 1.1; không gán
metrics/model 2018 cho 2024, không nối applicant IDs giữa hai bộ, không coi
đây là external validation cùng population và features.

Đã dùng Kaggle CLI authenticated để tải đúng training base, hai shard static_0
và feature dictionary; không tải competition Test. Quyền sử dụng theo
[competition rules](https://www.kaggle.com/c/home-credit-credit-risk-model-stability/rules).
Không tự chấp thuận điều khoản thay chủ tài khoản hoặc dùng mirror không rõ
quyền. Chỉ lấy training shards cần thiết, không tải cả CSV lẫn Parquet hoặc
competition test. Raw data không đưa lên GitHub.

## Protocol cần chốt trước khi xem kết quả

1. Kiểm kê metadata thời gian/schema; lưu manifest hash, nguồn, phạm vi sử
   dụng và thời điểm acquisition. Dữ liệu gốc nằm ngoài Git.
2. Chọn Train/Calibration/Selection/final holdout theo thời gian. Final
   holdout là Test thật mới và chưa được mở.
3. Xác định observation window, label horizon và thời điểm nhãn trưởng thành.
   Nếu không biết đủ horizon, ghi rõ chưa xác nhận purge gap an toàn;
   không giả định nhãn sẵn ngay ngày application.
4. Feature chỉ dùng dữ liệu có sẵn lúc application. Ngày/tuần/ID dùng để
   chia và audit, mặc định không làm predictor. Kiểm tra entity overlap nếu
   có customer identity; case khác nhau không chứng minh khác người.
5. Dùng expanding-window CV trong Train, preprocessing fit mỗi fold;
   cố định ngân sách. Calibration/Selection giữ đúng vai trò đã định.
6. Báo cáo ROC-AUC, PR-AUC, Brier, log loss, calibration theo tuần/tháng,
   số hồ sơ/positives và coverage. Không tạo AUC cho tuần chỉ có một lớp.
   Uncertainty phải xét phụ thuộc thời gian, ví dụ bootstrap block theo tuần.
7. Khóa artifact/protocol trước khi xin mở final holdout. Không chọn model
   hoặc threshold từ holdout; lưu cả kết quả suy giảm.

Thay model ứng dụng đòi contract và evidence mới; benchmark dưới đây không
là external validation của model 2018 và chưa nghiệm thu model production.

## E8 — retrospective chronological development

Source: 1.526.659 hồ sơ, tuần 0–91. Chỉ đọc metadata tuần/ngày để chốt protocol;
11 predictors chọn theo dictionary trước khi đọc target. Raw downloads và
manifest hashes ở `data/stability/`; không chứa credentials trong repo.

| Role | Tuần | Trạng thái nhãn |
|---|---|---|
| Train | 0–49 | Chỉ lấy tối đa 300.000 bằng hash case_id không dùng target |
| Calibration | 54–58 | Chưa mở nhãn; chưa fit calibrator ở E8 |
| Selection | 63–68 | Đánh giá trên 33.400 hồ sơ |
| Final Test | 73–81 | Chưa mở nhãn |
| Monitoring | 86–91 | Chưa mở nhãn |

Bốn tuần đầu sau mỗi boundary được loại; buffer này chưa được xác nhận theo
label horizon/maturity thực. Kết quả là retrospective development, không
chứng minh mô phỏng triển khai biết nhãn đúng thời điểm. Customer identity
không có, nên chưa loại trừ entity overlap qua tuần. Predictor availability
ở thời điểm quyết định dựa vào dictionary nguồn, chưa audit upstream độc lập.

Hai forward folds nằm hoàn toàn trong Train, preprocessing fit mỗi fold.
Các model/budget cố định; không tuning trên Selection. 1.000 bootstrap theo
block tuần giữ phụ thuộc trong tuần; chỉ sáu tuần Selection nên CI hạn chế,
không bao gồm bất định do training một model khác.

| Model | ROC-AUC | PR-AUC | Brier | Log loss |
|---|---:|---:|---:|---:|
| Logistic Regression | 0,615878 | 0,058438 | 0,031948 | 0,143065 |
| LightGBM | 0,583525 | 0,048628 | 0,032224 | 0,146721 |
| XGBoost CUDA | 0,584143 | 0,048113 | 0,032198 | 0,146475 |

Signal còn yếu với 11 predictors. Logistic Regression tốt hơn các boosting
pipelines được thử ở phạm vi này; không thay cấu hình sau khi xem kết quả.
LightGBM/XGBoost có Brier và log loss cao hơn LR trong run này. Không so sánh trực
tiếp AUC 0,616 ở đây với AUC 0,775 của Home Credit 2018: population, labels,
features và split khác nhau. Khoảng AUC block 95% của LR [0,601358; 0,641792];
LightGBM [0,562357; 0,598719]. Không sử dụng các số này để ra quyết định tín dụng.

Run hiện hành gồm ba model, không có Dummy. Run `5ce5550e61724c478e3e3fb0a003ad3d`, config SHA256
`8b11ac160573dd9b8c30545e277c5991c40460e493c5e1a93382e7adbb88be77`, source SHA256
`af9a6a5bf869f1fc72248044699bb6f268c7edae1eb7a69c8ddc3bd8920e59a2`.
Independent reload + sklearn recomputation của cả ba artifacts sai khác 0;
chỉ mở lại Selection cho check này. Không đọc final holdout.

## Tái lập acquisition và benchmark

Kaggle CLI dùng tài khoản đã có quyền dataset; không đặt token trong command,
notebook hoặc log. Các lệnh dưới tải training-only từ nguồn chính thức:

```bash
kaggle competitions download home-credit-credit-risk-model-stability -f parquet_files/train/train_base.parquet -p data/stability/raw --quiet
kaggle competitions download home-credit-credit-risk-model-stability -f parquet_files/train/train_static_0_0.parquet -p data/stability/raw --quiet
kaggle competitions download home-credit-credit-risk-model-stability -f parquet_files/train/train_static_0_1.parquet -p data/stability/raw --quiet
kaggle competitions download home-credit-credit-risk-model-stability -f feature_definitions.csv -p data/stability/raw --quiet
uv run python scripts/prepare_stability.py --data-root data/stability
uv run python scripts/portfolio_run.py --study e8 --data-root data/stability \
  --out reports/workspaces/fresh-e8
```

Acquisition được kiểm tra với Kaggle CLI 2.2.4. Prepare chỉ giải nén đúng file
training yêu cầu, hash nguồn và tạo manifest; không đọc target. Manifest đã
có không được ghi đè nếu bytes khác; dùng data root mới cho revision khác.

Bằng chứng tổng hợp: [benchmark evidence](benchmark-evidence.json).
