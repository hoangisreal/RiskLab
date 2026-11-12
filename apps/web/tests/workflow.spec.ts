import { expect, test, type Page } from "@playwright/test";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

const PASSWORD = "synthetic-browser-password";

async function login(page: Page, username: string) {
  await page.goto("/login");
  await page.getByLabel("Tên đăng nhập", { exact: true }).fill(username);
  await page.getByLabel("Mật khẩu", { exact: true }).fill(PASSWORD);
  await page.getByRole("button", { name: "Đăng nhập", exact: true }).click();
  await expect(page.getByRole("button", { name: "Đăng xuất" })).toBeVisible();
}

async function logout(page: Page) {
  await page.getByRole("button", { name: "Đăng xuất" }).click();
  await expect(
    page.getByRole("link", { name: "Đăng nhập", exact: true }).first(),
  ).toBeVisible();
}

/** Desktop and mobile projects share one backend; unique references isolate them. */
function importFile(prefix: string): string {
  const file = path.join(
    fs.mkdtempSync(path.join(os.tmpdir(), "risklab-e2e-")),
    "import.csv",
  );
  fs.writeFileSync(
    file,
    [
      "external_reference,snapshot_id,supersedes_application_id",
      `${prefix}-001,demo-history,`,
      `${prefix}-002,demo-no-history,`,
      `${prefix}-003,demo-missing-history,`,
      "",
    ].join("\n"),
  );
  return file;
}

async function filterByReference(page: Page, prefix: string) {
  await page.getByLabel("Mã hồ sơ").fill(prefix);
  await page.getByRole("button", { name: "Lọc hồ sơ" }).click();
}

test("manager import to analyst review to manager decision", async ({
  page,
  browser,
}, info) => {
  test.setTimeout(240_000);
  const prefix = `E2E-${info.project.name.toUpperCase()}`;
  await login(page, `manager.flow${info.project.name}`);
  await page.getByRole("link", { name: "Import hồ sơ" }).click();
  await expect(
    page.getByRole("heading", { name: "Import hồ sơ demo" }),
  ).toBeVisible();
  await page.getByLabel("Tệp CSV import").setInputFiles(importFile(prefix));
  await page.getByRole("button", { name: "Import hồ sơ", exact: true }).click();
  await expect(page.getByText(/Đã nhận import/)).toBeVisible();

  await page.goto("/applications");
  await filterByReference(page, prefix);
  await expect
    .poll(
      async () => {
        await page.reload();
        await filterByReference(page, prefix);
        const links = await page
          .getByRole("link", { name: "Xem hồ sơ" })
          .count();
        const scored = await page.getByText("SUCCEEDED").first().isVisible();
        return links === 3 && scored;
      },
      { timeout: 120_000 },
    )
    .toBe(true);
  await page
    .locator("tr", { hasText: "SUCCEEDED" })
    .getByRole("link", { name: "Xem hồ sơ" })
    .first()
    .click();
  await expect(
    page.getByRole("heading", { name: new RegExp(`Hồ sơ ${prefix}-`) }),
  ).toBeVisible();
  await expect(page.getByText(/Xác suất model/)).toBeVisible({
    timeout: 60_000,
  });

  await page
    .getByLabel("Analyst đang hoạt động")
    .selectOption({ label: `analyst.flow${info.project.name}` });
  await page.getByLabel("Lý do phân công").fill("Phân công review demo");
  await page.getByRole("button", { name: "Lưu phân công" }).click();
  await expect(page.getByText("Đã phân công hồ sơ.")).toBeVisible();

  const analystContext = await browser.newContext({
    baseURL: process.env.RISKLAB_E2E_BASE_URL ?? "http://127.0.0.1:13001",
  });
  const analyst = await analystContext.newPage();
  await login(analyst, `analyst.flow${info.project.name}`);
  await analyst.goto("/applications");
  await filterByReference(analyst, prefix);
  await analyst.getByRole("link", { name: "Xem hồ sơ" }).first().click();
  await analyst.getByRole("button", { name: "Bắt đầu review" }).click();
  await expect(
    analyst.getByRole("heading", { name: /Bản nháp review/ }),
  ).toBeVisible();
  await analyst.getByLabel("Nhận xét review").fill("Đã kiểm tra dữ liệu demo.");
  await analyst.getByLabel("Đề xuất của Analyst").selectOption("DECLINE");
  await analyst.getByRole("button", { name: "Gửi đề xuất" }).click();
  await expect(analyst.getByText("Đã gửi đề xuất cho Manager.")).toBeVisible();
  // The Analyst never sees a decision action.
  await expect(
    analyst.getByRole("button", { name: "Chốt quyết định mô phỏng" }),
  ).toHaveCount(0);

  await page.reload();
  await expect(
    page.getByRole("heading", { name: "Quyết định mô phỏng của Manager" }),
  ).toBeVisible({ timeout: 30_000 });
  await page
    .getByLabel("Lý do quyết định / trả lại")
    .fill("Đã xem đề xuất; quyết định mô phỏng.");
  page.once("dialog", (dialog) => void dialog.accept());
  await page.getByRole("button", { name: "Chốt quyết định mô phỏng" }).click();
  await expect(
    page.getByRole("heading", { name: "Quyết định cuối (mô phỏng)" }),
  ).toBeVisible({ timeout: 30_000 });
  await expect(page.getByText(/không mở lại hoặc sửa/)).toBeVisible();

  await logout(page);
  await analystContext.close();
});

test("admin has no business access and analyst list is scoped", async ({
  page,
}, info) => {
  await login(page, `admin.flow${info.project.name}`);
  await expect(page.getByRole("link", { name: "Hồ sơ" })).toHaveCount(0);
  const denied = await page.request.get("/api/v1/applications");
  expect(denied.status()).toBe(403);
  await logout(page);

  await login(page, `analyst.flow${info.project.name}`);
  await page.getByRole("link", { name: "Hồ sơ", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Hồ sơ", exact: true }),
  ).toBeVisible();
});
