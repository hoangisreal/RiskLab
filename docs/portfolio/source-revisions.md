# Source identity sau cleanup GitHub — 2026-11-16

Owner yêu cầu bỏ file instructions/settings/tasks của coding assistant khỏi
GitHub, giữ nguyên local để tiếp tục phát triển. Cleanup thực hiện trên mirror
riêng sau khi tạo và verify backup bundle local; không reset/rewrite repo làm
việc, không xóa local guides, không sửa locks hoặc các references ML lịch sử.

12 private paths được gỡ khỏi 30 commit. Kiểm tra từng old/new commit xác nhận
tree `src`, `configs`, `sql` giống hệt trước/sau cleanup. Hai writable branches
được cập nhật atomic với explicit force-with-lease, không ghi đè remote changes
không nằm trong kế hoạch. Không force-push từ repository local giữ lịch sử cũ.

| Revision lịch sử | Revision public tương ứng |
|---|---|
| `9237788f3e7a07f2b6661439e591dc37a869f480` — source của frozen Test evidence | `2ed0134fa0a52fa4a6aa4b52719e5a70f7b7e817` |
| `d1997186f36a4131ec2ff133748c5641d1eb6a11` — documentation snapshot | `b9f85066c30b62d739ee8ff29aa631f8142b3642` |
| `3367ae6756bd1c2f0def9c7e9e564dd2bbca1ec2` — branch fix/complete-risklab | `d6acceead5293b3cc8870dc9b851af93ff4811dd` |
| `555c32cf65569d941a7087716ea1a7b63a29852e` — master trước cleanup | `4617112b79fc4b629cfb8e870048f1cdc32f069c` |

Commit ID thay đổi do bỏ file/lịch sử cha. Model card/RESULTS giữ nguyên
original run/lock/source IDs; bảng trên giải thích public source tương ứng,
không thay thế provenance trong lock. `verify_lock` vẫn kiểm tra original
commit identity; không bỏ check hoặc sửa lock để công nhận revision mới.
Audit cần original commit phải dùng bản local/backup cũ; khóa một model/release
mới cần quy trình và authorization riêng, Test không tự mở lại.

## Phạm vi và giới hạn GitHub

Rewrite branch không loại được merged PR ref `refs/pull/1/head` chứa ancestry
cũ. Owner đã cho phép xóa/tạo lại repository cùng URL để thay graph công khai,
sau khi backup được verify; local history và hướng dẫn vẫn giữ nguyên.
Chỉ hai branch đã cleanup được xuất bản, không mirror refs từ bản Git gốc.
Hai prototype research commits được giữ trong backup local; bản public dùng
một commit tổng hợp của implementation/report hiện hành trên parent đã sạch.

Kiểm chứng yêu cầu clone mới, kiểm kê mọi advertised ref và quét từng reachable
commit tree bằng public-path policy, không chỉ kiểm tra tip. Settings repository
và Pages source `master:/docs` được lưu để khôi phục; Actions được chạy lại.
Không dùng rewrite để thay bằng chứng model hoặc mở Test.

[GitHub mô tả giới hạn PR refs và cached commits](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/removing-sensitive-data-from-a-repository).
Audit graph mới không chứng minh xóa mọi cache, backup nội bộ của GitHub hoặc
clone của bên thứ ba. Local pre-push guard chặn việc đẩy original private
history trở lại; bản public được chuẩn bị từ checkout riêng đã cleanup.
