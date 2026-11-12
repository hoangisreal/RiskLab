import type { ReactNode } from "react";

export function LoadingState({
  children = "Đang tải dữ liệu…",
}: {
  children?: ReactNode;
}) {
  return (
    <p className="notice" role="status">
      {children}
    </p>
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
