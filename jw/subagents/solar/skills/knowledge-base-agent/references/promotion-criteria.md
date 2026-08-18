# 晋升标准（§4.9.6 证据门）

candidate → canonical 只有证据门一条路：

1. **跨运行复现**（cross_run_reproduction）：条目内容或 provenance 关联 ≥2 个不同
   run_id 的支持证据。
DOI 只证明文献身份，不证明条目主张被直接支持。单篇文献或单源摘要即使有 DOI，也必须
保持 candidate；不得触发自动晋升。

要点：

- 不满足跨运行复现规则时返回 `promotion_not_ready`；条目保持 candidate，不影响检索使用
  （candidate 默认可见，但必须标注未晋升）。
- 你没有 `kb_promote`。你的工作是准备晋升依据：汇总支持 run_id、直接证据范围、相关性
  判定和反证检查，写成维护缺口交给主 Agent。DOI 只用于定位来源。
- 废弃与取代同样要求可追溯证据：发现 canonical 被新证据削弱时，提出废弃建议并说明理由
  （新证据矛盾 / 数据源校正 / 理论被削弱），而不是直接改条目。
