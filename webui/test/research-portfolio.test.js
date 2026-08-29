import assert from "node:assert/strict";
import test from "node:test";

import {
  FORECAST_ORIGIN_LABELS,
  PORTFOLIO_ROLE_LABELS,
  PORTFOLIO_STATUS_LABELS,
  describePortfolioSummary,
} from "../src/lib/researchPortfolio.js";

test("labels evidence lifecycle fields for reader-visible Chinese", () => {
  assert.equal(PORTFOLIO_ROLE_LABELS.empirical_anchor, "经验锚点");
  assert.equal(PORTFOLIO_ROLE_LABELS.physical_precursor, "物理前兆");
  assert.equal(PORTFOLIO_ROLE_LABELS.physical_discriminator, "物理判别");
  assert.equal(PORTFOLIO_STATUS_LABELS.active_top3, "现役");
  assert.equal(PORTFOLIO_STATUS_LABELS.blocked_by_data, "数据阻断");
  assert.equal(FORECAST_ORIGIN_LABELS.early_cycle, "早期周期信息");
  assert.equal(FORECAST_ORIGIN_LABELS.cycle_minimum, "极小期前兆");
});

test("counts only active_top3 hypotheses as active", () => {
  const summary = describePortfolioSummary([
    { portfolioStatus: "active_top3" },
    { portfolioStatus: "blocked_by_data" },
    { portfolioStatus: "rejected" },
  ]);

  assert.equal(summary.activeCount, 1);
  assert.equal(summary.totalCount, 3);
  assert.match(summary.label, /1 项现役假设/);
  assert.doesNotMatch(summary.label, /三个已达标假设/);
});

test("does not imply a completed portfolio when no hypothesis is active", () => {
  const summary = describePortfolioSummary([
    { portfolioStatus: "candidate_pending_test" },
    { portfolioStatus: "challenger_pool" },
    { portfolioStatus: "blocked_by_data" },
  ]);

  assert.equal(summary.activeCount, 0);
  assert.equal(summary.label, "暂无现役假设；候选项仍在检验、挑战或数据补齐阶段");
});
