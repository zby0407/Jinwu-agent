import assert from "node:assert/strict";
import test from "node:test";

import {
  artifactCategory,
  artifactSource,
  normalizeArtifactPath,
  sortAndDedupeArtifacts,
  verifiedCanonicalWorkArtifactPaths,
} from "../src/lib/artifacts.js";

test("recognizes trusted deliverable locations", () => {
  assert.equal(artifactSource("outputs/report.md"), "outputs");
  assert.equal(artifactSource("artifacts/chart.png"), "legacy");
  assert.equal(artifactSource("reports/final.pdf"), "legacy");
  assert.equal(artifactSource("results/table.csv"), "legacy");
  assert.equal(
    artifactSource("experiment/runs/run-1/report.md"),
    "experiment-report"
  );
  assert.equal(
    artifactSource("experiment/runs/run-1/public/plots/chart.png"),
    "experiment-public"
  );
  assert.equal(
    artifactSource("work/solar_data/cycle_features.csv"),
    "canonical-data"
  );
});

test("excludes inputs, intermediate state, audit files, and malformed paths", () => {
  for (const path of [
    "inputs/source.csv",
    "work/analyze.py",
    "work/other/result.csv",
    "work/solar_data/research_review/state.csv",
    "receipts/review.json",
    "experiment/runs/run-1/attempts/a/output/chart.png",
    "experiment/runs/run-1/stage_artifacts/data.csv",
    "experiment/runs/run-1/public/worker_result.json",
    "outputs/research_review/verdict.json",
    "outputs/.cache/result.csv",
    "../outputs/report.md",
    "/outputs/report.md",
  ]) {
    assert.equal(artifactSource(path), null, path);
  }
  assert.equal(normalizeArtifactPath("outputs\\plot.png"), "outputs/plot.png");
  assert.equal(normalizeArtifactPath("outputs//plot.png"), null);
});

test("selects only receipt-bound verified canonical data products", () => {
  const sha256 = "a".repeat(64);
  assert.deepEqual(
    verifiedCanonicalWorkArtifactPaths([
      {
        status: "verified",
        outputs: [
          { path: "work/solar_data/cycle_features.csv", sha256 },
          { path: "work/solar_data/cycle_features.csv", sha256 },
          { path: "work/solar_data/comparison.json", sha256 },
          { path: "outputs/report.md", sha256 },
          { path: "work/solar_data/unhashed.csv" },
        ],
      },
      {
        status: "failed",
        outputs: [{ path: "work/solar_data/failed.csv", sha256 }],
      },
      { status: "verified", outputs: "malformed" },
    ]),
    [
      "work/solar_data/cycle_features.csv",
      "work/solar_data/comparison.json",
    ]
  );
});

test("classifies common research formats", () => {
  assert.equal(artifactCategory("md"), "documents");
  assert.equal(artifactCategory("PDF"), "documents");
  assert.equal(artifactCategory("png"), "figures");
  assert.equal(artifactCategory("csv"), "data");
  assert.equal(artifactCategory("py"), "code");
  assert.equal(artifactCategory("zip"), "other");
});

test("deduplicates by normalized full path and sorts newest first", () => {
  const result = sortAndDedupeArtifacts([
    { path: "outputs/a.csv", ext: "csv", size: 1, mtime: 10 },
    { path: "outputs/a.csv", ext: "csv", size: 2, mtime: 20 },
    { path: "results/a.csv", ext: "csv", size: 3, mtime: 15 },
    { path: "work/ignored.csv", ext: "csv", size: 4, mtime: 30 },
    {
      path: "work/solar_data/verified.csv",
      ext: "csv",
      size: 5,
      mtime: 25,
    },
  ]);
  assert.deepEqual(
    result.map((item) => item.path),
    ["work/solar_data/verified.csv", "outputs/a.csv", "results/a.csv"]
  );
  assert.equal(result[1].size, 2);
  assert.equal(result[0].category, "data");
});

test("applies the response limit after deduplication and sorting", () => {
  const result = sortAndDedupeArtifacts(
    Array.from({ length: 8 }, (_, index) => ({
      path: `outputs/result-${index}.json`,
      ext: "json",
      size: index,
      mtime: index,
    })),
    3
  );
  assert.deepEqual(
    result.map((item) => item.path),
    ["outputs/result-7.json", "outputs/result-6.json", "outputs/result-5.json"]
  );
});
