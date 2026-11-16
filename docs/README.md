# RiskLab — tài liệu và bằng chứng

RiskLab là portfolio ML Engineer về mô phỏng hỗ trợ quyết định tín dụng:
Manager import → worker scoring → Analyst đề xuất → Manager quyết định mô phỏng.
Model ước lượng payment difficulties trên Home Credit; kết quả chưa chứng minh
hiệu quả tín dụng thực tế hoặc khả năng tổng quát theo thời gian.

| Nội dung | Tài liệu |
|---|---|
| Chạy ứng dụng synthetic và giới hạn | [README dự án](../README.md) |
| Metrics đã công bố, thí nghiệm E1–E5 | [RESULTS](RESULTS.md) |
| Artifact, population, subgroup và lịch sử mở Test | [Model card](model_card.md) |
| Quy trình ML/scoring và tái lập | [Operations](OPERATIONS.md) |
| Leakage và bằng chứng lịch sử | [Leakage controls](archive/leakage.md) |
| Tái lập và nghiên cứu portfolio mới | [Kiểm chứng portfolio](portfolio/verification.md) |
| So sánh model mở rộng | [Model comparison](portfolio/model-comparison.md) |
| Source identity sau cleanup GitHub | [Source revisions](portfolio/source-revisions.md) |
| Dataset và đánh giá theo thời gian | [Temporal validation](portfolio/temporal-validation.md) |

Metrics lịch sử gắn với run/lock của chính chúng; không gán cho model mới.
E6 là nghiên cứu mô tả trên Train/Selection, không thay champion, threshold,
không đăng ký/promotion và không mở Test thật.

Tài liệu công khai chỉ chứa hướng dẫn cho người đọc và bằng chứng tổng hợp.
Dữ liệu gốc, dự đoán từng hàng, tracking store, secrets và cấu hình/hướng dẫn
coding assistant được giữ ngoài Git. Kiểm tra bộ file bằng
`uv run python scripts/check_public_files.py`; dùng `--revision COMMIT` để
kiểm tra một commit. Kiểm tra tree khác kiểm tra toàn bộ lịch sử Git.

Bản ghi quyết định và hướng dẫn sản phẩm lịch sử còn trong
[snapshot tài liệu](https://github.com/hoangisreal/RiskLab/tree/b9f85066c30b62d739ee8ff29aa631f8142b3642/docs).
Snapshot là lịch sử, không đại diện trạng thái nghiệm thu mới.
