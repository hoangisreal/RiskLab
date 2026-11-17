"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import {
  Files,
  FlaskConical,
  LayoutDashboard,
  ListChecks,
  Menu,
  Newspaper,
  Settings,
  ShieldCheck,
  Upload,
  X,
} from "lucide-react";
import { useSession } from "./session";

type Item = {
  href: string;
  label: string;
  aria: string;
  Icon: typeof Files;
  roles: Array<"ADMIN" | "MANAGER" | "ANALYST">;
};

const items: Item[] = [
  {
    href: "/",
    label: "Tổng quan",
    aria: "Tổng quan",
    Icon: LayoutDashboard,
    roles: ["MANAGER", "ANALYST"],
  },
  {
    href: "/applications",
    label: "Hồ sơ",
    aria: "Hồ sơ",
    Icon: Files,
    roles: ["MANAGER", "ANALYST"],
  },
  {
    href: "/review-queue",
    label: "Hàng đợi review",
    aria: "Hàng đợi review",
    Icon: ListChecks,
    roles: ["MANAGER", "ANALYST"],
  },
  {
    href: "/imports",
    label: "Import hồ sơ",
    aria: "Import hồ sơ",
    Icon: Upload,
    roles: ["MANAGER"],
  },
  {
    href: "/reports",
    label: "Báo cáo",
    aria: "Báo cáo",
    Icon: Newspaper,
    roles: ["MANAGER", "ANALYST"],
  },
  {
    href: "/evidence",
    label: "Bằng chứng ML",
    aria: "Bằng chứng ML",
    Icon: FlaskConical,
    roles: ["MANAGER", "ANALYST"],
  },
  {
    href: "/audit",
    label: "Nhật ký audit",
    aria: "Audit Logs",
    Icon: ShieldCheck,
    roles: ["ADMIN"],
  },
  {
    href: "/settings",
    label: "Cài đặt",
    aria: "Settings",
    Icon: Settings,
    roles: ["ADMIN", "MANAGER", "ANALYST"],
  },
];

function currentFor(href: string, pathname: string): boolean {
  return href === "/" ? pathname === href : pathname.startsWith(href);
}

export function WorkspaceNavigation() {
  const { user } = useSession();
  const pathname = usePathname();
  const [open, setOpen] = useState(false);
  const toggleRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLElement>(null);

  const previousPath = useRef(pathname);
  useEffect(() => {
    // Close the drawer after in-app navigation without a cascading render:
    // links already close it on click; this only covers back/forward moves.
    if (previousPath.current !== pathname && open) {
      previousPath.current = pathname;
      const timer = window.setTimeout(() => setOpen(false), 0);
      return () => window.clearTimeout(timer);
    }
    previousPath.current = pathname;
    return undefined;
  }, [pathname, open]);

  useEffect(() => {
    if (!open) {
      toggleRef.current?.focus();
      return;
    }
    panelRef.current?.querySelector<HTMLElement>("nav a")?.focus();
  }, [open]);

  useEffect(() => {
    if (!open) return;
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") setOpen(false);
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open]);

  if (!user) return null;

  const visible = items.filter((item) => item.roles.includes(user.role));
  const adminUsers = user.role === "ADMIN";

  const nav = (
    <>
      <p className="nav-label">ĐIỀU HƯỚNG</p>
      <nav aria-label="Điều hướng chính">
        {visible.map(({ href, label, aria, Icon }) => (
          <Link
            key={href}
            href={href}
            aria-label={aria}
            aria-current={currentFor(href, pathname) ? "page" : undefined}
            onClick={() => setOpen(false)}
          >
            <Icon size={17} aria-hidden="true" />
            <span>{label}</span>
          </Link>
        ))}
        {adminUsers ? (
          <Link
            href="/admin/users"
            aria-label="Quản lý tài khoản"
            aria-current={pathname.startsWith("/admin") ? "page" : undefined}
            onClick={() => setOpen(false)}
          >
            <ShieldCheck size={17} aria-hidden="true" />
            <span>Quản lý tài khoản</span>
          </Link>
        ) : null}
      </nav>
      <div className="sidebar-note">
        <span className="status-dot" aria-hidden="true" />
        Môi trường mô phỏng
        <small>Dữ liệu synthetic. Quyết định do con người thực hiện.</small>
      </div>
    </>
  );

  return (
    <>
      <button
        ref={toggleRef}
        type="button"
        className="menu-toggle"
        aria-expanded={open}
        aria-controls="workspace-nav"
        aria-label={open ? "Đóng menu điều hướng" : "Mở menu điều hướng"}
        onClick={() => setOpen((value) => !value)}
      >
        {open ? (
          <X size={20} aria-hidden="true" />
        ) : (
          <Menu size={20} aria-hidden="true" />
        )}
      </button>
      {open ? (
        <button
          type="button"
          className="sidebar-scrim"
          aria-label="Đóng menu điều hướng"
          onClick={() => setOpen(false)}
        />
      ) : null}
      <aside
        id="workspace-nav"
        ref={panelRef}
        className={`sidebar${open ? " open" : ""}`}
      >
        {nav}
      </aside>
    </>
  );
}
