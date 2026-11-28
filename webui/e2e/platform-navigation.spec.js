import { test, expect } from "@playwright/test";

const config = { deploymentUrl: "http://127.0.0.1:1", assistantId: "JW" };

test.beforeEach(async ({ page }) => {
  await page.route("**/api/jw-config", route => route.fulfill({ json: { desktopManaged: false } }));
  await page.route(`${config.deploymentUrl}/**`, route => route.abort());
  await page.addInitScript((value) => localStorage.setItem("jw-config", JSON.stringify(value)), config);
});

test("workbench navigation preserves research entry points", async ({ page }) => {
  await page.goto("/?view=home");
  await expect(page.getByRole("heading", { name: "科研工作台" })).toBeVisible();
  const observatories = page.getByRole("link", { name: /观测与运行状态/ });
  await expect(observatories).toHaveAttribute("href", "/?view=observatories");
  await observatories.click();
  await expect(page.getByRole("heading", { name: "观测设施" })).toBeVisible();
  await page.getByRole("navigation", { name: "平台导航" }).getByRole("button", { name: "工作台" }).click();
  await expect(page.getByRole("region", { name: "平台状态" }).getByRole("heading", { name: "今日太阳" })).toBeVisible();
  await page.getByRole("button", { name: "查看太阳活动详情" }).click();
  await expect(page).toHaveURL(/view=solar/);
  const today = page.getByRole("tab", { name: "今日太阳" });
  const cycle = page.getByRole("tab", { name: "太阳活动周" });
  await expect(today).toHaveAttribute("aria-selected", "true");
  await today.press("ArrowRight");
  await expect(cycle).toBeFocused();
  await expect(cycle).toHaveAttribute("aria-selected", "true");
  await expect(page).toHaveURL(/solarTab=cycle/);
  await page.getByRole("navigation", { name: "平台导航" }).getByRole("button", { name: "工作台" }).click();
  await page.getByRole("button", { name: "查看太阳活动详情" }).click();
  await expect(today).toHaveAttribute("aria-selected", "true");
  await page.getByRole("navigation", { name: "平台导航" }).getByRole("button", { name: "观测设施" }).click();
  await expect(page.getByRole("heading", { name: "观测设施" })).toBeVisible();
  await expect(page.getByText("仪器实时遥测与设施控制尚未接入。")).toBeVisible();
  await expect(page.getByRole("navigation", { name: "平台导航" }).getByRole("button", { name: "科研工作流" })).toHaveCount(0);
  await page.getByRole("navigation", { name: "平台导航" }).getByRole("button", { name: "定时任务" }).click();
  await expect(page).toHaveURL(/view=schedule/);
  await expect(page.getByRole("heading", { name: "定时任务", level: 1 })).toBeVisible();
  await page.goto("/?view=workflow");
  await expect(page).toHaveURL(/view=workflow/);
  await expect(page.getByRole("heading", { name: "定时任务", level: 1 })).toBeVisible();
  await page.getByRole("navigation", { name: "平台导航" }).getByRole("button", { name: "金乌科研助手" }).click();
  await expect(page).not.toHaveURL(/view=(workflow|schedule)/);
  await expect(page.getByRole("complementary", { name: "研究会话" })).toContainText("后端未连接");
  await page.getByRole("navigation", { name: "平台导航" }).getByRole("button", { name: "知识库" }).click();
  await expect(page).toHaveURL(/view=wiki/);
  await expect(page.getByRole("complementary", { name: "研究会话" })).toBeHidden();
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

test("scientific data catalog keeps edits scoped and saves existing API payload", async ({ page }) => {
  const updates = [];
  const group = { id: "pdf", label: "PDF 表格识别", description: "文档表格提取配置" };
  const items = [
    { group: "pdf", field: "pdf_provider", label: "表格识别服务", env: "JW_PDF_CLOUD_PROVIDER", kind: "provider", hint: "选择现有服务", options: ["", "generic"], configured: false, source: "", masked: "", value: "", env_override: false },
    { group: "pdf", field: "pdf_key", label: "服务密钥", env: "JW_PDF_KEY", kind: "key", hint: "只会提交到后端", options: [], configured: false, source: "", masked: "", value: "", env_override: false },
  ];
  await page.route(`${config.deploymentUrl}/api/scientific-data/interfaces`, async route => {
    if (route.request().method() === "PUT") {
      updates.push(route.request().postDataJSON());
      await route.fulfill({ json: { status: "ok", groups: [group], items, note: "已保存" } });
    } else await route.fulfill({ json: { status: "ok", groups: [group], items } });
  });
  await page.goto("/?view=data");
  await expect(page.getByRole("heading", { name: "数据中心", level: 2 })).toBeVisible();
  await expect(page.getByLabel("表格识别服务")).toHaveCount(0);
  await expect(page.getByRole("button", { name: /PDF 表格识别/ })).toHaveCount(0);
  await page.getByRole("button", { name: "配置" }).click();
  await expect(page.getByRole("heading", { name: "数据中心配置", level: 2 })).toBeVisible();
  await page.getByRole("button", { name: /PDF 表格识别/ }).click();
  await page.getByLabel("表格识别服务").selectOption("generic");
  await expect(page.getByText("1 项修改未保存")).toBeVisible();
  await page.getByRole("button", { name: "保存修改" }).click();
  await expect.poll(() => updates.length).toBe(1);
  expect(updates[0]).toEqual({ settings: { pdf_provider: "generic" } });
  await expect(page.getByRole("button", { name: "保存修改" })).toHaveCount(0);
  await page.getByRole("button", { name: "返回接口目录" }).click();
  await expect(page.getByLabel("表格识别服务")).toHaveCount(0);
  await page.getByRole("button", { name: "返回数据中心" }).click();
  await expect(page.getByRole("heading", { name: "数据中心", level: 2 })).toBeVisible();
});

test("data center lists built-in sources and opens preview and query panels", async ({ page }) => {
  const localSource = {
    id: "silso-monthly-total-v2",
    relative: "solar_cycle/silso/monthly_total_v2/SN_m_tot_V2.0.txt",
    title_zh: "SILSO 月度总黑子数 V2.0",
    provider: "SILSO / Royal Observatory of Belgium",
    authority_url: "https://www.sidc.be/SILSO/DATA/SN_m_tot_V2.0.txt",
    category: "solar",
    query_mode: "local_dataset",
    description: "官方太阳活动观测数据集。",
    enabled: true,
    registration: {
      registered_at: "2026-11-01T00:00:00Z",
      retrieved_at: "2026-11-01T00:00:00Z",
      data_path: "solar_cycle/silso/monthly_total_v2/SN_m_tot_V2.0.txt",
      bytes: 2048,
      project: "default",
      coverage_start: "1749-01",
      coverage_end: "2026-10",
      row_count: 3334,
    },
  };
  const onlineSource = {
    id: "noaa-goes-xrs",
    relative: "",
    title_zh: "NOAA GOES XRS 近一天",
    provider: "NOAA Space Weather Prediction Center",
    authority_url: "https://services.swpc.noaa.gov/json/goes/primary/xrays-1-day.json",
    docs_url: "https://www.swpc.noaa.gov/products/goes-x-ray-flux",
    category: "solar",
    query_mode: "goes",
    default_query: "",
    description: "GOES 软 X 射线观测的近一天时间序列。",
    enabled: true,
    registration: null,
  };
  const feed = {
    id: "solar_cycle_prediction",
    title_zh: "太阳活动周预测与前兆",
    query: "solar cycle prediction precursor amplitude",
    providers: ["arxiv"],
    lookback_years: 3,
    sort: "relevance",
    limit: 25,
    enabled: true,
    custom: false,
    required_terms: [],
    required_any_terms: [],
    source_count: 1,
    latest_run: {
      id: 1,
      feed_id: "solar_cycle_prediction",
      providers: ["arxiv"],
      status: "ok",
      result_count: 1,
      new_source_count: 1,
      new_family_count: 1,
      diagnostics: {},
      started_at: "2026-11-20T00:00:00Z",
      completed_at: "2026-11-20T00:00:01Z",
    },
  };
  await page.route(`${config.deploymentUrl}/api/data-sources`, route => route.fulfill({ json: { status: "ok", sources: [localSource, onlineSource] } }));
  await page.route(`${config.deploymentUrl}/api/data-sources/silso-monthly-total-v2/preview`, route => route.fulfill({ json: {
    status: "ok",
    preview: {
      dataset_id: localSource.id,
      file: "SN_m_tot_V2.0.txt",
      registered: true,
      columns: [
        { name: "year", numeric: true, missing: 0 },
        { name: "month", numeric: true, missing: 0 },
        { name: "sunspot_number", numeric: true, missing: 0 },
      ],
      stats: [],
      row_count: 2,
      head_rows: [[1749, 1, 96.7], [1749, 2, 104.3]],
      tail_rows: [],
      omitted_rows: 0,
      series: [],
    },
  } }));
  await page.route(`${config.deploymentUrl}/api/data-sources/noaa-goes-xrs/query`, route => route.fulfill({ json: {
    status: "ok",
    source_id: onlineSource.id,
    query: "",
    columns: ["time_tag", "flux"],
    rows: [{ time_tag: "2026-11-27 00:00Z", flux: 0.0000012 }],
    row_count: 1,
    truncated: false,
    fetched_at: "2026-11-27T00:00:00Z",
    source_url: onlineSource.authority_url,
  } }));
  await page.route(`${config.deploymentUrl}/api/kb/literature/feeds`, route => route.fulfill({ json: { status: "ok", schema_version: "literature-feed-catalog-v1", total_sources: 1, feeds: [feed] } }));
  await page.route(`${config.deploymentUrl}/api/kb/sources*`, route => route.fulfill({ json: [{
    source_id: "src-1",
    family_id: "fam-1",
    canonical_source_id: "src-1",
    provider: "arxiv",
    source_version: "v1",
    content_fingerprint: "fp",
    title: "Solar cycle prediction with polar precursors",
    authors: ["A. Researcher"],
    year: 2026,
    publication_date: "2026-01-01",
    doi: "",
    url: "https://example.org/paper",
    is_refereed: false,
    is_retracted: false,
    abstract_chars: 120,
    fetched_at: "2026-11-20T00:00:00Z",
    first_seen_at: "2026-11-20T00:00:00Z",
    last_seen_at: "2026-11-20T00:00:00Z",
    distillation_count: 0,
    stage: "fetched",
  }] }));

  await page.goto("/?view=data");
  await expect(page.getByRole("heading", { name: "数据中心", level: 2 })).toBeVisible();
  await expect(page.getByRole("button", { name: /PDF 表格识别/ })).toHaveCount(0);
  await expect(page.getByText("3 个数据源")).toBeVisible();
  await expect(page.getByRole("button", { name: /SILSO 月度总黑子数/ })).toBeVisible();
  await expect(page.getByRole("button", { name: /NOAA GOES XRS/ })).toBeVisible();
  await expect(page.getByRole("button", { name: /太阳活动周预测与前兆/ })).toBeVisible();

  await page.getByRole("button", { name: /SILSO 月度总黑子数/ }).click();
  await expect(page.getByRole("heading", { name: "SILSO 月度总黑子数 V2.0" })).toBeVisible();
  await expect(page.getByText("覆盖范围")).toBeVisible();
  await expect(page.getByText("96.7")).toBeVisible();
  await page.getByRole("button", { name: "返回数据源目录" }).click();

  await page.getByRole("button", { name: /NOAA GOES XRS/ }).click();
  await expect(page.getByRole("heading", { name: "NOAA GOES XRS 近一天" })).toBeVisible();
  await page.getByRole("button", { name: "查询" }).click();
  await expect(page.getByText("2026-11-27 00:00Z")).toBeVisible();
  await page.getByRole("button", { name: "返回数据源目录" }).click();

  await page.getByRole("button", { name: /太阳活动周预测与前兆/ }).click();
  await expect(page.getByRole("heading", { name: "太阳活动周预测与前兆" })).toBeVisible();
  await expect(page.getByText("Solar cycle prediction with polar precursors")).toBeVisible();
});


test("solar activity tab renders authoritative monthly tables", async ({ page }) => {
  const overview = {
    status: "ok",
    generated_at: "2026-11-01T00:00:00+00:00",
    summary: {
      latest_sunspot: { month: "2026-10", value: 45.7 },
      latest_smoothed_sunspot: { month: "2026-04", value: 88.6 },
      latest_f107: { month: "2026-10", value: 108.47 },
      current_cycle: {
        cycle: 25,
        minimum: { year: 2019, month: 12, label: "2019-12", sunspot_number: 1.8 },
        maximum: null,
        duration: null,
        completed: false,
      },
      coverage_start: "1749-01",
      coverage_end: "2026-10",
    },
    monthly_rows: [
      {
        month: "2026-09",
        sunspot_number: 60.3,
        sunspot_stddev: 13.0,
        sunspot_observations: 1117,
        sunspot_provisional: true,
        smoothed_sunspot_number: null,
        smoothed_definitive: false,
        f107: 119.1,
        provisional: true,
      },
      {
        month: "2026-10",
        sunspot_number: 45.7,
        sunspot_stddev: 10.2,
        sunspot_observations: 1126,
        sunspot_provisional: true,
        smoothed_sunspot_number: null,
        smoothed_definitive: false,
        f107: 108.47,
        provisional: true,
      },
    ],
    cycles: [
      {
        cycle: 24,
        minimum: { year: 2008, month: 12, label: "2008-12", sunspot_number: 2.2 },
        maximum: { year: 2014, month: 4, label: "2014-04", sunspot_number: 116.4 },
        duration: { years: 11, months: 0, label: "11 年 0 个月" },
        completed: true,
      },
      {
        cycle: 25,
        minimum: { year: 2019, month: 12, label: "2019-12", sunspot_number: 1.8 },
        maximum: null,
        duration: null,
        completed: false,
      },
    ],
    sources: [
      {
        id: "silso-monthly-total-v2",
        title_zh: "SILSO 月度总黑子数 V2.0",
        provider: "SILSO / Royal Observatory of Belgium",
        authority_url: "https://www.sidc.be/SILSO/DATA/SN_m_tot_V2.0.txt",
        fetched_at: "2026-11-01T00:00:00+00:00",
        cache_state: "fresh",
        stale: false,
        license: "CC BY-NC 4.0",
      },
    ],
    capabilities: [
      { feature: "月度黑子数与 F10.7 表", state: "available", note: "已支持" },
      { feature: "蝴蝶图", state: "missing", note: "尚未接入黑子群纬度数据库" },
    ],
  };
  let overviewRequests = 0;
  await page.route(`${config.deploymentUrl}/api/solar-activity/overview*`, route => {
    overviewRequests++;
    return route.fulfill({ json: overview });
  });

  await page.goto("/?view=solar");
  await expect(page.getByRole("heading", { name: "太阳活动", level: 2 })).toBeVisible();
  await expect(page.getByText("实时太阳活动指标尚未接入")).toHaveCount(0);
  await page.getByRole("tab", { name: "太阳活动周" }).click();
  await expect(page.getByText("查询权威数据表", { exact: true })).toBeVisible();
  expect(overviewRequests).toBe(0);
  await page.getByText("查询权威数据表", { exact: true }).click();
  await expect(page.getByRole("heading", { name: "月度太阳活动观测表" })).toBeVisible();
  await expect(page.getByText("最新月度黑子数")).toBeVisible();
  const october = page.getByRole("row", { name: /2026-10/ });
  await expect(october).toContainText("45.7");
  await expect(october).toContainText("108.5");
  await expect(october).toContainText("暂定值");
  await expect(page.getByRole("heading", { name: "太阳活动周极值" })).toBeVisible();
  await expect(page.getByRole("row", { name: /第 25 周/ })).toContainText("进行中");
  await expect(page.getByText("此表的数据覆盖")).toBeVisible();
  await expect(page.getByText("尚未接入黑子群纬度数据库")).toBeVisible();
  await expect(page.getByRole("link", { name: /www.sidc.be/ })).toBeVisible();
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
