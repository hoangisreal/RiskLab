import Link from "next/link";
export default function NotFound() {
  return (
    <>
      <h1>Không tìm thấy trang</h1>
      <p>Địa chỉ không tồn tại hoặc không còn khả dụng.</p>
      <Link href="/">Về tổng quan</Link>
    </>
  );
}
