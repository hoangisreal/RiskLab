import { test, expect } from "@playwright/test";

test("same-origin health, honest readiness and accessible shell", async ({
  page,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto("/");
  await page.keyboard.press("Tab");
  await expect(
    page.getByRole("link", { name: "Đến nội dung chính" }),
  ).toBeFocused();
  await expect(page.getByRole("heading", { level: 1 })).toHaveText(
    "Không gian phân tích tín dụng",
  );
  await expect(
    page.getByText("API đang hoạt động", { exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Kiểm tra kết nối" }).click();
  await expect(
    page.getByText("API đang hoạt động", { exact: true }),
  ).toBeVisible();
  const readiness = await page.request.get("/api/v1/health/ready");
  expect(readiness.status()).toBe(200); // Disposable database is migrated; this does not assert model readiness.
  expect(await page.request.get("/predict").then((r) => r.status())).toBe(404);
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    path: test.info().outputPath("foundation.png"),
    fullPage: true,
  });
  expect(errors).toEqual([]);
});

test("connection failure can be retried without a fake success", async ({
  page,
}) => {
  await page.route("**/api/v1/health/live", (route) => route.abort());
  await page.goto("/");
  await expect(page.getByRole("main").getByRole("alert")).toContainText(
    "Không kết nối được API",
  );
  await page.unroute("**/api/v1/health/live");
  await page.getByRole("button", { name: "Thử lại" }).click();
  await expect(
    page.getByText("API đang hoạt động", { exact: true }),
  ).toBeVisible();
});
