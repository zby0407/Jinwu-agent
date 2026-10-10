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
const PROJECT_INPUT_CONTENT = "registered solar data\nsource: isolated fixture\n";
const PROJECT_ANSWER = {
  id: "answer-project-input",
  type: "ai",
  content: `登记输入：\`${PROJECT_INPUT_PATH}\``,
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
  { replay = false, versions = false, projectPreview = false } = {}
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
          projectPreview
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
    metadata: { graph_id: "JW", title: "隔离回放测试" },
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
      if (method === "POST" && apiPath === "/threads/search")
        return json([thread()]);
      if (method === "GET" && apiPath === `/threads/${THREAD_ID}/state`)
        return json(current());
      if (method === "GET" && apiPath === `/threads/${THREAD_ID}`)
        return json(thread());
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
            "Content-Disposition": `${download ? "attachment" : "inline"}; filename="cycles.txt"`,
          },
          body: PROJECT_INPUT_CONTENT,
        });
      }
      if (method === "GET" && path === "/api/memory")
        return json({ entries: [] });
      if (method === "GET" && path === "/api/workspace")
        return json({ dir: "/isolated-fixture", entries: [] });
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
  await page.getByRole("button", { name: PROJECT_INPUT_PATH, exact: true }).click();
  const dialog = page.getByRole("dialog", { name: PROJECT_INPUT_PATH, exact: true });
  await expect(dialog).toBeVisible();
  await expect(dialog.getByText("registered solar data", { exact: true })).toBeVisible();
  await expect(dialog.getByRole("button", { name: "编辑文件", exact: true })).toHaveCount(0);
  await expect(dialog.getByRole("button", { name: "删除文件", exact: true })).toHaveCount(0);
  await expect(dialog.getByRole("button", { name: "复制路径", exact: true })).toBeVisible();
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
