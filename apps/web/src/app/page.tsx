"use client";
import { useSession } from "@/components/session";
import { OverviewDashboard } from "@/components/resource-page";
import { ConnectionStatus } from "@/components/connection-status";

export default function Home() {
  const { user } = useSession();
  if (user)
    return (
      <>
        <OverviewDashboard />
        <section className="sheet section-gap">
          <h2>Kết nối ứng dụng</h2>
          <ConnectionStatus />
        </section>
      </>
    );
  return (
    <>
      <div className="page-heading">
        <h1>Không gian phân tích tín dụng</h1>
        <p>Kiểm tra môi trường ứng dụng trước khi bắt đầu xử lý hồ sơ demo.</p>
      </div>
      <section className="sheet" aria-labelledby="connection-heading">
        <h2 id="connection-heading">Kết nối ứng dụng</h2>
        <p>
          Trạng thái dưới đây được lấy trực tiếp từ FastAPI. API đang hoạt động
          không đồng nghĩa model hoặc cơ sở dữ liệu đã sẵn sàng.
        </p>
        <ConnectionStatus />
      </section>
      <section className="responsibilities" aria-labelledby="roles-heading">
        <h2 id="roles-heading">Phân tách trách nhiệm</h2>
        <dl>
          <div>
            <dt>Analyst</dt>
            <dd>
              Kiểm tra hồ sơ, ghi chú và đề xuất. Không tự chốt quyết định.
            </dd>
          </div>
          <div>
            <dt>Manager</dt>
            <dd>Review đề xuất và chốt quyết định mô phỏng, có lưu vết.</dd>
          </div>
          <div>
            <dt>Admin</dt>
            <dd>
              Quản lý tài khoản và quyền truy cập, không thay thế vai trò nghiệp
              vụ.
            </dd>
          </div>
        </dl>
      </section>
    </>
  );
}
