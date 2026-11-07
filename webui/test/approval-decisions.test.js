import assert from "node:assert/strict";
import test from "node:test";
import { submitApprovalDecision } from "../src/lib/approvalDecisions.js";

test("failed multi-card submission retains all decisions for a complete retry", async () => {
  const pending = { current: {} };
  const approve = { type: "approve" };
  const edit = { type: "edit", args: { command: "keep backup" } };
  const batches = [];
  const resume = async (batch) => { batches.push(batch); return batches.length > 1; };
  assert.equal(await submitApprovalDecision(pending, 2, 0, approve, resume), true);
  assert.equal(batches.length, 0);
  assert.equal(await submitApprovalDecision(pending, 2, 1, edit, resume), false);
  assert.deepEqual(pending.current, { 0: approve, 1: edit });
  assert.equal(await submitApprovalDecision(pending, 2, 1, edit, resume), true);
  assert.deepEqual(batches, [{ decisions: [approve, edit] }, { decisions: [approve, edit] }]);
  assert.deepEqual(pending.current, {});
});

test("a rejected transport promise preserves decisions", async () => {
  const pending = { current: {} };
  await assert.rejects(submitApprovalDecision(pending, 1, 0, { type: "reject" }, async () => { throw new Error("offline"); }), /offline/);
  assert.deepEqual(pending.current, { 0: { type: "reject" } });
});

test("a late success cannot clear decisions for a newer card or session", async () => {
  const pending = { current: {} };
  let complete;
  const result = submitApprovalDecision(pending, 1, 0, { type: "approve" }, () => new Promise(resolve => { complete = resolve; }));
  const newer = { 0: { type: "reject" } };
  pending.current = newer;
  complete(true);
  assert.equal(await result, true);
  assert.equal(pending.current, newer);
});
