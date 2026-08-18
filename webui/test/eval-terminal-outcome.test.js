import assert from "node:assert/strict";
import test from "node:test";

import {
  blockedResearchOutcome,
  classifyOutcome,
  isTerminalOutcome,
} from "../../research/review/evals/terminal_outcome.mjs";

test("does not count a research terminal protocol as a scientific answer", () => {
  const result = classifyOutcome(
    { status: "idle" },
    {
      values: {
        messages: [
          {
            type: "ai",
            content:
              "[RESEARCH REVIEW TERMINAL] status=blocked; " +
              "reason=REQUIRED_SPECIALIST_FAILED_TWICE.",
          },
        ],
      },
    },
    { status: "success" },
  );

  assert.equal(result.outcome, "research_blocked");
  assert.equal(result.terminal_status, "blocked");
  assert.equal(result.has_answer, false);
  assert.equal(result.assistant_answer_count, 0);
});

test("keeps ordinary assistant output as a completed answer", () => {
  const result = classifyOutcome(
    { status: "idle" },
    { values: { messages: [{ type: "ai", content: "科学假设结果" }] } },
    { status: "success" },
  );

  assert.equal(result.outcome, "completed_with_answer");
  assert.equal(result.has_answer, true);
});

test("treats a blocked research protocol as a polling terminal", () => {
  assert.equal(isTerminalOutcome("research_blocked"), true);
});

test("turns the persisted blocked review status into an immediate terminal", () => {
  const result = blockedResearchOutcome(
    {
      status: "blocked",
      currentStage: "data",
      terminal: {
        reasonCode: "REQUIRED_SPECIALIST_FAILED_TWICE",
        summary: "Evidence output could not be persisted.",
      },
    },
    { has_answer: false, assistant_answer_count: 0 },
  );

  assert.deepEqual(result, {
    outcome: "research_blocked",
    terminal_status: "blocked",
    has_answer: false,
    assistant_answer_count: 0,
    error_summary:
      "REQUIRED_SPECIALIST_FAILED_TWICE: Evidence output could not be persisted.",
    blocked_stage: "data",
  });
});
