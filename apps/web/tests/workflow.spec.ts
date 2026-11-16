import { expect, test, type Page, type Route } from "@playwright/test";
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

/** The mobile navigation lives in a closed drawer; open it before nav links. */
async function openNav(page: Page) {
  const toggle = page.getByRole("button", { name: "Mở menu điều hướng" });
  if (await toggle.isVisible()) await toggle.click();
}

test("manager import to analyst review to manager decision", async ({
  page,
  browser,
}, info) => {
  test.setTimeout(240_000);
  const prefix = `E2E-${info.project.name.toUpperCase()}`;
  await login(page, `manager.flow${info.project.name}`);
  const heldMetadata: Route[] = [];
  await page.route("**/api/v1/models/current", (route) => {
    heldMetadata.push(route);
  });
  await page.goto("/");
  await expect(page.getByText("Tổng hồ sơ", { exact: true })).toBeVisible({
    timeout: 5000,
  });
  for (const route of heldMetadata) await route.abort();
  await page.unroute("**/api/v1/models/current");
  await page.goto("/models");
  await expect(
    page.getByRole("heading", { name: "Model Registry", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText("synthetic-browser-v1", { exact: true }),
  ).toBeVisible();
  await page.goto("/settings");
  await expect(
    page.getByRole("heading", { name: "Settings", exact: true }),
  ).toBeVisible();
  await page.goto("/review-queue");
  await expect(
    page.getByRole("heading", { name: "Hàng đợi review", exact: true }),
  ).toBeVisible();
  await openNav(page);
  await page.getByRole("link", { name: "Import hồ sơ" }).click();
  await expect(
    page.getByRole("heading", { name: "Import hồ sơ demo" }),
  ).toBeVisible();
  await page.getByLabel("Tệp CSV import").setInputFiles(importFile(prefix));
  await page.evaluate(() => {
    const state = window as typeof window & { importKeys: string[] };
    state.importKeys = [];
    const original = window.fetch;
    let dropped = false;
    window.fetch = async (input, options) => {
      const response = await original(input, options);
      if (input === "/api/v1/imports" && options?.method === "POST") {
        state.importKeys.push(
          new Headers(options.headers).get("Idempotency-Key") ?? "",
        );
        if (!dropped && response.status === 202) {
          dropped = true;
          throw new TypeError("Synthetic response lost after commit");
        }
      }
      return response;
    };
  });
  await page.getByRole("button", { name: "Import hồ sơ", exact: true }).click();
  await expect(page.locator("main").getByRole("alert")).toContainText(
    "Không kết nối",
  );
  // Another tab rotates CSRF before the explicit retry of the same upload.
  await page.request.get("/api/v1/auth/csrf");
  await page.getByRole("button", { name: "Import hồ sơ", exact: true }).click();
  await expect(page.getByText(/Đã nhận import/)).toBeVisible();
  const importKeys = await page.evaluate(
    () => (window as typeof window & { importKeys: string[] }).importKeys,
  );
  expect(new Set(importKeys).size).toBe(1);
  expect(importKeys.length).toBeGreaterThanOrEqual(3);

  // Wait for the real worker independently of table hydration. Reloading and
  // immediately counting rows can repeatedly observe the loading skeleton.
  await expect
    .poll(
      async () => {
        const response = await page.request.get("/api/v1/applications", {
          params: { q: prefix, page_size: 20 },
        });
        expect(response.ok()).toBe(true);
        const { data } = (await response.json()) as {
          data: { scoring_state: string }[];
        };
        return (
          data.length === 3 &&
          data.some((row) => row.scoring_state === "SUCCEEDED")
        );
      },
      { timeout: 120_000 },
    )
    .toBe(true);
  await page.goto("/applications");
  await filterByReference(page, prefix);
  await expect(page.getByRole("link", { name: "Xem hồ sơ" })).toHaveCount(3);
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
  const secondTab = await analystContext.newPage();
  await secondTab.goto(analyst.url());
  await expect(secondTab.getByLabel("Nhận xét review")).toBeVisible();
  await analyst.getByLabel("Nhận xét review").fill("Bản mới từ tab thứ nhất.");
  const heldReload: Route[] = [];
  const applicationPath = new URL(analyst.url()).pathname;
  await analyst.route(`**/api/v1${applicationPath}`, (route) => {
    heldReload.push(route);
  });
  await analyst.getByRole("button", { name: "Lưu nháp", exact: true }).click();
  await expect(analyst.getByRole("status")).toContainText("Đang tải dữ liệu");
  await expect(
    analyst.getByRole("button", { name: "Lưu nháp", exact: true }),
  ).toHaveCount(0);
  await expect.poll(() => heldReload.length).toBe(1);
  await heldReload[0].fulfill({
    status: 503,
    json: { error: { code: "UNAVAILABLE" } },
  });
  await expect(analyst.locator("main").getByRole("alert")).toBeVisible();
  await expect(
    analyst.getByRole("button", { name: "Lưu nháp", exact: true }),
  ).toHaveCount(0);
  await expect(analyst.getByText("Đã lưu bản nháp.")).toHaveCount(0);
  await analyst.unroute(`**/api/v1${applicationPath}`);
  await analyst.getByRole("button", { name: "Thử lại", exact: true }).click();
  await expect(analyst.getByText("Đã lưu bản nháp.")).toBeVisible();
  await expect(
    analyst.getByRole("button", { name: "Lưu nháp", exact: true }),
  ).toBeEnabled();
  await secondTab
    .getByLabel("Nhận xét review")
    .fill("Bản cũ chưa được đồng bộ.");
  await secondTab
    .getByRole("button", { name: "Lưu nháp", exact: true })
    .click();
  await expect(secondTab.locator("main").getByRole("alert")).toContainText(
    "Dữ liệu đã thay đổi",
  );
  await expect(
    secondTab.getByRole("button", { name: "Lưu nháp", exact: true }),
  ).toBeDisabled();
  await expect(secondTab.getByLabel("Nhận xét review")).toHaveValue(
    "Bản cũ chưa được đồng bộ.",
  );

  await secondTab.getByRole("button", { name: "Tải bản mới nhất" }).click();
  await secondTab
    .getByRole("alertdialog")
    .getByRole("button", { name: "Xác nhận", exact: true })
    .click();
  await expect(secondTab.getByLabel("Nhận xét review")).toHaveValue(
    "Bản mới từ tab thứ nhất.",
  );
  await secondTab.close();
  await analyst.getByLabel("Nhận xét review").fill("Đã kiểm tra dữ liệu demo.");
  await analyst.getByLabel("Đề xuất của Analyst").selectOption("DECLINE");
  await analyst.route("**/reviews/*/submit", (route) =>
    route.fulfill({
      status: 422,
      contentType: "application/json",
      body: JSON.stringify({ error: { code: "VALIDATION_ERROR" } }),
    }),
  );
  await analyst.getByRole("button", { name: "Gửi đề xuất" }).click();
  await expect(analyst.locator("main").getByRole("alert")).toContainText(
    "Dữ liệu không hợp lệ",
  );
  await analyst.unroute("**/reviews/*/submit");
  await analyst
    .getByLabel("Nhận xét review")
    .fill("Đã kiểm tra dữ liệu demo và xác nhận lại.");
  // Opening and closing disclosures must not clear the draft form.
  await analyst.getByText("Nguồn dữ liệu và provenance").click();
  await analyst.getByText("Nguồn dữ liệu và provenance").click();
  await expect(analyst.getByLabel("Nhận xét review")).toHaveValue(
    "Đã kiểm tra dữ liệu demo và xác nhận lại.",
  );
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
  await page.request.get("/api/v1/auth/csrf");

  await page.getByRole("button", { name: "Chốt quyết định mô phỏng" }).click();
  await page
    .getByRole("alertdialog")
    .getByRole("button", { name: "Hủy", exact: true })
    .click();
  await expect(
    page.getByRole("button", { name: "Chốt quyết định mô phỏng" }),
  ).toBeFocused();
  await page.getByRole("button", { name: "Chốt quyết định mô phỏng" }).click();
  await page
    .getByRole("alertdialog")
    .getByRole("button", { name: "Xác nhận", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "Quyết định cuối (mô phỏng)" }),
  ).toBeVisible({ timeout: 30_000 });
  await expect(page.getByText(/không mở lại hoặc sửa/)).toBeVisible();

  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({
    path: info.outputPath("decision.png"),
    fullPage: true,
  });
  await page.route("**/api/v1/applications/*/history?*", async (route) => {
    const current = Number(
      new URL(route.request().url()).searchParams.get("page"),
    );
    await route.fulfill({
      json: {
        data: Array.from({ length: current <= 2 ? 100 : 1 }, (_, index) => ({
          id:
            current === 2 && index === 0
              ? "history-1-99"
              : `history-${current}-${index}`,
          action: current <= 2 ? "synthetic.recent" : "synthetic.oldest",
          actor_type: "SYSTEM",
          created_at: "2026-11-13T00:00:00Z",
          reason: null,
        })),
        pagination: { page: current, page_size: 100, total: 201 },
      },
    });
  });
  await page.reload();
  await page
    .getByRole("button", { name: "Tải thêm lịch sử", exact: true })
    .click();
  await page
    .getByRole("button", { name: "Tải thêm lịch sử", exact: true })
    .click();
  await expect(
    page.getByText("synthetic.oldest", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Tải thêm lịch sử", exact: true }),
  ).toHaveCount(0);
  await page.unroute("**/api/v1/applications/*/history?*");
  await logout(page);
  await analystContext.close();
});

test("admin has no business access and analyst list is scoped", async ({
  page,
}, info) => {
  await page.goto("/reports");
  await expect(
    page.getByRole("heading", { name: "Phiên đăng nhập không còn hiệu lực" }),
  ).toBeVisible();
  await login(page, `admin.flow${info.project.name}`);
  await page.goto("/audit");
  await expect(
    page.getByRole("heading", { name: "Audit Logs", exact: true }),
  ).toBeVisible();
  await expect(page.getByLabel("Tìm trong trang")).toBeVisible();
  await page.getByLabel("Tìm trong trang").fill("no-such-synthetic-event");
  await expect(page.getByText("Không có kết quả phù hợp.")).toBeVisible();
  // Isolate pagination rendering from the real-API authorization check above.
  const heldAudit: Route[] = [];
  await page.route("**/api/v1/admin/audit-events?*", async (route) => {
    const current = Number(
      new URL(route.request().url()).searchParams.get("page"),
    );
    if (current === 2) {
      heldAudit.push(route);
      return;
    }
    await route.fulfill({
      json: {
        data: Array.from({ length: 20 }, (_, index) => ({
          id: `first-${index}`,
          action: "synthetic.first-page",
          actor_id: "synthetic-actor",
          resource_id: "synthetic-resource",
          reason: "First page",
          created_at: "2026-11-13T00:00:00Z",
        })),
        pagination: { page: 1, page_size: 20, total: 40 },
      },
    });
  });
  await page.reload();
  await expect(page.getByText("First page").first()).toBeVisible();
  await page.getByRole("button", { name: "Trang sau", exact: true }).click();
  await expect(page.getByRole("status")).toContainText("Đang tải dữ liệu");
  await expect(page.getByText("First page")).toHaveCount(0);
  await expect.poll(() => heldAudit.length).toBe(1);
  await heldAudit[0].fulfill({
    json: {
      data: Array.from({ length: 20 }, (_, index) => ({
        id: `last-${index}`,
        action: "synthetic.last-page",
        actor_id: "synthetic-actor",
        resource_id: "synthetic-resource",
        reason: "Last page",
        created_at: "2026-11-13T00:00:00Z",
      })),
      pagination: { page: 2, page_size: 20, total: 40 },
    },
  });
  await expect(page.getByText("Last page").first()).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Trang sau", exact: true }),
  ).toBeDisabled();
  await page.unroute("**/api/v1/admin/audit-events?*");
  await page.goto("/settings");
  await expect(
    page.getByRole("heading", { name: "Settings", exact: true }),
  ).toBeVisible();
  await expect(
    page.locator("main").getByRole("link", { name: /Quản lý tài khoản/ }),
  ).toBeVisible();
  await page.goto("/reports");
  await expect(
    page.getByRole("heading", { name: "Không có quyền xem báo cáo nghiệp vụ" }),
  ).toBeVisible();
  await page.goto("/applications/00000000-0000-4000-8000-000000000001");
  await expect(
    page.getByRole("heading", { name: "Không có quyền truy cập hồ sơ" }),
  ).toBeVisible();
  await expect(page.getByRole("link", { name: "Hồ sơ" })).toHaveCount(0);
  const denied = await page.request.get("/api/v1/applications");
  expect(denied.status()).toBe(403);
  await logout(page);

  await login(page, `analyst.flow${info.project.name}`);
  await openNav(page);
  await page.getByRole("link", { name: "Hồ sơ", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Hồ sơ", exact: true }),
  ).toBeVisible();
});
