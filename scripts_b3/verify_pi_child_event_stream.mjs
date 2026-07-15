#!/usr/bin/env node
import assert from "node:assert/strict";
import {
  ChildEventProtocolError,
  ChildEventTracker,
  renderChildProgressContent,
} from "../.pi/extensions/b3-science/child-event-stream.ts";

const secretThinking = "RAW_CHAIN_OF_THOUGHT_MUST_NEVER_APPEAR";
const secretArgument = "SECRET_TOOL_ARGUMENT_MUST_NEVER_APPEAR";
const startedAt = 1_000_000;
const tracker = new ChildEventTracker(
  "b3-research-planner",
  "medium",
  startedAt,
  50 * 1024,
);
const visible = [];
const updates = [];

function feed(event, now) {
  const update = tracker.consumeLine(JSON.stringify(event), now);
  if (update) {
    updates.push(update);
    visible.push(JSON.stringify(update));
  }
}

const started = tracker.started(startedAt);
updates.push(started);
visible.push(JSON.stringify(started));
feed({ type: "session", version: 3 }, startedAt + 10);
feed(
  {
    type: "message_update",
    message: {
      role: "assistant",
      content: [{ type: "thinking", thinking: secretThinking }],
    },
    assistantMessageEvent: { type: "thinking_start" },
  },
  startedAt + 20,
);
feed(
  {
    type: "message_update",
    message: {
      role: "assistant",
      content: [{ type: "thinking", thinking: secretThinking }],
    },
    assistantMessageEvent: { type: "thinking_delta", delta: secretThinking },
  },
  startedAt + 30,
);
feed(
  {
    type: "tool_execution_start",
    toolCallId: "tool-1",
    toolName: "b3_run_tool",
    args: {
      toolId: "planning.audit_data_vintage",
      inputJson: secretArgument,
    },
  },
  startedAt + 40,
);
feed(
  {
    type: "tool_execution_end",
    toolCallId: "tool-1",
    toolName: "b3_run_tool",
    result: { content: secretArgument },
    isError: false,
  },
  startedAt + 1_040,
);
feed(
  {
    type: "tool_execution_start",
    toolCallId: "tool-2",
    toolName: "b3_run_tool",
    args: { toolId: "planning.validate_plan_draft" },
  },
  startedAt + 1_050,
);
feed(
  {
    type: "tool_execution_end",
    toolCallId: "tool-2",
    toolName: "b3_run_tool",
    isError: true,
  },
  startedAt + 2_050,
);
feed(
  {
    type: "message_update",
    assistantMessageEvent: { type: "text_start" },
  },
  startedAt + 2_060,
);
const finalText = JSON.stringify({
  schema_version: "b3-agent-handoff-v1",
  status: "needs_revision",
});
feed(
  {
    type: "message_end",
    message: {
      role: "assistant",
      content: [
        { type: "thinking", thinking: secretThinking },
        { type: "text", text: finalText },
      ],
    },
  },
  startedAt + 2_070,
);

const heartbeat = tracker.heartbeat(startedAt + 20_000);
updates.push(heartbeat);
visible.push(JSON.stringify(heartbeat));
const visibleText = visible.join("\n");
assert.equal(visibleText.includes(secretThinking), false);
assert.equal(visibleText.includes(secretArgument), false);
assert.equal(visibleText.includes("planning.audit_data_vintage"), true);
assert.equal(visibleText.includes("planning.validate_plan_draft"), false);
assert.equal(visibleText.includes("ResearchPlan 1.0 合同校验"), true);
assert.equal(heartbeat.currentRecorded, false);
assert.equal(heartbeat.progressLines.length <= 6, true);
assert.equal(heartbeat.workingMessage.includes("正在汇总最终 JSON 契约"), true);
assert.equal(heartbeat.workingMessage.includes("工具 2/2"), true);
assert.equal(heartbeat.workingMessage.includes("校验 1"), true);
assert.equal(heartbeat.workingMessage.includes("20秒"), true);
assert.equal(
  updates.every((update) => update.progressLines.length <= 6),
  true,
);
assert.equal(
  updates.every((update) => update.progressLines.every((line) => line.length <= 190)),
  true,
);
assert.equal(
  updates.every(
    (update) => renderChildProgressContent(update).split("\n").length <= 6,
  ),
  true,
);
assert.equal(tracker.summary().finalText, finalText);
assert.equal(tracker.summary().validationAttempts, 1);
assert.equal(tracker.summary().toolsStarted, 2);
assert.equal(tracker.summary().toolsCompleted, 2);
assert.equal(tracker.summary().toolsFailed, 1);

const diagnostic = tracker.diagnostic("wall_timeout", startedAt + 900_000);
assert.equal(diagnostic.includes(secretThinking), false);
assert.equal(diagnostic.includes(secretArgument), false);
assert.equal(diagnostic.includes("reason=wall_timeout"), true);
assert.equal(diagnostic.includes("validation_attempts=1"), true);

assert.throws(
  () => tracker.consumeLine("not-json", startedAt + 2_080),
  (error) =>
    error instanceof ChildEventProtocolError && error.code === "non_json_line",
);

process.stdout.write(
  `${JSON.stringify({
    schema_version: "b3-child-event-stream-verifier-v1",
    passed: true,
    raw_thinking_hidden: true,
    tool_arguments_hidden: true,
    bounded_progress_history: true,
    bounded_rendered_progress: true,
    dynamic_working_summary: true,
    user_facing_tool_aliases: true,
    heartbeat_deduplicated: true,
    final_text_extracted: true,
    timeout_diagnostic_bounded: true,
    event_version: tracker.summary().eventVersion,
  })}\n`,
);
