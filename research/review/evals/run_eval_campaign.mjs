import { spawn } from "node:child_process";
import { access, appendFile, mkdir, readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const suiteFile = path.resolve(
  process.env.JW_EVAL_SUITE || path.join(HERE, "all_visible_e2e_v2.json")
);
const phase = process.argv[2];
if (!["closed-core", "two-pass-core", "two-pass-rest"].includes(phase)) {
  throw new Error(
    "Usage: node run_eval_campaign.mjs closed-core|two-pass-core|two-pass-rest"
  );
}

async function loadCases() {
  const suite = JSON.parse(await readFile(suiteFile, "utf8"));
  const rows = [];
  for (const source of suite.source_suites || []) {
    const sourceDoc = JSON.parse(
      await readFile(path.resolve(path.dirname(suiteFile), source), "utf8")
    );
    for (const entry of sourceDoc.cases || []) {
      rows.push({
        ...(suite.defaults || {}),
        ...entry,
        ...(suite.case_overrides?.[entry.id] || {}),
      });
    }
  }
  return rows;
}

const allCases = await loadCases();
const core = allCases.filter((entry) => entry.id.startsWith("SC26-"));
const nonCore = allCases.filter((entry) => !entry.id.startsWith("SC26-"));
const reviewMode = phase === "closed-core" ? "closed" : "two_pass";
const reviewer = process.env.JW_EVAL_REVIEWER || "kimi";
if (!["kimi", "deepseek", "qwen"].includes(reviewer)) {
  throw new Error("JW_EVAL_REVIEWER must be kimi, deepseek, or qwen");
}
const reviewerEnv =
  reviewer === "qwen"
    ? {
        JW_EVAL_REVIEWER_MODEL:
          process.env.JW_EVAL_QWEN_REVIEWER_MODEL || "qwen3.8-max",
        JW_EVAL_REVIEWER_PROVIDER:
          process.env.JW_EVAL_QWEN_REVIEWER_PROVIDER || "custom-openai",
        JW_EVAL_REVIEWER_FAMILY: "qwen",
        JW_EVAL_HETEROGENEOUS: "0",
      }
    : reviewer === "deepseek"
    ? {
        JW_EVAL_REVIEWER_MODEL:
          process.env.JW_EVAL_DEEPSEEK_REVIEWER_MODEL || "deepseek-v4-pro",
        JW_EVAL_REVIEWER_PROVIDER:
          process.env.JW_EVAL_DEEPSEEK_REVIEWER_PROVIDER || "deepseek",
        JW_EVAL_REVIEWER_FAMILY: "deepseek",
      }
    : {
        JW_EVAL_REVIEWER_MODEL:
          process.env.JW_EVAL_KIMI_REVIEWER_MODEL || "kimi-k3",
        JW_EVAL_REVIEWER_PROVIDER:
          process.env.JW_EVAL_KIMI_REVIEWER_PROVIDER || "kimi-coding",
        JW_EVAL_REVIEWER_FAMILY: "kimi",
        JW_EVAL_HETEROGENEOUS: "1",
      };
const jobs =
  phase === "closed-core"
    ? core.map((entry) => ({ entry, repetition: 1 }))
    : phase === "two-pass-core"
    ? core.map((entry) => ({ entry, repetition: 1 }))
    : [
        ...nonCore.map((entry) => ({ entry, repetition: 1 })),
        ...core.flatMap((entry) =>
          [2, 3].map((repetition) => ({ entry, repetition }))
        ),
      ];
const concurrency =
  phase === "two-pass-rest"
    ? Math.max(1, Math.min(2, Number(process.env.JW_EVAL_CONCURRENCY || 2)))
    : 1;
const debugPortBase = Number(process.env.JW_EVAL_DEBUG_PORT_BASE || 9300);
const campaignLog = path.join(HERE, "runs", `campaign.${phase}.jsonl`);
await mkdir(path.dirname(campaignLog), { recursive: true });

async function runJob(job, workerIndex) {
  const label = `formal.${reviewMode}.${job.entry.id}.r${job.repetition}`;
  const metadataPath = path.join(HERE, "runs", label, "metadata.json");
  if (process.env.JW_EVAL_RERUN !== "1") {
    try {
      await access(metadataPath);
      return { label, status: "skipped_existing" };
    } catch {}
  }
  const child = spawn(
    process.execPath,
    [path.join(HERE, "run_webui_case.mjs"), job.entry.id, label],
    {
      cwd: path.resolve(HERE, "../../.."),
      env: {
        ...process.env,
        JW_EVAL_SUITE: suiteFile,
        JW_EVIDENCE_REVIEW_MODE: reviewMode,
        JW_EVAL_DEBUG_PORT: String(debugPortBase + workerIndex),
        JW_EVAL_MODEL: process.env.JW_EVAL_MODEL || "qwen3.8-max",
        JW_EVAL_PROVIDER:
          process.env.JW_EVAL_PROVIDER || "custom-openai",
        JW_EVAL_PRODUCER_MODEL:
          process.env.JW_EVAL_PRODUCER_MODEL || "qwen3.8-max",
        JW_EVAL_PRODUCER_PROVIDER:
          process.env.JW_EVAL_PRODUCER_PROVIDER || "custom-openai",
        ...reviewerEnv,
      },
      stdio: "inherit",
    }
  );
  const exitCode = await new Promise((resolve) => child.once("exit", resolve));
  return {
    label,
    status: exitCode === 0 ? "completed" : "failed",
    exit_code: exitCode,
  };
}

let cursor = 0;
const results = [];
async function worker(workerIndex) {
  while (cursor < jobs.length) {
    const job = jobs[cursor++];
    const result = await runJob(job, workerIndex);
    results.push(result);
    await appendFile(
      campaignLog,
      `${JSON.stringify({
        ...result,
        recorded_at: new Date().toISOString(),
      })}\n`,
      "utf8"
    );
    if (result.status === "failed" && process.env.JW_EVAL_FAIL_FAST === "1")
      break;
  }
}

await Promise.all(
  Array.from({ length: concurrency }, (_, index) => worker(index))
);
const failed = results.filter((result) => result.status === "failed");
process.stdout.write(
  `${JSON.stringify({
    phase,
    review_mode: reviewMode,
    reviewer,
    total: results.length,
    failed: failed.length,
  })}\n`
);
if (failed.length > 0) process.exitCode = 2;
