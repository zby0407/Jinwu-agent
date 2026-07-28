import assert from "node:assert/strict";
import test from "node:test";

import { isCrossOriginRequest } from "../src/lib/server/origin-guard.js";

const localRequest = {
  origin: "http://localhost:4716",
  host: "localhost:4716",
  protocol: "http:",
};

test("allows same-origin Fetch Metadata despite an internal listener URL", () => {
  assert.equal(
    isCrossOriginRequest({
      ...localRequest,
      secFetchSite: "same-origin",
      // Deliberately not part of the guard contract: internal origins must not
      // influence a request the browser has identified as same-origin.
      requestUrlOrigin: "http://127.0.0.1:3000",
    }),
    false
  );
});

test("allows a matching Origin and external Host without Fetch Metadata", () => {
  assert.equal(isCrossOriginRequest(localRequest), false);
});

test("allows a matching HTTPS origin behind a reverse proxy", () => {
  assert.equal(
    isCrossOriginRequest({
      origin: "https://research.example.test",
      host: "127.0.0.1:4716",
      forwardedHost: "research.example.test",
      protocol: "http:",
      forwardedProto: "https",
    }),
    false
  );
});

test("rejects same-site and cross-site Fetch Metadata", () => {
  for (const secFetchSite of ["same-site", "cross-site"]) {
    assert.equal(
      isCrossOriginRequest({ ...localRequest, secFetchSite }),
      true,
      secFetchSite
    );
  }
});

test("rejects a different host or protocol", () => {
  assert.equal(
    isCrossOriginRequest({
      ...localRequest,
      origin: "http://127.0.0.1:4716",
    }),
    true
  );
  assert.equal(
    isCrossOriginRequest({
      ...localRequest,
      origin: "https://localhost:4716",
    }),
    true
  );
});

test("rejects malformed, opaque, and non-serialized origins", () => {
  for (const origin of [
    "not a URL",
    "null",
    "http://localhost:4716/path",
    "http://user:pass@localhost:4716",
  ]) {
    assert.equal(
      isCrossOriginRequest({ ...localRequest, origin }),
      true,
      origin
    );
  }
});

test("allows direct navigation without an Origin header", () => {
  assert.equal(
    isCrossOriginRequest({
      host: "localhost:4716",
      protocol: "http:",
      secFetchSite: "none",
    }),
    false
  );
});
