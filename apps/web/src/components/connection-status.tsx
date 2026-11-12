"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { ErrorState, LoadingState } from "./states";

async function connection(signal?: AbortSignal): Promise<"online" | "offline"> {
  try {
    const result = await api<{ status: string }>("/api/v1/health/live", {
      signal,
    });
    return result.status === "ok" ? "online" : "offline";
  } catch {
    return "offline";
  }
}

export function ConnectionStatus() {
  const [state, setState] = useState<"loading" | "online" | "offline">(
    "loading",
  );
  useEffect(() => {
    const controller = new AbortController();
    void connection(controller.signal).then((result) => {
      if (!controller.signal.aborted) setState(result);
    });
    return () => controller.abort();
  }, []);
  if (state === "loading")
    return <LoadingState>Đang kết nối với API…</LoadingState>;
  function refresh() {
    setState("loading");
    void connection().then(setState);
  }
  if (state === "offline")
    return (
      <ErrorState retry={refresh}>
        Không kết nối được API. Kiểm tra backend rồi thử lại.
      </ErrorState>
    );
  return (
    <div className="connection">
      <span className="status good">API đang hoạt động</span>
      <button onClick={refresh}>Kiểm tra kết nối</button>
    </div>
  );
}
