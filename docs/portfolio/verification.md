# Kiểm chứng portfolio — 2026-11-16

Mục tiêu: tái lập baseline, nghiên cứu cải tiến có giới hạn và giữ bằng chứng
đủ để người đọc đánh giá năng lực ML Engineer. Không tự đổi model đang phục vụ.

## Máy và CUDA

Máy kiểm tra: Fedora 45, i5-12500H (16 logical CPUs), RAM khoảng 15 GiB,
RTX 3050 Laptop 4.096 MiB, compute capability 8.6, driver 620.70.06.
Driver đã hoạt động; không cần cài lại hoặc thay cấu hình hệ thống.
Sandbox không thấy device nodes có thể làm `nvidia-smi` thất bại;
kết quả ở đây được xác nhận ngoài sandbox.

Đã chạy `uv run python scripts/gpu_preflight.py --out reports/operations/gpu-preflight-20261116.json`:
XGBoost và CatBoost đều fit dữ liệu synthetic trên GPU, không cảnh báo
fallback. XGBoost kiểm tra thêm effective device trong booster config.
Thời gian fit khoảng 0,423 và 0,277 giây; đây là smoke test nhỏ, không phải
benchmark hay đánh giá Home Credit. Wheel từ môi trường `uv.lock` hiện có
đã chạy được, không cần cài thêm CUDA toolkit.

E6 dùng một trial mỗi lần, depth tối đa 6, bốn CPU threads. Theo
[XGBoost](https://xgboost.readthedocs.io/en/stable/gpu/index.html), bộ nhớ phụ thuộc
dữ liệu và histogram. [CatBoost](https://catboost.ai/docs/en/features/training-on-gpu)
công khai GPU training không deterministic; không yêu cầu artifact GPU giống byte CPU.
Baseline LightGBM tiếp tục chạy CPU để đối chiếu evidence lịch sử.

## Tái lập

Chạy từ root với `uv sync --locked`, Python 3.12; raw Home Credit đã có quyền
sử dụng đặt tại `data/raw`, không commit. Chọn output chưa tồn tại:

```bash
uv run python scripts/portfolio_run.py --out reports/workspaces/fresh-baseline
uv run python scripts/portfolio_run.py --out reports/workspaces/fresh-e6 \
  --baseline reports/workspaces/fresh-baseline/baseline
```

Runner giới hạn 12 giờ mỗi đợt, tạo MLflow HTTP riêng trên loopback,
database/artifacts và log riêng,
rồi dừng server khi xong hoặc lỗi. Không dùng tracking store lịch sử và không
đụng Docker demo. Baseline chạy ingest, partition, validation, features,
E1/E2/E1-served, policy, diagnostics, E3/E4, candidate và reference verify.
Giữ nguyên seed/shares, SQL, preprocessing, features và metric tolerances.
Không tạo lock, đăng ký hoặc thay alias; source dirty được ghi đúng provenance.

E6 khai báo trước tại `experiments/e6.yaml`: bốn cấu hình XGBoost × ba fold
trong Train; preprocessing học riêng mỗi fold, early stopping dùng validation
của fold. Chọn cấu hình/rounds bằng Train-CV rồi mới score Selection.
XGBoost dùng native categorical với category sets học trong Train, khác
one-hot preprocessing của E5; chênh lệch không được quy riêng cho tuning.
So sánh LightGBM 283 rounds trên đúng served set; bỏ từng nguồn lịch sử trên
tập đó, bỏ từng `EXT_SOURCE_*` và cả nhóm. Fault simulation đặt external
scores thành missing không thay đổi validation required-source của API.

Primary ROC-AUC; flag nghiên cứu khi gain ≥ 0,005 và cận dưới paired 95% CI
> 0 (1.000 bootstrap). Flag không cấp quyền promotion. Selection đã tái sử
dụng; intervals mang tính khám phá, chưa điều chỉnh đa so sánh. Threshold
scenarios mô tả recall/khối lượng review; policy phục vụ giữ nguyên.
Mở Test thật cần quyết định riêng cho lock cụ thể.

## Trạng thái thực chạy

Baseline tại `reports/workspaces/portfolio-20261116` đã hoàn tất: 9/9 aggregate
reference checks pass, candidate parity 500 hồ sơ pass, không mở Test.
So sánh bảy bảng features Train (169.131 hồ sơ) với baseline local: values,
keys và missing masks khớp chính xác, rtol=atol=0. Report aggregate tại
`reports/workspaces/portfolio-20261116/feature-comparison.json`.
E3 run `0f127989b0d3416ea4072e10a986f980`, E4 `ea551cfe090546e78c93733a7ef58e8c`;
artifact E3 có SHA256 giống chính xác artifact được công bố trong
[model card](../model_card.md). LightGBM Selection ROC-AUC 0,772521,
PR-AUC 0,263679, Brier 0,066776, log loss 0,241557; threshold 0,08040201.
Chưa tạo lock mới hoặc thay model phục vụ.

## Kết quả E6 (Selection; không phải Test mới)

Run `c6fb772e904046599de3263ebfee27e7`, 169.131 Train / 30.751 Selection.
Config SHA256 `98bb53e8db5b00dd4e2bdaff8a5959ac98ba6aaff5346c4ff6aece73f5dea948`;
source SHA256 `8fa0deedbbbe666324547ae21426c53cbb1387c0b8a87f1b8d580a04b615f26a`.
Report local: `reports/workspaces/portfolio-e6-20261116/study/summary.json`.

| Model | ROC-AUC | PR-AUC | Brier | Log loss |
|---|---:|---:|---:|---:|
| LightGBM reference | 0,772521 | 0,263679 | 0,066776 | 0,241557 |
| XGBoost CUDA, chọn bằng Train-CV | 0,774047 | 0,266446 | 0,066641 | 0,241023 |

Train-CV chọn depth 4, min_child_weight 30, 621 rounds, learning rate 0,03.
AUC gain +0,001527; paired 95% CI [-0,000693; +0,003593]. Không đạt rule
+0,005 và cận dưới > 0, nên không flag superior, giữ LightGBM champion.
Không mở rộng search sau khi xem Selection. Khối fit/evaluation 167,3 giây,
chưa tính nạp dữ liệu hoặc khởi động tracking; không phải CPU/GPU speed benchmark.

Artifact nghiên cứu SHA256
`cc982140538f4c88a7671e539c04ae433390cfb5437b26438802d8cc7f2e0d32`.
Training device được kiểm tra là `cuda:0`; artifact dùng CPU để prediction.
Serialize/reload pipeline giữ nguyên probabilities, maximum difference 0.
Một tiến trình Python riêng tải lại artifact và tái tính bốn metrics bằng
sklearn trực tiếp trên Selection: sai khác metrics tối đa 0; source/artifact
hash khớp report. Aggregate check lưu ở `study/independent-verification.json`.
Challenger chưa tích hợp với explanation/registry contract của ứng dụng;
không được coi là model có thể tự thay thế champion.

| Ablation: train lại LightGBM với phần còn lại | ROC-AUC | Δ AUC so với reference |
|---|---:|---:|
| Bỏ cả nhóm EXT_SOURCE | 0,745761 | -0,026759 |
| Bỏ EXT_SOURCE_1 | 0,769770 | -0,002751 |
| Bỏ EXT_SOURCE_2 | 0,759478 | -0,013042 |
| Bỏ EXT_SOURCE_3 | 0,764151 | -0,008369 |
| Bỏ bureau trên served set | 0,769158 | -0,003363 |
| Bỏ previous trên served set | 0,767110 | -0,005411 |
| Bỏ installments trên served set | 0,765407 | -0,007114 |

Đây là đóng góp có điều kiện trên bộ nguồn đang phục vụ, khác cumulative E2.
Không cộng các gain và không diễn giải nhân quả. Bỏ từng nguồn lịch sử đều
làm giảm AUC trong phép đo này. Interval của EXT_SOURCE_1 chứa 0; so sánh
mang tính khám phá, không điều chỉnh đa kiểm định.

Giữ nguyên champion nhưng giả lập cả ba EXT_SOURCE missing khi inference:
AUC 0,728064, PR-AUC 0,205051, Brier 0,069755, log loss 0,256085. Khác với
train lại không có external scores; chưa phải fallback model được nghiệm thu.
Kết quả củng cố ưu tiên kiểm soát availability/quality của upstream scores.
Không tự điền rỗng nguồn bắt buộc để vượt API validation.

Tại threshold đã đóng băng, Selection review fraction 30,78%, recall 68,21%.
Scenario threshold 0,05 đưa review fraction lên 48,69%, recall 83,32%; chỉ là
mô tả trade-off, không chọn policy mới hoặc threshold theo nhóm.

Kết quả mới tách khỏi [RESULTS lịch sử](../RESULTS.md).

## Benchmark bổ sung và evidence hiện hành

- [E7](model-comparison.md): 12 model, run `05a1da3d6f3c4900ab0ebe12a119cdcf`.
  XGBoost AUC 0,774287; gain +0,001766 chưa đạt superiority rule. Champion giữ
  nguyên. Reload độc lập cả 12 artifacts, metric difference tối đa 0.
- [E8](temporal-validation.md): ba model trên dữ liệu training chính thức,
  run `5ce5550e61724c478e3e3fb0a003ad3d`. 300.000 Train, 33.400 Selection,
  11 predictors; LR AUC 0,615878. Reload độc lập ba artifacts, metric difference
  tối đa 0. Không mở nhãn Calibration/Test/Monitoring hoặc competition Test.
- Dummy đã bỏ khỏi code và report hiện hành theo yêu cầu owner; old runs giữ
  nguyên local. Không đổi seed, budgets hoặc hyperparameters theo kết quả.
- [Evidence JSON](benchmark-evidence.json) chứa metrics, paired/block intervals,
  config/source/artifact hashes và trạng thái reload. Không chứa dữ liệu cá nhân
  hoặc predictions từng hàng. Đây là bản tổng hợp; raw artifacts vẫn private.

## Gate local hiện hành

| Command/check | Kết quả ngày 2026-11-16 |
|---|---|
| `make test` | 742 passed, 10 deselected; 242,54 giây; PostgreSQL disposable thật |
| Ruff / format / mypy trong `make test` | Pass; 219 files formatted, 91 source files typed |
| `uv run python scripts/check_public_files.py --links` | Pass; 360 candidate paths |
| `git diff --check` | Pass |
| Frozen protocol và ba evidence files lịch sử | Hash/bytes không đổi |
| GitHub Markdown + Mermaid parse/render | Pass; ảnh và sơ đồ được xem trực tiếp |

Có một Starlette TestClient deprecation warning; không đổi dependencies để
che warning. Các pytest checks chỉ dùng synthetic data. Frontend production
build đã được dựng lại bằng Docker để chụp ảnh; gate frontend/E2E đầy đủ nằm
trong CI, trạng thái run xem bên dưới.

## Ứng dụng và giới hạn nghiệm thu

README có ba ảnh thật từ production build local, API/PostgreSQL/worker thật,
nhưng dùng cases và model synthetic. [Provenance ảnh](../assets/README.md).
Mermaid được parse/render kiểm tra; Markdown render theo GitHub API.
`make demo-up` là đường chạy thử, không phải deployment production.

CI kiểm tra Python, PostgreSQL thật, frontend build, Docker scoring smoke và
Playwright desktop/mobile. Trạng thái từng run xem tại
[GitHub Actions](https://github.com/hoangisreal/RiskLab/actions/workflows/ci.yml).
VPS/TLS, image-registry release và off-VPS recovery chưa được nghiệm thu.
Không suy production readiness từ việc CI pass.

## Phạm vi công khai và source identity

Public candidate files, relative Markdown links và whitespace được kiểm tra
bằng `scripts/check_public_files.py --links` và `git diff --check`. CI chạy cùng
policy. Local pre-push guard kiểm tra từng outgoing commit, kể cả private file
được thêm rồi xóa trước tip; với ref mới kiểm tra toàn ancestry. Hook local
không đi theo clone; CLI và CI có trong repository.

Cleanup/history mapping và giới hạn audit tại [source revisions](source-revisions.md).
Local guides, Git history gốc và backup bundles giữ nguyên. Frozen protocol,
locks, references, metric tolerances và bytes của RESULTS/model card/leakage
không đổi. Không tạo lock/promotion hoặc mở Test thật cho đợt này.
