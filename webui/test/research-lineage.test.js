import assert from "node:assert/strict";
import test from "node:test";

import {
  buildResearchTurns,
  classifyResearchArtifact,
  collectResearchRoutes,
  extractLineageFiles,
  mergeCheckpointHistory,
} from "../src/lib/researchLineage.js";

test("groups persisted research events by human turn and pairs tool results", () => {
  const turns = buildResearchTurns([
    { type: "human", id: "h1", content: "分析太阳黑子" },
    {
      type: "ai",
      id: "a1",
      content: "",
      tool_calls: [
        { id: "c1", name: "kb_search", args: { query: "sunspots" } },
        {
          id: "c2",
          name: "task",
          args: { subagent_type: "solar-knowledge", description: "检索资料" },
        },
      ],
    },
    {
      type: "tool",
      id: "t2",
      name: "task",
      tool_call_id: "c2",
      content: "已生成 outputs/report.md",
    },
    {
      type: "tool",
      id: "t1",
      name: "kb_search",
      tool_call_id: "c1",
      content: "3 results",
    },
    { type: "ai", id: "a2", content: "研究结论" },
    { type: "human", id: "h2", content: "继续分析磁场" },
    { type: "ai", id: "a3", content: "第二轮结论" },
  ]);

  assert.equal(turns.length, 2);
  assert.equal(turns[0].nodes[0].kind, "tool");
  assert.equal(turns[0].nodes[0].detail, "3 results");
  assert.equal(turns[0].nodes[1].kind, "agent");
  assert.equal(turns[0].nodes[1].title, "solar-knowledge");
  assert.deepEqual(turns[0].files, ["outputs/report.md"]);
  assert.equal(turns[0].finalAnswer.detail, "研究结论");
  assert.deepEqual(
    turns[0].artifacts.map(({ path, category, importance }) => ({
      path,
      category,
      importance,
    })),
    [{ path: "outputs/report.md", category: "docs", importance: "core" }]
  );
  assert.deepEqual(
    turns[0].keyNodes.map((node) => node.id),
    ["c2"]
  );
  assert.equal(turns[0].status, "complete");
  assert.equal(turns[1].title, "继续分析磁场");
});

test("marks cancelled and malformed tool results without throwing", () => {
  const turns = buildResearchTurns([
    null,
    { type: "human", id: "h", content: [{ text: "运行实验" }] },
    {
      type: "ai",
      id: "a",
      additional_kwargs: {
        tool_calls: [
          { id: "c", function: { name: "execute", arguments: "not-json" } },
        ],
      },
    },
    {
      type: "tool",
      id: "t",
      tool_call_id: "c",
      content: "任务已取消",
    },
  ]);
  assert.equal(turns[0].nodes[0].status, "cancelled");
  assert.deepEqual(turns[0].nodes[0].args, { input: "not-json" });
  assert.equal(turns[0].status, "cancelled");
  assert.deepEqual(
    turns[0].keyNodes.map((node) => node.id),
    ["c"]
  );
});

test("keeps failed tools visible as key process nodes without artifacts", () => {
  const [turn] = buildResearchTurns([
    { type: "human", id: "h", content: "运行分析" },
    {
      type: "ai",
      id: "a",
      tool_calls: [{ id: "c", name: "execute", args: { command: "run" } }],
    },
    {
      type: "tool",
      id: "t",
      tool_call_id: "c",
      status: "error",
      content: "execution failed",
    },
  ]);

  assert.equal(turn.status, "failed");
  assert.deepEqual(
    turn.keyNodes.map((node) => node.id),
    ["c"]
  );
});

test("extracts only path-shaped supported files", () => {
  assert.deepEqual(
    extractLineageFiles(
      "open outputs/chart.png and /memories/note.md, not foo.md"
    ),
    ["outputs/chart.png", "/memories/note.md"]
  );
  assert.deepEqual(
    extractLineageFiles({ path: "report.pdf", query: "foo.md" }),
    ["report.pdf"]
  );
});

test("uses the last non-empty assistant text as the persisted final answer", () => {
  const [turn] = buildResearchTurns([
    { type: "human", id: "h", content: "完成研究" },
    { type: "ai", id: "a1", content: "我先检查数据。" },
    {
      type: "ai",
      id: "a2",
      content: "最终结论见 outputs/final_report.pdf。",
    },
    { type: "ai", id: "a3", content: "" },
  ]);

  assert.equal(turn.finalAnswer.id, "a2");
  assert.deepEqual(turn.artifacts, [
    {
      path: "outputs/final_report.pdf",
      category: "docs",
      importance: "core",
      sourceNodeIds: ["a2"],
    },
  ]);
});

test("classifies result artifacts conservatively and keeps runtime files in details", () => {
  assert.equal(
    classifyResearchArtifact("artifacts/chart.png").importance,
    "core"
  );
  assert.equal(
    classifyResearchArtifact("work/reproduce.py").importance,
    "core"
  );
  assert.equal(
    classifyResearchArtifact("scripts/reproduce.py").importance,
    "detail"
  );
  assert.equal(
    classifyResearchArtifact("scripts/reproduce.py", {
      referencedByFinalAnswer: true,
    }).importance,
    "core"
  );
  assert.equal(
    classifyResearchArtifact("/skills/solar/scripts/run.py", {
      referencedByFinalAnswer: true,
    }).importance,
    "detail"
  );
  assert.equal(
    classifyResearchArtifact("/tmp/result.json", {
      referencedByFinalAnswer: true,
    }).importance,
    "detail"
  );
  assert.equal(
    classifyResearchArtifact("outputs/runtime.log").importance,
    "detail"
  );
});

test("normalizes exact paths without merging different directories", () => {
  const [turn] = buildResearchTurns(
    [
      { type: "human", id: "h", content: "生成结果" },
      {
        type: "ai",
        id: "a",
        content: "查看 ./outputs/result.csv 和 reports/result.csv。",
      },
    ],
    {
      "outputs/result.csv": "one",
      "reports/result.csv": "two",
    }
  );

  assert.deepEqual(
    turn.artifacts.map((artifact) => artifact.path),
    ["outputs/result.csv", "reports/result.csv"]
  );
});

test("keeps a completed turn without files as an explicit no-artifact state", () => {
  const [turn] = buildResearchTurns([
    { type: "human", id: "h", content: "解释现象" },
    { type: "ai", id: "a", content: "这是最终回答。" },
  ]);

  assert.equal(turn.finalAnswer.id, "a");
  assert.deepEqual(turn.artifacts, []);
  assert.deepEqual(turn.keyNodes, []);
});

test("collects the latest checkpoint snapshot for each SDK branch path", () => {
  const state = (id, message) => ({
    checkpoint: { checkpoint_id: id },
    created_at: id,
    values: { messages: [{ type: "human", id: message, content: message }] },
  });
  const routes = collectResearchRoutes({
    type: "sequence",
    items: [
      { type: "node", path: [], value: state("root", "root-message") },
      {
        type: "fork",
        items: [
          {
            type: "sequence",
            items: [
              { type: "node", path: ["a"], value: state("a1", "route-a") },
              { type: "node", path: ["a"], value: state("a2", "route-a2") },
            ],
          },
          {
            type: "sequence",
            items: [
              { type: "node", path: ["b"], value: state("b1", "route-b") },
            ],
          },
        ],
      },
    ],
  });
  assert.deepEqual(
    routes.map((route) => [route.path, route.checkpointId]),
    [
      ["a", "a2"],
      ["b", "b1"],
    ]
  );
});

test("merges older checkpoint pages without cursor overlap", () => {
  const result = mergeCheckpointHistory(
    [{ id: "new" }, { id: "cursor" }],
    [{ id: "cursor" }, { id: "old" }, { id: null }],
    (item) => item.id
  );
  assert.deepEqual(result.merged, [
    { id: "new" },
    { id: "cursor" },
    { id: "old" },
  ]);
  assert.equal(result.added, 1);
});
