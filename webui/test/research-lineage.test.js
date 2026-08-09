import assert from "node:assert/strict";
import test from "node:test";

import {
  buildResearchTurns,
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
