"use client";
import { ErrorState } from "@/components/states";
export default function Error({ reset }: { reset: () => void }) {
  return (
    <ErrorState retry={reset}>
      Không tải được nội dung. Hãy thử lại; nếu lỗi tiếp tục, liên hệ người vận
      hành.
    </ErrorState>
  );
}
