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
  await page.getByRole("navigation", { name: "平台导航" }).getByRole("button", { name: "太阳活动" }).click();
  await expect(page).toHaveURL(/view=solar/);
  const live = page.getByRole("tab", { name: "实时观测" });
  const cycle = page.getByRole("tab", { name: "太阳活动周" });
  await expect(live).toHaveAttribute("aria-selected", "true");
  await expect(page.getByRole("tab", { name: "今日太阳" })).toHaveCount(0);
  await live.press("ArrowRight");
  await expect(cycle).toBeFocused();
  await expect(cycle).toHaveAttribute("aria-selected", "true");
  await expect(page).toHaveURL(/solarTab=cycle/);
  await page.getByRole("navigation", { name: "平台导航" }).getByRole("button", { name: "工作台" }).click();
  await page.getByRole("navigation", { name: "平台导航" }).getByRole("button", { name: "太阳活动" }).click();
  await expect(live).toHaveAttribute("aria-selected", "true");
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

test("model management hides agent collaboration and consolidates system settings", async ({ page }) => {
  await page.route("**/api/model-config/models", route => route.fulfill({
    json: { ok: true, provider: "ollama", models: ["qwen3:8b", "llama3.2:3b"] },
  }));
  const saves = [];
  await page.route("**/api/model-config", route => {
    if (route.request().method() === "POST") saves.push(route.request().postDataJSON());
    return route.fulfill({
    json: {
      ok: true,
      available: true,
      configured: true,
      provider: "dashscope",
      model: "qwen3.7-plus",
      hasCredential: true,
      credentialProviders: ["dashscope"],
      configPath: "/tmp/jw/config.yaml",
      schema: [
        { id: "dashscope", label: "阿里云百炼 / DashScope（Qwen，默认）", defaultModel: "qwen3.7-plus", requiresApiKey: true, requiresBaseUrl: false, placeholder: "sk-..." },
        { id: "custom-openai", label: "自定义 OpenAI 兼容接口", defaultModel: "qwen3.7-plus", requiresApiKey: true, requiresBaseUrl: true, placeholder: "sk-..." },
        { id: "ollama", label: "Ollama（本机）", defaultModel: "qwen3:8b", requiresApiKey: false, requiresBaseUrl: false },
      ],
    },
    });
  });
  await page.route(`${config.deploymentUrl}/api/data-sources`, route => route.fulfill({ json: { status: "ok", sources: [] } }));
  await page.route(`${config.deploymentUrl}/api/kb/literature/feeds`, route => route.fulfill({ json: { status: "ok", schema_version: "literature-feed-catalog-v1", total_sources: 0, feeds: [] } }));
  const interfaceGroup = { id: "pdf", label: "PDF 表格识别", description: "文档表格提取配置" };
  const interfaceItems = [
    { group: "pdf", field: "pdf_provider", label: "表格识别服务", env: "JW_PDF_CLOUD_PROVIDER", kind: "provider", hint: "选择现有服务", options: ["", "generic"], configured: false, source: "", masked: "", value: "", env_override: false },
  ];
  await page.route(`${config.deploymentUrl}/api/scientific-data/interfaces`, route => route.fulfill({ json: { status: "ok", groups: [interfaceGroup], items: interfaceItems } }));

  await page.goto("/?view=home&inspector=1&inspectorTab=agents");
  await expect(page.getByRole("heading", { name: "科研工作台" })).toBeVisible();
  await expect(page.getByRole("button", { name: "智能体协同" })).toHaveCount(0);

  await page.getByRole("navigation", { name: "平台导航" }).getByRole("button", { name: "模型管理" }).click();
  await expect(page).toHaveURL(/view=models/);
  await expect(page.getByRole("tab", { name: "模型与 API" })).toBeVisible();
  await expect(page.getByRole("button", { name: "连接设置" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "设置", exact: true })).toHaveCount(0);
  await expect(page.getByText("自定义 OpenAI 兼容接口")).toBeVisible();
  await page.getByRole("button", { name: /Ollama（本机）/ }).click();
  await expect(page.getByText("连接本机或局域网内的 Ollama 服务")).toBeVisible();
  await expect(page.getByLabel(/Base URL/)).toHaveAttribute("placeholder", "http://127.0.0.1:11434");
  await expect(page.getByText(/无需云端 API Key/)).toBeVisible();
  const modelInput = page.getByLabel("模型", { exact: true });
  await modelInput.click();
  const modelOptions = page.getByRole("listbox");
  await expect(modelOptions).toBeVisible();
  await expect(page.getByRole("option", { name: "qwen3:8b" })).toBeVisible();
  await expect(page.getByRole("option", { name: "llama3.2:3b" })).toBeVisible();
  const inputBox = await modelInput.boundingBox();
  const optionsBox = await modelOptions.boundingBox();
  expect(inputBox).not.toBeNull();
  expect(optionsBox).not.toBeNull();
  expect(Math.abs(optionsBox.x - inputBox.x)).toBeLessThan(2);
  expect(Math.abs(optionsBox.width - inputBox.width)).toBeLessThan(2);
  expect(optionsBox.y).toBeGreaterThan(inputBox.y);
  await page.getByRole("option", { name: "llama3.2:3b" }).click();
  await expect(modelInput).toHaveValue("llama3.2:3b");
  await expect(modelOptions).toBeHidden();
  await page.getByLabel(/Base URL/).fill("http://192.168.1.10:11434");
  await page.getByRole("button", { name: "保存并应用" }).click();
  await expect.poll(() => saves.length).toBe(1);
  expect(saves[0]).toMatchObject({
    provider: "ollama",
    model: "llama3.2:3b",
    base_url: "http://192.168.1.10:11434",
  });

  await expect(page.getByRole("tab", { name: "数据源" })).toHaveCount(0);
  await expect(page.getByRole("tab", { name: "文献源" })).toHaveCount(0);
  await page.getByRole("tab", { name: "识别 API" }).click();
  await expect(page.getByRole("heading", { name: "识别 API", exact: true })).toBeVisible();
  await page.getByRole("button", { name: /PDF 表格识别/ }).click();
  await expect(page.getByLabel("表格识别服务")).toBeVisible();
  await page.getByRole("tab", { name: "界面偏好" }).click();
  await expect(page.getByLabel("默认折叠 Agent 操作")).toBeVisible();

  await page.getByRole("navigation", { name: "平台导航" }).getByRole("button", { name: "连接" }).click();
  await expect(page).toHaveURL(/view=tools/);
  await expect(page.getByRole("heading", { name: "数据源", exact: true })).toBeVisible();
  await expect(page.getByRole("tab", { name: "数据源" })).toHaveAttribute("aria-selected", "true");
  await page.getByRole("tab", { name: "文献源" }).click();
  await expect(page.getByRole("heading", { name: "文献源", exact: true })).toBeVisible();
  await page.getByRole("tab", { name: "MCP 工具" }).click();
  await expect(page.getByRole("heading", { name: "MCP 工具", exact: true })).toBeVisible();
});

test("model management recognition API settings keep edits scoped and save existing payload", async ({ page }) => {
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
  await page.goto("/?view=models");
  await expect(page.getByRole("tab", { name: "模型与 API" })).toBeVisible();
  await expect(page.getByLabel("表格识别服务")).toHaveCount(0);
  await expect(page.getByRole("button", { name: /PDF 表格识别/ })).toHaveCount(0);
  await page.getByRole("tab", { name: "识别 API" }).click();
  await expect(page.getByRole("heading", { name: "识别 API", exact: true })).toBeVisible();
  await page.getByRole("button", { name: /PDF 表格识别/ }).click();
  await page.getByLabel("表格识别服务").selectOption("generic");
  await expect(page.getByText("1 项修改未保存")).toBeVisible();
  await page.getByRole("button", { name: "保存修改" }).click();
  await expect.poll(() => updates.length).toBe(1);
  expect(updates[0]).toEqual({ settings: { pdf_provider: "generic" } });
  await expect(page.getByRole("button", { name: "保存修改" })).toHaveCount(0);
  await page.getByRole("button", { name: "返回接口目录" }).click();
  await expect(page.getByLabel("表格识别服务")).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "识别 API", exact: true })).toBeVisible();
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
  await expect(page.getByLabel("数据源统计")).toBeVisible();
  await expect(page.getByRole("button", { name: "配置", exact: true })).toHaveCount(0);
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

test("live solar observations render NOAA and SDO panels", async ({ page }) => {
  const end = Date.parse("2026-11-28T05:00:00Z");
  const minute = 60_000;
  const xrayPoints = Array.from({ length: 121 }, (_, index) => {
    const time = end - (120 - index) * minute;
    return [time, index === 60 ? 2.17e-6 : 7.5e-7];
  });
  const protonPoints = (flux) => Array.from({ length: 73 }, (_, index) => [end - (72 - index) * 3_600_000, flux]);
  const image = (label, instrument) => ({
    status: "ok", label, instrument, description: label, credit: "NASA/SDO",
    source_url: `https://example.test/${label}.jpg`, observed_at: "2026-11-28T04:59:00+00:00", fetched_at: "2026-11-28T05:00:00+00:00",
  });
  const overview = {
    status: "ok",
    generated_at: "2026-11-28T05:00:30+00:00",
    xray: {
      status: "ok", points: xrayPoints, latest_at: "2026-11-28T05:00:00+00:00",
      current: { flux: 7.5e-7, class: "B7.5" }, max_2h: { flux: 2.17e-6, class: "C2.1" }, max_24h: { flux: 3.93e-6, class: "C3.9" },
      source_url: "https://services.swpc.noaa.gov/json/goes/primary/xrays-1-day.json",
    },
    flares: { status: "ok", events: [{ begin_at: "2026-11-28T04:19:00+00:00", peak_at: "2026-11-28T04:28:00+00:00", end_at: "2026-11-28T04:44:00+00:00", class: "C2.1" }], source_url: "https://services.swpc.noaa.gov/json/goes/primary/xray-flares-7-day.json" },
    protons: {
      status: "ok", series: { gte10: protonPoints(150), gte50: protonPoints(2), gte100: protonPoints(0.3) },
      latest_gte10: 150, s_scale: 2, source_url: "https://services.swpc.noaa.gov/json/goes/primary/integral-protons-3-day.json",
    },
    regions: {
      status: "ok", report_date: "2026-11-27", reference_at: "2026-11-28T00:00:00+00:00", projected_at: "2026-11-28T04:59:00+00:00", b0_deg: 1.3,
      rows: [
        { number: 4583, location: "N05E33", latitude: 5, longitude_west: -30.2, spots: 5, area: 30, spot_class: "Cao", mag_class: "B", c_probability: 20, m_probability: 1, x_probability: 1, first_date: null, disk: { x: -0.5, y: 0.07, visible: true } },
        { number: 4579, location: "N15W95", latitude: 15, longitude_west: 98, spots: 1, area: 10, spot_class: "Hsx", mag_class: "A", c_probability: 1, m_probability: 1, x_probability: 1, first_date: null, disk: { x: 0.93, y: 0.26, visible: false } },
      ],
      source_url: "https://services.swpc.noaa.gov/json/solar_regions.json",
    },
    images: {
      hmi: image("HMI 连续谱", "SDO/HMI"), aia193: image("AIA 193 Å", "SDO/AIA"), aia131: image("AIA 131 Å", "SDO/AIA"),
      ccor1: image("CCOR-1", "GOES-19/CCOR-1"), ccor2: image("CCOR-2", "SOLAR-1/CCOR-2"), lasco: { status: "error", error: "down" },
    },
  };
  const jpeg = Buffer.from("/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRofHh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/wAALCAABAAEBAREA/8QAFAABAAAAAAAAAAAAAAAAAAAACf/EABQQAQAAAAAAAAAAAAAAAAAAAAD/2gAIAQEAAD8AKp//2Q==", "base64");
  const imageRequests = [];
  await page.route(`${config.deploymentUrl}/api/solar-live/overview`, route => route.fulfill({ json: overview }));
  await page.route(`${config.deploymentUrl}/api/solar-live/image/**`, route => {
    imageRequests.push(new URL(route.request().url()).pathname);
    return route.fulfill({ body: jpeg, contentType: "image/jpeg" });
  });

  await page.goto("/?view=solar");
  const rail = page.getByRole("tablist", { name: "实时观测" });
  await expect(rail.getByRole("tab", { name: "太阳黑子区域" })).toHaveAttribute("aria-selected", "true");
  const sunspots = page.getByRole("region", { name: "太阳黑子区域" });
  await expect(sunspots).toBeVisible();
  await expect(page.getByRole("region", { name: "日冕洞" })).toHaveCount(0);
  await expect(sunspots.getByRole("row", { name: /4583/ })).toContainText("β");
  await expect(sunspots.getByRole("row", { name: /4579/ })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "活动区编号" })).toHaveAttribute("aria-pressed", "true");
  await page.getByRole("button", { name: "日面经纬网" }).click();
  await expect(page.getByRole("button", { name: "日面经纬网" })).toHaveAttribute("aria-pressed", "true");

  await rail.getByRole("tab", { name: "太阳耀斑" }).click();
  const flares = page.getByRole("region", { name: "太阳耀斑" });
  await expect(flares.getByRole("img", { name: "过去两小时的太阳活动" })).toBeVisible();
  await expect(flares.getByText("B7.5", { exact: true })).toBeVisible();
  await expect(flares.getByText("C3.9", { exact: true })).toBeVisible();
  await expect(page.getByRole("region", { name: "太阳黑子区域" })).toHaveCount(0);

  await rail.getByRole("tab", { name: "日冕洞" }).click();
  await expect(page.getByRole("region", { name: "日冕洞" }).getByRole("img", { name: /193/ })).toBeVisible();

  await rail.getByRole("tab", { name: "高能太阳质子" }).click();
  await expect(page.getByRole("region", { name: "高能太阳质子" })).toContainText("太阳辐射风暴 S2");

  await rail.getByRole("tab", { name: "日冕物质抛射" }).click();
  const cme = page.getByRole("region", { name: "日冕物质抛射" });
  await expect(cme.getByRole("tab", { name: "LASCO C2" })).toBeDisabled();
  await cme.getByRole("tab", { name: "CCOR-2" }).click();
  await expect(cme.getByRole("img", { name: /CCOR-2/ })).toBeVisible();
  await expect.poll(() => imageRequests).toContain("/api/solar-live/image/ccor2");

  await rail.getByRole("tab", { name: "太阳黑子区域" }).press("ArrowDown");
  await expect(rail.getByRole("tab", { name: "日冕洞" })).toHaveAttribute("aria-selected", "true");
});

test("solar history mode rewrites every observation panel to a past moment", async ({ page }) => {
  const end = Date.parse("2026-11-28T05:00:00Z");
  const xrayPoints = Array.from({ length: 121 }, (_, index) => [end - (120 - index) * 60_000, 7.5e-7]);
  const image = (label, instrument) => ({
    status: "ok", label, instrument, description: label, credit: "NASA/SDO",
    source_url: `https://example.test/${label}.jpg`, observed_at: "2026-11-28T04:59:00+00:00", fetched_at: "2026-11-28T05:00:00+00:00",
  });
  const overview = {
    status: "ok", generated_at: "2026-11-28T05:00:30+00:00",
    xray: { status: "ok", points: xrayPoints, latest_at: "2026-11-28T05:00:00+00:00",
      current: { flux: 7.5e-7, class: "B7.5" }, max_2h: { flux: 7.5e-7, class: "B7.5" }, max_24h: { flux: 7.5e-7, class: "B7.5" },
      source_url: "https://services.swpc.noaa.gov/json/goes/primary/xrays-1-day.json" },
    flares: { status: "ok", events: [], source_url: "https://services.swpc.noaa.gov/json/goes/primary/xray-flares-7-day.json" },
    protons: { status: "ok", series: { gte10: [], gte50: [], gte100: [] }, latest_gte10: null, s_scale: 0, source_url: "https://services.swpc.noaa.gov/json/goes/primary/integral-protons-3-day.json" },
    regions: { status: "ok", report_date: "2026-11-27", reference_at: "2026-11-28T00:00:00+00:00", projected_at: "2026-11-28T04:59:00+00:00", b0_deg: 1.3,
      rows: [{ number: 4583, location: "N05E33", latitude: 5, longitude_west: -30.2, spots: 5, area: 30, spot_class: "Cao", mag_class: "B", c_probability: 20, m_probability: 1, x_probability: 1, first_date: null, disk: { x: -0.5, y: 0.07, visible: true } }],
      source_url: "https://services.swpc.noaa.gov/json/solar_regions.json" },
    images: { hmi: image("HMI 连续谱", "SDO/HMI"), aia193: image("AIA 193 Å", "SDO/AIA"), aia131: image("AIA 131 Å", "SDO/AIA"),
      ccor1: image("CCOR-1", "GOES-19/CCOR-1"), ccor2: image("CCOR-2", "SOLAR-1/CCOR-2"), lasco: { status: "error", error: "down" } },
  };
  const times = (step, count) => Array.from({ length: count }, (_, index) => {
    const total = index * step;
    return `${String(Math.floor(total / 60)).padStart(2, "0")}${String(total % 60).padStart(2, "0")}00`;
  });
  const channel = (label, entries) => ({ label, instrument: "SDO", times: entries, count: entries.length });
  const listing = {
    status: "ok", date: "2024-05-10", source_url: "https://sdo.gsfc.nasa.gov/assets/img/browse/",
    channels: { hmi: channel("HMI 连续谱", times(15, 96)), hmimag: channel("HMI 彩色磁图", times(15, 96)), aia193: channel("AIA 193 Å", times(10, 144)), aia131: channel("AIA 131 Å", times(10, 144)) },
  };
  const jpeg = Buffer.from("/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRofHh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/wAALCAABAAEBAREA/8QAFAABAAAAAAAAAAAAAAAAAAAACf/EABQQAQAAAAAAAAAAAAAAAAAAAAD/2gAIAQEAAD8AKp//2Q==", "base64");
  const imageRequests = [];
  await page.route(`${config.deploymentUrl}/api/solar-live/overview`, route => route.fulfill({ json: overview }));
  await page.route(/\/api\/solar-live\/history\/\d{4}-\d{2}-\d{2}$/, route => route.fulfill({ json: { ...listing, date: new URL(route.request().url()).pathname.split("/").pop() } }));
  await page.route(/\/api\/solar-live\/history\/image\//, route => {
    imageRequests.push(new URL(route.request().url()).pathname);
    return route.fulfill({ body: jpeg, contentType: "image/jpeg" });
  });
  await page.route(/\/api\/solar-live\/image\//, route => route.fulfill({ body: jpeg, contentType: "image/jpeg" }));

  await page.goto("/?view=solar");
  await expect(page.getByRole("region", { name: "太阳黑子区域" })).toBeVisible();

  // 进入历史模式：默认昨天正午，时间选择展开。
  await page.getByRole("button", { name: "查看历史观测" }).click();
  await expect(page.getByText(/历史模式 ·/)).toBeVisible();
  await expect(page.getByRole("grid", { name: "选择 UTC 日期" })).toBeVisible();
  await expect.poll(() => imageRequests.length).toBeGreaterThan(0);
  expect(imageRequests[0]).toContain("/api/solar-live/history/image/hmi/");
  expect(imageRequests[0]).toContain("120000");

  // 黑子面板：实时活动区表格隐藏，换成档案说明；叠加开关也不再出现。
  const sunspots = page.getByRole("region", { name: "太阳黑子区域" });
  await expect(sunspots.getByText(/历史活动区报告/)).toBeVisible();
  await expect(page.getByRole("button", { name: "活动区编号" })).toHaveCount(0);

  // 每个图像面板都请求各自通道的历史图像。
  await page.getByRole("tablist", { name: "实时观测" }).getByRole("tab", { name: "日冕洞" }).click();
  await expect.poll(() => imageRequests.at(-1)).toContain("/image/aia193/");
  await page.getByRole("tablist", { name: "实时观测" }).getByRole("tab", { name: "太阳耀斑" }).click();
  await expect.poll(() => imageRequests.at(-1)).toContain("/image/aia131/");
  await expect(page.getByRole("region", { name: "太阳耀斑" }).getByText(/历史 X 射线流量曲线/)).toBeVisible();
  await page.getByRole("tablist", { name: "实时观测" }).getByRole("tab", { name: "高能太阳质子" }).click();
  await expect(page.getByRole("region", { name: "高能太阳质子" }).getByText(/历史质子通量曲线/)).toBeVisible();
  await page.getByRole("tablist", { name: "实时观测" }).getByRole("tab", { name: "日冕物质抛射" }).click();
  await expect(page.getByRole("region", { name: "日冕物质抛射" }).getByText(/LASCO 历史日冕仪图像接入中/)).toBeVisible();

  // 滑块步进改变请求的时刻；返回实时后恢复实时面板。
  await page.getByRole("slider", { name: "选择时刻" }).fill("0");
  await expect(page.getByText("00:00 UTC", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "下一帧" }).click();
  await expect(page.getByText("00:10 UTC", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "返回实时观测" }).click();
  await expect(page.getByText(/历史模式 ·/)).toHaveCount(0);
  await page.getByRole("tablist", { name: "实时观测" }).getByRole("tab", { name: "太阳黑子区域" }).click();
  await expect(page.getByRole("button", { name: "活动区编号" })).toBeVisible();
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
