"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { mutate, setCsrfToken, messageFor } from "@/lib/api";
import { useSession, type User } from "@/components/session";
import { ErrorState } from "@/components/states";

export default function Login() {
  const router = useRouter();
  const { refresh } = useSession();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    setBusy(true);
    setError("");
    try {
      const result = await mutate<{ user: User; csrf_token: string }>(
        "/api/v1/auth/login",
        {
          username: data.get("username"),
          password: data.get("password"),
        },
      );
      setCsrfToken(result.csrf_token);
      await refresh();
      router.push(result.user.role === "ADMIN" ? "/admin/users" : "/");
    } catch (failure) {
      setError(messageFor(failure));
    } finally {
      setBusy(false);
    }
  }
  return (
    <>
      <div className="page-heading">
        <h1>Đăng nhập</h1>
        <p>
          Sử dụng tài khoản do Admin cấp. Không chia sẻ mật khẩu hoặc dùng dữ
          liệu người vay thật.
        </p>
      </div>
      <form className="sheet form-sheet" onSubmit={submit} aria-busy={busy}>
        <div className="field">
          <label htmlFor="username">Tên đăng nhập</label>
          <input
            id="username"
            name="username"
            autoComplete="username"
            required
            pattern="[a-z0-9_.\-]{3,64}"
            minLength={3}
            maxLength={64}
            autoCapitalize="none"
            spellCheck={false}
          />
        </div>
        <div className="field">
          <label htmlFor="password">Mật khẩu</label>
          <input
            id="password"
            name="password"
            type="password"
            autoComplete="current-password"
            minLength={12}
            maxLength={128}
            required
          />
        </div>
        <button type="submit" disabled={busy}>
          {busy ? "Đang đăng nhập…" : "Đăng nhập"}
        </button>
        {error && <ErrorState>{error}</ErrorState>}
      </form>
    </>
  );
}
