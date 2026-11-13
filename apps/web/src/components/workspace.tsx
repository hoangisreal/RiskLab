"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  LayoutDashboard,
  Files,
  ListChecks,
  Upload,
  Boxes,
  ShieldCheck,
  Settings,
  ChevronsUpDown,
} from "lucide-react";
import { useSession } from "./session";
const items = [
  ["/", "Overview", LayoutDashboard, "Tổng quan"],
  ["/applications", "Applications", Files, "Hồ sơ"],
  ["/review-queue", "Review Queue", ListChecks, "Hàng đợi review"],
  ["/imports", "Data Imports", Upload, "Import hồ sơ"],
  ["/models", "Model Registry", Boxes, "Model Registry"],
  ["/audit", "Audit Logs", ShieldCheck, "Audit Logs"],
  ["/settings", "Settings", Settings, "Settings"],
] as const;
export function WorkspaceNavigation() {
  const { user } = useSession();
  const pathname = usePathname();
  return (
    <aside className="sidebar">
      <div className="workspace-switch">
        <span className="workspace-mark">R</span>
        <span>
          RiskLab Workspace<small>Credit decision support</small>
        </span>
        <ChevronsUpDown size={14} />
      </div>
      <p className="nav-label">WORKSPACE</p>
      <nav aria-label="Điều hướng chính">
        {items
          .filter(
            ([href]) =>
              href === "/" ||
              href === "/settings" ||
              (user &&
                (user.role === "ADMIN"
                  ? href === "/audit"
                  : href !== "/imports" || user.role === "MANAGER")),
          )
          .map(([href, label, Icon, vi]) => (
            <Link
              key={href}
              href={href}
              aria-label={vi}
              aria-current={
                (href === "/" ? pathname === href : pathname.startsWith(href))
                  ? "page"
                  : undefined
              }
            >
              <Icon size={17} />
              <span>{label}</span>
            </Link>
          ))}
        {user?.role === "ADMIN" && (
          <Link
            href="/admin/users"
            aria-current={pathname.startsWith("/admin") ? "page" : undefined}
          >
            <ShieldCheck size={17} />
            Quản lý tài khoản
          </Link>
        )}
      </nav>
      <div className="sidebar-note">
        <span className="status-dot" />
        Simulation environment
        <small>Dữ liệu synthetic. Quyết định có con người kiểm soát.</small>
      </div>
    </aside>
  );
}
