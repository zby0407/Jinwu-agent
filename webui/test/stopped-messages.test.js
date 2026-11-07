import assert from "node:assert/strict";
import test from "node:test";
import { mergeStoppedMessages } from "../src/lib/stoppedMessages.js";
import { isTransientProviderError, isRecoverableProviderError } from "../src/lib/runRecovery.js";

test("a stale cancelled stream cannot overwrite checkpointed successful evidence", () => {
  const durable = [{id:"u",type:"human",content:"research"}, {id:"reply",type:"tool",content:"genuine successful report",tool_call_id:"call"}];
  const stale = [{id:"reply",type:"tool",content:"cancelled or error"}, {id:"partial",type:"ai",content:"visible unfinished text"}];
  const merged = mergeStoppedMessages(durable, stale);
  assert.equal(merged[1].content, "genuine successful report");
  assert.equal(merged[2].content, "visible unfinished text");
  assert.equal(durable.length, 2);
});

test("partial snapshot never introduces orphaned tools or unfinished calls", () => {
  const merged = mergeStoppedMessages([], [{id:"u",type:"human",content:"new"}, {id:"tool",type:"tool",content:"orphan"}, {id:"call",type:"ai",tool_calls:[{id:"pending"}]}]);
  assert.deepEqual(merged.map(m => m.id), ["u"]);
});

test("local model wall deadlines never trigger network recovery", () => {
  const error = "APIConnectionError caused by LocalModelTimeoutError [JW_PROVIDER:LOCAL_TIMEOUT]";
  assert.equal(isTransientProviderError(error), false);
  assert.equal(isRecoverableProviderError(error), false);
  assert.equal(isTransientProviderError("[JW_PROVIDER:TIMEOUT] APITimeoutError"), true);
});
