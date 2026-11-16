import type { Metadata } from "next";
import Link from "next/link";
import "./globals.css";
import { WorkspaceNavigation } from "@/components/workspace";
import { SessionProvider, SessionNavigation } from "@/components/session";

export const metadata: Metadata = {
  title: {
    default: "RiskLab · Phân tích tín dụng mô phỏng",
    template: "%s · RiskLab",
  },
  description:
    "Không gian review hồ sơ demo và hỗ trợ quyết định tín dụng mô phỏng.",
  robots: { index: false, follow: false },
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="vi">
      <body>
        <SessionProvider>
          <a className="skip-link" href="#main">
            Đến nội dung chính
          </a>
          <header className="topbar">
            <div className="topbar-inner">
              <Link href="/" className="brand" aria-label="RiskLab — tổng quan">
                <svg
                  width="24"
                  height="24"
                  viewBox="0 0 24 24"
                  aria-hidden="true"
                >
                  <path
                    d="M3 19V5h7a5 5 0 0 1 0 10H3m10-1 7 5"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="2"
                  />
                </svg>
                RiskLab
              </Link>
              <WorkspaceNavigation />
              <SessionNavigation />
            </div>
          </header>
          <details className="simulation" open>
            <summary>
              Chỉ mô phỏng hỗ trợ quyết định — không dùng cho phê duyệt tín dụng
              thực tế.
            </summary>
            <div className="simulation-body">
              Điểm số model là thông tin tham khảo. Đề xuất của Analyst và quyết
              định của Manager là hai bước độc lập do con người thực hiện và
              chịu trách nhiệm.
            </div>
          </details>
          <main id="main" className="canvas">
            {children}
          </main>
          <footer className="footer">
            RiskLab · Dữ liệu demo synthetic · Quyết định do con người thực hiện
          </footer>
        </SessionProvider>
      </body>
    </html>
  );
}
