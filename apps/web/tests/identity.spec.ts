import { test, expect, type Page } from "@playwright/test";

async function login(page: Page, username: string) {
  await page.goto("/login");
  await page.getByLabel("Tên đăng nhập", { exact: true }).fill(username);
  await page
    .getByLabel("Mật khẩu", { exact: true })
    .fill("synthetic-browser-password");
  await page.getByRole("button", { name: "Đăng nhập", exact: true }).click();
  await expect(page.getByRole("button", { name: "Đăng xuất" })).toBeVisible();
}

test("Admin creates, changes and disables an account; active session is revoked", async ({
  page,
  browser,
}, info) => {
  await login(page, "admin.browser");
  await expect(
    page.getByRole("heading", { name: "Quản lý tài khoản", exact: true }),
  ).toBeVisible();
  const username = `analyst.${info.project.name}`;
  await page.getByLabel("Tên đăng nhập mới").fill(username);
  await page.getByLabel("Mật khẩu khởi tạo").fill("synthetic-browser-password");
  await page
    .getByRole("button", { name: "Tạo tài khoản", exact: true })
    .click();
  await expect(
    page.getByRole("status").filter({ hasText: "Đã tạo tài khoản" }),
  ).toBeVisible();

  const other = await browser.newContext({
    baseURL: new URL(page.url()).origin,
  });
  const analyst = await other.newPage();
  await login(analyst, username);
  await page
    .getByRole("button", { name: `Quản lý ${username}`, exact: true })
    .click();
  await page.getByLabel("Vai trò tài khoản").selectOption("MANAGER");
  await page.getByLabel("Lý do thay đổi").fill("Browser test role change");
  await page.getByRole("button", { name: "Lưu quyền và trạng thái" }).click();
  await expect(
    page.getByRole("status").filter({ hasText: "Đã cập nhật" }),
  ).toBeVisible();
  // A revoked session must also disappear from a still-mounted client shell on logout.
  await analyst.getByRole("button", { name: "Đăng xuất" }).click();
  await expect(
    analyst
      .getByRole("banner")
      .getByRole("link", { name: "Đăng nhập", exact: true }),
  ).toBeVisible();
  await analyst.goto("/admin/users");
  await expect(
    analyst.getByRole("heading", {
      name: "Phiên đăng nhập không còn hiệu lực",
    }),
  ).toBeVisible();

  await page.getByLabel("Trạng thái tài khoản").selectOption("false");
  await page.getByLabel("Lý do thay đổi").fill("Browser test account disable");
  page.once("dialog", (dialog) => dialog.accept());
  await page.getByRole("button", { name: "Lưu quyền và trạng thái" }).click();
  await expect(page.getByLabel("Trạng thái tài khoản")).toHaveValue("false");
  await page.screenshot({
    path: info.outputPath("accounts.png"),
    fullPage: true,
  });
  await other.close();
});

for (const role of ["analyst", "manager"]) {
  test(`${role} cannot administer accounts even using direct API`, async ({
    page,
  }) => {
    await login(page, `${role}.browser`);
    await expect(
      page.getByRole("link", { name: "Quản lý tài khoản", exact: true }),
    ).toHaveCount(0);
    expect((await page.request.get("/api/v1/admin/users")).status()).toBe(403);
    await page.goto("/admin/users");
    await expect(
      page.getByRole("heading", { name: "Không có quyền quản trị tài khoản" }),
    ).toBeVisible();
    await page.getByRole("button", { name: "Đăng xuất" }).click();
    await expect(
      page
        .getByRole("banner")
        .getByRole("link", { name: "Đăng nhập", exact: true }),
    ).toBeVisible();
  });
}
