# RiskLab web

Next.js frontend của ứng dụng mô phỏng hỗ trợ quyết định. Hướng dẫn chuẩn tại
[application guide](../../docs/development/application.md); phạm vi theo
[PRD](../../docs/product/prd.md). UI không truy cập PostgreSQL hoặc tự cấp quyền.

```bash
npm ci --ignore-scripts
npm run dev
npm run lint
npm run typecheck
npm run format:check
npm run build
npm run test:e2e
```

`RISKLAB_API_ORIGIN` là operator config (mặc định http://127.0.0.1:8000), không
phải browser input hoặc biến chứa credentials. Mở http://localhost:3000 để Origin
khớp cấu hình backend. E2E cần Docker, chạy backend/DB synthetic riêng; không dùng
production credentials. Secrets/tokens không lưu localStorage hoặc `NEXT_PUBLIC_*`.
