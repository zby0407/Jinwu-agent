import { test, expect } from "@playwright/test";

const config = { deploymentUrl: "http://127.0.0.1:1", assistantId: "JW" };

test.beforeEach(async ({ page }) => {
  await page.addInitScript((value) => localStorage.setItem("jw-config", JSON.stringify(value)), config);
});

test("workbench navigation preserves research entry points", async ({ page }) => {
  await page.goto("/?view=home");
  await expect(page.getByRole("heading", { name: "科研工作台" })).toBeVisible();
  await page.getByRole("navigation", { name: "平台导航" }).getByRole("button", { name: "观测设施" }).click();
  await expect(page.getByRole("heading", { name: "观测设施" })).toBeVisible();
  await expect(page.getByText("仪器实时遥测与设施控制尚未接入。")).toBeVisible();
  await page.getByRole("navigation", { name: "平台导航" }).getByRole("button", { name: "科研工作流" }).click();
  await expect(page.getByRole("heading", { name: "科研工作流" })).toBeVisible();
  await page.getByRole("navigation", { name: "平台导航" }).getByRole("button", { name: "金乌科研助手" }).click();
  await expect(page).not.toHaveURL(/view=workflow/);
  await page.getByRole("navigation", { name: "平台导航" }).getByRole("button", { name: "知识库" }).click();
  await expect(page).toHaveURL(/view=wiki/);
  await page.getByRole("navigation", { name: "平台导航" }).getByRole("button", { name: "定时任务" }).click();
  await expect(page).toHaveURL(/view=schedule/);
  await page.getByRole("navigation", { name: "平台导航" }).getByRole("button", { name: "技能", exact: true }).click();
  await expect(page).toHaveURL(/view=skills/);
  await page.getByRole("navigation", { name: "平台导航" }).getByRole("button", { name: "金乌记忆" }).click();
  await expect(page).toHaveURL(/view=memory/);
  await page.getByRole("navigation", { name: "平台导航" }).getByRole("button", { name: "工作台" }).click();
  await page.getByRole("button", { name: "新建研究任务" }).click();
  await expect(page).not.toHaveURL(/view=home/);
});

test("idle workbench ignores legacy inspector state and keeps agent board accessible", async ({ page }) => {
  await page.goto("/?view=home&inspector=1&inspectorTab=agents");
  await expect(page.getByRole("heading", { name: "科研工作台" })).toBeVisible();
  await expect(page.getByRole("heading", { name: /智能体协同 · 运行中/ })).toHaveCount(0);
  await page.getByRole("navigation", { name: "平台导航" }).getByRole("button", { name: "智能体协同" }).click();
  await expect(page).toHaveURL(/view=agents/);
  await expect(page.getByRole("heading", { name: "智能体协同" })).toBeVisible();
});

test("mobile platform navigation opens and closes", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/?view=home");
  await page.getByRole("button", { name: "打开平台导航" }).click();
  const navigation = page.getByRole("navigation", { name: "平台导航" });
  await expect(navigation).toBeVisible();
  await navigation.getByRole("button", { name: "观测设施" }).click();
  await expect(navigation).toBeHidden();
  await expect(page.getByRole("heading", { name: "观测设施" })).toBeVisible();
});
