# Fixtures synthetic cho business/import contract

Đây là fixtures synthetic viết tay; không phải dữ liệu khách hàng thật hoặc
catalog được triển khai. Catalog runtime được chuẩn bị riêng bởi demo-init.

- `catalog.json`: ba snapshot mẫu, payload raw scoring contract 1.1. ID là
  lookup key opaque, không phải đường dẫn filesystem.
- `import-valid.csv`: hai hồ sơ READY và một INCOMPLETE không được scoring.
- `import-unknown-snapshot.csv`: từ chối snapshot lạ, không fetch URL/path.
- `import-duplicate-reference.csv`: từ chối toàn import có reference trùng.
- `import-path-injection.csv`: từ chối path giả làm snapshot ID.
- `import-extra-column.csv`: từ chối header/label ngoài contract.
- `invalid-history.json`: từ chối realized event ở tương lai; không thuộc
  trusted catalog. Dữ liệu sai khác nguồn chưa cung cấp hoặc không có lịch sử.

Contracts thực thi: [business schemas](../../../src/risklab/business/schemas.py)
và [import parser](../../../src/risklab/business/import_csv.py).

Tests schema/shared SQL kiểm tra ý nghĩa từng fixture. Kiểm chứng importer,
worker và PostgreSQL dùng [import tests](../../business/test_imports.py) và
[workflow tests](../../business/test_workflow.py); fixture tự nó không chứng
minh ứng dụng hoặc môi trường production đã được nghiệm thu.
