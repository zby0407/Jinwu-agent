# 晋升标准（§4.9.6 证据门）

candidate → canonical 只有证据门一条路：

1. **显式跨运行复现**（cross_run_reproduction）：通过 `kb_record_reproduction` 为条目
   登记 ≥2 个不同 run_id 的独立证据，每条都要带可定位的 evidence_ref（报告、产物、
   结果文件或哈希回执）。
2. **冲突必须先行解决**：存在未解决的反例冲突时，即使复现证据充分也返回
   `promotion_blocked_by_conflict`，条目保持 candidate。解除阻断的唯一方式是显式裁决
   （`kb_adjudicate_conflict` / WebUI「成长与冲突」）：`uphold_canonical` 让反例退役、
   `overturn_canonical` 让既有结论退役（反例仍需自己补齐两个独立 run 的证据）、
   `defer` 只记录复核而冲突继续阻断。裁决本身不晋升任何条目。
3. **阅读不是证据**：`kb_read` / provenance_log 只记录使用，不构成复现；读取次数再多
   也不能推进晋升。

DOI 只证明文献身份，不证明条目主张被直接支持。单篇文献或单源摘要即使有 DOI，也必须
保持 candidate；不得触发自动晋升。技术失败只写入 `lesson`，不能伪装成 scientific finding。

**实验 run 的自动累积**：`knowledge_base.compiler` 在 finalize 时按稳定 claim 指纹
（问题 + primary estimand + claim scope；lesson 用 failure_mode）判断是否同一主张。同一
主张被另一个 run 复现时落到同一条 candidate 并自动登记 `entry_reproduction_evidence`
（evidence_ref 形如 `run://<run_id>/record.json#sha256:<hash>`）；两个独立 run 齐备即由
编译器调用同一个证据门自动晋升，冲突未解决时同样被阻断。实验主张不需要手工建条或手工
登记复现——`kb_record_reproduction` 留给文献/概念类条目和补充证据。

要点：

- 不满足跨运行复现规则时返回 `promotion_not_ready`；条目保持 candidate，不影响检索使用
  （candidate 默认可见，但必须标注未晋升）。
- 你没有 `kb_promote` / `kb_record_reproduction`。你的工作是准备晋升依据：汇总支持
  run_id、直接证据范围、相关性判定和反证检查，写成维护缺口交给主 Agent。DOI 只用于定位来源。
- 废弃与取代同样要求可追溯证据：发现 canonical 被新证据削弱时，提出废弃建议并说明理由
  （新证据矛盾 / 数据源校正 / 理论被削弱），而不是直接改条目。
