import type { ReactNode } from "react";
import { Skeleton } from "./primitives";

export function LoadingState({
  children = "Đang tải dữ liệu…",
}: {
  children?: ReactNode;
}) {
  return (
    <div
      role="status"
      aria-label={typeof children === "string" ? children : "Đang tải dữ liệu"}
    >
      <Skeleton lines={4} />
      <p className="notice">{children}</p>
    </div>
  );
}

export function ErrorState({
  children,
  retry,
}: {
  children: ReactNode;
  retry?: () => void;
}) {
  return (
    <div className="notice error" role="alert">
      <p>{children}</p>
      {retry && <button onClick={retry}>Thử lại</button>}
    </div>
  );
}

export function EmptyState({ children }: { children: ReactNode }) {
  return <p className="notice">{children}</p>;
}
