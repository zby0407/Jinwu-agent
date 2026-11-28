import { test, expect } from "@playwright/test";

const THREAD_ID = "00000000-0000-4000-8000-000000000001";
const RUN_ID = "00000000-0000-4000-8000-000000000002";
const HUMAN = {
  id: "question-1",
  type: "human",
  content: "请整理离线证据报告。",
};
const OLD_HUMAN = {
  id: "question-old",
  type: "human",
  content: "第一版问题。",
};
const NEW_HUMAN = {
  id: "question-new",
  type: "human",
  content: "第二版问题。",
};
const CHUNKS = Array.from({ length: 1000 }, (_, index) =>
  index === 0
    ? "# 离线回放报告\n\n"
    : index === 999
    ? "\n\n回放完成（1000块）"
    : "证"
);
const REPORT = CHUNKS.join("");
const ANSWER = { id: "answer-1", type: "ai", content: REPORT };
const PROJECT_INPUT_PATH = "/project/data/solar/cycles.txt";
const PROJECT_INPUT_CONTENT =
  "registered solar data\nsource: isolated fixture\n";
const PROJECT_ANSWER = {
  id: "answer-project-input",
  type: "ai",
  content: `## 观测摘要\n\n登记输入：\`${PROJECT_INPUT_PATH}\`\n\n- 来源已登记\n\n| 观测 | 状态 |\n| --- | --- |\n| HMI | 可读 |`,
};
const observations = new WeakMap();

test.afterEach(async ({ page }, testInfo) => {
  if (testInfo.status !== testInfo.expectedStatus) {
    console.log(
      "Isolated backend observations:",
      JSON.stringify(observations.get(page))
    );
  }
  await testInfo.attach("isolated-backend-observations", {
    body: JSON.stringify(observations.get(page), null, 2),
    contentType: "application/json",
  });
});

function checkpoint(id) {
  return { thread_id: THREAD_ID, checkpoint_ns: "", checkpoint_id: id };
}

function snapshot(id, messages, parent = null) {
  return {
    values: { messages, todos: [], files: {}, async_tasks: {} },
    next: [],
    tasks: [],
    checkpoint: checkpoint(id),
    parent_checkpoint: parent ? checkpoint(parent) : null,
    created_at: "2026-01-01T00:00:00.000Z",
    metadata: { source: "loop", step: 1, parents: {} },
  };
}

/**
 * Fetch/SDK application and backend requests are answered in the browser's
 * isolated context. Unknown API routes and non-local traffic fail closed.
 * Native downloads can bypass page interception in Chromium, so download
 * links are inspected and fetched here; server route tests cover attachments.
 */
async function installBackend(
  page,
  baseURL,
  {
    replay = false,
    versions = false,
    projectPreview = false,
    empty = false,
    extraThreads = 0,
    threadTitle = "隔离回放测试",
  } = {}
) {
  const backendPrefix = "/__e2e_langgraph";
  const origin = new URL(baseURL).origin;
  const observed = {
    joins: [],
    mutations: [],
    unexpected: [],
    errors: [],
    projectReads: [],
  };
  observations.set(page, observed);
  let persisted = false;
  page.on("pageerror", (error) => observed.errors.push(error.message));
  page.on("console", (message) => {
    if (message.type() === "error") observed.errors.push(message.text());
  });

  const root = snapshot("000", []);
  const older = snapshot("001", [OLD_HUMAN], "000");
  const newer = snapshot("002", [NEW_HUMAN], "000");
  const current = () =>
    versions
      ? newer
      : snapshot(
          "003",
          empty
            ? []
            : projectPreview
            ? [HUMAN, PROJECT_ANSWER]
            : persisted
            ? [HUMAN, ANSWER]
            : [HUMAN]
        );
  const history = () => (versions ? [newer, older, root] : [current()]);
  const thread = () => ({
    thread_id: THREAD_ID,
    created_at: "2026-01-01T00:00:00.000Z",
    updated_at: "2026-01-01T00:00:00.000Z",
    status: "idle",
    metadata: { graph_id: "JW", title: threadTitle },
    values: current().values,
    interrupts: {},
  });
  const assistant = {
    assistant_id: "00000000-0000-4000-8000-000000000003",
    graph_id: "JW",
    name: "JW",
    metadata: { created_by: "system" },
    config: {},
    context: {},
    version: 1,
    created_at: "2026-01-01T00:00:00.000Z",
    updated_at: "2026-01-01T00:00:00.000Z",
  };

  await page.route("**/*", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    const method = request.method();
    const json = (body) => route.fulfill({ status: 200, json: body });

    if (url.origin !== origin) {
      observed.unexpected.push(`${method} ${request.url()}`);
      return route.abort("blockedbyclient");
    }
    if (path.startsWith(backendPrefix)) {
      const apiPath = path.slice(backendPrefix.length);
      if (method === "POST" && apiPath === "/assistants/search")
        return json([assistant]);
      if (method === "POST" && apiPath === "/threads/search") {
        const primary = thread();
        const more = Array.from({ length: extraThreads }, (_, index) => ({
          ...primary,
          thread_id: `00000000-0000-4000-8000-${String(1000 + index).padStart(12, "0")}`,
          metadata: { graph_id: "JW", title: `历史样例 ${index + 1}` },
        }));
        return json([primary, ...more]);
      }
      if (method === "GET" && apiPath === `/threads/${THREAD_ID}/state`)
        return json(current());
      if (method === "GET" && apiPath === `/threads/${THREAD_ID}`)
        return json(thread());
      if (method === "GET" && apiPath === `/threads/${THREAD_ID}/runs`)
        return json([]);
      if (method === "POST" && apiPath === `/threads/${THREAD_ID}/history`)
        return json(history());
      if (
        method === "GET" &&
        apiPath === `/threads/${THREAD_ID}/runs/${RUN_ID}`
      ) {
        return json({
          run_id: RUN_ID,
          thread_id: THREAD_ID,
          // Keep the polling fallback from clearing the resume marker for us:
          // this test must finish by consuming the SDK stream successfully.
          status: "running",
        });
      }
      if (
        method === "GET" &&
        apiPath === `/threads/${THREAD_ID}/runs/${RUN_ID}/stream`
      ) {
        observed.joins.push({
          lastEventId: request.headers()["last-event-id"],
        });
        const initialValues = current().values;
        // This is a completed server run with a buffered SSE tail. Rejoining
        // replays that tail, while later state/history reads see its persisted
        // answer, just as they do against LangGraph after normal completion.
        persisted = true;
        const body =
          `event: values\ndata: ${JSON.stringify(initialValues)}\n\n` +
          CHUNKS.map(
            (content, index) =>
              `id: ${index}\nevent: messages\ndata: ${JSON.stringify([
                { type: "AIMessageChunk", id: ANSWER.id, content },
                { langgraph_node: "model", langgraph_checkpoint_ns: "" },
              ])}\n\n`
          ).join("");
        return route.fulfill({
          status: 200,
          contentType: "text/event-stream",
          headers: { "Cache-Control": "no-cache" },
          body,
        });
      }
      if (apiPath === "/api/models")
        return json({ entries: [], default: null });
      if (apiPath === "/api/runtime-status")
        return json({ backendReady: true, canSend: true, elapsedMs: 0 });
      if (method === "POST" && apiPath === "/runs/crons/search")
        return json([]);
      if (apiPath === "/info") return json({ version: "e2e" });
      if (method !== "GET") observed.mutations.push(`${method} ${apiPath}`);
      observed.unexpected.push(`${method} ${apiPath}`);
      return route.fulfill({
        status: 418,
        json: { error: "Unexpected isolated backend request" },
      });
    }
    if (path.startsWith("/api/")) {
      if (method !== "GET") observed.mutations.push(`${method} ${path}`);
      if (method === "GET" && path === "/api/jw-config")
        return json({ desktopManaged: false });
      if (
        projectPreview &&
        method === "GET" &&
        path === "/api/workspace/file" &&
        url.searchParams.get("threadId") === THREAD_ID &&
        url.searchParams.get("path") === PROJECT_INPUT_PATH
      ) {
        const download = url.searchParams.get("download") === "1";
        observed.projectReads.push({
          threadId: url.searchParams.get("threadId"),
          path: url.searchParams.get("path"),
          download,
        });
        return route.fulfill({
          status: 200,
          contentType: "text/plain; charset=utf-8",
          headers: {
            "Content-Security-Policy": "sandbox",
            "Content-Disposition": `${
              download ? "attachment" : "inline"
            }; filename="cycles.txt"`,
          },
          body: PROJECT_INPUT_CONTENT,
        });
      }
      if (method === "GET" && path === "/api/memory")
        return json({ entries: [] });
      if (method === "GET" && path === "/api/memory/observations")
        return json({ observations: [], total: 0 });
      if (method === "GET" && path === "/api/memory/executions")
        return json({ executions: [], total: 0 });
      if (method === "GET" && path === "/api/workspace")
        return json({ dir: "/isolated-fixture", entries: [] });
      if (method === "GET" && path === "/api/workspace/artifacts")
        return json({ artifacts: [], truncated: false });
      if (method === "GET" && path === "/api/research-review/status")
        return json({ active: false });
      observed.unexpected.push(`${method} ${path}`);
      return route.fulfill({
        status: 418,
        json: { error: "Unexpected isolated application request" },
      });
    }
    return route.continue();
  });

  await page.addInitScript(
    ({ deploymentUrl, threadId, runId, shouldReplay }) => {
      localStorage.setItem(
        "jw-config",
        JSON.stringify({ deploymentUrl, assistantId: "JW" })
      );
      if (shouldReplay && !sessionStorage.getItem("jw-e2e-replay-seeded")) {
        sessionStorage.setItem(`lg:stream:${threadId}`, runId);
        sessionStorage.setItem("jw-e2e-replay-seeded", "1");
      }
    },
    {
      deploymentUrl: `${origin}${backendPrefix}`,
      threadId: THREAD_ID,
      runId: RUN_ID,
      shouldReplay: replay,
    }
  );

  return { observed };
}

test("workbench filters recent sessions and opens the selected conversation", async ({ page, baseURL }) => {
  const threadTitle = "怀柔望远镜采集记录与太阳活动观测图像质量核对及后续研究会话";
  await installBackend(page, baseURL, { projectPreview: true, extraThreads: 4, threadTitle });
  await page.goto("/?view=home");

  const recent = page.getByRole("region", { name: "最近研究" });
  const titles = recent.locator(".research-thread-row .truncate");
  await expect(titles).toHaveText([threadTitle, "历史样例 1", "历史样例 2", "历史样例 3", "历史样例 4"]);
  const timestamps = recent.locator("time");
  await expect(timestamps).toHaveCount(5);
  await expect(timestamps.first()).toHaveAttribute("datetime", "2026-01-01T00:00:00.000Z");
  await expect(timestamps.first()).toHaveText(/2026.*\d{2}:\d{2}/);
  await recent.getByRole("textbox", { name: "筛选最近研究" }).fill("怀柔望远镜");
  await expect(titles).toHaveText([threadTitle]);
  await page.setViewportSize({ width: 390, height: 844 });
  await recent.getByRole("button", { name: new RegExp(`^${threadTitle}`) }).click();

  await expect(page).toHaveURL(new RegExp(`threadId=${THREAD_ID}`));
  await expect(recent).toBeHidden();
  await expect(page.getByText(HUMAN.content, { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "打开研究会话" }).click();
  await expect(page.getByRole("dialog", { name: "研究会话" }).getByRole("button", { name: new RegExp(`^${threadTitle}`) })).toHaveAttribute("aria-current", "true");
});

test("desktop research history, icon hints and the chat palette work with existing threads", async ({ page, baseURL }) => {
  await installBackend(page, baseURL, { projectPreview: true, extraThreads: 16 });
  await page.goto("/?view=home");
  const nav = page.getByRole("navigation", { name: "平台导航" });
  const assistant = nav.getByRole("button", { name: "金乌科研助手" });
  const workbench = nav.getByRole("button", { name: "工作台" });
  const sidebar = page.getByRole("complementary", { name: "研究会话" });
  await expect(sidebar).toBeHidden();
  const iconPositions = () => nav.locator("button > svg").evaluateAll(icons => icons.map(icon => {
    const { x, y } = icon.getBoundingClientRect();
    return { x, y };
  }));
  const expandedPositions = await iconPositions();
  await assistant.click();
  await expect(nav).toHaveAttribute("data-compact", "true");
  await expect(nav.getByRole("heading", { name: "研究", exact: true })).toBeHidden();
  expect(await iconPositions()).toEqual(expandedPositions);
  await expect.poll(() => nav.locator(".platform-navigation-heading").evaluateAll(headings =>
    headings.map(heading => getComputedStyle(heading, "::after").opacity)
  )).toEqual(["1", "1", "1"]);
  expect(await iconPositions()).toEqual(expandedPositions);
  await expect(sidebar).toBeVisible();
  await workbench.hover();
  await expect(page.getByRole("tooltip")).toHaveText("工作台");
  await expect(workbench).toHaveCSS("background-size", "100% 100%");
  const search = sidebar.getByRole("searchbox", { name: "搜索研究会话" });
  await search.hover();
  await expect(page.getByRole("tooltip")).toHaveCount(0);
  await workbench.focus();
  await page.keyboard.press("Tab");
  await expect(assistant).toBeFocused();
  await expect(page.getByRole("tooltip")).toHaveText("金乌科研助手");
  await page.keyboard.press("Escape");
  await expect(page.getByRole("tooltip")).toHaveCount(0);

  const previous = sidebar.getByRole("button", { name: /^隔离回放测试/ });
  await expect(previous).toBeVisible();
  const historyViewport = sidebar.locator("[data-slot=scroll-area-viewport]");
  await expect.poll(() => historyViewport.evaluate(element => element.scrollHeight > element.clientHeight)).toBe(true);
  await search.fill("找不到的对话");
  await expect(sidebar.getByText("没有匹配搜索条件的研究会话。")).toBeVisible();
  await search.fill("隔离回放");
  await expect(sidebar.getByText("历史样例 1", { exact: true })).toHaveCount(0);
  await expect(sidebar.getByRole("combobox")).toBeVisible();
  await expect(sidebar.getByLabel("更多会话操作")).toHaveCount(0);
  await previous.click();
  await expect(page).toHaveURL(new RegExp(`threadId=${THREAD_ID}`));
  await expect(previous).toHaveAttribute("aria-current", "true");

  const userMessage = page.locator('[data-message-role="user"] [data-message-content]');
  const reply = page.locator('[data-message-role="assistant"] [data-message-content]');
  await expect(userMessage).toHaveCSS("background-color", "rgb(53, 117, 180)");
  await expect(userMessage.locator("p")).toHaveCSS("color", "rgb(255, 255, 255)");
  await expect(reply).toHaveCSS("background-color", "rgb(255, 255, 255)");
  await expect(reply.locator("p")).toHaveCSS("color", "rgb(17, 24, 39)");
  await expect(reply.getByRole("heading", { name: "观测摘要" })).toHaveCSS("color", "rgb(17, 24, 39)");
  await expect(reply.getByRole("listitem")).toHaveCSS("color", "rgb(17, 24, 39)");
  await expect(reply.getByRole("cell", { name: "HMI" })).toHaveCSS("color", "rgb(17, 24, 39)");
  const background = page.locator(".chat-workspace-background");
  await expect(background).toHaveCSS("background-image", /huairou-sunset\.jpg/);
  await expect(background).toHaveCSS("position", "absolute");
  await expect(background).toHaveCSS("bottom", "0px");
  const send = page.getByRole("button", { name: "发送消息", exact: true });
  await expect(send).toBeDisabled();
  await expect(send).toHaveCSS("background-color", "rgb(191, 219, 254)");
  await page.getByRole("textbox", { name: "消息", exact: true }).fill("仅用于检查按钮配色");
  await expect(send).toBeEnabled();
  await expect(send).toHaveCSS("color", "rgb(255, 255, 255)");

  await workbench.click();
  await expect(nav).toHaveAttribute("data-compact", "false");
  await expect(sidebar).toBeHidden();
  await assistant.click();
  await expect(previous).toHaveAttribute("aria-current", "true");
  await expect(userMessage).toBeVisible();
  await sidebar.getByRole("button", { name: "开始新实验" }).click();
  await expect(page).not.toHaveURL(/threadId=/);
  await expect(page.getByRole("textbox", { name: "消息", exact: true })).toHaveValue("");
});

test("research workspace slides both ways and stays in the current conversation", async ({ page, baseURL }) => {
  await installBackend(page, baseURL, { projectPreview: true });
  await page.goto(`/?threadId=${THREAD_ID}`);
  const open = page.getByRole("button", { name: "展开右侧工作区" });
  const close = page.getByRole("button", { name: "收起右侧工作区" });
  const tabs = page.getByRole("tablist", { name: "研究工作区" });
  const drawer = page.locator(".chat-workspace-dock");
  await expect(open).toHaveAttribute("aria-expanded", "false");
  await expect(tabs).toBeHidden();
  await drawer.evaluate(element => {
    element.addEventListener("transitionend", event => {
      if (event.target === element && event.propertyName === "width") {
        element.dataset.lastSlide = element.dataset.open === "true" ? "out" : "in";
      }
    });
  });
  const closedButton = await open.boundingBox();

  await open.click();
  await expect(drawer).toHaveAttribute("data-last-slide", "out");
  await expect(close).toHaveAttribute("aria-expanded", "true");
  expect((await close.boundingBox()).x).toBeLessThan(closedButton.x - 200);
  await expect(tabs.getByRole("tab")).toHaveText(["工作区", "产物", "实时活动"]);
  await expect(page.getByRole("tabpanel", { name: "工作区" }).getByText("目录树")).toBeVisible();
  await tabs.getByRole("tab", { name: "产物" }).click();
  await expect(page.getByRole("tabpanel", { name: "产物" }).getByText("暂无产物")).toBeVisible();
  await tabs.getByRole("tab", { name: "产物" }).press("End");
  await expect(page.getByRole("tabpanel", { name: "实时活动" }).getByText("空闲")).toBeVisible();
  await expect(page.getByText(HUMAN.content, { exact: true })).toBeVisible();
  await expect(page).toHaveURL(new RegExp(`threadId=${THREAD_ID}`));

  await close.click();
  await expect(drawer).toHaveAttribute("data-last-slide", "in");
  await expect(drawer).toHaveCSS("width", "0px");
  await expect(tabs).toBeHidden();
  const composerWorkspace = page.getByRole("button", { name: /^打开工作区：/ });
  await composerWorkspace.click();
  await expect(page.getByRole("tabpanel", { name: "工作区" })).toBeVisible();
  await expect(page).not.toHaveURL(/view=workspace/);
});

test("mobile keeps the workspace arrow reachable and can open an old artifacts link", async ({ page, baseURL }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await installBackend(page, baseURL, { projectPreview: true });
  await page.goto(`/?view=artifacts&threadId=${THREAD_ID}`);
  await expect(page.getByRole("tabpanel", { name: "产物" })).toBeVisible();
  await expect(page.getByRole("separator", { name: "调整右侧工作区宽度" })).toBeHidden();
  await expect(page).not.toHaveURL(/view=artifacts/);
  await page.getByRole("button", { name: "收起右侧工作区" }).click();
  const open = page.getByRole("button", { name: "展开右侧工作区" });
  await expect(open).toBeInViewport();
  await open.click();
  await expect(page.getByRole("tablist", { name: "研究工作区" })).toBeInViewport();
  await page.getByRole("tab", { name: "实时活动" }).press("Escape");
  await expect(open).toBeFocused();
  await expect(page.getByRole("tablist", { name: "研究工作区" })).toBeHidden();
});

test("desktop sidebar widths follow the mouse and keep room for the conversation", async ({ page, baseURL }) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  await installBackend(page, baseURL, { projectPreview: true });
  await page.goto(`/?threadId=${THREAD_ID}`);
  const open = page.getByRole("button", { name: "展开右侧工作区" });
  const close = page.getByRole("button", { name: "收起右侧工作区" });
  const history = page.getByRole("complementary", { name: "研究会话" });
  const historyGrip = page.getByRole("separator", { name: "调整左侧会话栏宽度" });
  const workspaceGrip = page.getByRole("separator", { name: "调整右侧工作区宽度" });
  const drawer = page.locator(".chat-workspace-dock");
  const drag = async (grip, distance) => {
    const { x, y, width } = await grip.boundingBox();
    const startX = x + width / 2;
    const startY = y + 100;
    await page.mouse.move(startX, startY);
    await page.mouse.down();
    await page.mouse.move(startX + distance, startY, { steps: 6 });
    await page.mouse.up();
  };

  await expect(historyGrip).toBeVisible();
  const startingHistoryWidth = Number(await historyGrip.getAttribute("aria-valuenow"));
  await open.click();
  const startingWorkspaceWidth = Number(await workspaceGrip.getAttribute("aria-valuenow"));
  await expect(drawer).toHaveCSS("width", `${startingWorkspaceWidth}px`);

  await drag(historyGrip, 72);
  await expect(history).toHaveCSS("width", `${startingHistoryWidth + 72}px`);
  await expect(drawer).toHaveCSS("width", `${startingWorkspaceWidth}px`);

  await drag(workspaceGrip, -80);
  await expect(drawer).toHaveCSS("width", `${startingWorkspaceWidth + 80}px`);
  await expect(history).toHaveCSS("width", `${startingHistoryWidth + 72}px`);
  await drag(workspaceGrip, 34);
  const chosenWorkspaceWidth = startingWorkspaceWidth + 46;
  await expect(drawer).toHaveCSS("width", `${chosenWorkspaceWidth}px`);

  const widestHistory = Number(await historyGrip.getAttribute("aria-valuemax"));
  await historyGrip.press("End");
  await expect(history).toHaveCSS("width", `${widestHistory}px`);
  expect((await page.locator('[data-panel-id="chat"]').boundingBox()).width).toBeGreaterThanOrEqual(360);

  await close.click();
  await expect(drawer).toHaveCSS("width", "0px");
  await expect(workspaceGrip).toBeHidden();
  await open.click();
  await expect(drawer).toHaveCSS("width", `${chosenWorkspaceWidth}px`);
  await expect(page.getByText(HUMAN.content, { exact: true })).toBeVisible();

  await page.setViewportSize({ width: 1024, height: 800 });
  await expect(drawer).toHaveCSS("position", "absolute");
  const chat = page.locator('[data-panel-id="chat"]');
  const narrowChatWidth = (await chat.boundingBox()).width;
  await drag(workspaceGrip, 24);
  await expect(drawer).toHaveCSS("width", `${chosenWorkspaceWidth - 24}px`);
  expect((await chat.boundingBox()).width).toBe(narrowChatWidth);
});

test("mobile research history opens separately and closes after selecting or starting a thread", async ({ page, baseURL }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await installBackend(page, baseURL, { projectPreview: true });
  await page.goto("/?view=home");
  await page.getByRole("button", { name: "打开平台导航" }).click();
  const navigation = page.getByRole("dialog", { name: "平台导航" });
  await navigation.getByRole("button", { name: "金乌科研助手" }).click();
  await expect(navigation).toBeHidden();
  const openHistory = page.getByRole("button", { name: "打开研究会话" });
  await openHistory.click();
  const history = page.getByRole("dialog", { name: "研究会话" });
  await expect(history).toBeVisible();
  await history.getByRole("button", { name: /^隔离回放测试/ }).click();
  await expect(history).toBeHidden();
  await expect(page.getByText(HUMAN.content, { exact: true })).toBeVisible();
  await openHistory.click();
  await history.getByRole("button", { name: "开始新实验" }).click();
  await expect(history).toBeHidden();
  await expect(page).not.toHaveURL(/threadId=/);
});

test("starter buttons fill actionable editable drafts without submitting", async ({
  page,
  baseURL,
}) => {
  const backend = await installBackend(page, baseURL, { empty: true });
  await page.goto(`/?threadId=${THREAD_ID}&assistantId=JW`);
  const draft = page.getByRole("textbox", { name: "消息", exact: true });
  await expect(draft).toHaveValue("");
  for (const [label, expected] of [
    ["调研某个主题的最新论文", "太阳耀斑预测近三年"],
    ["设计一份实验方案", "暂不执行实验"],
    ["预测24h内耀斑爆发情况", "从本次真实签发时刻起未来24小时"],
  ]) {
    await page.getByRole("button", { name: label, exact: true }).click();
    await expect(draft).toHaveValue(new RegExp(expected));
    await expect(draft).toBeFocused();
    expect(await draft.inputValue()).not.toBe(label);
    expect(backend.observed.mutations).toEqual([]);
  }
  await expect(draft).toHaveValue(/HTML下载入口/);
  await draft.fill((await draft.inputValue()) + " 请保留全部测试年份。");
  await expect(draft).toHaveValue(/请保留全部测试年份。$/);
  await expect(
    page.getByRole("button", { name: "分析工作区文件", exact: true })
  ).toHaveCount(0);
  expect(backend.observed.joins).toEqual([]);
  expect(backend.observed.mutations).toEqual([]);
});

test("reconnect replays 1000 message chunks without losing the report or creating a run", async ({
  page,
  baseURL,
}) => {
  const backend = await installBackend(page, baseURL, { replay: true });
  await page.goto(`/?threadId=${THREAD_ID}&assistantId=JW`);

  // Check both the rendered report and successful SSE consumption. A fallback
  // checkpoint alone cannot pass: only the SDK's successful join removes its
  // resume marker; the #185 failure leaves it behind and logs a browser error.
  await expect(
    page.getByRole("heading", { name: "离线回放报告", exact: true })
  ).toBeVisible();
  await expect(
    page.getByText("回放完成（1000块）", { exact: true })
  ).toBeVisible();
  await expect(page.getByText("证".repeat(998), { exact: true })).toBeVisible();
  await expect(
    page.getByRole("button", { name: "编辑消息", exact: true })
  ).toBeEnabled();
  await expect
    .poll(() =>
      page.evaluate(
        (id) => sessionStorage.getItem(`lg:stream:${id}`),
        THREAD_ID
      )
    )
    .toBeNull();
  expect(backend.observed.joins).toEqual([{ lastEventId: "-1" }]);
  expect(backend.observed.mutations).toEqual([]);
  expect(backend.observed.unexpected).toEqual([]);
  expect(backend.observed.errors).toEqual([]);

  await page.reload();
  await expect(page.getByText("证".repeat(998), { exact: true })).toBeVisible();
  await expect(
    page.getByText("回放完成（1000块）", { exact: true })
  ).toBeVisible();
  expect(backend.observed.joins).toHaveLength(1);
  expect(backend.observed.mutations).toEqual([]);
  expect(backend.observed.unexpected).toEqual([]);
  expect(backend.observed.errors).toEqual([]);
});

test("project inputs preserve their namespace and thread binding in read-only preview and download links", async ({
  page,
  baseURL,
}) => {
  const backend = await installBackend(page, baseURL, { projectPreview: true });
  await page.goto(`/?threadId=${THREAD_ID}&assistantId=JW`);
  await page
    .getByRole("button", { name: PROJECT_INPUT_PATH, exact: true })
    .click();
  const dialog = page.getByRole("dialog", {
    name: PROJECT_INPUT_PATH,
    exact: true,
  });
  await expect(dialog).toBeVisible();
  await expect(
    dialog.getByText("registered solar data", { exact: true })
  ).toBeVisible();
  await expect(
    dialog.getByRole("button", { name: "编辑文件", exact: true })
  ).toHaveCount(0);
  await expect(
    dialog.getByRole("button", { name: "删除文件", exact: true })
  ).toHaveCount(0);
  await expect(
    dialog.getByRole("button", { name: "复制路径", exact: true })
  ).toBeVisible();
  const download = dialog.getByRole("link", { name: "下载", exact: true });
  await expect(download).toHaveAttribute("download", "cycles.txt");
  const downloadUrl = new URL(await download.getAttribute("href"), baseURL);
  expect(downloadUrl.pathname).toBe("/api/workspace/file");
  expect(Object.fromEntries(downloadUrl.searchParams)).toEqual({
    path: PROJECT_INPUT_PATH,
    download: "1",
    threadId: THREAD_ID,
  });
  // Keep the request inside the isolated page transport. Native browser
  // download handling can bypass page interception; the server route tests
  // separately verify attachment headers and the exact registered bytes.
  const downloaded = await page.evaluate(async (url) => {
    const response = await fetch(url);
    return {
      status: response.status,
      disposition: response.headers.get("content-disposition"),
      content: await response.text(),
    };
  }, downloadUrl.toString());
  expect(downloaded).toEqual({
    status: 200,
    disposition: 'attachment; filename="cycles.txt"',
    content: PROJECT_INPUT_CONTENT,
  });
  expect(backend.observed.projectReads).toEqual([
    { threadId: THREAD_ID, path: PROJECT_INPUT_PATH, download: false },
    { threadId: THREAD_ID, path: PROJECT_INPUT_PATH, download: true },
  ]);
  expect(backend.observed.mutations).toEqual([]);
  expect(backend.observed.unexpected).toEqual([]);
  expect(backend.observed.errors).toEqual([]);
});

test("editing and copying preserve their state across cancellation and version selection", async ({
  page,
  baseURL,
}) => {
  const backend = await installBackend(page, baseURL, { versions: true });
  await page.context().grantPermissions(["clipboard-read", "clipboard-write"], {
    origin: new URL(baseURL).origin,
  });
  await page.goto(`/?threadId=${THREAD_ID}&assistantId=JW`);
  const edit = page.getByRole("button", { name: "编辑消息", exact: true });
  const draft = page.getByRole("textbox", { name: "编辑消息", exact: true });
  await expect(
    page.getByText(NEW_HUMAN.content, { exact: true })
  ).toBeVisible();
  await page.getByRole("button", { name: "复制消息", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "已复制", exact: true })
  ).toBeVisible();
  expect(await page.evaluate(() => navigator.clipboard.readText())).toBe(
    NEW_HUMAN.content
  );
  // State updates from the copy action must not immediately reset its feedback.
  await expect(
    page.getByRole("button", { name: "已复制", exact: true })
  ).toBeVisible();
  await edit.click();
  await expect(draft).toHaveValue(NEW_HUMAN.content);
  await draft.fill("未提交的草稿");
  await draft.press("Escape");
  await expect(draft).toHaveCount(0);
  await edit.click();
  await expect(draft).toHaveValue(NEW_HUMAN.content);
  await draft.fill("另一份未提交草稿");
  await page.getByRole("button", { name: "取消", exact: true }).click();
  await expect(draft).toHaveCount(0);

  await page
    .getByRole("button", { name: "上一个消息版本", exact: true })
    .click();
  await expect(
    page.getByText(OLD_HUMAN.content, { exact: true })
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "复制消息", exact: true })
  ).toBeVisible();
  await edit.click();
  await expect(draft).toHaveValue(OLD_HUMAN.content);
  await draft.fill("切换版本前未提交的草稿");
  await draft.press("Escape");
  await page
    .getByRole("button", { name: "下一个消息版本", exact: true })
    .click();
  await expect(draft).toHaveCount(0);
  await expect(
    page.getByText(NEW_HUMAN.content, { exact: true })
  ).toBeVisible();
  await edit.click();
  await expect(draft).toHaveValue(NEW_HUMAN.content);
  // Sending unchanged text simply closes the editor; it must not create a run.
  await draft.press("Enter");
  await expect(draft).toHaveCount(0);
  expect(backend.observed.mutations).toEqual([]);
  expect(backend.observed.unexpected).toEqual([]);
  expect(backend.observed.errors).toEqual([]);
});
