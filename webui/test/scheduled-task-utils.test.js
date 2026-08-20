import assert from "node:assert/strict";
import test from "node:test";

import { validateFivePartCron } from "../src/lib/cronValidation.js";
import {
  classifyScheduledRunStatus,
  finalScheduledFeedback,
  initialScheduledPrompt,
  legacyTaskKeyForPrompt,
  needsScheduledTaskMigration,
  scheduledPromptFromRun,
} from "../src/lib/scheduledTaskUtils.js";

test("validates five-part cron values and syntax", () => {
  assert.equal(validateFivePartCron("0 9 * * 1-5"), null);
  assert.equal(validateFivePartCron("*/15 0,12 * * *"), null);
  assert.match(validateFivePartCron("60 9 * * *"), /分钟字段无效/);
  assert.match(validateFivePartCron("0 24 * * *"), /小时字段无效/);
  assert.match(validateFivePartCron("0 9 0 * *"), /日期字段无效/);
  assert.match(validateFivePartCron("0 9 * 13 *"), /月份字段无效/);
  assert.match(validateFivePartCron("0 9 * * 8"), /星期字段无效/);
  assert.match(validateFivePartCron("0 9 * *"), /五段/);
});

test("extracts the initial prompt and final assistant markdown", () => {
  const values = {
    messages: [
      { type: "human", content: "按日调查新论文" },
      { type: "tool", content: "ignored" },
      { type: "ai", content: [{ type: "text", text: "# 完成\n\n结果" }] },
    ],
  };
  assert.equal(initialScheduledPrompt(values), "按日调查新论文");
  assert.equal(finalScheduledFeedback(values), "# 完成\n\n结果");
});

test("extracts a legacy prompt from run metadata while its thread is still busy", () => {
  assert.equal(
    scheduledPromptFromRun({ metadata: { prompt: "  每日论文  " } }),
    "每日论文"
  );
  assert.equal(
    scheduledPromptFromRun({
      kwargs: {
        input: { messages: [{ type: "human", content: "运行任务" }] },
      },
    }),
    "运行任务"
  );
});

test("classifies terminal limit text as failure even after server success", () => {
  assert.equal(classifyScheduledRunStatus("running"), "running");
  assert.equal(classifyScheduledRunStatus("success", "报告完成"), "completed");
  assert.equal(
    classifyScheduledRunStatus("success", "Model call limits exceeded: run limit (24/24)"),
    "failed"
  );
  assert.equal(classifyScheduledRunStatus("success", "执行超时"), "timeout");
  assert.equal(classifyScheduledRunStatus("interrupted"), "interrupted");
});

test("binds legacy runs only when their prompt matches one task exactly", () => {
  const unique = [{ task_key: "a", prompt: "same" }];
  assert.equal(legacyTaskKeyForPrompt(unique, "same"), "a");
  assert.equal(legacyTaskKeyForPrompt(unique, "other"), null);
  assert.equal(
    legacyTaskKeyForPrompt(
      [
        { task_key: "a", prompt: "same" },
        { task_key: "b", prompt: "same" },
      ],
      "same"
    ),
    null
  );
});

test("detects legacy tasks needing timezone and retention migration", () => {
  assert.equal(
    needsScheduledTaskMigration({
      task_key: "a",
      timezone: "Asia/Shanghai",
      on_run_completed: "keep",
      enabled: true,
    }),
    false
  );
  assert.equal(
    needsScheduledTaskMigration({ timezone: null, on_run_completed: "delete" }),
    true
  );
});
