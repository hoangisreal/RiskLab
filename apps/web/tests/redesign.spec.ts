import { test, expect, type Page } from "@playwright/test";

// NOTE: the API allows 10 login attempts per IP every 5 minutes for the whole
// run, and the default suite already spends 8 (identity desktop + mobile).
// This file adds exactly 2 (one manager login per project). Do not add more
// logins here; move extra checks into the workflow suite, which has its own
// backend and budget.

async function login(page: Page, username: string) {
  await page.goto("/login");
  await page.getByLabel("Tên đăng nhập", { exact: true }).fill(username);
  await page
    .getByLabel("Mật khẩu", { exact: true })
    .fill("synthetic-browser-password");
  await page.getByRole("button", { name: "Đăng nhập", exact: true }).click();
  await expect(page.getByRole("button", { name: "Đăng xuất" })).toBeVisible();
}

test("manager drawer, filters in URL and back navigation", async ({
  page,
}, info) => {
  await login(page, "manager.browser");
  if (info.project.name === "mobile") {
    await page.goto("/");
    const toggle = page.getByRole("button", { name: "Mở menu điều hướng" });
    await expect(toggle).toBeVisible();
    await toggle.click();
    await expect(
      page.getByRole("link", { name: "Báo cáo", exact: true }),
    ).toBeVisible();
    await expect(
      page.getByRole("link", { name: "Hồ sơ", exact: true }),
    ).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(
      page.getByRole("button", { name: "Đóng menu điều hướng" }),
    ).toHaveCount(0);
    await expect(toggle).toBeVisible();
  }
  await page.goto("/applications");
  await page.getByLabel("Mã hồ sơ").fill("zzz-no-such-application");
  await page.getByRole("button", { name: "Lọc hồ sơ" }).click();
  await expect(page).toHaveURL(/q=zzz-no-such-application/);
  await expect(page.getByText("Tổng 0 hồ sơ khớp bộ lọc")).toBeVisible();
  await page.goto("/reports");
  await page.goBack();
  await expect(page).toHaveURL(/q=zzz-no-such-application/);
  await expect(page.getByLabel("Mã hồ sơ")).toHaveValue(
    "zzz-no-such-application",
  );
  await page.getByRole("button", { name: "Xóa bộ lọc" }).click();
  await expect(page).not.toHaveURL(/q=/);
});
