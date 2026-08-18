import assert from "node:assert/strict";
import test from "node:test";

import {
  describeResearchTerminal,
  parseResearchTerminalMessage,
} from "../src/lib/researchReviewTerminal.js";

test("parses a persisted blocked terminal protocol message", () => {
  const raw =
    "[RESEARCH REVIEW TERMINAL] No further tool action is allowed in this turn. " +
    "status=blocked; reason=REQUIRED_SPECIALIST_FAILED_TWICE. " +
    "The current artifact remains persisted.";
  const parsed = parseResearchTerminalMessage(raw);

  assert.equal(parsed?.status, "blocked");
  assert.equal(parsed?.reasonCode, "REQUIRED_SPECIALIST_FAILED_TWICE");
  assert.equal(parsed?.raw, raw);
  assert.equal(describeResearchTerminal(parsed).title, "数据处理未完成");
});

test("describes a repeated data specialist failure in Chinese", () => {
  const copy = describeResearchTerminal({
    status: "blocked",
    reasonCode: "REQUIRED_SPECIALIST_FAILED_TWICE",
    stage: "data",
    producer: "solar-data",
    failureCount: 2,
    recovery: "new_task_after_fix",
  });

  assert.equal(copy.title, "数据处理未完成");
  assert.match(copy.description, /solar-data/);
  assert.match(copy.action, /新建一个任务/);
});

test("uses a safe fallback for unknown terminal reasons", () => {
  const copy = describeResearchTerminal({
    status: "blocked",
    reasonCode: "SOMETHING_NEW",
    stage: "integration",
  });

  assert.equal(copy.title, "综合审查已停止");
  assert.match(copy.action, /技术详情/);
});

test("does not reinterpret normal assistant content", () => {
  assert.equal(parseResearchTerminalMessage("正常科研回答"), null);
});
