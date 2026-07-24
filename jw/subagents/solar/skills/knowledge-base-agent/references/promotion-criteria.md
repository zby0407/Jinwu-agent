# 晋升标准（§4.9.6 审核门）

candidate → canonical 只有审核门一条路。只有两条自动规则，满足任一才 auto_approved：

1. **跨运行复现**（cross_run_reproduction）：条目内容或 provenance 关联 ≥2 个不同
   run_id 的支持证据。
2. **专家审核**（expert_review）：有非空 reviewer 的人工背书。

DOI 只证明文献身份，不证明条目主张被直接支持。单篇文献或单源摘要即使有 DOI，也必须
保持 candidate 并进入人工审核；不得触发自动晋升。

要点：

- auto_approved 的条目在 provenance 永久带 `human_reviewed` 标记（无 reviewer 时为
  false），面板可筛选复查——自动审不等于人审。
- 不满足自动规则时晋升请求进入 pending 队列，由人通过 `kb_review_decide` 决定；
  条目保持 candidate，不影响检索使用（candidate 默认可见，但必须标注未晋升）。
- 你没有 `kb_promote`。你的工作是准备晋升依据：汇总支持 run_id、直接证据范围、相关性
  判定和反证检查，写成建议交给人/主 Agent 触发审核门。DOI 只用于定位来源。
- 废弃与取代同样走审核门：发现 canonical 被新证据削弱时，提出废弃建议并说明理由
  （新证据矛盾 / 数据源校正 / 理论被削弱），而不是直接改条目。
