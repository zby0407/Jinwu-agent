export const PORTFOLIO_ROLE_LABELS = Object.freeze({
  empirical_anchor: "经验锚点",
  physical_precursor: "物理前兆",
  physical_discriminator: "物理判别",
  challenger: "挑战者",
});

export const PORTFOLIO_STATUS_LABELS = Object.freeze({
  candidate_pending_test: "待检验",
  active_top3: "现役",
  challenger_pool: "挑战池",
  rejected: "已拒绝",
  blocked_by_data: "数据阻断",
});

export const FORECAST_ORIGIN_LABELS = Object.freeze({
  early_cycle: "早期周期信息",
  cycle_minimum: "极小期前兆",
  not_applicable: "不适用",
});

export function describePortfolioSummary(rows) {
  const values = Array.isArray(rows) ? rows : [];
  const activeCount = values.filter(
    (row) => row?.portfolioStatus === "active_top3"
  ).length;
  const totalCount = values.length;
  const label = activeCount
    ? `${activeCount} 项现役假设（其余为待检验、挑战、拒绝或数据阻断）`
    : "暂无现役假设；候选项仍在检验、挑战或数据补齐阶段";
  return { activeCount, totalCount, label };
}
