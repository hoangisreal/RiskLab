"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
} from "react";
import Link from "next/link";
import { api, ApiError, mutate, setCsrfToken } from "@/lib/api";

export type User = {
  id: string;
  username: string;
  role: "ADMIN" | "MANAGER" | "ANALYST";
  is_active: boolean;
  revision: number;
  created_at: string;
};
type Session = {
  user: User | null;
  pending: boolean;
  refresh: () => Promise<void>;
};
const Context = createContext<Session>({
  user: null,
  pending: true,
  refresh: async () => {},
});

export function SessionProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [pending, setPending] = useState(true);
  const refresh = useCallback(async () => {
    try {
      setUser(await api<User>("/api/v1/auth/me"));
    } catch {
      setUser(null);
      setCsrfToken(undefined);
    } finally {
      setPending(false);
    }
  }, []);
  useEffect(() => {
    let mounted = true;
    void api<User>("/api/v1/auth/me")
      .then((value) => {
        if (mounted) setUser(value);
      })
      .catch(() => {
        if (mounted) setUser(null);
      })
      .finally(() => {
        if (mounted) setPending(false);
      });
    return () => {
      mounted = false;
    };
  }, []);
  return (
    <Context.Provider value={{ user, pending, refresh }}>
      {children}
    </Context.Provider>
  );
}

export function useSession() {
  return useContext(Context);
}

export function SessionNavigation() {
  const { user, pending, refresh } = useSession();
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);
  async function signOut() {
    setBusy(true);
    setFailed(false);
    try {
      await mutate("/api/v1/auth/logout");
      setCsrfToken(undefined);
      await refresh();
    } catch (error) {
      if (error instanceof ApiError && error.status === 401) {
        setCsrfToken(undefined);
        await refresh();
      } else {
        setFailed(true);
      }
    } finally {
      setBusy(false);
    }
  }
  if (pending)
    return (
      <span className="workspace" role="status">
        Đang kiểm tra phiên…
      </span>
    );
  if (!user) return <Link href="/login">Đăng nhập</Link>;
  return (
    <nav aria-label="Tài khoản" className="session-nav">
      {user.role === "ADMIN" && (
        <Link href="/admin/users">Quản lý tài khoản</Link>
      )}
      {user.role === "MANAGER" && <Link href="/imports">Import hồ sơ</Link>}
      {user.role !== "ADMIN" && <Link href="/applications">Hồ sơ</Link>}
      {user.role !== "ADMIN" && <Link href="/reports">Báo cáo</Link>}
      <span className="workspace">
        {user.username} · {user.role}
      </span>
      <button onClick={() => void signOut()} disabled={busy}>
        Đăng xuất
      </button>
      {failed && <span role="alert">Chưa đăng xuất được. Thử lại.</span>}
    </nav>
  );
}
